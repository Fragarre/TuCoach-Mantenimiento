"""
TuCoach - Cliente nuevo para legislación consolidada del BOE.

Principio de seguridad:
- una norma solo se acepta cuando su tipo, número y año coinciden exactamente;
- una norma que simplemente menciona o modifica la solicitada se rechaza;
- ante ambigüedad, se produce BOEError y nunca se elige arbitrariamente.

Interfaz compatible con resolver_referencias_boe.py:
    buscar_norma(nombre_norma) -> NormaBOE
    obtener_articulo(nombre_norma, articulo) -> ArticuloBOE
    limpiar_cache_norma(nombre_norma)
    limpiar_cache_articulo(id_boe, id_bloque)

Uso desde la raíz del proyecto:

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


def texto_articulo_manifiestamente_incompleto(
    texto: str | None,
    titulo_bloque: str | None = None,
) -> bool:
    """Detecta corpus manifiestamente incompleto sin usar umbrales de longitud."""
    limpio = limpiar(texto)
    if not limpio:
        return True

    normal = normalizar(limpio).rstrip(" .:;-")
    titulo = normalizar(titulo_bloque).rstrip(" .:;-")

    # Solo es inequívoco que falte el cuerpo cuando el supuesto texto se limita
    # a un encabezado corto. Algunos importadores históricos guardaron en
    # titulo_bloque el artículo completo; en esos casos texto == titulo es válido.
    if titulo and normal == titulo and len(limpio) < 80:
        return True

    # Caso inequívoco: solo "Artículo N" sin rúbrica ni cuerpo.
    return bool(
        re.fullmatch(
            r"(?:articulo|art\.?)\s+"
            r"\d+(?:\.\d+)*(?:\s*(?:bis|ter|quater|quinquies))?"
            r"(?:\.[a-z])?\s*\.?",
            normal,
            flags=re.I,
        )
    )


def texto_articulo_suficiente(
    texto: str | None,
    titulo_bloque: str | None = None,
) -> bool:
    return not texto_articulo_manifiestamente_incompleto(texto, titulo_bloque)


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
        raise BOEError(f"No se reconoce una cita normativa inequívoca: {nombre_norma}")

    fecha_iso = ""
    patron_fecha = re.search(
        r"\b(?:de\s+)?(\d{1,2})\s+de\s+([a-záéíóúü]+)\s+de\s+(\d{4})\b",
        texto_n,
        flags=re.I,
    )
    if patron_fecha:
        mes = MESES.get(normalizar(patron_fecha.group(2)))
        if mes:
            fecha_iso = (
                f"{patron_fecha.group(3)}-{mes}-{int(patron_fecha.group(1)):02d}"
            )

    ambito = ""
    if "generalitat" in texto_n or "comunitat valenciana" in texto_n:
        ambito = "comunitat valenciana"

    return CitaNormativa(
        tipo=tipo_encontrado,
        numero=numero,
        anio=anio,
        fecha_iso=fecha_iso,
        ambito=ambito,
    )


class _ExtractorTextoHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.partes: list[str] = []

    def handle_data(self, data: str) -> None:
        if limpiar(data):
            self.partes.append(limpiar(data))

    @property
    def texto(self) -> str:
        return limpiar(" ".join(self.partes))


def _cargar_cache_normas() -> dict[str, Any]:
    if not CACHE_NORMAS.exists():
        return {}
    try:
        return json.loads(CACHE_NORMAS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _guardar_cache_normas(datos: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_NORMAS.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _peticion_json(url: str) -> dict[str, Any]:
    respuesta = requests.get(
        url,
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def _peticion_texto(url: str) -> str:
    respuesta = requests.get(url, timeout=TIMEOUT)
    respuesta.raise_for_status()
    return respuesta.text


def _norma_desde_id(nombre_norma: str, id_boe: str) -> NormaBOE:
    datos_verificados = DATOS_IDS_VERIFICADOS.get(id_boe)
    if datos_verificados:
        return NormaBOE(
            nombre_buscado=nombre_norma,
            id_boe=id_boe,
            titulo=datos_verificados["titulo"],
            departamento=datos_verificados.get("departamento", ""),
        )

    datos = _peticion_json(f"{BASE_URL}/id/{id_boe}")
    meta = datos.get("data", {})
    return NormaBOE(
        nombre_buscado=nombre_norma,
        id_boe=id_boe,
        titulo=limpiar(meta.get("titulo")),
        departamento=limpiar(meta.get("departamento")),
    )


def buscar_norma(nombre_norma: str) -> NormaBOE:
    nombre_n = normalizar(nombre_norma)

    id_especial = NORMAS_ESPECIALES.get(nombre_n)
    if id_especial:
        return _norma_desde_id(nombre_norma, id_especial)

    cita = extraer_cita(nombre_norma)
    if cita.fecha_iso:
        clave_fecha = f"{cita.tipo}|{cita.numero}|{cita.anio}|{cita.fecha_iso}"
        if clave_fecha in IDS_VERIFICADOS_POR_FECHA:
            return _norma_desde_id(
                nombre_norma,
                IDS_VERIFICADOS_POR_FECHA[clave_fecha],
            )

    clave_simple = f"{cita.tipo}|{cita.numero}|{cita.anio}"
    if clave_simple in IDS_VERIFICADOS:
        return _norma_desde_id(nombre_norma, IDS_VERIFICADOS[clave_simple])

    cache = _cargar_cache_normas()
    if cita.clave in cache:
        dato = cache[cita.clave]
        return NormaBOE(
            nombre_buscado=nombre_norma,
            id_boe=dato["id_boe"],
            titulo=dato["titulo"],
            departamento=dato.get("departamento", ""),
        )

    consulta = requests.get(
        f"{BASE_URL}/buscar",
        params={"query": cita.referencia},
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    consulta.raise_for_status()
    payload = consulta.json()

    candidatos: list[NormaBOE] = []
    for item in payload.get("data", []):
        titulo = limpiar(item.get("titulo"))
        id_boe = extraer_id_boe(str(item.get("id", ""))) or limpiar(item.get("id"))
        departamento = limpiar(item.get("departamento"))
        titulo_n = normalizar(titulo)

        patron = rf"\b{re.escape(cita.tipo)}\s+{re.escape(cita.numero)}\s*/\s*{re.escape(cita.anio)}\b"
        if not re.search(patron, titulo_n):
            continue

        if cita.ambito == "comunitat valenciana":
            conjunto = f"{titulo_n} {normalizar(departamento)}"
            if not any(
                marca in conjunto
                for marca in ("comunitat valenciana", "generalitat", "valenciana")
            ):
                continue

        candidatos.append(
            NormaBOE(
                nombre_buscado=nombre_norma,
                id_boe=id_boe,
                titulo=titulo,
                departamento=departamento,
            )
        )

    ids_unicos = {c.id_boe for c in candidatos}
    if len(ids_unicos) != 1:
        raise BOEError(
            "No se pudo resolver de forma inequívoca la norma "
            f"{nombre_norma!r}: {len(ids_unicos)} candidatos válidos."
        )

    elegido = candidatos[0]
    cache[cita.clave] = {
        "id_boe": elegido.id_boe,
        "titulo": elegido.titulo,
        "departamento": elegido.departamento,
    }
    _guardar_cache_normas(cache)
    return elegido


def limpiar_cache_norma(nombre_norma: str) -> None:
    nombre_n = normalizar(nombre_norma)
    if nombre_n in NORMAS_ESPECIALES:
        return
    cita = extraer_cita(nombre_norma)
    cache = _cargar_cache_normas()
    if cita.clave in cache:
        del cache[cita.clave]
        _guardar_cache_normas(cache)


def limpiar_cache_articulo(id_boe: str, id_bloque: str) -> None:
    ruta = CACHE_DIR / "articulos" / id_boe / f"{id_bloque}.json"
    if ruta.exists():
        ruta.unlink()


def _extraer_articulos_xml(xml_texto: str) -> list[ArticuloBOE]:
    raiz = ET.fromstring(xml_texto)
    articulos: list[ArticuloBOE] = []

    for elem in raiz.iter():
        nombre = nombre_etiqueta(elem)
        if nombre not in {"bloque", "articulo", "article"}:
            continue

        id_bloque = limpiar(elem.attrib.get("id"))
        titulo = ""
        cuerpo = ""
        for hijo in elem:
            etiqueta = nombre_etiqueta(hijo)
            if etiqueta in {"titulo", "h2", "h3"} and not titulo:
                titulo = texto_elemento(hijo)
            elif etiqueta in {"texto", "p", "parrafo"}:
                cuerpo = limpiar(f"{cuerpo} {texto_elemento(hijo)}")

        texto_total = limpiar(f"{titulo} {cuerpo}")
        m = re.search(r"\bArt(?:ículo|iculo|\.)\s+([^\.\s:]+)", texto_total, re.I)
        if not m:
            continue

        articulos.append(
            ArticuloBOE(
                nombre_norma="",
                id_boe="",
                departamento="",
                articulo=limpiar(m.group(1)),
                id_bloque=id_bloque,
                titulo_bloque=titulo,
                texto=texto_total,
            )
        )

    return articulos


def obtener_articulo(nombre_norma: str, articulo: str) -> ArticuloBOE:
    norma = buscar_norma(nombre_norma)
    articulo_limpio = limpiar(articulo)
    ruta_cache = CACHE_DIR / "articulos" / norma.id_boe / f"{articulo_limpio}.json"

    if ruta_cache.exists():
        try:
            dato = json.loads(ruta_cache.read_text(encoding="utf-8"))
            return ArticuloBOE(**dato)
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    xml_texto = _peticion_texto(f"{BASE_URL}/id/{norma.id_boe}/texto")
    encontrados = _extraer_articulos_xml(xml_texto)

    candidatos = [
        art
        for art in encontrados
        if normalizar(art.articulo) == normalizar(articulo_limpio)
    ]

    if len(candidatos) != 1:
        raise BOEError(
            f"Artículo {articulo!r} no resuelto inequívocamente en {norma.id_boe}: "
            f"{len(candidatos)} candidatos."
        )

    elegido = candidatos[0]
    resultado = ArticuloBOE(
        nombre_norma=nombre_norma,
        id_boe=norma.id_boe,
        departamento=norma.departamento,
        articulo=articulo_limpio,
        id_bloque=elegido.id_bloque,
        titulo_bloque=elegido.titulo_bloque,
        texto=elegido.texto,
    )

    ruta_cache.parent.mkdir(parents=True, exist_ok=True)
    ruta_cache.write_text(
        json.dumps(resultado.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("nombre_norma")
    parser.add_argument("articulo")
    args = parser.parse_args()

    articulo = obtener_articulo(args.nombre_norma, args.articulo)
    print(json.dumps(articulo.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
