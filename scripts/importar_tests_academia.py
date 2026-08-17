"""
===============================================================================
Proyecto : OpoCoach
Tipo     : Importación de tests de academia desde PDF estructurado
Archivo  : importar_tests_academia.py
Ubicación:
    scripts/importar_tests_academia.py

OBJETIVO
--------
Importar en ``lote_preguntas`` los PDF depositados en ``data_academia``.

El formato esperado es el de los PDF de Auténtica Oposiciones:

* preguntas numeradas con cuatro opciones a), b), c) y d);
* la respuesta correcta está marcada con un cuadrado oscuro;
* cada pregunta termina con una línea de metadatos:

      artículo ||| materia ||| norma ||| nivel: N

La norma y el artículo se extraen literalmente de esa línea. El script no
los deduce ni llama a la IA.

EJECUCIÓN
---------
Procesar todos los PDF:

    python scripts/importar_tests_academia.py

Procesar uno concreto:

    python scripts/importar_tests_academia.py --pdf A2_01.pdf

Forzar su reimportación:

    python scripts/importar_tests_academia.py --pdf A2_01.pdf --forzar

TRAZABILIDAD
------------
Se utiliza ``importaciones_ficheros`` igual que en
``importar_tests_imagen.py``. Cada pregunta conserva
``importacion_fichero_id`` y ``pagina_origen``. Un PDF con el mismo hash y
estado ``COMPLETADO`` se omite, salvo que se use ``--forzar`` o tenga
``reimportar = 1``. La publicación es una única transacción. Las preguntas válidas se publican
aunque existan errores parciales de extracción; dichos errores quedan anotados
en la trazabilidad y en el log. Un error general conserva la importación anterior.

DEPENDENCIA
-----------
    pip install pymupdf
===============================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf as fitz
from importacion_preguntas_comun import (
    buscar_importacion_fichero,
    debe_omitirse,
    publicar_importacion,
    registrar_importacion_fallida,
)


RUTA_SCRIPT = Path(__file__).resolve()
RAIZ = RUTA_SCRIPT.parent.parent

CARPETA_PDF = RAIZ / "data_academia"
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
RUTA_LOG = RAIZ / "logs" / "importar_tests_academia.log"

TIPO_FUENTE = "tests"
VERSION_SCRIPT = "2026-08-06-v2-errores-parciales"

RE_PREGUNTA = re.compile(r"^\s*(\d+)\.\s+(.+)$", re.DOTALL)
RE_OPCION = re.compile(r"^\s*([a-dA-D])\)\s*(.+)$", re.DOTALL)
RE_PIE = re.compile(
    r"^\s*(.*?)\s*\|\|\|\s*(.*?)\s*\|\|\|\s*(.*?)\s*"
    r"\|\|\|\s*nivel\s*:\s*(\d*)\s*$",
    re.IGNORECASE | re.DOTALL,
)
RE_ORIGEN = re.compile(r"^(A1|A2|C1|C2)(?:\b|[_\-])", re.IGNORECASE)

COLUMNAS_LOTE_ESPERADAS = {
    "id",
    "enunciado",
    "opcion_a",
    "opcion_b",
    "opcion_c",
    "opcion_d",
    "respuesta_correcta",
    "tipo_clasificacion",
    "tipo_norma",
    "nombre_norma",
    "articulo",
    "tema_no_juridico",
    "origen_oposicion",
    "tipo_fuente",
    "importacion_fichero_id",
    "pagina_origen",
}

COLUMNAS_IMPORTACION_ESPERADAS = {
    "id",
    "ruta_relativa",
    "nombre_fichero",
    "hash_sha256",
    "tipo_fuente",
    "estado",
    "paginas_totales",
    "paginas_insertadas",
    "paginas_omitidas",
    "paginas_error",
    "fecha_inicio",
    "fecha_fin",
    "reimportar",
    "ultimo_error",
}


@dataclass(frozen=True)
class Bloque:
    pagina: int
    x0: float
    y0: float
    x1: float
    y1: float
    texto: str


@dataclass
class PreguntaExtraida:
    numero: int
    pagina_origen: int
    enunciado: str
    opciones: dict[str, str]
    respuesta_correcta: str | None
    articulo: str | None = None
    materia: str | None = None
    norma: str | None = None
    nivel: int | None = None


def leer_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Importa un PDF concreto o todos los PDF de data_academia."
        )
    )
    parser.add_argument(
        "--pdf",
        help=(
            "Nombre o ruta del único PDF que se desea procesar. "
            "Si se omite, se procesa toda la carpeta data_academia."
        ),
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help=(
            "Reimporta el PDF aunque ya figure como procesado. "
            "Solo puede usarse junto con --pdf."
        ),
    )
    args = parser.parse_args()
    if args.forzar and not args.pdf:
        parser.error("--forzar requiere indicar también --pdf.")
    return args


def configurar_logging() -> None:
    RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(RUTA_LOG, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def limpiar_texto(valor: object) -> str:
    texto = "" if valor is None else str(valor)
    texto = texto.replace("\u00ad", "")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def columnas_tabla(
    conexion: sqlite3.Connection,
    tabla: str,
) -> set[str]:
    return {
        str(fila[1])
        for fila in conexion.execute(f'PRAGMA table_info("{tabla}")')
    }


def validar_base_datos(conexion: sqlite3.Connection) -> None:
    tablas = {
        str(fila[0])
        for fila in conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for tabla in ("lote_preguntas", "importaciones_ficheros"):
        if tabla not in tablas:
            raise RuntimeError(f"No existe la tabla obligatoria {tabla}.")

    faltan_lote = COLUMNAS_LOTE_ESPERADAS - columnas_tabla(
        conexion, "lote_preguntas"
    )
    if faltan_lote:
        raise RuntimeError(
            "Faltan columnas en lote_preguntas: "
            + ", ".join(sorted(faltan_lote))
        )

    faltan_importacion = COLUMNAS_IMPORTACION_ESPERADAS - columnas_tabla(
        conexion, "importaciones_ficheros"
    )
    if faltan_importacion:
        raise RuntimeError(
            "Faltan columnas en importaciones_ficheros: "
            + ", ".join(sorted(faltan_importacion))
        )


def resolver_pdf_indicado(valor: str) -> Path:
    candidato = Path(valor).expanduser()
    candidatos = []
    if candidato.is_absolute():
        candidatos.append(candidato)
    else:
        candidatos.extend(
            [
                (Path.cwd() / candidato).resolve(),
                (RAIZ / candidato).resolve(),
                (CARPETA_PDF / candidato).resolve(),
            ]
        )

    for ruta in candidatos:
        if ruta.is_file() and ruta.suffix.lower() == ".pdf":
            return ruta

    raise FileNotFoundError(f"No se encuentra el PDF indicado: {valor}")


def obtener_pdfs(pdf_indicado: str | None) -> list[Path]:
    if pdf_indicado:
        return [resolver_pdf_indicado(pdf_indicado)]

    if not CARPETA_PDF.is_dir():
        raise FileNotFoundError(
            f"No existe la carpeta de entrada: {CARPETA_PDF}"
        )

    rutas = sorted(
        (ruta for ruta in CARPETA_PDF.iterdir() if ruta.suffix.lower() == ".pdf"),
        key=lambda ruta: ruta.name.casefold(),
    )
    if not rutas:
        logging.info("SIN PDF PENDIENTES | carpeta=%s", CARPETA_PDF)
    return rutas


def obtener_origen(nombre_pdf: str) -> str:
    coincidencia = RE_ORIGEN.match(Path(nombre_pdf).stem)
    if coincidencia is None:
        raise ValueError(
            "El nombre del PDF debe comenzar por A1, A2, C1 o C2: "
            f"{nombre_pdf}"
        )
    return coincidencia.group(1).upper()


def calcular_sha256(ruta: Path) -> str:
    resumen = hashlib.sha256()
    with ruta.open("rb") as fichero:
        for bloque in iter(lambda: fichero.read(1024 * 1024), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def bloques_texto(pagina: fitz.Page, numero_pagina: int) -> list[Bloque]:
    bloques: list[Bloque] = []
    for bruto in pagina.get_text("blocks", sort=True):
        x0, y0, x1, y1, texto = bruto[:5]
        texto_limpio = limpiar_texto(texto)
        if not texto_limpio:
            continue
        bloques.append(
            Bloque(
                pagina=numero_pagina,
                x0=float(x0),
                y0=float(y0),
                x1=float(x1),
                y1=float(y1),
                texto=texto_limpio,
            )
        )
    return bloques


def centros_cuadrados_correctos(pagina: fitz.Page) -> list[float]:
    centros: list[float] = []
    for dibujo in pagina.get_drawings():
        rectangulo = dibujo.get("rect")
        relleno = dibujo.get("fill")
        if rectangulo is None or relleno is None:
            continue
        if not (9.0 <= rectangulo.width <= 15.0):
            continue
        if not (9.0 <= rectangulo.height <= 15.0):
            continue
        if not (50.0 <= rectangulo.x0 <= 72.0):
            continue
        luminosidad = sum(float(componente) for componente in relleno) / len(
            relleno
        )
        if luminosidad < 0.45:
            centros.append((rectangulo.y0 + rectangulo.y1) / 2.0)
    return centros


def bloque_es_respuesta_correcta(
    bloque: Bloque,
    centros_correctos: list[float],
) -> bool:
    margen = 3.0
    return any(
        bloque.y0 - margen <= centro <= bloque.y1 + margen
        for centro in centros_correctos
    )


def validar_pregunta(pregunta: PreguntaExtraida) -> None:
    faltan = [letra for letra in "abcd" if not pregunta.opciones.get(letra)]
    if faltan:
        raise ValueError(
            f"Pregunta {pregunta.numero}: faltan opciones {', '.join(faltan)}."
        )
    if pregunta.respuesta_correcta not in {"a", "b", "c", "d"}:
        raise ValueError(
            f"Pregunta {pregunta.numero}: no hay una única respuesta correcta."
        )
    if not pregunta.norma:
        raise ValueError(f"Pregunta {pregunta.numero}: falta la norma del pie.")
    if pregunta.articulo is None or pregunta.articulo == "":
        raise ValueError(f"Pregunta {pregunta.numero}: falta el artículo del pie.")
    if not pregunta.materia:
        raise ValueError(f"Pregunta {pregunta.numero}: falta la materia del pie.")


def extraer_preguntas(documento: fitz.Document) -> tuple[list[PreguntaExtraida], list[str]]:
    preguntas: list[PreguntaExtraida] = []
    errores: list[str] = []
    actual: PreguntaExtraida | None = None

    for indice in range(documento.page_count):
        pagina = documento.load_page(indice)
        numero_pagina = indice + 1
        correctas_y = centros_cuadrados_correctos(pagina)

        for bloque in bloques_texto(pagina, numero_pagina):
            pie = RE_PIE.match(bloque.texto)
            if pie is not None and actual is not None:
                actual.articulo = limpiar_texto(pie.group(1))
                actual.materia = limpiar_texto(pie.group(2))
                actual.norma = limpiar_texto(pie.group(3))
                nivel_literal = limpiar_texto(pie.group(4))
                actual.nivel = int(nivel_literal) if nivel_literal else None
                try:
                    validar_pregunta(actual)
                    preguntas.append(actual)
                except Exception as exc:
                    errores.append(str(exc))
                actual = None
                continue

            inicio = RE_PREGUNTA.match(bloque.texto)
            if inicio is not None:
                if actual is not None:
                    errores.append(
                        f"Pregunta {actual.numero}: apareció la pregunta "
                        f"{inicio.group(1)} antes de su pie de metadatos."
                    )
                actual = PreguntaExtraida(
                    numero=int(inicio.group(1)),
                    pagina_origen=numero_pagina,
                    enunciado=limpiar_texto(inicio.group(2)),
                    opciones={},
                    respuesta_correcta=None,
                )
                continue

            opcion = RE_OPCION.match(bloque.texto)
            if opcion is not None and actual is not None:
                letra = opcion.group(1).lower()
                if letra in actual.opciones:
                    errores.append(
                        f"Pregunta {actual.numero}: opción {letra}) repetida."
                    )
                    continue
                actual.opciones[letra] = limpiar_texto(opcion.group(2))
                if bloque_es_respuesta_correcta(bloque, correctas_y):
                    if actual.respuesta_correcta is not None:
                        errores.append(
                            f"Pregunta {actual.numero}: más de una respuesta marcada."
                        )
                        actual.respuesta_correcta = None
                    else:
                        actual.respuesta_correcta = letra

    if actual is not None:
        errores.append(
            f"Pregunta {actual.numero}: fin del PDF antes del pie de metadatos."
        )

    numeros = [pregunta.numero for pregunta in preguntas]
    repetidos = sorted(
        numero for numero in set(numeros) if numeros.count(numero) > 1
    )
    if repetidos:
        errores.append(
            "Números de pregunta repetidos: "
            + ", ".join(map(str, repetidos))
        )

    return preguntas, errores


def inferir_tipo_norma(nombre_norma: str) -> str | None:
    nombre = limpiar_texto(nombre_norma).casefold()
    reglas = (
        ("constitución", "CONSTITUCION"),
        ("ley orgánica", "LEY ORGANICA"),
        ("l.o.", "LEY ORGANICA"),
        ("real decreto legislativo", "REAL DECRETO LEGISLATIVO"),
        ("real decreto-ley", "REAL DECRETO-LEY"),
        ("real decreto", "REAL DECRETO"),
        ("decreto legislativo", "DECRETO LEGISLATIVO"),
        ("decreto-ley", "DECRETO-LEY"),
        ("decreto", "DECRETO"),
        ("tratado", "TRATADO"),
        ("reglamento", "REGLAMENTO"),
        ("ley", "LEY"),
    )
    for comienzo, tipo in reglas:
        if nombre.startswith(comienzo):
            return tipo
    return None


def preparar_registro(
    pregunta: PreguntaExtraida,
    origen: str,
    importacion_id: int,
) -> dict[str, Any]:
    materia = limpiar_texto(pregunta.materia)
    norma = limpiar_texto(pregunta.norma)
    articulo = limpiar_texto(pregunta.articulo)
    es_informatica = "informática" in materia.casefold()

    if es_informatica:
        tipo_clasificacion = "INFORMATICA"
        tipo_norma = None
        nombre_norma = None
        articulo_db = None
        tema_no_juridico = materia
    else:
        tipo_clasificacion = "JURIDICA"
        tipo_norma = inferir_tipo_norma(norma)
        nombre_norma = norma
        articulo_db = articulo
        tema_no_juridico = None

    return {
        "enunciado": limpiar_texto(pregunta.enunciado),
        "opcion_a": limpiar_texto(pregunta.opciones["a"]),
        "opcion_b": limpiar_texto(pregunta.opciones["b"]),
        "opcion_c": limpiar_texto(pregunta.opciones["c"]),
        "opcion_d": limpiar_texto(pregunta.opciones["d"]),
        "respuesta_correcta": str(pregunta.respuesta_correcta).upper(),
        "tipo_clasificacion": tipo_clasificacion,
        "tipo_norma": tipo_norma,
        "nombre_norma": nombre_norma,
        "articulo": articulo_db,
        "tema_no_juridico": tema_no_juridico,
        "origen_oposicion": origen,
        "tipo_fuente": TIPO_FUENTE,
        "importacion_fichero_id": importacion_id,
        "pagina_origen": pregunta.pagina_origen,
    }


def procesar_pdf(
    ruta_pdf: Path,
    conexion: sqlite3.Connection,
    forzar: bool,
) -> tuple[str, dict[str, int]]:
    hash_sha256 = calcular_sha256(ruta_pdf)
    importacion = buscar_importacion_fichero(
        conexion,
        hash_sha256=hash_sha256,
        tipo_fuente=TIPO_FUENTE,
        ruta_pdf=ruta_pdf,
        raiz=RAIZ,
    )
    totales = {
        "paginas": 0,
        "insertadas": 0,
        "duplicadas": 0,
        "omitidas": 0,
        "errores": 0,
    }

    if debe_omitirse(importacion, forzar, hash_sha256):
        logging.info(
            "FICHERO OMITIDO | %s | importacion_id=%s | estado=%s",
            ruta_pdf.name,
            importacion["id"],
            importacion["estado"],
        )
        return "SALTADO", totales

    documento = fitz.open(ruta_pdf)
    try:
        totales["paginas"] = documento.page_count
        origen = obtener_origen(ruta_pdf.name)
        preguntas, errores_extraccion = extraer_preguntas(documento)
        for error in errores_extraccion:
            totales["errores"] += 1
            logging.error("%s | ERROR DE EXTRACCIÓN | %s", ruta_pdf.name, error)

        registros: list[dict[str, Any]] = []
        for pregunta in preguntas:
            try:
                registros.append(preparar_registro(pregunta, origen, 0))
            except Exception as exc:
                totales["errores"] += 1
                totales["omitidas"] += 1
                logging.exception(
                    "%s | pregunta %d | ERROR | %s",
                    ruta_pdf.name,
                    pregunta.numero,
                    exc,
                )

        if not registros:
            raise RuntimeError(
                "El PDF no contiene ninguna pregunta válida para publicar."
            )

        importacion_id, insertadas, duplicadas = publicar_importacion(
            conexion,
            raiz=RAIZ,
            ruta_pdf=ruta_pdf,
            hash_sha256=hash_sha256,
            tipo_importacion=TIPO_FUENTE,
            paginas_totales=documento.page_count,
            registros=registros,
            omitidas_previas=totales["omitidas"],
        )
        totales["insertadas"] = insertadas
        totales["duplicadas"] = duplicadas
        totales["omitidas"] += duplicadas

        if totales["errores"]:
            conexion.execute(
                """
                UPDATE importaciones_ficheros
                SET paginas_error = ?,
                    ultimo_error = ?
                WHERE id = ?
                """,
                (
                    totales["errores"],
                    (
                        "Importación completada con "
                        f"{totales['errores']} errores parciales de extracción"
                    ),
                    importacion_id,
                ),
            )
            conexion.commit()

        logging.info(
            "FIN PDF | %s | preguntas=%d | insertadas=%d | "
            "duplicadas=%d | omitidas=%d | errores=%d",
            ruta_pdf.name,
            len(preguntas),
            totales["insertadas"],
            totales["duplicadas"],
            totales["omitidas"],
            totales["errores"],
        )
        return "PROCESADO", totales

    except Exception as exc:
        registrar_importacion_fallida(
            conexion,
            raiz=RAIZ,
            ruta_pdf=ruta_pdf,
            hash_sha256=hash_sha256,
            tipo_importacion=TIPO_FUENTE,
            paginas_totales=totales["paginas"] or None,
            errores=totales["errores"] or 1,
            mensaje=str(exc),
        )
        logging.exception("IMPORTACIÓN RECHAZADA | %s | %s", ruta_pdf, exc)
        return "ERROR", totales
    finally:
        documento.close()


def main() -> int:
    configurar_logging()
    logging.info("VERSIÓN SCRIPT | %s | archivo=%s", VERSION_SCRIPT, RUTA_SCRIPT)
    args = leer_argumentos()

    if not RUTA_DB.is_file():
        logging.error("No existe la base de datos: %s", RUTA_DB)
        return 1

    try:
        rutas_pdf = obtener_pdfs(args.pdf)
    except Exception as exc:
        logging.exception("Error de configuración: %s", exc)
        return 1

    globales = {
        "pdf_encontrados": len(rutas_pdf),
        "pdf_procesados": 0,
        "pdf_saltados": 0,
        "pdf_error": 0,
        "paginas": 0,
        "insertadas": 0,
        "duplicadas": 0,
        "omitidas": 0,
        "errores": 0,
    }

    try:
        with sqlite3.connect(RUTA_DB) as conexion:
            conexion.row_factory = sqlite3.Row
            validar_base_datos(conexion)

            for ruta_pdf in rutas_pdf:
                try:
                    resultado, totales = procesar_pdf(
                        ruta_pdf, conexion, args.forzar
                    )
                    if resultado == "SALTADO":
                        globales["pdf_saltados"] += 1
                    elif resultado == "ERROR":
                        globales["pdf_error"] += 1
                    else:
                        globales["pdf_procesados"] += 1
                    for clave in (
                        "paginas",
                        "insertadas",
                        "duplicadas",
                        "omitidas",
                        "errores",
                    ):
                        globales[clave] += totales[clave]
                except Exception as exc:
                    globales["pdf_error"] += 1
                    logging.exception(
                        "ERROR DE FICHERO | %s | %s", ruta_pdf, exc
                    )
    except Exception as exc:
        logging.exception("Error general: %s", exc)
        return 1

    modo = "PDF CONCRETO" if args.pdf else "CARPETA COMPLETA"
    print()
    print("=" * 76)
    print("RESUMEN IMPORTACIÓN TESTS DE ACADEMIA")
    print("=" * 76)
    print(f"Modo:                   {modo}")
    print(f"PDF encontrados:        {globales['pdf_encontrados']}")
    print(f"PDF procesados:         {globales['pdf_procesados']}")
    print(f"PDF ya importados:      {globales['pdf_saltados']}")
    print(f"PDF con error general:  {globales['pdf_error']}")
    print(f"Páginas procesadas:     {globales['paginas']}")
    print(f"Preguntas insertadas:   {globales['insertadas']}")
    print(f"Preguntas duplicadas:   {globales['duplicadas']}")
    print(f"Preguntas omitidas:     {globales['omitidas']}")
    print(f"Errores de extracción:  {globales['errores']}")
    print(f"Log:                    {RUTA_LOG}")
    print("=" * 76)

    return 1 if globales["pdf_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
