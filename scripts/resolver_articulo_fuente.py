"""Resolución de un artículo a partir de una fuente normativa ya validada.

Este módulo separa dos responsabilidades:
- localizador_fuentes.localizar_fuente() decide QUÉ documento es la norma;
- este módulo obtiene el artículo desde ESE documento y comprueba que la
  identidad documental no cambia durante la extracción.

No escribe en SQLite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from boe_api import ArticuloBOE, BOEError, obtener_articulo
from localizador_fuentes import (
    FuenteNormativa,
    LocalizadorFuenteError,
    es_valenciana,
    extraer_fecha,
    extraer_identidad,
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


def _nombre_boe_canonico(nombre: str) -> str:
    """Construye una cita BOE sin referencias normativas secundarias.

    La fuente ya ha sido validada por localizador_fuentes. Esta cita se usa
    únicamente para que boe_api descargue el artículo sin volver a confundirse
    con otra norma citada más tarde en el título oficial.
    """
    identidad = extraer_identidad(nombre)
    if identidad is None:
        return nombre

    tipo, numero, anio = identidad
    tipo_visible = tipo.replace("real decreto ley", "real decreto-ley").replace(
        "decreto ley", "decreto-ley"
    )
    partes = [f"{tipo_visible} {numero}/{anio}"]
    fecha = extraer_fecha(nombre)
    if fecha:
        y, m, d = fecha.split("-")
        meses = {
            "01": "enero", "02": "febrero", "03": "marzo", "04": "abril",
            "05": "mayo", "06": "junio", "07": "julio", "08": "agosto",
            "09": "septiembre", "10": "octubre", "11": "noviembre", "12": "diciembre",
        }
        partes.append(f"de {int(d)} de {meses[m]} de {y}")
    if es_valenciana(nombre):
        partes.append("Comunitat Valenciana")
    return ", ".join(partes)


def _obtener_boe(nombre: str, articulo: str, fuente: FuenteNormativa) -> ArticuloBOE:
    consulta = _nombre_boe_canonico(nombre)
    resultado = obtener_articulo(consulta, articulo)
    if limpiar(resultado.id_boe).upper() != limpiar(fuente.id_fuente).upper():
        raise BOEError(
            "La extracción BOE cambió de identidad documental: "
            f"esperada={fuente.id_fuente}, obtenida={resultado.id_boe}."
        )
    return ArticuloBOE(
        nombre_norma=nombre,
        id_boe=resultado.id_boe,
        departamento=resultado.departamento,
        articulo=resultado.articulo,
        id_bloque=resultado.id_bloque,
        titulo_bloque=resultado.titulo_bloque,
        texto=resultado.texto,
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
        # En EUR-Lex la línea posterior suele ser la rúbrica. Solo se incorpora
        # como título cuando no parece ya cuerpo numerado del artículo.
        siguiente = bloque[1]
        if not re.match(r"^\d+[.)]?\s", siguiente):
            titulo = f"Artículo {base}. {siguiente}"
        texto = "\n".join(bloque).strip()
        candidatos.append((len(texto), titulo, texto))

    if not candidatos:
        raise BOEError(f"EUR-Lex no contiene un bloque inequívoco para el artículo {solicitado}.")

    # El índice puede repetir encabezados sin cuerpo. El artículo normativo es
    # el candidato con mayor cuerpo textual.
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
