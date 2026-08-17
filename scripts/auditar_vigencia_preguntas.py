"""
OpoCoach-Mantenimiento - Auditoría conservadora de vigencia jurídica.

Comprueba exclusivamente:
    1. Si una norma identificada de forma inequívoca figura expresamente
       como derogada en la fuente oficial.
    2. Si un artículo localizado figura expresamente como derogado.
    3. Si la norma y, cuando proceda, el artículo están localizados sin una
       indicación expresa de derogación.

Principio de seguridad:
    No encontrar una norma o un artículo NO demuestra que esté derogado.
    Las ausencias, ambigüedades y errores de consulta se clasifican como
    NO_VERIFICABLE y nunca como OBSOLETA.

Estados en lote_preguntas.estado_vigencia:
    VIGENTE                       -> comprobación oficial concluyente.
    OBSOLETA_NORMA                -> la cabecera oficial indica expresamente
                                     que la disposición está derogada.
    OBSOLETA_ARTICULO_DEROGADO    -> el encabezado del artículo indica
                                     expresamente que está derogado.
    NO_VERIFICABLE                -> pregunta analizada sin evidencia oficial
                                     suficiente para concluir.

No se utiliza OBSOLETA_ARTICULO: la mera ausencia del artículo en una
consulta no permite concluir que haya sido derogado.

Idempotencia:
    El campo vigencia_revision_clave guarda una huella del método, la norma y
    el artículo analizados. Las preguntas cuya huella no ha cambiado se omiten
    automáticamente. Si cambia la referencia o la versión del procedimiento,
    se vuelven a analizar. --forzar permite repetirlas expresamente.

Rendimiento:
    Las preguntas pendientes se agrupan por norma normalizada y artículo. Cada
    combinación se consulta una sola vez por ejecución.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests

import boe_api as boe_cliente
from boe_api import BOEError, buscar_norma, extraer_cita, obtener_articulo


RAIZ = Path(__file__).resolve().parents[1]
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
REGISTROS = RAIZ / "registros"
TIMEOUT = 35
VERSION_PROCEDIMIENTO = "vigencia-conservadora-6"

ESTADO_VIGENTE = "VIGENTE"
ESTADO_OBSOLETA_NORMA = "OBSOLETA_NORMA"
ESTADO_OBSOLETA_ARTICULO_DEROGADO = "OBSOLETA_ARTICULO_DEROGADO"
ESTADO_NO_VERIFICABLE = "NO_VERIFICABLE"
ESTADOS_VALIDOS = {
    ESTADO_VIGENTE,
    ESTADO_OBSOLETA_NORMA,
    ESTADO_OBSOLETA_ARTICULO_DEROGADO,
    ESTADO_NO_VERIFICABLE,
}


MARCADORES_AMBITO_VALENCIANO = (
    "comunitat valenciana",
    "comunidad valenciana",
    "generalitat valenciana",
    "generalitat",
    "consell",
    "gobierno valenciano",
    "govern valencia",
    "funcion publica valenciana",
    "electoral valenciana",
    "sindicatura de cuentas",
    "sindicatura de comptes",
    "presupuestos gva",
    "hacienda gva",
)


def nombre_indica_ambito_valenciano(nombre: object | None) -> bool:
    """Detecta solo indicadores explícitos presentes en las denominaciones."""
    nombre_n = normalizar(nombre)
    if any(marcador in nombre_n for marcador in MARCADORES_AMBITO_VALENCIANO):
        return True
    # GVA aparece como sigla independiente en muchas denominaciones históricas.
    return bool(re.search(r"\bgva\b", nombre_n))


@dataclass(frozen=True)
class Pregunta:
    id: int
    enunciado: str
    respuesta_correcta: str
    nombre_norma: str
    articulo: str
    nombre_norma_normalizado: str
    articulo_normalizado: str
    norma_id_normalizada: int | None
    estado_vigencia: str
    vigencia_revision_clave: str


@dataclass(frozen=True)
class Resultado:
    estado: str
    fuente: str
    referencia: str
    motivo: str


_CACHE_TEXTO_URL: dict[str, str] = {}
_CACHE_RESULTADOS: dict[tuple[str, str], Resultado] = {}
_CACHE_DATOS_DOGV: dict[str, tuple[dict[str, object], str]] = {}
_CACHE_TEXTO_DOGV: dict[str, str] = {}
_CACHE_REGLAMENTO_CORTS: tuple[str, str] | None = None

# obtener_articulo() consulta el texto XML completo de la norma. La caché
# original evita descargarlo otra vez, pero volvía a leerlo y parsearlo para
# cada artículo. Esta caché limitada mantiene en memoria las últimas normas;
# los grupos se procesan ordenados por norma para aprovecharla sin crecimiento
# de memoria ilimitado.
if not hasattr(boe_cliente.obtener_texto_completo, "cache_info"):
    boe_cliente.obtener_texto_completo = lru_cache(maxsize=4)(
        boe_cliente.obtener_texto_completo
    )


class ExtractorTextoHTML(HTMLParser):
    BLOQUES = {
        "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "br", "section", "article", "tr", "td", "th",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.omitir = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self.omitir += 1
        elif not self.omitir and tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self.omitir:
            self.omitir -= 1
        elif not self.omitir and tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.omitir and data:
            self.partes.append(data)

    def texto(self) -> str:
        lineas = [limpiar(linea) for linea in "".join(self.partes).splitlines()]
        return "\n".join(linea for linea in lineas if linea)


def limpiar(valor: object | None) -> str:
    return " ".join(str(valor or "").split()).strip()


def normalizar(valor: object | None) -> str:
    texto = unicodedata.normalize("NFKD", limpiar(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def numero_articulo(valor: str) -> str:
    texto = normalizar(valor).replace(",", ".")
    coincidencia = re.search(
        r"\b(\d+(?:\.\d+)*(?:\s+(?:bis|ter|quater|quinquies|sexies|"
        r"septies|octies|nonies|decies))?|unico)\b",
        texto,
    )
    return limpiar(coincidencia.group(1)) if coincidencia else ""


def columnas_tabla(conexion: sqlite3.Connection, tabla: str) -> set[str]:
    return {
        str(fila[1])
        for fila in conexion.execute(f'PRAGMA table_info("{tabla}")')
    }


def asegurar_estructura(conexion: sqlite3.Connection) -> None:
    columnas = columnas_tabla(conexion, "lote_preguntas")
    if "estado_vigencia" not in columnas:
        conexion.execute(
            "ALTER TABLE lote_preguntas ADD COLUMN estado_vigencia TEXT"
        )
    if "vigencia_revision_clave" not in columnas:
        conexion.execute(
            "ALTER TABLE lote_preguntas ADD COLUMN vigencia_revision_clave TEXT"
        )


def copia_seguridad(ruta_db: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = ruta_db.with_name(
        f"{ruta_db.stem}_antes_auditar_vigencia_{marca}{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, destino)
    return destino


def cargar_preguntas(
    conexion: sqlite3.Connection,
    pregunta_id: int | None,
) -> list[Pregunta]:
    columnas = columnas_tabla(conexion, "lote_preguntas")
    estado_sql = (
        "COALESCE(estado_vigencia, '')"
        if "estado_vigencia" in columnas
        else "''"
    )
    clave_sql = (
        "COALESCE(vigencia_revision_clave, '')"
        if "vigencia_revision_clave" in columnas
        else "''"
    )

    condiciones = [
        "UPPER(TRIM(COALESCE(tipo_clasificacion, ''))) = 'JURIDICA'",
    ]
    parametros: list[object] = []
    if pregunta_id is not None:
        condiciones.append("id = ?")
        parametros.append(pregunta_id)

    filas = conexion.execute(
        f"""
        SELECT
            id,
            enunciado,
            respuesta_correcta,
            nombre_norma,
            articulo,
            nombre_norma_normalizado,
            articulo_normalizado,
            norma_id_normalizada,
            {estado_sql} AS estado_vigencia,
            {clave_sql} AS vigencia_revision_clave
        FROM lote_preguntas
        WHERE {' AND '.join(condiciones)}
        ORDER BY id
        """,
        parametros,
    ).fetchall()

    return [
        Pregunta(
            id=int(fila["id"]),
            enunciado=limpiar(fila["enunciado"]),
            respuesta_correcta=limpiar(fila["respuesta_correcta"]),
            nombre_norma=limpiar(fila["nombre_norma"]),
            articulo=limpiar(fila["articulo"]),
            nombre_norma_normalizado=limpiar(
                fila["nombre_norma_normalizado"]
            ),
            articulo_normalizado=limpiar(fila["articulo_normalizado"]),
            norma_id_normalizada=(
                int(fila["norma_id_normalizada"])
                if fila["norma_id_normalizada"] is not None
                else None
            ),
            estado_vigencia=limpiar(fila["estado_vigencia"]),
            vigencia_revision_clave=limpiar(
                fila["vigencia_revision_clave"]
            ),
        )
        for fila in filas
    ]


def nombre_para_consulta(pregunta: Pregunta) -> str:
    original = limpiar(pregunta.nombre_norma).replace("_", " ")
    normalizado = limpiar(pregunta.nombre_norma_normalizado).replace("_", " ")

    if original:
        try:
            extraer_cita(original)
            return original
        except BOEError:
            pass

    if original and normalizado:
        try:
            cita = extraer_cita(normalizado)
        except BOEError:
            pass
        else:
            if re.search(r"\b\d+\s*[/_-]\s*\d{4}\b", original):
                return f"{cita.referencia} {original}"

    resultado = normalizado or original
    alias = {
        "ley organica del poder judicial": (
            "Ley Orgánica 6/1985, de 1 de julio, del Poder Judicial"
        ),
        "estatuto de autonomia de la comunitat valenciana": (
            "Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía "
            "de la Comunidad Valenciana"
        ),
    }
    return alias.get(normalizar(resultado), resultado)


def articulo_para_consulta(pregunta: Pregunta) -> str:
    valor = pregunta.articulo_normalizado or pregunta.articulo
    return numero_articulo(valor) or limpiar(valor)


def articulo_base_para_consulta(pregunta: Pregunta) -> str:
    articulo = articulo_para_consulta(pregunta)
    numero = numero_articulo(articulo)
    if not numero:
        return articulo
    # En la base, 15.2 o 48.5 identifican apartados. La vigencia se comprueba
    # sobre el artículo 15 o 48, no buscando un supuesto artículo independiente.
    return numero.split(".", 1)[0] if "." in numero else numero


def clave_cita_basica(pregunta: Pregunta) -> str:
    try:
        cita = extraer_cita(nombre_para_consulta(pregunta))
    except BOEError:
        return ""
    return "|".join((cita.tipo, cita.numero, cita.anio))


def clave_agrupacion(pregunta: Pregunta) -> tuple[str, str]:
    articulo = normalizar(articulo_base_para_consulta(pregunta))
    if pregunta.norma_id_normalizada is not None:
        return f"norma_id:{pregunta.norma_id_normalizada}", articulo
    return f"texto:{normalizar(nombre_para_consulta(pregunta))}", articulo


def clave_revision(pregunta: Pregunta) -> str:
    grupo = clave_agrupacion(pregunta)
    contenido = "|".join((VERSION_PROCEDIMIENTO, grupo[0], grupo[1]))
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def calidad_representante(pregunta: Pregunta) -> tuple[int, int, int]:
    nombre = nombre_para_consulta(pregunta)
    nombre_n = normalizar(nombre)
    tiene_fecha = bool(
        re.search(
            r"\bde\s+\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|"
            r"junio|julio|agosto|septiembre|setiembre|octubre|noviembre|"
            r"diciembre)\b",
            nombre_n,
        )
    )
    tiene_ambito = nombre_indica_ambito_valenciano(nombre)
    # El ámbito es más importante que la longitud/fecha: evita seleccionar
    # una homónima estatal cuando la norma_id identifica una norma valenciana.
    return int(tiene_ambito), int(tiene_fecha), len(nombre)


def descargar_texto(url: str) -> str:
    if url in _CACHE_TEXTO_URL:
        return _CACHE_TEXTO_URL[url]

    respuesta = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "OpoCoach-Mantenimiento/2.0"},
        allow_redirects=True,
    )
    respuesta.raise_for_status()
    parser = ExtractorTextoHTML()
    parser.feed(respuesta.text)
    texto = parser.texto()
    _CACHE_TEXTO_URL[url] = texto
    return texto


def cabecera_oficial(texto: str) -> str:
    texto_n = normalizar(texto)
    cortes = [
        posicion
        for marcador in (
            "publicado en:",
        )
        if (posicion := texto_n.find(marcador)) >= 0
    ]
    fin = min(cortes) if cortes else min(len(texto_n), 5000)
    return texto_n[:fin]


def norma_expresamente_derogada(texto: str) -> bool:
    cabecera = cabecera_oficial(texto)
    return bool(re.search(r"\bdisposicion\s+derogada\b", cabecera))


def articulo_expresamente_derogado(titulo: str, texto: str) -> bool:
    for muestra in (titulo, texto[:300]):
        muestra_n = normalizar(muestra)
        muestra_n = re.sub(
            r"^articulo\s+(?:\d+(?:\.\d+)*(?:\s+\w+)?|unico)\s*[.\-:]*\s*",
            "",
            muestra_n,
        )
        muestra_n = muestra_n.lstrip("[() .:-")
        if re.match(r"^derogad[oa]s?\b", muestra_n):
            return True
    return False


def contiene_error_red(error: BaseException) -> bool:
    actual: BaseException | None = error
    while actual is not None:
        texto = normalizar(actual)
        nombre = actual.__class__.__name__.lower()
        modulo = actual.__class__.__module__.lower()
        if "requests" in modulo or any(
            fragmento in nombre
            for fragmento in ("timeout", "connection", "http", "proxy")
        ):
            return True
        if any(
            fragmento in texto
            for fragmento in (
                "error al consultar el boe",
                "timeout",
                "timed out",
                "connection",
                "temporarily unavailable",
                "http 5",
            )
        ):
            return True
        actual = actual.__cause__ or actual.__context__
    return False


def resultado_no_verificable(
    fuente: str,
    referencia: str,
    motivo: str,
) -> Resultado:
    return Resultado(
        ESTADO_NO_VERIFICABLE,
        fuente,
        referencia,
        motivo,
    )



ALIAS_BOE_OFICIALES = {
    "ley de expropiacion forzosa de 1954": (
        "Ley de 16 de diciembre de 1954 sobre expropiación forzosa"
    ),
    "ley de expropiacion forzosa": (
        "Ley de 16 de diciembre de 1954 sobre expropiación forzosa"
    ),
    "codigo civil": (
        "Real Decreto de 24 de julio de 1889 por el que se publica el Código Civil"
    ),
}

URL_UE_TUE = (
    "https://eur-lex.europa.eu/legal-content/ES/TXT/"
    "?uri=CELEX:12016M/TXT"
)
URL_UE_TFUE = (
    "https://eur-lex.europa.eu/legal-content/ES/TXT/"
    "?uri=CELEX:12016E/TXT"
)


def nombre_es_tue(nombre: str) -> bool:
    n = normalizar(nombre)
    return n in {
        "tue",
        "tratado de la union europea",
    }


def nombre_es_tfue(nombre: str) -> bool:
    n = normalizar(nombre)
    return n in {
        "tfue",
        "tratado de funcionamiento de la union europea",
    }


def nombre_es_normativa_ue(nombre: str) -> bool:
    n = normalizar(nombre)
    return (
        nombre_es_tue(nombre)
        or nombre_es_tfue(nombre)
        or "(ue)" in n
        or "union europea" in n
        or n.startswith("reglamento ue ")
        or n.startswith("directiva ue ")
        or n.startswith("decision ue ")
    )


def segmento_articulo_ue(texto: str, numero: str) -> str | None:
    texto_n = normalizar(texto)
    numero_re = re.escape(normalizar(numero))
    inicio = re.search(
        rf"\barticulo\s+{numero_re}(?=\s|\.|\[|$)",
        texto_n,
        flags=re.I,
    )
    if inicio is None:
        return None

    siguiente = re.search(
        r"\barticulo\s+\d+(?=\s|\.|\[|$)",
        texto_n[inicio.end():],
        flags=re.I,
    )
    fin = (
        inicio.end() + siguiente.start()
        if siguiente
        else min(len(texto_n), inicio.start() + 8000)
    )
    return texto_n[inicio.start():fin]


def revisar_tratado_ue(pregunta: Pregunta) -> Resultado:
    nombre = nombre_para_consulta(pregunta)
    articulo = articulo_base_para_consulta(pregunta)

    if nombre_es_tue(nombre):
        url = URL_UE_TUE
        etiqueta = "Tratado de la Unión Europea"
    elif nombre_es_tfue(nombre):
        url = URL_UE_TFUE
        etiqueta = "Tratado de Funcionamiento de la Unión Europea"
    else:
        return resultado_no_verificable(
            "EUR-LEX",
            "",
            "Norma de la Unión Europea detectada, pero esta versión del "
            "auditor solo tiene resolución oficial inequívoca para TUE y TFUE.",
        )

    try:
        texto = descargar_texto(url)
    except requests.RequestException as exc:
        return resultado_no_verificable(
            "EUR-LEX",
            url,
            f"Error de consulta EUR-Lex: {limpiar(exc)}",
        )

    texto_n = normalizar(texto)
    etiqueta_n = normalizar(etiqueta)
    if etiqueta_n not in texto_n:
        return resultado_no_verificable(
            "EUR-LEX",
            url,
            "La página oficial descargada no identifica inequívocamente "
            f"el {etiqueta}.",
        )

    if not articulo:
        return Resultado(
            ESTADO_VIGENTE,
            "EUR-LEX",
            url,
            "Tratado localizado en la versión consolidada oficial de EUR-Lex.",
        )

    segmento = segmento_articulo_ue(texto, articulo)
    if segmento is None:
        return resultado_no_verificable(
            "EUR-LEX",
            url,
            "El artículo no se localizó en la versión consolidada oficial; "
            "su ausencia no demuestra derogación.",
        )

    if articulo_expresamente_derogado(segmento[:220], segmento[:350]):
        return Resultado(
            ESTADO_OBSOLETA_ARTICULO_DEROGADO,
            "EUR-LEX",
            url,
            "El encabezado oficial del artículo indica expresamente que está derogado.",
        )

    return Resultado(
        ESTADO_VIGENTE,
        "EUR-LEX",
        url,
        "Tratado y artículo localizados en la versión consolidada oficial de EUR-Lex.",
    )


def revisar_boe(pregunta: Pregunta) -> Resultado | None:
    nombre = nombre_para_consulta(pregunta)
    articulo = articulo_base_para_consulta(pregunta)

    # La LO 1/2006 es la ley de reforma. El texto consolidado vigente del
    # Estatuto, incluidos sus artículos actuales, se publica oficialmente bajo
    # la LO 5/1982 (BOE-A-1982-17235).
    nombre_n = normalizar(nombre)
    if "estatuto de autonomia" in nombre_n and (
        "ley organica 1/2006" in nombre_n
        or "ley organica 5/1982" in nombre_n
    ):
        nombre = "Ley Orgánica 5/1982, de 1 de julio"

    if not nombre:
        return resultado_no_verificable(
            "DATOS", "", "Falta el nombre de la norma."
        )

    nombre = ALIAS_BOE_OFICIALES.get(normalizar(nombre), nombre)

    try:
        norma = buscar_norma(nombre)
    except BOEError as exc:
        if contiene_error_red(exc):
            return resultado_no_verificable(
                "BOE", "", f"Error de consulta BOE: {limpiar(exc)}"
            )
        return None
    except Exception as exc:
        return resultado_no_verificable(
            "BOE",
            "",
            f"Error inesperado BOE: {exc.__class__.__name__}: {limpiar(exc)}",
        )

    referencia = norma.id_boe
    url = f"https://www.boe.es/buscar/act.php?id={norma.id_boe}"
    try:
        texto_norma = descargar_texto(url)
    except requests.RequestException as exc:
        return resultado_no_verificable(
            "BOE",
            referencia,
            f"No se pudo descargar la cabecera oficial: {limpiar(exc)}",
        )

    if norma_expresamente_derogada(texto_norma):
        return Resultado(
            ESTADO_OBSOLETA_NORMA,
            "BOE",
            referencia,
            "La cabecera oficial indica expresamente: Disposición derogada.",
        )

    if not articulo:
        return Resultado(
            ESTADO_VIGENTE,
            "BOE",
            referencia,
            "Norma localizada sin indicación expresa de derogación.",
        )

    try:
        articulo_boe = obtener_articulo(nombre, articulo)
    except BOEError as exc:
        return resultado_no_verificable(
            "BOE",
            referencia,
            "No se pudo verificar el artículo; su ausencia en la consulta "
            f"no demuestra derogación: {limpiar(exc)}",
        )
    except Exception as exc:
        return resultado_no_verificable(
            "BOE",
            referencia,
            "Error inesperado al consultar el artículo: "
            f"{exc.__class__.__name__}: {limpiar(exc)}",
        )

    referencia_articulo = f"{norma.id_boe}#{articulo_boe.id_bloque}"
    if articulo_expresamente_derogado(
        articulo_boe.titulo_bloque,
        articulo_boe.texto,
    ):
        return Resultado(
            ESTADO_OBSOLETA_ARTICULO_DEROGADO,
            "BOE",
            referencia_articulo,
            "El encabezado oficial del artículo indica expresamente que "
            "está derogado.",
        )

    return Resultado(
        ESTADO_VIGENTE,
        "BOE",
        referencia_articulo,
        "Norma y artículo localizados sin indicación expresa de derogación.",
    )


CODIGOS_ELI_DOGV = {
    "ley": "l",
    "decreto": "d",
    "orden": "o",
    "decreto-ley": "dl",
    "decreto legislativo": "dlg",
}
DOGV_API = "https://dogv.gva.es/dogv-portal"


def construir_url_publica_dogv(cita, fecha_iso: str) -> str:
    codigo = CODIGOS_ELI_DOGV[cita.tipo]
    anio, mes, dia = fecha_iso.split("-")
    return (
        "https://dogv.gva.es/es/eli/es-vc/"
        f"{codigo}/{anio}/{mes}/{dia}/{cita.numero}"
    )


def url_dogv(nombre_norma: str) -> str | None:
    try:
        cita = extraer_cita(nombre_norma)
    except BOEError:
        return None

    # Sin una indicación expresa del ámbito valenciano no se construye una
    # URL autonómica: una norma estatal puede compartir tipo, número y año.
    nombre_n = normalizar(nombre_norma)
    ambito_valenciano = (
        cita.ambito == "valenciana"
        or nombre_indica_ambito_valenciano(nombre_norma)
    )
    if not ambito_valenciano:
        return None

    if cita.tipo not in CODIGOS_ELI_DOGV or not cita.fecha_iso:
        return None
    return construir_url_publica_dogv(cita, cita.fecha_iso)


def disposicion_dogv_por_busqueda(cita) -> int | None:
    """Resuelve una cita autonómica incompleta sin elegir coincidencias dudosas."""
    if cita.tipo not in {
        "ley", "decreto", "decreto-ley", "decreto legislativo", "orden"
    }:
        return None

    rotulo = {
        "ley": "LEY",
        "decreto": "DECRETO",
        "decreto-ley": "DECRETO LEY",
        "decreto legislativo": "DECRETO LEGISLATIVO",
        "orden": "ORDEN",
    }[cita.tipo]
    respuesta = requests.post(
        f"{DOGV_API}/dogv/search",
        params={
            "lang": "es_es",
            "page": 0,
            "size": 50,
            "sort": "fechaPublicacion,asc",
        },
        json={"texto": f'"{rotulo} {cita.numero}/{cita.anio}"'},
        timeout=TIMEOUT,
        headers={
            "Accept": "application/json",
            "User-Agent": "OpoCoach-Mantenimiento/2.0",
        },
    )
    respuesta.raise_for_status()
    contenido = respuesta.json().get("content", [])
    coincidencias: set[int] = set()
    for candidata in contenido if isinstance(contenido, list) else []:
        if not isinstance(candidata, dict):
            continue
        titulo = limpiar(candidata.get("titulo"))
        try:
            cita_titulo = extraer_cita(titulo)
        except BOEError:
            continue
        if (cita_titulo.tipo, cita_titulo.numero, cita_titulo.anio) != (
            cita.tipo, cita.numero, cita.anio
        ):
            continue
        # No acepta resoluciones u otras disposiciones que solo mencionan la
        # norma buscada dentro de su título.
        titulo_n = normalizar(titulo).replace("-", " ")
        prefijo = normalizar(
            f"{rotulo} {cita.numero}/{cita.anio}"
        ).replace("-", " ")
        if not titulo_n.startswith(prefijo):
            continue
        identificador = candidata.get("id")
        if isinstance(identificador, int) and identificador > 0:
            coincidencias.add(identificador)

    return next(iter(coincidencias)) if len(coincidencias) == 1 else None


def obtener_datos_dogv(
    nombre_norma: str,
) -> tuple[dict[str, object], str] | None:
    url_publica = url_dogv(nombre_norma)
    cita = extraer_cita(nombre_norma)
    if cita.clave in _CACHE_DATOS_DOGV:
        return _CACHE_DATOS_DOGV[cita.clave]

    cabeceras = {
        "Accept": "application/json",
        "User-Agent": "OpoCoach-Mantenimiento/2.0",
    }

    if url_publica:
        respuesta_id = requests.get(
            f"{DOGV_API}/disposicion/id",
            params={
                "date": cita.fecha_iso,
                "type": CODIGOS_ELI_DOGV[cita.tipo],
                "number": cita.numero,
                "jurisdiction": "es-vc",
            },
            timeout=TIMEOUT,
            headers=cabeceras,
        )
        respuesta_id.raise_for_status()
        id_disposicion = respuesta_id.json()
    else:
        id_disposicion = disposicion_dogv_por_busqueda(cita)
        if id_disposicion is None:
            return None
    if not isinstance(id_disposicion, int) or id_disposicion <= 0:
        raise ValueError("El DOGV no devolvió un identificador válido.")

    respuesta_datos = requests.get(
        f"{DOGV_API}/disposicion/{id_disposicion}",
        params={"lang": "es_es"},
        timeout=TIMEOUT,
        headers=cabeceras,
    )
    respuesta_datos.raise_for_status()
    datos = respuesta_datos.json()
    if not isinstance(datos, dict):
        raise ValueError("El DOGV no devolvió datos de disposición válidos.")

    titulo = limpiar(datos.get("titulo"))
    fecha = limpiar(datos.get("fechaDisposicion"))
    try:
        cita_titulo = extraer_cita(titulo)
    except BOEError as exc:
        raise ValueError(
            "El título oficial del DOGV no contiene una referencia exacta."
        ) from exc

    if (
        cita_titulo.tipo,
        cita_titulo.numero,
        cita_titulo.anio,
    ) != (cita.tipo, cita.numero, cita.anio):
        raise ValueError(
            "La disposición devuelta por el DOGV no coincide exactamente "
            "en tipo, número y año."
        )
    if cita.fecha_iso and fecha != cita.fecha_iso:
        raise ValueError(
            "La fecha de la disposición devuelta por el DOGV no coincide."
        )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        raise ValueError("El DOGV no devolvió una fecha normalizada válida.")
    if fecha[:4] != cita.anio:
        raise ValueError("El año devuelto por el DOGV no coincide con la cita.")
    url_publica = construir_url_publica_dogv(cita, fecha)

    resultado = datos, url_publica
    _CACHE_DATOS_DOGV[cita.clave] = resultado
    return resultado


def segmento_articulo_dogv(texto: str, numero: str) -> str | None:
    texto_n = normalizar(texto)
    numero_re = re.escape(normalizar(numero))
    inicio = re.search(
        rf"\barticulo\s+{numero_re}(?=\.|\s|\[|$)",
        texto_n,
        flags=re.I,
    )
    if inicio is None:
        return None

    siguiente = re.search(
        r"\barticulo\s+(?:\d+(?:\.\d+)*|unico)(?=\.|\s|\[|$)",
        texto_n[inicio.end():],
        flags=re.I,
    )
    fin = (
        inicio.end() + siguiente.start()
        if siguiente
        else min(len(texto_n), inicio.start() + 6000)
    )
    return texto_n[inicio.start():fin]


def revisar_dogv(pregunta: Pregunta) -> Resultado:
    nombre = nombre_para_consulta(pregunta)
    articulo = articulo_base_para_consulta(pregunta)
    try:
        datos_y_url = obtener_datos_dogv(nombre)
    except requests.RequestException as exc:
        return resultado_no_verificable(
            "DOGV", "", f"Error de consulta DOGV: {limpiar(exc)}"
        )
    except (BOEError, ValueError) as exc:
        return resultado_no_verificable(
            "DOGV", "", f"Respuesta DOGV no verificable: {limpiar(exc)}"
        )
    except Exception as exc:
        return resultado_no_verificable(
            "DOGV",
            "",
            f"Error inesperado DOGV: {exc.__class__.__name__}: {limpiar(exc)}",
        )

    if datos_y_url is None:
        return resultado_no_verificable(
            "DATOS",
            "",
            "No existe una referencia BOE exacta ni datos suficientes para "
            "construir una URL ELI valenciana inequívoca.",
        )

    datos, url = datos_y_url
    estado_dogv = ""
    estado_objeto = datos.get("estado")
    if isinstance(estado_objeto, dict):
        estado_dogv = normalizar(estado_objeto.get("descripcion"))

    if estado_dogv == "derogada":
        return Resultado(
            ESTADO_OBSOLETA_NORMA,
            "DOGV",
            url,
            "El servicio oficial del DOGV indica expresamente estado DEROGADA.",
        )

    if estado_dogv != "vigente":
        return resultado_no_verificable(
            "DOGV",
            url,
            f"El servicio oficial devolvió un estado no concluyente: "
            f"{estado_dogv or '(vacío)' }.",
        )

    if not articulo:
        return Resultado(
            ESTADO_VIGENTE,
            "DOGV",
            url,
            "Norma localizada sin indicación expresa de derogación.",
        )

    texto = _CACHE_TEXTO_DOGV.get(url, "")
    if not texto:
        texto_html = str(datos.get("texto") or "")
        if not limpiar(texto_html):
            return resultado_no_verificable(
                "DOGV",
                url,
                "El servicio oficial no devolvió el texto de la norma.",
            )
        parser = ExtractorTextoHTML()
        parser.feed(texto_html)
        texto = parser.texto()
        _CACHE_TEXTO_DOGV[url] = texto

    numero = numero_articulo(articulo)
    if not numero:
        return resultado_no_verificable(
            "DOGV", url, "No se pudo extraer el número del artículo."
        )

    segmento = segmento_articulo_dogv(texto, numero)
    if segmento is None:
        return resultado_no_verificable(
            "DOGV",
            url,
            "El artículo no se localizó; la ausencia no demuestra derogación.",
        )

    if articulo_expresamente_derogado(segmento[:180], segmento[:300]):
        return Resultado(
            ESTADO_OBSOLETA_ARTICULO_DEROGADO,
            "DOGV",
            url,
            "El encabezado oficial del artículo indica expresamente que "
            "está derogado.",
        )

    return Resultado(
        ESTADO_VIGENTE,
        "DOGV",
        url,
        "Norma y artículo localizados sin indicación expresa de derogación.",
    )


def obtener_reglamento_corts() -> tuple[str, str]:
    global _CACHE_REGLAMENTO_CORTS
    if _CACHE_REGLAMENTO_CORTS is not None:
        return _CACHE_REGLAMENTO_CORTS

    pagina = "https://www.cortsvalencianes.es/es/composicion/normas/reglamento"
    cabeceras = {"User-Agent": "OpoCoach-Mantenimiento/2.0"}
    respuesta = requests.get(pagina, timeout=TIMEOUT, headers=cabeceras)
    respuesta.raise_for_status()
    enlaces = re.findall(
        r'''href=["']([^"']+\.pdf(?:\?[^"']*)?)["']''',
        respuesta.text,
        flags=re.I,
    )
    enlaces_rcv = [
        urljoin(pagina, enlace)
        for enlace in enlaces
        if "rcv" in normalizar(enlace)
    ]
    enlaces_rcv = list(dict.fromkeys(enlaces_rcv))
    if len(enlaces_rcv) != 1:
        raise ValueError(
            "La página oficial no contiene un único PDF vigente del Reglamento."
        )

    url_pdf = enlaces_rcv[0]
    if (urlparse(url_pdf).hostname or "").lower() != "www.cortsvalencianes.es":
        raise ValueError("El PDF del Reglamento no pertenece al dominio oficial.")

    pdf = requests.get(url_pdf, timeout=TIMEOUT, headers=cabeceras)
    pdf.raise_for_status()
    if not pdf.content.startswith(b"%PDF"):
        raise ValueError("La fuente oficial no devolvió un PDF válido.")
    try:
        import fitz
    except ImportError as exc:
        raise ValueError(
            "PyMuPDF no está instalado para leer el Reglamento oficial."
        ) from exc

    documento = fitz.open(stream=pdf.content, filetype="pdf")
    try:
        texto = "\n".join(pagina_pdf.get_text() for pagina_pdf in documento)
    finally:
        documento.close()
    if "reglamento de les corts valencianes" not in normalizar(texto[:2000]):
        raise ValueError(
            "El PDF oficial no se identifica como Reglamento de Les Corts Valencianes."
        )
    _CACHE_REGLAMENTO_CORTS = texto, url_pdf
    return _CACHE_REGLAMENTO_CORTS


def revisar_reglamento_corts(pregunta: Pregunta) -> Resultado:
    numero = numero_articulo(articulo_base_para_consulta(pregunta))
    if not numero:
        return resultado_no_verificable(
            "CORTS VALENCIANES", "", "No se pudo extraer el número del artículo."
        )
    try:
        texto, url_pdf = obtener_reglamento_corts()
    except (requests.RequestException, ValueError) as exc:
        return resultado_no_verificable(
            "CORTS VALENCIANES",
            "",
            f"No se pudo verificar la fuente oficial: {limpiar(exc)}",
        )

    texto_n = normalizar(texto)
    inicio = re.search(
        rf"\barticulo\s+{re.escape(normalizar(numero))}(?=\.|\s|$)",
        texto_n,
    )
    if inicio is None:
        return resultado_no_verificable(
            "CORTS VALENCIANES",
            url_pdf,
            "El artículo no se localizó; la ausencia no demuestra derogación.",
        )
    siguiente = re.search(
        r"\barticulo\s+(?:\d+|unico)(?=\.|\s|$)",
        texto_n[inicio.end():],
    )
    fin = (
        inicio.end() + siguiente.start()
        if siguiente
        else min(len(texto_n), inicio.start() + 6000)
    )
    segmento = texto_n[inicio.start():fin]
    if articulo_expresamente_derogado(segmento[:180], segmento[:300]):
        return Resultado(
            ESTADO_OBSOLETA_ARTICULO_DEROGADO,
            "CORTS VALENCIANES",
            url_pdf,
            "El encabezado oficial del artículo indica expresamente que está derogado.",
        )
    return Resultado(
        ESTADO_VIGENTE,
        "CORTS VALENCIANES",
        url_pdf,
        "Reglamento y artículo localizados en la edición vigente publicada por Les Corts.",
    )


def revisar_pregunta(pregunta: Pregunta) -> Resultado:
    clave = (
        normalizar(nombre_para_consulta(pregunta)),
        normalizar(articulo_base_para_consulta(pregunta)),
    )
    if clave in _CACHE_RESULTADOS:
        return _CACHE_RESULTADOS[clave]

    nombre = nombre_para_consulta(pregunta)
    nombre_n = normalizar(nombre)

    if "reglamento" in nombre_n and "corts valencianes" in nombre_n:
        resultado = revisar_reglamento_corts(pregunta)
        _CACHE_RESULTADOS[clave] = resultado
        return resultado

    # Derecho de la UE: nunca debe caer por descarte en DOGV.
    if nombre_es_tue(nombre) or nombre_es_tfue(nombre):
        resultado = revisar_tratado_ue(pregunta)
        _CACHE_RESULTADOS[clave] = resultado
        return resultado

    if nombre_es_normativa_ue(nombre):
        resultado = resultado_no_verificable(
            "EUR-LEX",
            "",
            "Norma de la Unión Europea detectada. No se consulta DOGV; "
            "falta una resolución EUR-Lex inequívoca específica para esta norma.",
        )
        _CACHE_RESULTADOS[clave] = resultado
        return resultado

    # DOGV solo cuando la propia denominación identifica ámbito valenciano
    # o cuando el tipo autonómico es inequívoco (decreto-ley / decreto
    # legislativo sin prefijo Real). No se usa DOGV como fallback general.
    try:
        cita = extraer_cita(nombre)
    except BOEError:
        cita = None

    ambito_valenciano_expreso = nombre_indica_ambito_valenciano(nombre)
    if cita is not None and (
        cita.tipo in {"decreto-ley", "decreto legislativo"}
        or (
            cita.tipo in CODIGOS_ELI_DOGV
            and ambito_valenciano_expreso
        )
    ):
        resultado = revisar_dogv(pregunta)
        _CACHE_RESULTADOS[clave] = resultado
        return resultado

    # Resto: BOE. Si BOE no identifica la norma, NO se prueba DOGV por
    # descarte, porque eso produjo falsos NO_VERIFICABLE de fuente DOGV
    # para normas estatales y europeas.
    resultado_boe = revisar_boe(pregunta)
    if resultado_boe is None:
        resultado = resultado_no_verificable(
            "BOE",
            "",
            "La norma no pudo identificarse de forma inequívoca en BOE. "
            "No se intenta DOGV porque no existe indicio valenciano expreso.",
        )
    else:
        resultado = resultado_boe

    _CACHE_RESULTADOS[clave] = resultado
    return resultado


def actualizar_estado(
    conexion: sqlite3.Connection,
    pregunta_id: int,
    resultado: Resultado,
    clave: str,
) -> None:
    if resultado.estado not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado de vigencia no válido: {resultado.estado}")
    conexion.execute(
        """
        UPDATE lote_preguntas
        SET estado_vigencia = ?, vigencia_revision_clave = ?
        WHERE id = ?
        """,
        (resultado.estado, clave, pregunta_id),
    )


def escribir_csv_no_verificables(
    filas: Iterable[tuple[Pregunta, Resultado]],
) -> Path | None:
    filas = list(filas)
    if not filas:
        return None

    REGISTROS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = REGISTROS / f"preguntas_vigencia_no_verificable_{marca}.csv"

    with ruta.open("w", newline="", encoding="utf-8-sig") as fichero:
        escritor = csv.writer(fichero, delimiter=";")
        escritor.writerow(
            [
                "pregunta_id",
                "nombre_norma",
                "articulo",
                "enunciado",
                "fuente",
                "referencia",
                "motivo",
            ]
        )
        for pregunta, resultado in filas:
            escritor.writerow(
                [
                    pregunta.id,
                    nombre_para_consulta(pregunta),
                    articulo_para_consulta(pregunta),
                    pregunta.enunciado,
                    resultado.fuente,
                    resultado.referencia,
                    resultado.motivo,
                ]
            )

    return ruta


def ejecutar(args: argparse.Namespace) -> None:
    ruta_db = Path(args.db).resolve()
    if not ruta_db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {ruta_db}")

    copia: Path | None = None
    if args.aplicar and not args.sin_copia_seguridad:
        copia = copia_seguridad(ruta_db)

    estadisticas = {
        "seleccionadas": 0,
        "omitidas_idempotencia": 0,
        "grupos_consultados": 0,
        ESTADO_VIGENTE: 0,
        ESTADO_OBSOLETA_NORMA: 0,
        ESTADO_OBSOLETA_ARTICULO_DEROGADO: 0,
        ESTADO_NO_VERIFICABLE: 0,
    }
    no_verificables: list[tuple[Pregunta, Resultado]] = []

    with sqlite3.connect(ruta_db, timeout=30) as conexion:
        conexion.row_factory = sqlite3.Row
        if args.aplicar:
            asegurar_estructura(conexion)
            conexion.commit()

        todas = cargar_preguntas(conexion, pregunta_id=args.pregunta_id)

        # Una misma norma aparece con denominaciones completas y abreviadas.
        # Se obtiene primero la mejor denominación disponible en toda la base
        # para cada referencia tipo+número+año. Después se reutiliza en todos
        # sus artículos, aunque el registro concreto solo diga "42/2019".
        universo_nombres = (
            cargar_preguntas(conexion, pregunta_id=None)
            if args.pregunta_id is not None
            else todas
        )
        # La mejor denominación debe elegirse por norma_id_normalizada cuando
        # exista. Tipo+número+año no basta: distintas administraciones pueden
        # tener normas con la misma numeración.
        mejor_por_norma: dict[str, Pregunta] = {}
        for candidata in universo_nombres:
            if candidata.norma_id_normalizada is not None:
                clave_norma = f"norma_id:{candidata.norma_id_normalizada}"
            else:
                clave_cita = clave_cita_basica(candidata)
                if not clave_cita:
                    continue
                clave_norma = f"cita:{clave_cita}"

            anterior = mejor_por_norma.get(clave_norma)
            if anterior is None or calidad_representante(
                candidata
            ) > calidad_representante(anterior):
                mejor_por_norma[clave_norma] = candidata

        if args.solo_no_verificables:
            todas = [
                pregunta
                for pregunta in todas
                if pregunta.estado_vigencia == ESTADO_NO_VERIFICABLE
            ]

        pendientes: list[Pregunta] = []
        for pregunta in todas:
            clave = clave_revision(pregunta)
            ya_analizada = (
                pregunta.vigencia_revision_clave == clave
                and pregunta.estado_vigencia in ESTADOS_VALIDOS
            )
            if ya_analizada and not args.forzar:
                estadisticas["omitidas_idempotencia"] += 1
                continue
            pendientes.append(pregunta)

        if args.limite is not None:
            pendientes = pendientes[:args.limite]
        estadisticas["seleccionadas"] = len(pendientes)

        grupos: dict[tuple[str, str], list[Pregunta]] = {}
        for pregunta in pendientes:
            grupos.setdefault(clave_agrupacion(pregunta), []).append(pregunta)

        total_grupos = len(grupos)
        grupos_ordenados = sorted(grupos.items(), key=lambda elemento: elemento[0])
        for posicion, (_, preguntas_grupo) in enumerate(
            grupos_ordenados,
            start=1,
        ):
            representante = max(preguntas_grupo, key=calidad_representante)
            if representante.norma_id_normalizada is not None:
                clave_norma = f"norma_id:{representante.norma_id_normalizada}"
            else:
                clave_cita = clave_cita_basica(representante)
                clave_norma = f"cita:{clave_cita}" if clave_cita else ""

            mejor_global = mejor_por_norma.get(clave_norma) if clave_norma else None
            if mejor_global is not None:
                representante = replace(
                    representante,
                    nombre_norma=mejor_global.nombre_norma,
                    nombre_norma_normalizado=(
                        mejor_global.nombre_norma_normalizado
                    ),
                )
            print(
                f"[{posicion}/{total_grupos}] "
                f"{nombre_para_consulta(representante)} | "
                f"art. {articulo_base_para_consulta(representante) or '(sin artículo)'} "
                f"| preguntas={len(preguntas_grupo)}"
            )

            resultado = revisar_pregunta(representante)
            estadisticas["grupos_consultados"] += 1
            estadisticas[resultado.estado] += len(preguntas_grupo)
            print(
                f"  {resultado.estado} | {resultado.fuente} | "
                f"{resultado.motivo}"
            )

            for pregunta in preguntas_grupo:
                if resultado.estado == ESTADO_NO_VERIFICABLE:
                    no_verificables.append((pregunta, resultado))
                if args.aplicar:
                    actualizar_estado(
                        conexion,
                        pregunta.id,
                        resultado,
                        clave_revision(pregunta),
                    )

            if args.aplicar:
                conexion.commit()

    ruta_csv = escribir_csv_no_verificables(no_verificables)

    print("\nAUDITORÍA DE VIGENCIA TERMINADA")
    print(f"Base de datos: {ruta_db}")
    print(f"Modo: {'APLICAR' if args.aplicar else 'VISTA PREVIA'}")
    if copia is not None:
        print(f"Copia de seguridad: {copia}")
    print(f"Preguntas seleccionadas: {estadisticas['seleccionadas']}")
    print(
        "Preguntas omitidas por idempotencia: "
        f"{estadisticas['omitidas_idempotencia']}"
    )
    print(f"Consultas norma/artículo: {estadisticas['grupos_consultados']}")
    print(f"Vigentes: {estadisticas[ESTADO_VIGENTE]}")
    print(f"Normas derogadas: {estadisticas[ESTADO_OBSOLETA_NORMA]}")
    print(
        "Artículos expresamente derogados: "
        f"{estadisticas[ESTADO_OBSOLETA_ARTICULO_DEROGADO]}"
    )
    print(f"No verificables: {estadisticas[ESTADO_NO_VERIFICABLE]}")
    print(f"CSV no verificables: {ruta_csv or 'no generado'}")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita conservadoramente la vigencia de preguntas jurídicas."
    )
    parser.add_argument("--db", default=str(DB_PREDETERMINADA))
    parser.add_argument("--limite", type=int)
    parser.add_argument("--pregunta-id", type=int)
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Guarda el resultado en lote_preguntas.",
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Repite también las preguntas ya analizadas con la misma huella.",
    )
    parser.add_argument(
        "--solo-no-verificables",
        action="store_true",
        help=(
            "Selecciona únicamente preguntas cuyo estado actual sea "
            "NO_VERIFICABLE. Útil para reevaluar incidencias sin repetir "
            "las ya concluyentes."
        ),
    )
    parser.add_argument("--sin-copia-seguridad", action="store_true")
    return parser


def validar_argumentos(args: argparse.Namespace) -> None:
    if args.limite is not None and args.limite <= 0:
        raise ValueError("--limite debe ser mayor que cero.")
    if args.pregunta_id is not None and args.pregunta_id <= 0:
        raise ValueError("--pregunta-id debe ser mayor que cero.")


def main() -> int:
    parser = construir_parser()
    args = parser.parse_args()
    try:
        validar_argumentos(args)
        ejecutar(args)
        return 0
    except KeyboardInterrupt:
        print("\nProceso cancelado por el usuario.")
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())