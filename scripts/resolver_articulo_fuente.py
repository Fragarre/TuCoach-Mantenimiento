"""Resolución de un artículo a partir de una fuente normativa ya validada.

Este módulo separa dos responsabilidades:
- localizador_fuentes.localizar_fuente() decide QUÉ documento es la norma;
- este módulo obtiene el artículo desde ESE documento y comprueba que la
  identidad documental no cambia durante la extracción.

No escribe en SQLite.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from boe_api import (
    ArticuloBOE,
    BOEError,
    _datos_bloque_indice,
    _seleccionar_version_actualizada,
    _texto_version,
    _titulo_corresponde_articulo,
    campos_metadatos,
    extraer_articulo_desde_html,
    normalizar_numero_articulo,
    obtener_bloque_texto,
    obtener_indice_texto,
    texto_articulo_suficiente,
)
from localizador_fuentes import (
    FuenteNormativa,
    LocalizadorFuenteError,
    localizar_fuente,
)
from pdf_normas import obtener_articulo as obtener_articulo_pdf

TIMEOUT = 40
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36 OpoCoach/1.0"
)


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def _obtener_boe(nombre: str, articulo: str, fuente: FuenteNormativa) -> ArticuloBOE:
    """Obtiene un artículo BOE directamente desde el ID ya validado.

    No vuelve a localizar la norma por su título. La identidad documental
    queda fijada por localizador_fuentes antes de entrar aquí.
    """
    id_boe = limpiar(fuente.id_fuente).upper()
    solicitado = limpiar(articulo).replace(",", ".")
    base = normalizar_numero_articulo(solicitado).split(".", 1)[0]
    if not base:
        raise BOEError(f"Número de artículo no válido: {articulo}")

    try:
        _, departamento, _ = campos_metadatos(id_boe)
    except BOEError:
        departamento = ""

    try:
        indice = obtener_indice_texto(id_boe)
    except BOEError:
        respaldo = extraer_articulo_desde_html(id_boe, base)
        if respaldo is None:
            raise
        id_bloque, titulo, contenido = respaldo
        if not texto_articulo_suficiente(contenido, titulo):
            raise BOEError(
                f"El respaldo HTML de {id_boe} no contiene texto suficiente "
                f"para el artículo {solicitado}."
            )
        return ArticuloBOE(
            nombre_norma=nombre,
            id_boe=id_boe,
            departamento=departamento,
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
        respaldo = extraer_articulo_desde_html(id_boe, base)
        if respaldo is not None:
            id_bloque, titulo, contenido = respaldo
            if texto_articulo_suficiente(contenido, titulo):
                return ArticuloBOE(
                    nombre_norma=nombre,
                    id_boe=id_boe,
                    departamento=departamento,
                    articulo=solicitado,
                    id_bloque=id_bloque,
                    titulo_bloque=titulo,
                    texto=contenido,
                )
        raise BOEError(
            f"El índice consolidado de {id_boe} no contiene el artículo "
            f"{solicitado} y el respaldo HTML tampoco permitió recuperarlo."
        )

    resueltos: list[tuple[str, str, str, str]] = []
    errores: list[str] = []
    for id_bloque, titulo, fecha_actualizacion in candidatos:
        try:
            raiz_bloque = obtener_bloque_texto(id_boe, id_bloque)
            version = _seleccionar_version_actualizada(
                raiz_bloque,
                fecha_actualizacion,
            )
            contenido = _texto_version(version)
            if not texto_articulo_suficiente(contenido, titulo):
                respaldo = extraer_articulo_desde_html(id_boe, base)
                if respaldo is not None:
                    id_html, titulo_html, contenido_html = respaldo
                    if texto_articulo_suficiente(contenido_html, titulo_html):
                        id_bloque = id_html
                        titulo = titulo_html
                        contenido = contenido_html
            if not texto_articulo_suficiente(contenido, titulo):
                raise BOEError(
                    "El artículo recuperado no contiene cuerpo normativo; "
                    "solo se obtuvo el título/rúbrica o texto vacío."
                )
            resueltos.append(
                (id_bloque, titulo, fecha_actualizacion, contenido)
            )
        except BOEError as exc:
            errores.append(f"{id_bloque}: {exc}")

    if not resueltos:
        detalle = "; ".join(errores)
        raise BOEError(
            f"No se pudo resolver el artículo {solicitado} de {id_boe}. "
            f"{detalle}"
        )

    if len(resueltos) > 1:
        fecha_maxima = max(item[2] for item in resueltos)
        mas_recientes = [item for item in resueltos if item[2] == fecha_maxima]
        if len(mas_recientes) != 1:
            resumen = "; ".join(
                f"{item[0]} ({item[2]})" for item in resueltos
            )
            raise BOEError(
                f"Hay varios bloques válidos para el artículo {solicitado} "
                f"de {id_boe}; no se selecciona arbitrariamente: {resumen}"
            )
        resueltos = mas_recientes

    id_bloque, titulo, _, contenido = resueltos[0]
    return ArticuloBOE(
        nombre_norma=nombre,
        id_boe=id_boe,
        departamento=departamento,
        articulo=solicitado,
        id_bloque=id_bloque,
        titulo_bloque=titulo,
        texto=contenido,
    )


def _lineas_eurlex(html: str) -> list[str]:
    sopa = BeautifulSoup(html, "html.parser")
    for nodo in sopa(["script", "style", "noscript"]):
        nodo.decompose()
    texto = sopa.get_text("\n")
    return [limpiar(x) for x in texto.splitlines() if limpiar(x)]


def _es_encabezado_articulo(linea: str) -> re.Match[str] | None:
    return re.fullmatch(
        r"Art[ií]culo\s+(\d+(?:\s*(?:bis|ter|quater|quinquies))?)\.?",
        linea,
        flags=re.I,
    )


def _extraer_articulo_eurlex(html: str, solicitado: str) -> tuple[str, str]:
    base = limpiar(solicitado).replace(",", ".").split(".", 1)[0]
    lineas = _lineas_eurlex(html)
    posiciones: list[int] = []
    for i, linea in enumerate(lineas):
        m = _es_encabezado_articulo(linea)
        if m and limpiar(m.group(1)).lower() == base.lower():
            posiciones.append(i)

    candidatos: list[tuple[int, str, str]] = []
    for inicio in posiciones:
        fin = len(lineas)
        for j in range(inicio + 1, len(lineas)):
            if _es_encabezado_articulo(lineas[j]):
                fin = j
                break
        bloque = lineas[inicio:fin]
        if len(bloque) < 3:
            continue
        titulo = f"Artículo {base}"
        siguiente = bloque[1]
        if not re.match(r"^\d+[.)]?\s", siguiente):
            titulo = f"Artículo {base}. {siguiente}"
        texto = "\n".join(bloque).strip()
        candidatos.append((len(texto), titulo, texto))

    if not candidatos:
        raise BOEError(
            f"EUR-Lex no contiene un bloque inequívoco para el artículo {solicitado}."
        )

    candidatos.sort(key=lambda x: x[0], reverse=True)
    _, titulo, texto = candidatos[0]
    return titulo, texto


def _token_consolidacion_celex(celex: str) -> str:
    """Convierte un CELEX legislativo sector 3 en su token de consolidación."""
    celex = limpiar(celex).upper()
    if not re.fullmatch(r"3\d{4}[A-Z]\d{4}", celex):
        raise BOEError(
            f"CELEX no soportado para resolución CELLAR automática: {celex}"
        )
    return celex[1:]


def _fecha_consolidacion_desde_url(url: str) -> str:
    m = re.search(r"%2F(\d{8})", url, flags=re.I)
    return m.group(1) if m else "00000000"


def _localizar_xhtml_cellar(celex: str) -> str:
    """Localiza de forma inequívoca la manifestación XHTML española.

    Se consulta el árbol oficial CELLAR del CELEX y se elige la versión
    consolidada española más reciente cuyo identificador documental coincide
    exactamente con el CELEX validado. No se seleccionan recursos relacionados.
    """
    token = _token_consolidacion_celex(celex)
    url_arbol = f"https://publications.europa.eu/resource/celex/{celex}"
    try:
        r = requests.get(
            url_arbol,
            timeout=TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/xml;notice=tree",
                "Accept-Language": "spa",
            },
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise BOEError(
            f"No se pudo obtener el árbol CELLAR del CELEX {celex}."
        ) from exc

    if not r.content:
        raise BOEError(f"CELLAR devolvió vacío el árbol del CELEX {celex}.")

    try:
        raiz = ET.fromstring(r.content)
    except ET.ParseError as exc:
        raise BOEError(
            f"CELLAR no devolvió XML válido para el CELEX {celex}."
        ) from exc

    urls: set[str] = set()
    for elem in raiz.iter():
        if elem.tag.split("}")[-1] != "VALUE":
            continue
        valor = limpiar(elem.text)
        if not valor:
            continue
        if not valor.lower().endswith(".spa.xhtml"):
            continue
        if "/resource/consolidation/" not in valor.lower():
            continue
        if token.lower() not in valor.lower():
            continue
        urls.add(valor)

    if not urls:
        raise BOEError(
            f"CELLAR no ofrece una manifestación XHTML española consolidada "
            f"inequívoca para el CELEX {celex}."
        )

    ordenadas = sorted(
        urls,
        key=lambda url: (_fecha_consolidacion_desde_url(url), url),
        reverse=True,
    )
    fecha_maxima = _fecha_consolidacion_desde_url(ordenadas[0])
    mejores = [u for u in ordenadas if _fecha_consolidacion_desde_url(u) == fecha_maxima]
    if len(mejores) != 1:
        raise BOEError(
            f"CELLAR devuelve varias manifestaciones XHTML españolas para la "
            f"misma versión de {celex}; no se selecciona arbitrariamente."
        )
    return mejores[0]


def _descargar_xhtml_cellar(celex: str) -> tuple[str, str]:
    url = _localizar_xhtml_cellar(celex)
    try:
        r = requests.get(
            url,
            timeout=TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/xhtml+xml,text/html",
                "Accept-Language": "es",
            },
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise BOEError(
            f"No se pudo descargar la manifestación XHTML de CELLAR: {url}"
        ) from exc

    tipo = (r.headers.get("content-type") or "").lower()
    if not r.content or ("html" not in tipo and "xhtml" not in tipo):
        raise BOEError(
            f"CELLAR no devolvió XHTML utilizable para el CELEX {celex}."
        )
    return r.url, r.text


def _es_tag_encabezado_cellar(tag: Tag) -> bool:
    if tag.name != "p":
        return False
    clases = tag.get("class") or []
    return "title-article-norm" in clases


def _extraer_articulo_cellar(html: str, solicitado: str) -> tuple[str, str]:
    """Extrae un artículo usando la estructura normativa explícita de CELLAR."""
    base = limpiar(solicitado).replace(",", ".").split(".", 1)[0]
    sopa = BeautifulSoup(html, "html.parser")
    encabezados = [
        tag for tag in sopa.find_all("p")
        if isinstance(tag, Tag) and _es_tag_encabezado_cellar(tag)
    ]

    objetivo: Tag | None = None
    coincidencias = 0
    for tag in encabezados:
        texto = limpiar(tag.get_text(" ", strip=True))
        m = _es_encabezado_articulo(texto)
        if m and limpiar(m.group(1)).lower() == base.lower():
            coincidencias += 1
            objetivo = tag

    if coincidencias != 1 or objetivo is None:
        raise BOEError(
            f"CELLAR no contiene un encabezado inequívoco para el artículo "
            f"{solicitado}."
        )

    titulo = f"Artículo {base}"
    siguiente_p = objetivo.find_next("p")
    if isinstance(siguiente_p, Tag):
        clases = siguiente_p.get("class") or []
        if "stitle-article-norm" in clases:
            subtitulo = limpiar(siguiente_p.get_text(" ", strip=True))
            if subtitulo:
                titulo = f"Artículo {base}. {subtitulo}"

    lineas: list[str] = [limpiar(objetivo.get_text(" ", strip=True))]
    for nodo in objetivo.next_elements:
        if nodo is objetivo:
            continue
        if isinstance(nodo, Tag) and _es_tag_encabezado_cellar(nodo):
            break
        if not isinstance(nodo, NavigableString):
            continue
        padre = nodo.parent
        if isinstance(padre, Tag) and padre.name in {"script", "style", "noscript"}:
            continue
        texto = limpiar(str(nodo))
        if texto:
            lineas.append(texto)

    texto = "\n".join(lineas).strip()
    if len(texto) < 80:
        raise BOEError(
            f"CELLAR recuperó texto insuficiente para el artículo {solicitado}."
        )
    return titulo, texto


def _obtener_doue(nombre: str, articulo: str, fuente: FuenteNormativa) -> ArticuloBOE:
    celex = limpiar(fuente.id_fuente).upper().removeprefix("DOUE-CELEX-")
    if not celex:
        raise BOEError(
            f"La fuente DOUE no contiene un CELEX válido: {fuente.id_fuente}"
        )

    _, html = _descargar_xhtml_cellar(celex)
    titulo, texto = _extraer_articulo_cellar(html, articulo)
    base = limpiar(articulo).replace(",", ".").split(".", 1)[0]
    return ArticuloBOE(
        nombre_norma=nombre,
        id_boe=fuente.id_fuente,
        departamento="Unión Europea",
        articulo=limpiar(articulo).replace(",", "."),
        id_bloque=f"cellar-{celex.lower()}-art-{base.lower()}",
        titulo_bloque=titulo,
        texto=texto,
    )


def obtener_articulo_desde_fuente(nombre: str, articulo: str) -> ArticuloBOE:
    """Localiza la fuente una sola vez y obtiene el artículo desde ella."""
    try:
        fuente = localizar_fuente(nombre)
    except (LocalizadorFuenteError, ValueError) as exc:
        raise BOEError(str(exc)) from exc

    proveedor = fuente.proveedor.upper()
    if proveedor == "PDF_LOCAL":
        resultado = obtener_articulo_pdf(nombre, articulo)
        if limpiar(resultado.id_boe).upper() != limpiar(fuente.id_fuente).upper():
            raise BOEError(
                "La extracción PDF cambió de identidad documental: "
                f"esperada={fuente.id_fuente}, obtenida={resultado.id_boe}."
            )
        return resultado
    if proveedor == "BOE":
        return _obtener_boe(nombre, articulo, fuente)
    if proveedor == "DOUE":
        return _obtener_doue(nombre, articulo, fuente)

    raise BOEError(
        f"Proveedor no soportado para resolución de artículo: {fuente.proveedor}"
    )


def id_fuente_validada(nombre: str) -> str:
    """Devuelve la identidad documental decidida por el localizador común."""
    try:
        return localizar_fuente(nombre).id_fuente
    except (LocalizadorFuenteError, ValueError) as exc:
        raise BOEError(str(exc)) from exc
