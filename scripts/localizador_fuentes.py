"""
Localizador común de identidad documental normativa.

Objetivo: identificar de forma conservadora la fuente oficial de una norma
sin modificar SQLite ni descargar corpus completo.

Prioridad:
1. PDF local inequívoco (incluidos GEN-*).
2. DOUE/EUR-Lex para Directivas y Reglamentos UE.
3. DOGV para normativa valenciana.
4. BOE para normativa estatal.

Nunca elige por similitud libre: una fuente solo se acepta si la identidad
jurídica tipo + número + año coincide exactamente con la referencia pedida.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from boe_api import BOEError
from localizador_normativa import localizar_norma as localizar_boe
from pdf_normas import buscar_norma_local


TIMEOUT = 40
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36 OpoCoach/1.0"
)


class LocalizadorFuenteError(RuntimeError):
    pass


@dataclass(frozen=True)
class FuenteNormativa:
    proveedor: str
    id_fuente: str
    titulo_oficial: str
    url_oficial: str
    metodo: str


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def normalizar(texto: str | None) -> str:
    valor = unicodedata.normalize("NFKD", texto or "")
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    valor = valor.lower()
    valor = re.sub(r"[^a-z0-9]+", " ", valor)
    return re.sub(r"\s+", " ", valor).strip()


def extraer_identidad(texto: str) -> tuple[str, str, str] | None:
    n = normalizar(texto)
    patrones = (
        ("directiva", r"\bdirectiva(?: ue| cee| ce)?\s+(\d{4})\s+(\d+)\b", True),
        ("reglamento", r"\breglamento(?: ue euratom| ue| ce euratom| ce| euratom)?\s+(\d{4})\s+(\d+)\b", True),
        ("real decreto legislativo", r"\breal decreto legislativo\s+(\d+)\s+(\d{4})\b", False),
        ("real decreto ley", r"\breal decreto ley\s+(\d+)\s+(\d{4})\b", False),
        ("decreto legislativo", r"\bdecreto legislativo\s+(\d+)\s+(\d{4})\b", False),
        ("decreto ley", r"\bdecreto ley\s+(\d+)\s+(\d{4})\b", False),
        ("ley organica", r"\bley organica\s+(\d+)\s+(\d{4})\b", False),
        ("real decreto", r"\breal decreto\s+(\d+)\s+(\d{4})\b", False),
        ("ley", r"\bley\s+(\d+)\s+(\d{4})\b", False),
        ("decreto", r"\bdecreto\s+(\d+)\s+(\d{4})\b", False),
        ("orden", r"\borden\s+(\d+)\s+(\d{4})\b", False),
    )
    for tipo, patron, anio_primero in patrones:
        m = re.search(patron, n)
        if not m:
            continue
        if anio_primero:
            anio, numero = m.group(1), str(int(m.group(2)))
        else:
            numero, anio = str(int(m.group(1))), m.group(2)
        return tipo, numero, anio
    return None


def extraer_fecha(texto: str) -> str | None:
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "setiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12,
    }
    n = normalizar(texto)
    m = re.search(
        r"\bde\s+(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|setiembre|octubre|noviembre|diciembre)"
        r"(?:\s+de\s+(\d{4}))?\b",
        n,
    )
    if not m:
        return None
    identidad = extraer_identidad(texto)
    anio = m.group(3) or (identidad[2] if identidad else None)
    if not anio:
        return None
    try:
        return datetime(int(anio), meses[m.group(2)], int(m.group(1))).date().isoformat()
    except ValueError:
        return None


def es_union_europea(nombre: str) -> bool:
    identidad = extraer_identidad(nombre)
    return bool(identidad and identidad[0] in {"directiva", "reglamento"})


def es_valenciana(nombre: str) -> bool:
    n = normalizar(nombre)
    if any(x in n for x in (
        "comunitat valenciana", "comunidad valenciana",
        "generalitat valenciana", "generalitat", "consell",
    )):
        return True
    identidad = extraer_identidad(nombre)
    if not identidad:
        return False
    tipo = identidad[0]
    # Decreto/Decreto-ley/Decreto legislativo sin "Real" y con "Consell"
    # ya quedan cubiertos arriba. No se atribuyen autonomías solo por el rango.
    return False


def localizar_pdf_local(nombre: str) -> FuenteNormativa | None:
    try:
        pdf = buscar_norma_local(nombre)
    except BOEError:
        return None
    return FuenteNormativa(
        proveedor="PDF_LOCAL",
        id_fuente=pdf.id_fuente,
        titulo_oficial=pdf.titulo,
        url_oficial=str(Path(pdf.ruta).resolve()),
        metodo="pdf_local_inequivoco",
    )


def celex_desde_identidad(identidad: tuple[str, str, str]) -> str:
    tipo, numero, anio = identidad
    letra = {"directiva": "L", "reglamento": "R"}.get(tipo)
    if letra is None:
        raise LocalizadorFuenteError(f"Tipo UE no soportado: {tipo}")
    return f"3{anio}{letra}{int(numero):04d}"


def localizar_doue(nombre: str) -> FuenteNormativa:
    identidad = extraer_identidad(nombre)
    if identidad is None or identidad[0] not in {"directiva", "reglamento"}:
        raise LocalizadorFuenteError("La referencia no contiene una Directiva o Reglamento UE inequívoco.")

    tipo, numero, anio = identidad
    eli_tipo = "dir" if tipo == "directiva" else "reg"
    url = f"https://eur-lex.europa.eu/eli/{eli_tipo}/{anio}/{int(numero)}/oj"
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise LocalizadorFuenteError(f"No se pudo consultar EUR-Lex: {exc}") from exc

    texto = limpiar(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
    identidad_doc = extraer_identidad(texto)
    if identidad_doc != identidad:
        raise LocalizadorFuenteError(
            f"EUR-Lex no confirmó la identidad exacta {identidad}; obtuvo {identidad_doc}."
        )

    celex = celex_desde_identidad(identidad)
    if celex not in texto.upper():
        m = re.search(r"\b[03]\d{4}[LRD]\d{4}\b", texto.upper())
        if not m or m.group(0) != celex:
            raise LocalizadorFuenteError(
                f"EUR-Lex no confirmó el CELEX esperado {celex}."
            )

    titulo = nombre
    sopa = BeautifulSoup(r.text, "html.parser")
    h1 = sopa.find("h1")
    if h1:
        titulo = limpiar(h1.get_text(" ", strip=True)) or titulo

    return FuenteNormativa(
        proveedor="DOUE",
        id_fuente=f"DOUE-CELEX-{celex}",
        titulo_oficial=titulo,
        url_oficial=r.url,
        metodo="eurlex_eli_exacta",
    )


def _candidatos_dogv_fecha(fecha_iso: str) -> list[tuple[str, str]]:
    fecha = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
    base = f"https://dogv.gva.es/datos/{fecha:%Y/%m/%d}/"
    urls_portal = (
        urljoin(base, "PortalCAS.html"),
        urljoin(base, "PortalVAL.html"),
    )
    candidatos: dict[str, str] = {}
    ultimo_error: Exception | None = None
    for portal in urls_portal:
        try:
            r = requests.get(portal, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            if r.status_code == 404:
                continue
            r.raise_for_status()
        except requests.RequestException as exc:
            ultimo_error = exc
            continue
        sopa = BeautifulSoup(r.text, "html.parser")
        for a in sopa.find_all("a", href=True):
            href = str(a.get("href", ""))
            if "/pdf/" not in href.lower() and not href.lower().endswith(".pdf"):
                continue
            url = urljoin(r.url, href)
            contexto = limpiar(a.get_text(" ", strip=True))
            if a.parent is not None:
                contexto = limpiar(a.parent.get_text(" ", strip=True)) or contexto
            candidatos[url] = contexto
    if not candidatos and ultimo_error is not None:
        raise LocalizadorFuenteError(
            f"No se pudo consultar el sumario DOGV de {fecha_iso}: {ultimo_error}"
        )
    return sorted(candidatos.items())


def localizar_dogv(nombre: str) -> FuenteNormativa:
    identidad = extraer_identidad(nombre)
    fecha_iso = extraer_fecha(nombre)
    if identidad is None or fecha_iso is None:
        raise LocalizadorFuenteError(
            "DOGV requiere identidad tipo+número+año y fecha completa de disposición."
        )

    candidatos = _candidatos_dogv_fecha(fecha_iso)
    exactos: list[tuple[str, str]] = []
    for url, contexto in candidatos:
        if extraer_identidad(contexto) == identidad:
            exactos.append((url, contexto))

    # El DOGV puede publicar la disposición días después de la fecha de firma.
    # Si el sumario del día de disposición no contiene coincidencia, no se
    # adivina otra fecha: se deja pendiente para un localizador DOGV más amplio.
    if len(exactos) != 1:
        raise LocalizadorFuenteError(
            f"DOGV no produjo una única coincidencia exacta para {identidad} "
            f"en la fecha {fecha_iso}. Coincidencias: {len(exactos)}."
        )

    url, titulo = exactos[0]
    m = re.search(r"/pdf/([^/?#]+)\.pdf", url, re.I)
    if not m:
        raise LocalizadorFuenteError(f"No se pudo obtener identificador DOGV de {url}")
    signatura = m.group(1)
    id_fuente = f"DOGV-{signatura.upper()}"
    return FuenteNormativa(
        proveedor="DOGV",
        id_fuente=id_fuente,
        titulo_oficial=titulo or nombre,
        url_oficial=url,
        metodo="dogv_sumario_fecha_exacta",
    )


def localizar_fuente(nombre: str) -> FuenteNormativa:
    nombre = limpiar(nombre)
    if not nombre:
        raise ValueError("Debe indicarse una referencia normativa.")

    local = localizar_pdf_local(nombre)
    if local is not None:
        return local

    if es_union_europea(nombre):
        return localizar_doue(nombre)

    if es_valenciana(nombre):
        return localizar_dogv(nombre)

    try:
        norma = localizar_boe(nombre)
    except Exception as exc:
        raise LocalizadorFuenteError(str(exc)) from exc
    return FuenteNormativa(
        proveedor="BOE",
        id_fuente=norma.id_boe,
        titulo_oficial=norma.titulo,
        url_oficial=norma.url_indice,
        metodo=norma.metodo,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Localiza una fuente normativa sin modificar SQLite.")
    parser.add_argument("referencia")
    args = parser.parse_args()
    try:
        fuente = localizar_fuente(args.referencia)
    except Exception as exc:
        print(f"ERROR: {exc.__class__.__name__}: {exc}")
        return 1
    for clave, valor in asdict(fuente).items():
        print(f"{clave}: {valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
