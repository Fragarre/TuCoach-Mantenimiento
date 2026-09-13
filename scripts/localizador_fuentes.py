"""
Localizador común de identidad documental normativa.

Objetivo: identificar de forma conservadora la fuente oficial de una norma
sin modificar SQLite ni descargar corpus completo.

Prioridad operativa:
1. PDF local inequívoco, pero solo cuando su identidad está respaldada por el
   nombre del fichero, por un identificador oficial o por una identidad
   controlada (GEN/TUE/TFUE).
2. DOUE/EUR-Lex para Directivas y Reglamentos UE.
3. BOE para cualquier norma que el BOE pueda identificar de forma exacta,
   incluida normativa autonómica publicada también en BOE.
4. DOGV queda como localizador explícito, no como barrido automático: si BOE
   no resuelve con seguridad, el flujo normal debe pedir PDF local.

Nunca elige por similitud libre: una fuente solo se acepta si la identidad
jurídica tipo + número + año coincide exactamente con la referencia pedida.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from boe_api import BOEError, CitaNormativa, campos_metadatos, consultar_candidatos
from localizador_normativa import localizar_norma as localizar_boe
from pdf_normas import buscar_norma_local


TIMEOUT = 40
DOGV_DIAS_BUSQUEDA = 15
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
    """Devuelve la primera referencia normativa que aparece en el texto.

    La prioridad de los patrones solo resuelve empates en la misma posición
    (por ejemplo, Real Decreto Legislativo frente a Real Decreto). Nunca se
    permite que una norma citada más tarde en el título sustituya a la norma
    que encabeza el documento.
    """
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

    encontrados: list[tuple[int, int, str, str, str]] = []
    for prioridad, (tipo, patron, anio_primero) in enumerate(patrones):
        m = re.search(patron, n)
        if not m:
            continue
        if anio_primero:
            anio, numero = m.group(1), str(int(m.group(2)))
        else:
            numero, anio = str(int(m.group(1))), m.group(2)
        encontrados.append((m.start(), prioridad, tipo, numero, anio))

    if not encontrados:
        return None

    _, _, tipo, numero, anio = min(encontrados, key=lambda x: (x[0], x[1]))
    return tipo, numero, anio


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
    return False


def _pdf_local_identidad_respaldada(nombre: str, pdf) -> bool:
    """Impide que un recopilatorio adquiera identidad por una norma interna."""
    stem_n = normalizar(Path(pdf.ruta).stem)
    id_n = str(pdf.id_fuente or "").upper()

    # Identidades controladas o documentales oficiales.
    if stem_n.startswith("gen "):
        return True
    if id_n.startswith(("BOE-A-", "DOGV-", "LOCAL-DOGV-", "DOUE-")):
        return True
    if "tfue" in stem_n or re.search(r"\btue\b", stem_n):
        return True

    identidad_solicitada = extraer_identidad(nombre)
    if identidad_solicitada is None:
        # Normas históricas/descriptivas sin patrón número/año siguen usando el
        # criterio conservador previo de pdf_normas.
        return True

    # Para un LOCAL-PDF-* ordinario, el nombre físico debe identificar la misma
    # norma. Una aparición interna en el contenido no basta.
    identidad_fichero = extraer_identidad(Path(pdf.ruta).stem)
    return identidad_fichero == identidad_solicitada


def localizar_pdf_local(nombre: str) -> FuenteNormativa | None:
    try:
        pdf = buscar_norma_local(nombre)
    except BOEError:
        return None

    if not _pdf_local_identidad_respaldada(nombre, pdf):
        return None

    return FuenteNormativa(
        proveedor="PDF_LOCAL",
        id_fuente=pdf.id_fuente,
        titulo_oficial=pdf.titulo,
        url_oficial=str(Path(pdf.ruta).resolve()),
        metodo="pdf_local_identidad_respaldada",
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

    celex = celex_desde_identidad(identidad)
    url = f"https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:{celex}"
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise LocalizadorFuenteError(f"No se pudo consultar EUR-Lex: {exc}") from exc

    # La identidad fuerte en EUR-Lex es CELEX. No se valida el título HTML,
    # porque puede servirse en cualquier lengua de la UE.
    huella = f"{r.url}\n{r.text}".upper()
    if celex not in huella:
        raise LocalizadorFuenteError(
            f"EUR-Lex no confirmó el CELEX esperado {celex}."
        )

    sopa = BeautifulSoup(r.text, "html.parser")
    titulo = nombre
    h1 = sopa.find("h1")
    if h1:
        titulo = limpiar(h1.get_text(" ", strip=True)) or titulo

    return FuenteNormativa(
        proveedor="DOUE",
        id_fuente=f"DOUE-CELEX-{celex}",
        titulo_oficial=titulo,
        url_oficial=r.url,
        metodo="eurlex_celex_exacto",
    )


def _cita_boe_desde_nombre(nombre: str) -> CitaNormativa:
    identidad = extraer_identidad(nombre)
    if identidad is None:
        raise LocalizadorFuenteError(
            f"No se pudo extraer identidad normativa exacta de: {nombre}"
        )
    tipo, numero, anio = identidad
    return CitaNormativa(
        tipo=tipo.replace("real decreto ley", "real decreto-ley").replace("decreto ley", "decreto-ley"),
        numero=numero,
        anio=anio,
        fecha_iso=extraer_fecha(nombre) or "",
        ambito="valenciana" if es_valenciana(nombre) else "",
    )


def localizar_boe_fuente(nombre: str) -> FuenteNormativa:
    """Localiza BOE y corrige de forma conservadora títulos con normas citadas.

    Primero reutiliza el localizador BOE existente. Si este rechaza el candidato
    por haber extraído una norma citada más tarde en el título, se hace una sola
    búsqueda oficial por la cita canónica y se valida con la primera identidad
    normativa del título y de los metadatos oficiales.
    """
    try:
        norma = localizar_boe(nombre)
        return FuenteNormativa(
            proveedor="BOE",
            id_fuente=norma.id_boe,
            titulo_oficial=norma.titulo,
            url_oficial=norma.url_indice,
            metodo=norma.metodo,
        )
    except Exception as error_primario:
        cita = _cita_boe_desde_nombre(nombre)
        try:
            candidatos = consultar_candidatos(cita)
        except Exception as exc:
            raise LocalizadorFuenteError(str(error_primario)) from exc

        objetivo = extraer_identidad(nombre)
        exactos = []
        for candidato in candidatos:
            if extraer_identidad(candidato.titulo) != objetivo:
                continue
            try:
                titulo_meta, departamento, _ = campos_metadatos(candidato.id_boe)
            except Exception:
                continue
            titulo_meta = titulo_meta or candidato.titulo
            departamento = departamento or candidato.departamento
            if extraer_identidad(titulo_meta) != objetivo:
                continue

            fecha_objetivo = extraer_fecha(nombre)
            fecha_titulo = extraer_fecha(titulo_meta)
            if fecha_objetivo and fecha_titulo and fecha_objetivo != fecha_titulo:
                continue

            if es_valenciana(nombre):
                dep_n = normalizar(departamento)
                if not any(x in dep_n for x in (
                    "comunitat valenciana", "comunidad valenciana",
                    "generalitat valenciana", "consell",
                )):
                    continue

            exactos.append((candidato.id_boe, titulo_meta))

        exactos = sorted(set(exactos))
        if len(exactos) != 1:
            raise LocalizadorFuenteError(str(error_primario)) from error_primario

        id_boe, titulo = exactos[0]
        return FuenteNormativa(
            proveedor="BOE",
            id_fuente=id_boe,
            titulo_oficial=titulo,
            url_oficial=f"https://www.boe.es/buscar/act.php?id={id_boe}&tn=2",
            metodo="boe_api_identidad_primera",
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

    fecha_disposicion = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
    exactos: dict[str, str] = {}
    errores_red: list[str] = []

    for desplazamiento in range(DOGV_DIAS_BUSQUEDA + 1):
        fecha = fecha_disposicion + timedelta(days=desplazamiento)
        try:
            candidatos = _candidatos_dogv_fecha(fecha.isoformat())
        except LocalizadorFuenteError as exc:
            errores_red.append(str(exc))
            continue
        for url, contexto in candidatos:
            if extraer_identidad(contexto) == identidad:
                exactos[url] = contexto

    if len(exactos) != 1:
        detalle = f" Coincidencias: {len(exactos)}."
        if not exactos and errores_red:
            detalle += f" Errores de consulta: {len(errores_red)}."
        raise LocalizadorFuenteError(
            f"DOGV no produjo una única coincidencia exacta para {identidad} "
            f"entre {fecha_disposicion.isoformat()} y "
            f"{(fecha_disposicion + timedelta(days=DOGV_DIAS_BUSQUEDA)).isoformat()}."
            + detalle
        )

    url, titulo = next(iter(exactos.items()))
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
        metodo="dogv_sumarios_ventana_exacta",
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

    # BOE se intenta también para normativa autonómica publicada allí. Si no
    # existe coincidencia segura, el flujo normal pide fuente PDF local; DOGV
    # no se barre automáticamente.
    return localizar_boe_fuente(nombre)


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
