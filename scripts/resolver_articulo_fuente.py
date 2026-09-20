"""Resolución de un artículo a partir de una fuente normativa ya validada.

Este módulo separa dos responsabilidades:
- localizador_fuentes.localizar_fuente() decide QUÉ documento es la norma;
- este módulo obtiene el artículo desde ESE documento y comprueba que la
  identidad documental no cambia durante la extracción.

No escribe en SQLite.
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

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
    "AppleWebKit/537.36 Chrome/120 Safari/537.36 TuCoach/1.0"
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


def _obtener_doue(nombre: str, articulo: str, fuente: FuenteNormativa) -> ArticuloBOE:
    try:
        r = requests.get(
            fuente.url_oficial,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es"},
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise BOEError(f"No se pudo descargar EUR-Lex: {fuente.url_oficial}") from exc

    celex = fuente.id_fuente.upper().removeprefix("DOUE-CELEX-")
    huella = f"{r.url}\n{r.text}".upper()
    if celex and celex not in huella:
        raise BOEError(f"EUR-Lex no confirmó el CELEX esperado {celex}.")

    titulo, texto = _extraer_articulo_eurlex(r.text, articulo)
    base = limpiar(articulo).replace(",", ".").split(".", 1)[0]
    return ArticuloBOE(
        nombre_norma=nombre,
        id_boe=fuente.id_fuente,
        departamento="Unión Europea",
        articulo=limpiar(articulo).replace(",", "."),
        id_bloque=f"eurlex-{celex.lower()}-art-{base.lower()}",
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
