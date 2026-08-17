"""
OpoCoach - Cliente nuevo para legislación consolidada del BOE.

Principio de seguridad:
- una norma solo se acepta cuando su tipo, número y año coinciden exactamente;
- una norma que simplemente menciona o modifica la solicitada se rechaza;
- ante ambigüedad, se produce BOEError y nunca se elige arbitrariamente.

Interfaz compatible con resolver_referencias_boe.py:
    buscar_norma(nombre_norma) -> NormaBOE
    obtener_articulo(nombre_norma, articulo) -> ArticuloBOE
    limpiar_cache_norma(nombre_norma)
    limpiar_cache_articulo(id_boe, id_bloque)

Uso:
    python scripts/boe_api.py "La Ley 39/2015, de 1 de octubre" 23
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


BASE_URL = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
TIMEOUT = 35

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
CACHE_DIR = RAIZ_PROYECTO / "cache_boe_v2"
CACHE_NORMAS = CACHE_DIR / "normas.json"


class BOEError(RuntimeError):
    """Error controlado al localizar o interpretar legislación del BOE."""


@dataclass(frozen=True)
class NormaBOE:
    nombre_buscado: str
    id_boe: str
    titulo: str
    departamento: str = ""


@dataclass(frozen=True)
class ArticuloBOE:
    nombre_norma: str
    id_boe: str
    departamento: str
    articulo: str
    id_bloque: str
    titulo_bloque: str
    texto: str


@dataclass(frozen=True)
class CitaNormativa:
    tipo: str
    numero: str
    anio: str
    fecha_iso: str = ""
    ambito: str = ""

    @property
    def referencia(self) -> str:
        return f"{self.tipo} {self.numero}/{self.anio}"

    @property
    def clave(self) -> str:
        return "|".join(
            (self.tipo, self.numero, self.anio, self.fecha_iso, self.ambito)
        )


TIPOS_NORMA: list[tuple[str, str]] = [
    (r"real\s+decreto\s+legislativo", "real decreto legislativo"),
    (r"real\s+decreto[\s-]+ley", "real decreto-ley"),
    (r"decreto\s+legislativo", "decreto legislativo"),
    (r"decreto[\s-]+ley", "decreto-ley"),
    (r"ley\s+organica", "ley organica"),
    (r"real\s+decreto", "real decreto"),
    (r"ley", "ley"),
    (r"decreto", "decreto"),
    (r"orden", "orden"),
]

MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12",
}

# Atajos verificados para normas extremadamente frecuentes.
# No sustituyen el buscador genérico: solo evitan una consulta innecesaria.
IDS_VERIFICADOS = {
    "ley|39|2015": "BOE-A-2015-10565",
    "ley|40|2015": "BOE-A-2015-10566",
    "real decreto legislativo|5|2015": "BOE-A-2015-11719",
    "real decreto legislativo|2|2015": "BOE-A-2015-11430",
}

# Identificadores verificados para referencias que incluyen fecha completa.
# Esta tabla tiene prioridad sobre la clave genérica tipo|número|año y evita
# que una búsqueda incompleta del BOE descarte una norma autonómica válida.
IDS_VERIFICADOS_POR_FECHA = {
    "ley|6|2025|2025-05-30": "BOE-A-2025-11960",
}

# Algunos documentos oficiales existen en el BOE, pero todavía no forman
# parte de la API de legislación consolidada. Para esos identificadores se
# guardan los metadatos oficiales verificados y se evita consultar un endpoint
# que responde 404.
DATOS_IDS_VERIFICADOS = {
    "BOE-A-2025-11960": {
        "titulo": (
            "Ley 6/2025, de 30 de mayo, de presupuestos de la Generalitat "
            "para el ejercicio 2025."
        ),
        "departamento": "Comunitat Valenciana",
    },
}

# Normas cuyo nombre oficial no contiene el patrón tipo + número/año.
# Se resuelven únicamente mediante alias explícitos y un identificador BOE
# verificado. Nunca se seleccionan por semejanza textual.
NORMAS_ESPECIALES = {
    "constitucion espanola": "BOE-A-1978-31229",
    "constitucion espanola de 1978": "BOE-A-1978-31229",
    "constitucion de 1978": "BOE-A-1978-31229",
    "la constitucion espanola": "BOE-A-1978-31229",
}


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def nombre_etiqueta(elemento: ET.Element) -> str:
    return elemento.tag.rsplit("}", 1)[-1].lower()


def texto_elemento(elemento: ET.Element | None) -> str:
    if elemento is None:
        return ""
    return limpiar(" ".join(t for t in elemento.itertext() if limpiar(t)))


def extraer_id_boe(texto: str) -> str:
    m = re.search(r"\bBOE-A-\d{4}-\d+\b", texto or "", re.I)
    return m.group(0).upper() if m else ""


def extraer_cita(nombre_norma: str) -> CitaNormativa:
    texto_n = normalizar(nombre_norma)

    tipo_encontrado = ""
    numero = ""
    anio = ""

    for patron_tipo, tipo_canonico in TIPOS_NORMA:
        m = re.search(
            rf"\b({patron_tipo})\s+(\d+)\s*[/_-]\s*(\d{{4}})\b",
            texto_n,
            flags=re.I,
        )
        if m:
            tipo_encontrado = tipo_canonico
            numero = m.group(2)
            anio = m.group(3)
            break

    if not tipo_encontrado:
        raise BOEError(
            "No se pudo extraer una referencia normativa exacta "
            f"(tipo, número y año) de: {nombre_norma}"
        )

    fecha_iso = ""
    # La fecha puede venir completa:
    #   "Ley 6/2025, de 30 de mayo de 2025"
    # o sin repetir el año:
    #   "Ley 6/2025, de 30 de mayo"
    # En el segundo caso se utiliza el año de la referencia normativa.
    m_fecha = re.search(
        r"\bde\s+(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|setiembre|octubre|noviembre|diciembre)"
        r"(?:\s+de\s+(\d{4}))?\b",
        texto_n,
    )
    if m_fecha:
        dia = int(m_fecha.group(1))
        mes = MESES[m_fecha.group(2)]
        anio_fecha = m_fecha.group(3) or anio
        fecha_iso = f"{anio_fecha}-{mes}-{dia:02d}"

    ambito = ""
    if any(x in texto_n for x in (
        "comunitat valenciana",
        "comunidad valenciana",
        "generalitat valenciana",
        "consell",
    )):
        ambito = "valenciana"

    return CitaNormativa(
        tipo=tipo_encontrado,
        numero=numero,
        anio=anio,
        fecha_iso=fecha_iso,
        ambito=ambito,
    )


def referencia_de_titulo(titulo: str) -> tuple[str, str, str] | None:
    try:
        cita = extraer_cita(titulo)
    except BOEError:
        return None
    return cita.tipo, cita.numero, cita.anio


def preparar_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_NORMAS.exists():
        CACHE_NORMAS.write_text("{}", encoding="utf-8")


def cargar_cache() -> dict[str, Any]:
    preparar_cache()
    try:
        datos = json.loads(CACHE_NORMAS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BOEError(f"No se pudo leer la caché: {CACHE_NORMAS}") from exc
    return datos if isinstance(datos, dict) else {}


def guardar_cache(datos: dict[str, Any]) -> None:
    preparar_cache()
    temporal = CACHE_NORMAS.with_suffix(".json.tmp")
    temporal.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporal.replace(CACHE_NORMAS)


def ruta_cache(id_boe: str, nombre: str) -> Path:
    seguro = re.sub(r"[^A-Za-z0-9_.-]+", "_", nombre)
    return CACHE_DIR / id_boe / seguro


def descargar_xml(url: str, ruta: Path | None = None) -> ET.Element:
    if ruta and ruta.exists():
        try:
            return ET.fromstring(ruta.read_bytes())
        except (OSError, ET.ParseError):
            ruta.unlink(missing_ok=True)

    try:
        r = requests.get(
            url,
            timeout=TIMEOUT,
            headers={
                "Accept": "application/xml",
                "User-Agent": "OpoCoach/2.0",
            },
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise BOEError(f"Error al consultar el BOE: {url}") from exc

    try:
        raiz = ET.fromstring(r.content)
    except ET.ParseError as exc:
        raise BOEError(f"El BOE no devolvió XML válido: {url}") from exc

    if ruta:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(ruta.suffix + ".tmp")
        temporal.write_bytes(r.content)
        temporal.replace(ruta)

    return raiz


def buscar_descendiente_directo(
    elemento: ET.Element,
    nombres: set[str],
) -> str:
    nombres_n = {normalizar(n) for n in nombres}
    for hijo in list(elemento):
        if normalizar(nombre_etiqueta(hijo)) in nombres_n:
            valor = texto_elemento(hijo)
            if valor:
                return valor
    return ""


def buscar_descendiente(
    elemento: ET.Element,
    nombres: set[str],
) -> str:
    nombres_n = {normalizar(n) for n in nombres}
    for nodo in elemento.iter():
        if normalizar(nombre_etiqueta(nodo)) in nombres_n:
            valor = texto_elemento(nodo)
            if valor:
                return valor
    return ""


def obtener_metadatos(id_boe: str) -> ET.Element:
    return descargar_xml(
        f"{BASE_URL}/id/{id_boe}",
        ruta_cache(id_boe, "metadatos.xml"),
    )


def campos_metadatos(id_boe: str) -> tuple[str, str, str]:
    raiz = obtener_metadatos(id_boe)
    titulo = buscar_descendiente(
        raiz, {"titulo", "titulo_oficial", "nombre", "descripcion"}
    )
    departamento = buscar_descendiente(
        raiz,
        {
            "departamento",
            "departamento_nombre",
            "nombre_departamento",
            "texto_departamento",
        },
    )
    fecha = buscar_descendiente(
        raiz,
        {"fecha_publicacion", "fecha_disposicion", "fecha", "fecha_publicación"},
    )
    return limpiar(titulo), limpiar(departamento), limpiar(fecha)


def candidato_desde_elemento(elemento: ET.Element) -> tuple[str, str, str] | None:
    """
    Extrae un candidato solo cuando ID y título pertenecen al mismo registro.

    No usa texto agregado de nodos raíz, evitando que un BOE mencionado dentro
    de otra norma se confunda con el identificador del registro.
    """
    id_texto = buscar_descendiente_directo(
        elemento,
        {"identificador", "id", "id_boe", "referencia"},
    )
    titulo = buscar_descendiente_directo(
        elemento,
        {"titulo", "titulo_oficial", "nombre", "descripcion"},
    )

    id_boe = extraer_id_boe(id_texto)

    if not id_boe or not titulo:
        return None

    departamento = buscar_descendiente_directo(
        elemento,
        {
            "departamento",
            "departamento_nombre",
            "nombre_departamento",
            "texto_departamento",
        },
    )
    return id_boe, limpiar(titulo), limpiar(departamento)


def extraer_candidatos(raiz: ET.Element) -> list[NormaBOE]:
    candidatos: list[NormaBOE] = []
    vistos: set[str] = set()

    # Primera pasada: registros con campos hermanos directos.
    for elemento in raiz.iter():
        candidato = candidato_desde_elemento(elemento)
        if not candidato:
            continue
        id_boe, titulo, departamento = candidato
        if id_boe in vistos:
            continue
        candidatos.append(NormaBOE("", id_boe, titulo, departamento))
        vistos.add(id_boe)

    # Respaldo: algunos XML envuelven los campos un nivel adicional.
    if not candidatos:
        for elemento in raiz.iter():
            hijos = list(elemento)
            if not hijos:
                continue
            id_boe = ""
            titulo = ""
            departamento = ""
            for hijo in hijos:
                if not id_boe:
                    id_boe = extraer_id_boe(
                        buscar_descendiente(
                            hijo, {"identificador", "id", "id_boe", "referencia"}
                        )
                    )
                if not titulo:
                    titulo = buscar_descendiente(
                        hijo,
                        {"titulo", "titulo_oficial", "nombre", "descripcion"},
                    )
                if not departamento:
                    departamento = buscar_descendiente(
                        hijo,
                        {
                            "departamento",
                            "departamento_nombre",
                            "nombre_departamento",
                        },
                    )
            if id_boe and titulo and id_boe not in vistos:
                candidatos.append(
                    NormaBOE("", id_boe, limpiar(titulo), limpiar(departamento))
                )
                vistos.add(id_boe)

    return candidatos


def consultar_candidatos(cita: CitaNormativa) -> list[NormaBOE]:
    # Se busca únicamente la referencia canónica, no el nombre largo.
    consulta = {
        "query": {
            "query_string": {
                "query": f'titulo:"{cita.referencia}"'
            },
            "range": {},
        },
        "sort": [],
    }

    try:
        r = requests.get(
            BASE_URL,
            params={
                "query": json.dumps(
                    consulta,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "limit": 50,
            },
            timeout=TIMEOUT,
            headers={
                "Accept": "application/xml",
                "User-Agent": "OpoCoach/2.0",
            },
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise BOEError(
            f"No se pudo buscar {cita.referencia} en el BOE."
        ) from exc

    try:
        raiz = ET.fromstring(r.content)
    except ET.ParseError as exc:
        raise BOEError(
            f"La búsqueda de {cita.referencia} no devolvió XML válido."
        ) from exc

    return extraer_candidatos(raiz)


def validar_candidato(
    cita: CitaNormativa,
    candidato: NormaBOE,
) -> NormaBOE | None:
    """
    Validación cerrada: el título oficial debe contener exactamente la misma
    clase de norma, número y año. Una mera mención en el texto no sirve.
    """
    referencia = referencia_de_titulo(candidato.titulo)
    if referencia != (cita.tipo, cita.numero, cita.anio):
        return None

    datos_verificados = DATOS_IDS_VERIFICADOS.get(candidato.id_boe)
    if datos_verificados:
        titulo = limpiar(datos_verificados.get("titulo", "")) or candidato.titulo
        departamento = (
            limpiar(datos_verificados.get("departamento", ""))
            or candidato.departamento
        )
        fecha = ""
    else:
        titulo, departamento, fecha = campos_metadatos(candidato.id_boe)
        titulo = titulo or candidato.titulo
        departamento = departamento or candidato.departamento

    referencia_meta = referencia_de_titulo(titulo)
    if referencia_meta != (cita.tipo, cita.numero, cita.anio):
        return None

    if cita.fecha_iso:
        # Se compara con la fecha de la disposición que aparece en el título
        # oficial. El campo genérico "fecha" del BOE puede ser la fecha de
        # publicación y no debe descartar una norma correcta.
        try:
            cita_titulo = extraer_cita(titulo)
        except BOEError:
            cita_titulo = None

        if (
            cita_titulo is not None
            and cita_titulo.fecha_iso
            and cita_titulo.fecha_iso != cita.fecha_iso
        ):
            return None

    if cita.ambito == "valenciana":
        dep_n = normalizar(departamento)
        if not any(x in dep_n for x in (
            "comunitat valenciana",
            "comunidad valenciana",
            "generalitat valenciana",
        )):
            return None

    return NormaBOE(
        nombre_buscado="",
        id_boe=candidato.id_boe,
        titulo=titulo,
        departamento=departamento,
    )


def resolver_ambiguedad(
    cita: CitaNormativa,
    candidatos: list[NormaBOE],
) -> list[NormaBOE]:
    """
    Reduce una lista de candidatos válidos aplicando criterios objetivos.

    Orden de prioridad:
    1. Fecha completa de la disposición, cuando figura en la referencia.
    2. Ámbito valenciano, como regla general del proyecto OpoCoach.
    3. Jefatura del Estado, solo si no existe candidato valenciano.

    Si aún quedan varias coincidencias, no se elige arbitrariamente.
    """
    restantes = list(candidatos)

    if len(restantes) <= 1:
        return restantes

    if cita.fecha_iso:
        por_fecha: list[NormaBOE] = []
        for candidato in restantes:
            _, _, fecha = campos_metadatos(candidato.id_boe)
            fecha_limpia = limpiar(fecha)
            fecha_numerica = re.sub(r"\D", "", fecha_limpia)
            fecha_objetivo_numerica = cita.fecha_iso.replace("-", "")

            coincide = cita.fecha_iso in fecha_limpia
            if not coincide and fecha_objetivo_numerica in fecha_numerica:
                coincide = True

            if not coincide:
                m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", fecha_limpia)
                if m:
                    coincide = (
                        f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                        == cita.fecha_iso
                    )

            if coincide:
                por_fecha.append(candidato)

        if por_fecha:
            restantes = por_fecha

    if len(restantes) <= 1:
        return restantes

    valencianos = [
        candidato
        for candidato in restantes
        if any(
            termino in normalizar(candidato.departamento)
            for termino in (
                "comunitat valenciana",
                "comunidad valenciana",
                "generalitat valenciana",
                "consell",
            )
        )
    ]
    if valencianos:
        restantes = valencianos

    if len(restantes) <= 1:
        return restantes

    estatales = [
        candidato
        for candidato in restantes
        if "jefatura del estado" in normalizar(candidato.departamento)
    ]
    if estatales:
        restantes = estatales

    return restantes


def buscar_norma(nombre_norma: str) -> NormaBOE:
    nombre_norma = limpiar(nombre_norma)
    nombre_normalizado = normalizar(nombre_norma)

    # Algunas normas, como la Constitución Española, no tienen número/año
    # en su denominación. Solo se admiten alias explícitos verificados.
    id_especial = NORMAS_ESPECIALES.get(nombre_normalizado)
    if id_especial:
        titulo, departamento, _ = campos_metadatos(id_especial)
        if not titulo:
            raise BOEError(
                f"No se pudieron validar los metadatos oficiales de {id_especial}."
            )
        return NormaBOE(
            nombre_buscado=nombre_norma,
            id_boe=id_especial,
            titulo=titulo,
            departamento=departamento,
        )

    cita = extraer_cita(nombre_norma)

    cache = cargar_cache()
    datos_cache = cache.get(cita.clave)
    if isinstance(datos_cache, dict):
        candidato_cache = NormaBOE(
            nombre_buscado=nombre_norma,
            id_boe=limpiar(str(datos_cache.get("id_boe", ""))),
            titulo=limpiar(str(datos_cache.get("titulo", ""))),
            departamento=limpiar(str(datos_cache.get("departamento", ""))),
        )
        if candidato_cache.id_boe:
            validado = validar_candidato(cita, candidato_cache)
            if validado:
                return NormaBOE(
                    nombre_buscado=nombre_norma,
                    id_boe=validado.id_boe,
                    titulo=validado.titulo,
                    departamento=validado.departamento,
                )
        cache.pop(cita.clave, None)
        guardar_cache(cache)

    clave_verificada_fecha = (
        f"{cita.tipo}|{cita.numero}|{cita.anio}|{cita.fecha_iso}"
        if cita.fecha_iso
        else ""
    )
    clave_verificada = f"{cita.tipo}|{cita.numero}|{cita.anio}"
    id_verificado = (
        IDS_VERIFICADOS_POR_FECHA.get(clave_verificada_fecha)
        or IDS_VERIFICADOS.get(clave_verificada)
    )

    if id_verificado:
        datos_verificados = DATOS_IDS_VERIFICADOS.get(id_verificado)
        if datos_verificados:
            titulo = limpiar(datos_verificados["titulo"])
            departamento = limpiar(datos_verificados["departamento"])
        else:
            titulo, departamento, _ = campos_metadatos(id_verificado)

        candidato = NormaBOE("", id_verificado, titulo, departamento)
        validado = validar_candidato(cita, candidato)
        if not validado:
            raise BOEError(
                f"El identificador verificado {id_verificado} no superó "
                f"la validación para {cita.referencia}."
            )
        seleccionada = validado
    else:
        candidatos = consultar_candidatos(cita)
        validos: list[NormaBOE] = []

        for candidato in candidatos:
            validado = validar_candidato(cita, candidato)
            if validado:
                validos.append(validado)

        # Deduplicar por ID.
        validos = list({n.id_boe: n for n in validos}.values())

        # Resolver ambigüedades mediante criterios objetivos:
        # fecha completa, ámbito valenciano y, en último término,
        # Jefatura del Estado.
        validos = resolver_ambiguedad(cita, validos)

        if not validos:
            resumen = "; ".join(
                f"{c.id_boe}: {c.titulo}" for c in candidatos[:10]
            )
            detalle = f" Candidatos recibidos: {resumen}" if resumen else ""
            raise BOEError(
                f"No se encontró una coincidencia exacta para "
                f"{cita.referencia}.{detalle}"
            )

        if len(validos) > 1:
            resumen = "; ".join(
                f"{n.id_boe} | {n.departamento} | {n.titulo}"
                for n in validos
            )
            raise BOEError(
                f"Hay varias coincidencias exactas para {cita.referencia}; "
                f"no se selecciona ninguna automáticamente: {resumen}"
            )

        seleccionada = validos[0]

    resultado = NormaBOE(
        nombre_buscado=nombre_norma,
        id_boe=seleccionada.id_boe,
        titulo=seleccionada.titulo,
        departamento=seleccionada.departamento,
    )

    cache[cita.clave] = {
        "id_boe": resultado.id_boe,
        "titulo": resultado.titulo,
        "departamento": resultado.departamento,
        "referencia_exacta": cita.referencia,
        "fecha_iso": cita.fecha_iso,
        "version_cache": 3,
    }
    guardar_cache(cache)
    return resultado


def obtener_texto_completo(id_boe: str) -> ET.Element:
    id_boe = extraer_id_boe(id_boe)
    if not id_boe:
        raise BOEError("Identificador BOE no válido.")
    return descargar_xml(
        f"{BASE_URL}/id/{id_boe}/texto",
        ruta_cache(id_boe, "texto_completo.xml"),
    )


def obtener_indice_texto(id_boe: str) -> ET.Element:
    """Obtiene el índice oficial de bloques de la legislación consolidada."""
    id_boe = extraer_id_boe(id_boe)
    if not id_boe:
        raise BOEError("Identificador BOE no válido.")
    return descargar_xml(
        f"{BASE_URL}/id/{id_boe}/texto/indice",
        ruta_cache(id_boe, "texto_indice.xml"),
    )


def obtener_bloque_texto(id_boe: str, id_bloque: str) -> ET.Element:
    """Obtiene del BOE todas las versiones oficiales de un bloque concreto."""
    id_boe = extraer_id_boe(id_boe)
    id_bloque = limpiar(id_bloque)
    if not id_boe:
        raise BOEError("Identificador BOE no válido.")
    if not id_bloque:
        raise BOEError("Identificador de bloque BOE vacío.")
    return descargar_xml(
        f"{BASE_URL}/id/{id_boe}/texto/bloque/{id_bloque}",
        ruta_cache(id_boe, f"bloque_{id_bloque}.xml"),
    )


class _ExtractorTextoBOEHTML(HTMLParser):
    """Convierte el HTML del texto consolidado en líneas de texto legibles."""
    BLOQUES = {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br",
        "section", "article", "tr", "td", "th",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.partes.append(data)

    def texto(self) -> str:
        lineas = [limpiar(linea) for linea in "".join(self.partes).splitlines()]
        return "\n".join(linea for linea in lineas if linea)


def obtener_texto_consolidado_html(id_boe: str) -> str:
    """Descarga la vista HTML consolidada del BOE como respaldo."""
    id_boe = extraer_id_boe(id_boe)
    if not id_boe:
        raise BOEError("Identificador BOE no válido.")

    ruta = ruta_cache(id_boe, "texto_consolidado.html")
    if ruta.exists():
        try:
            contenido = ruta.read_text(encoding="utf-8")
            if contenido.strip():
                return contenido
        except OSError:
            ruta.unlink(missing_ok=True)

    try:
        respuesta = requests.get(
            "https://www.boe.es/buscar/act.php",
            params={"id": id_boe, "tn": 0},
            timeout=TIMEOUT,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "OpoCoach/2.0",
            },
            allow_redirects=True,
        )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise BOEError(
            f"No se pudo descargar el texto consolidado HTML de {id_boe}."
        ) from exc

    respuesta.encoding = respuesta.encoding or "utf-8"
    contenido = respuesta.text
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(contenido, encoding="utf-8")
    temporal.replace(ruta)
    return contenido


def obtener_documento_original_html(id_boe: str) -> str:
    """Descarga la publicación oficial original mediante doc.php."""
    id_boe = extraer_id_boe(id_boe)
    if not id_boe:
        raise BOEError("Identificador BOE no válido.")

    ruta = ruta_cache(id_boe, "documento_original.html")
    if ruta.exists():
        try:
            contenido = ruta.read_text(encoding="utf-8")
            if contenido.strip():
                return contenido
        except OSError:
            ruta.unlink(missing_ok=True)

    try:
        respuesta = requests.get(
            "https://www.boe.es/buscar/doc.php",
            params={"id": id_boe},
            timeout=TIMEOUT,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "OpoCoach/2.0",
            },
            allow_redirects=True,
        )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise BOEError(
            f"No se pudo descargar el documento oficial HTML de {id_boe}."
        ) from exc

    respuesta.encoding = respuesta.encoding or "utf-8"
    contenido = respuesta.text
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(contenido, encoding="utf-8")
    temporal.replace(ruta)
    return contenido


def extraer_articulo_desde_html(
    id_boe: str,
    articulo_base: str,
) -> tuple[str, str, str] | None:
    """Respaldo para documentos no disponibles en la API consolidada."""
    fuentes: list[str] = []

    try:
        fuentes.append(obtener_texto_consolidado_html(id_boe))
    except BOEError:
        pass

    try:
        original = obtener_documento_original_html(id_boe)
        if original not in fuentes:
            fuentes.append(original)
    except BOEError:
        pass

    variantes = variantes_encabezado_articulo(articulo_base)
    alternativas = "|".join(
        re.escape(variante) for variante in sorted(variantes, key=len, reverse=True)
    )
    patron_inicio = re.compile(
        rf"(?im)^(?:art[ií]culo|art\.?)\s*(?:{alternativas})"
        rf"(?=\.|\s|$)\.?\s*(.*)$"
    )
    patron_siguiente = re.compile(
        r"(?im)^(?:art[ií]culo|art\.?)\s*"
        r"(?:\d+(?:\.\d+)*(?:\s+(?:bis|ter|quater|quinquies|"
        r"sexies|septies|octies|nonies|decies))?|[uú]nico)"
        r"(?=\.|\s|$)"
    )

    candidatos: list[tuple[str, str, str]] = []

    for html in fuentes:
        parser = _ExtractorTextoBOEHTML()
        parser.feed(html)
        texto = parser.texto()

        for inicio in patron_inicio.finditer(texto):
            siguiente = patron_siguiente.search(texto, inicio.end())
            fin = siguiente.start() if siguiente else len(texto)
            cuerpo = limpiar(texto[inicio.start():fin].replace("\\n", " "))
            if not cuerpo:
                continue
            resto_titulo = limpiar(inicio.group(1))
            titulo = f"Artículo {articulo_base}"
            if resto_titulo:
                titulo += f". {resto_titulo}"
            candidatos.append((f"a{articulo_base}", titulo, cuerpo))

    if not candidatos:
        return None

    candidatos.sort(key=lambda item: (len(item[2]) < 25, len(item[2])))
    return candidatos[0]


def normalizar_numero_articulo(articulo: str) -> str:
    valor = limpiar(articulo).replace(",", ".")
    valor = re.sub(r"^(articulo|art\.?)\s*", "", valor, flags=re.I)
    return normalizar(valor.strip(" .ºª"))


UNIDADES_ARTICULO = {
    1: "primero", 2: "segundo", 3: "tercero", 4: "cuarto",
    5: "quinto", 6: "sexto", 7: "septimo", 8: "octavo",
    9: "noveno", 10: "diez", 11: "once", 12: "doce",
    13: "trece", 14: "catorce", 15: "quince", 16: "dieciseis",
    17: "diecisiete", 18: "dieciocho", 19: "diecinueve",
    20: "veinte", 21: "veintiuno", 22: "veintidos",
    23: "veintitres", 24: "veinticuatro", 25: "veinticinco",
    26: "veintiseis", 27: "veintisiete", 28: "veintiocho",
    29: "veintinueve",
}
DECENAS_ARTICULO = {
    30: "treinta", 40: "cuarenta", 50: "cincuenta",
    60: "sesenta", 70: "setenta", 80: "ochenta", 90: "noventa",
}


def numero_articulo_en_letras(numero: int) -> str:
    if numero in UNIDADES_ARTICULO:
        return UNIDADES_ARTICULO[numero]
    if numero in DECENAS_ARTICULO:
        return DECENAS_ARTICULO[numero]
    if 30 < numero < 100:
        decena = (numero // 10) * 10
        unidad = numero % 10
        unidad_texto = "uno" if unidad == 1 else UNIDADES_ARTICULO[unidad]
        return f"{DECENAS_ARTICULO[decena]} y {unidad_texto}"
    if numero == 100:
        return "cien"
    return ""


def variantes_encabezado_articulo(articulo_base: str) -> set[str]:
    base = normalizar(articulo_base)
    variantes = {base}
    if base.isdigit():
        en_letras = numero_articulo_en_letras(int(base))
        if en_letras:
            variantes.add(en_letras)
    return variantes


def encabezado_articulo(texto: str) -> tuple[str, str]:
    texto = limpiar(texto)
    m = re.match(
        r"^articulo\s+("
        r"(?:\d+(?:\.\d+)*(?:\s+(?:bis|ter|quater|quinquies|"
        r"sexies|septies|octies|nonies|decies))?)"
        r"|unico"
        r")(?:\.|\s|$)\s*(.*)$",
        normalizar(texto),
        flags=re.I | re.U,
    )
    if not m:
        return "", ""
    return limpiar(m.group(1)), limpiar(m.group(2))


def _datos_bloque_indice(elemento: ET.Element) -> tuple[str, str, str] | None:
    if nombre_etiqueta(elemento) != "bloque":
        return None
    id_bloque = buscar_descendiente_directo(elemento, {"id"})
    titulo = buscar_descendiente_directo(elemento, {"titulo"})
    fecha_actualizacion = buscar_descendiente_directo(
        elemento, {"fecha_actualizacion"}
    )
    if not id_bloque or not titulo:
        return None
    return (
        limpiar(id_bloque),
        limpiar(titulo),
        re.sub(r"\D", "", fecha_actualizacion),
    )


def _titulo_corresponde_articulo(titulo: str, articulo_base: str) -> bool:
    titulo_n = normalizar(titulo).strip(" .")
    for variante in variantes_encabezado_articulo(articulo_base):
        if re.fullmatch(
            rf"(?:articulo|art\.?)\s*{re.escape(variante)}",
            titulo_n,
            flags=re.I | re.U,
        ):
            return True
    return False


def _versiones_bloque(raiz: ET.Element) -> list[ET.Element]:
    return [
        elemento for elemento in raiz.iter()
        if nombre_etiqueta(elemento) == "version"
    ]


def _seleccionar_version_actualizada(
    raiz_bloque: ET.Element,
    fecha_actualizacion: str,
) -> ET.Element:
    versiones = _versiones_bloque(raiz_bloque)
    if not versiones:
        raise BOEError("El bloque BOE no contiene ninguna versión.")

    fecha_objetivo = re.sub(r"\D", "", fecha_actualizacion)
    if not fecha_objetivo:
        raise BOEError(
            "El índice BOE no proporciona fecha_actualizacion para el bloque."
        )

    coincidentes = []
    for version in versiones:
        fecha_publicacion = re.sub(
            r"\D", "", limpiar(version.attrib.get("fecha_publicacion", ""))
        )
        if fecha_publicacion == fecha_objetivo:
            coincidentes.append(version)

    if len(coincidentes) != 1:
        raise BOEError(
            "No existe una única versión cuya fecha_publicacion coincida con "
            f"fecha_actualizacion={fecha_objetivo}."
        )
    return coincidentes[0]


def _texto_version(version: ET.Element) -> str:
    """Extrae solo el contenido normativo; excluye las notas <blockquote>."""
    partes: list[str] = []
    for hijo in list(version):
        if nombre_etiqueta(hijo) == "blockquote":
            continue
        texto = texto_elemento(hijo)
        if texto:
            partes.append(texto)
    return limpiar(" ".join(partes))


def obtener_articulo(nombre_norma: str, articulo: str) -> ArticuloBOE:
    """
    Obtiene el artículo mediante:
        índice -> bloque -> versión cuya fecha_publicacion coincide
        con fecha_actualizacion.
    """
    norma = buscar_norma(nombre_norma)
    solicitado = limpiar(articulo).replace(",", ".")
    base = normalizar_numero_articulo(solicitado).split(".", 1)[0]

    if not base:
        raise BOEError(f"Número de artículo no válido: {articulo}")

    try:
        indice = obtener_indice_texto(norma.id_boe)
    except BOEError:
        respaldo_html = extraer_articulo_desde_html(norma.id_boe, base)
        if not respaldo_html:
            raise
        id_bloque, titulo, contenido = respaldo_html
        return ArticuloBOE(
            nombre_norma=nombre_norma,
            id_boe=norma.id_boe,
            departamento=norma.departamento,
            articulo=solicitado,
            id_bloque=id_bloque,
            titulo_bloque=titulo,
            texto=contenido,
        )

    candidatos: list[tuple[str, str, str]] = []
    for elemento in indice.iter():
        datos = _datos_bloque_indice(elemento)
        if not datos:
            continue
        id_bloque, titulo, fecha_actualizacion = datos
        if _titulo_corresponde_articulo(titulo, base):
            candidatos.append((id_bloque, titulo, fecha_actualizacion))

    if not candidatos:
        raise BOEError(
            f"El índice consolidado de {norma.id_boe} no contiene "
            f"el artículo {solicitado}."
        )

    resueltos: list[tuple[str, str, str, str]] = []
    errores: list[str] = []

    for id_bloque, titulo, fecha_actualizacion in candidatos:
        try:
            raiz_bloque = obtener_bloque_texto(norma.id_boe, id_bloque)
            version = _seleccionar_version_actualizada(
                raiz_bloque, fecha_actualizacion
            )
            contenido = _texto_version(version)
            if not contenido:
                raise BOEError(
                    "La versión seleccionada no contiene texto normativo."
                )
            resueltos.append(
                (id_bloque, titulo, fecha_actualizacion, contenido)
            )
        except BOEError as exc:
            errores.append(f"{id_bloque}: {exc}")

    if not resueltos:
        detalle = "; ".join(errores)
        raise BOEError(
            f"No se pudo resolver el artículo {solicitado} de "
            f"{norma.id_boe}. {detalle}"
        )

    if len(resueltos) > 1:
        fecha_maxima = max(item[2] for item in resueltos)
        mas_recientes = [
            item for item in resueltos if item[2] == fecha_maxima
        ]
        if len(mas_recientes) != 1:
            resumen = "; ".join(
                f"{item[0]} ({item[2]})" for item in resueltos
            )
            raise BOEError(
                f"Hay varios bloques válidos para el artículo {solicitado} "
                f"de {norma.id_boe}; no se selecciona arbitrariamente: "
                f"{resumen}"
            )
        resueltos = mas_recientes

    id_bloque, titulo, _, contenido = resueltos[0]
    return ArticuloBOE(
        nombre_norma=nombre_norma,
        id_boe=norma.id_boe,
        departamento=norma.departamento,
        articulo=solicitado,
        id_bloque=id_bloque,
        titulo_bloque=titulo,
        texto=contenido,
    )


def limpiar_cache_norma(nombre_norma: str) -> None:
    cita = extraer_cita(nombre_norma)
    cache = cargar_cache()
    if cita.clave in cache:
        del cache[cita.clave]
        guardar_cache(cache)


def limpiar_cache_articulo(id_boe: str, id_bloque: str) -> None:
    # Fuerza una nueva consulta del índice y del bloque concreto.
    ruta_cache(id_boe, "texto_indice.xml").unlink(missing_ok=True)
    if limpiar(id_bloque):
        ruta_cache(id_boe, f"bloque_{limpiar(id_bloque)}.xml").unlink(
            missing_ok=True
        )


def prueba_manual() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Localiza una norma por coincidencia jurídica exacta y obtiene "
            "un artículo de su texto consolidado."
        )
    )
    parser.add_argument("norma")
    parser.add_argument("articulo")
    args = parser.parse_args()

    resultado = obtener_articulo(args.norma, args.articulo)

    print()
    print(f"Norma solicitada: {resultado.nombre_norma}")
    print(f"BOE: {resultado.id_boe}")
    print(f"Departamento: {resultado.departamento}")
    print(f"Artículo: {resultado.articulo}")
    print(f"Bloque: {resultado.id_bloque}")
    print(f"Título: {resultado.titulo_bloque}")
    print()
    print(resultado.texto)


if __name__ == "__main__":
    prueba_manual()