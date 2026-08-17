"""
Importa los PDF de data_examenes/modelo y data_examenes/apoyo
en la tabla existente lote_preguntas.

Los PDF sin capa de texto, o con un formato que no pueda extraerse,
se anotan en logs/importar_examenes.log y se pasa al siguiente archivo.

Dependencia:
    pip install pymupdf
"""

from pathlib import Path
from bisect import bisect_right
import logging
import re
import sqlite3

import pymupdf as fitz
from importacion_preguntas_comun import (
    buscar_importacion_fichero,
    calcular_sha256,
    debe_omitirse,
    publicar_importacion,
    registrar_importacion_fallida,
)


RAIZ = Path(__file__).resolve().parent.parent
CARPETA_EXAMENES = RAIZ / "data_examenes"
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
RUTA_LOG = RAIZ / "logs" / "importar_examenes.log"

# Exclusión acordada: el cuestionario carece de capa de texto y no se desea
# procesarlo mediante OCR ni IA visual.
PDF_EXCLUIDOS = {
    "examen_c2-01_69_18.pdf",
}


PATRON_ARCHIVO = re.compile(
    r"^Examen_(A1|A2|C1|C2)(?:-\d{2})?_\d+_\d{2,4}(?:_P)?\.pdf$",
    re.IGNORECASE,
)

PATRON_OPCIONES = re.compile(
    r"(?ms)^(.*?)"
    r"^\s*A\s*[\)\.\-:]\s*(.*?)"
    r"^\s*B\s*[\)\.\-:]\s*(.*?)"
    r"^\s*C\s*[\)\.\-:]\s*(.*?)"
    r"^\s*D\s*[\)\.\-:]\s*(.*)$"
)

PATRON_MARCADOR_OPCION = re.compile(
    r"(?m)^[ \t]*([ABCD])[ \t]*([\)\.\-:])[ \t]*"
)

PATRON_ARTICULO = re.compile(
    r"\bart(?:í|i)culo(?:s)?\s+(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)

PATRONES_NORMA = [
    re.compile(r"\bLey\s+Orgánica\s+\d+/\d{4}", re.IGNORECASE),
    re.compile(r"\bLey\s+\d+/\d{4}", re.IGNORECASE),
    re.compile(r"\bReal\s+Decreto(?:-ley|\s+Legislativo)?\s+\d+/\d{4}", re.IGNORECASE),
    re.compile(r"\bDecreto(?:-ley|\s+Legislativo)?\s+\d+/\d{4}", re.IGNORECASE),
    re.compile(r"\bConstitución\s+Española\b", re.IGNORECASE),
]


def configurar_log() -> None:
    RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(RUTA_LOG, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def limpiar(texto: str) -> str:
    texto = texto.replace("\u00ad", "")
    texto = re.sub(r"-\s*\n\s*(?=\w)", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def es_examen_practico(ruta: Path) -> bool:
    """
    Un examen cuyo nombre termina en _P.pdf se considera práctico.
    Ejemplo: Examen_A1-01_1_25_P.pdf
    """
    return ruta.stem.upper().endswith("_P")


def asegurar_columna_teorica_practica(
    conexion: sqlite3.Connection,
) -> None:
    columnas = {
        fila[1]
        for fila in conexion.execute("PRAGMA table_info(lote_preguntas)")
    }

    if "teorica_practica" not in columnas:
        conexion.execute(
            "ALTER TABLE lote_preguntas "
            "ADD COLUMN teorica_practica TEXT"
        )


def leer_pdf(ruta: Path) -> list[str]:
    with fitz.open(ruta) as pdf:
        paginas = [pagina.get_text("text") for pagina in pdf]

    texto_total = "".join(paginas).strip()

    if len(texto_total) < 100:
        raise ValueError("PDF sin capa de texto suficiente; necesita OCR")

    return paginas


def extraer_respuestas(primera_pagina: str) -> dict[int, str]:
    respuestas: dict[int, str] = {}

    for numero, letra in re.findall(
        r"(?im)(?:^|\s)(\d{1,3})\s*[.\-:)]?\s*([ABCD])(?=\s|$)",
        primera_pagina,
    ):
        respuestas[int(numero)] = letra.upper()

    if not respuestas:
        raise ValueError("No se ha podido leer la tabla de soluciones")

    return respuestas


def extraer_preguntas(
    paginas: list[str],
    respuestas: dict[int, str],
) -> tuple[list[tuple], int]:
    paginas_preguntas = paginas[1:]
    inicios: list[int] = []
    partes: list[str] = []
    posicion = 0
    for pagina in paginas_preguntas:
        inicios.append(posicion)
        partes.append(pagina)
        posicion += len(pagina) + 1
    texto = "\n".join(partes)

    marcadores: list[tuple[int, re.Match[str]]] = []
    omitidas = 0
    buscar_desde = 0
    for numero in sorted(respuestas):
        numero_extraido = "(?:46|A6)" if numero == 46 else str(numero)
        patron = re.compile(
            rf"(?m)^[ \t]*{numero_extraido}(?:"
            rf"[ \t]*\.[ \t]*[-–—]?|"
            rf"[ \t]*[-–—),][ \t]*|"
            rf"[ \t]+(?=[¿¡A-ZÁÉÍÓÚ])"
            rf")"
        )
        coincidencia = patron.search(texto, buscar_desde)
        if coincidencia is None:
            omitidas += 1
            logging.warning(
                "Pregunta %s rechazada: no se ha localizado su inicio en el texto",
                numero,
            )
            continue
        marcadores.append((numero, coincidencia))
        buscar_desde = coincidencia.end()

    resultado: list[tuple] = []
    for indice, (numero, coincidencia) in enumerate(marcadores):
        final = (
            marcadores[indice + 1][1].start()
            if indice + 1 < len(marcadores)
            else len(texto)
        )
        contenido = texto[coincidencia.end():final].strip()
        contenido = corregir_rotulos_ocr(contenido, numero)
        contenido = corregir_rotulo_cuarta_opcion(contenido, numero)
        opciones = PATRON_OPCIONES.match(contenido)
        pagina_origen = bisect_right(inicios, coincidencia.start()) + 1

        if opciones is None:
            omitidas += 1
            logging.warning(
                "Pregunta %s (página %s) rechazada: no se han extraído "
                "íntegramente las opciones A, B, C y D; posible contenido "
                "gráfico o tabular",
                numero,
                pagina_origen,
            )
            continue

        respuesta = respuestas.get(numero)

        if respuesta not in {"A", "B", "C", "D"}:
            omitidas += 1
            logging.warning(
                "Pregunta %s (página %s) rechazada: respuesta no válida",
                numero,
                pagina_origen,
            )
            continue

        enunciado = limpiar(opciones.group(1))
        opcion_a = limpiar(opciones.group(2))
        opcion_b = limpiar(opciones.group(3))
        opcion_c = limpiar(opciones.group(4))
        opcion_d = limpiar(opciones.group(5))

        if not enunciado:
            omitidas += 1
            logging.warning(
                "Pregunta %s (página %s) rechazada: enunciado vacío",
                numero,
                pagina_origen,
            )
            continue

        opciones_limpias = (opcion_a, opcion_b, opcion_c, opcion_d)
        vacias = [
            letra
            for letra, texto_opcion in zip("ABCD", opciones_limpias)
            if not texto_opcion
        ]
        if vacias:
            omitidas += 1
            logging.warning(
                "Pregunta %s (página %s) rechazada: opciones incompletas "
                "(%s); posible contenido gráfico o tabular",
                numero,
                pagina_origen,
                ", ".join(vacias),
            )
            continue

        resultado.append(
            (
                enunciado,
                opcion_a,
                opcion_b,
                opcion_c,
                opcion_d,
                respuesta,
                pagina_origen,
            )
        )

    return resultado, omitidas


def corregir_rotulos_ocr(contenido: str, numero: int) -> str:
    """Corrige únicamente confusiones de OCR observadas en los PDF revisados."""
    sustituciones = (
        (r"(?m)^([ \t]*)€([ \t]*\))", r"\1C\2", "€", "C"),
        (r"(?m)^([ \t]*)8([ \t]*\))", r"\1B\2", "8", "B"),
        (r"(?m)^([ \t]*)A([ \t]*\})", r"\1A)", "A}", "A)"),
    )

    for patron, reemplazo, original, corregido in sustituciones:
        contenido_nuevo, cantidad = re.subn(patron, reemplazo, contenido)
        if cantidad:
            logging.warning(
                "Pregunta %s: rótulo OCR %s interpretado como %s",
                numero,
                original,
                corregido,
            )
            contenido = contenido_nuevo

    return contenido


def corregir_rotulo_cuarta_opcion(contenido: str, numero: int) -> str:
    """
    Admite la errata material A/B/C/C observada en un examen oficial.

    Solo se corrige el rótulo de la cuarta opción para poder separarla. El
    texto de las cuatro opciones y la respuesta de la plantilla no cambian.
    """
    marcadores = list(PATRON_MARCADOR_OPCION.finditer(contenido))
    etiquetas = [coincidencia.group(1).upper() for coincidencia in marcadores]

    if etiquetas != ["A", "B", "C", "C"]:
        return contenido

    cuarta = marcadores[3]
    logging.warning(
        "Pregunta %s con rótulos A/B/C/C; la cuarta opción se interpreta "
        "por posición como D",
        numero,
    )
    return contenido[:cuarta.start(1)] + "D" + contenido[cuarta.end(1):]


def detectar_norma(texto: str) -> tuple[str | None, str | None]:
    for patron in PATRONES_NORMA:
        coincidencia = patron.search(texto)
        if coincidencia:
            nombre = limpiar(coincidencia.group(0))

            if nombre.lower().startswith("ley orgánica"):
                tipo = "LEY_ORGANICA"
            elif nombre.lower().startswith("ley "):
                tipo = "LEY"
            elif nombre.lower().startswith("real decreto"):
                tipo = "REAL_DECRETO"
            elif nombre.lower().startswith("decreto"):
                tipo = "DECRETO"
            else:
                tipo = "CONSTITUCION"

            return tipo, nombre

    return None, None


def clasificar(
    enunciado: str,
    opciones: tuple[str, str, str, str],
) -> tuple[str, str | None, str | None, str | None]:
    texto = " ".join((enunciado, *opciones))

    tipo_norma, nombre_norma = detectar_norma(texto)
    coincidencia_articulo = PATRON_ARTICULO.search(texto)
    articulo = coincidencia_articulo.group(1) if coincidencia_articulo else None

    if nombre_norma and articulo:
        return "JURIDICA", tipo_norma, nombre_norma, articulo

    return "PENDIENTE", tipo_norma, nombre_norma, articulo


def importar_pdf(
    conexion: sqlite3.Connection,
    ruta: Path,
) -> tuple[str, int, int]:
    coincidencia = PATRON_ARCHIVO.match(ruta.name)

    if coincidencia is None:
        raise ValueError("Nombre de archivo no reconocido")

    origen = coincidencia.group(1).upper()
    fuente = ruta.parent.name.lower()
    teorica_practica_fichero = (
        "PRACTICA" if es_examen_practico(ruta) else None
    )

    if fuente not in {"modelo", "apoyo"}:
        raise ValueError("El PDF no está en modelo o apoyo")

    hash_sha256 = calcular_sha256(ruta)
    importacion = buscar_importacion_fichero(
        conexion,
        hash_sha256=hash_sha256,
        tipo_fuente=fuente,
        ruta_pdf=ruta,
        raiz=RAIZ,
    )
    if debe_omitirse(importacion, False, hash_sha256):
        return "SALTADO", 0, 0

    paginas_totales: int | None = None
    try:
        paginas = leer_pdf(ruta)
        paginas_totales = len(paginas)
        respuestas = extraer_respuestas(paginas[0])
        preguntas, omitidas_extraccion = extraer_preguntas(paginas, respuestas)

        registros = []
        for datos in preguntas:
            enunciado, a, b, c, d, correcta, pagina_origen = datos
            tipo_clasificacion, tipo_norma, nombre_norma, articulo = clasificar(
                enunciado,
                (a, b, c, d),
            )
            registros.append(
                {
                    "enunciado": enunciado,
                    "opcion_a": a,
                    "opcion_b": b,
                    "opcion_c": c,
                    "opcion_d": d,
                    "respuesta_correcta": correcta,
                    "tipo_clasificacion": tipo_clasificacion,
                    "tipo_norma": tipo_norma,
                    "nombre_norma": nombre_norma,
                    "articulo": articulo,
                    "tema_no_juridico": None,
                    "origen_oposicion": origen,
                    "tipo_fuente": fuente,
                    "importacion_fichero_id": 0,
                    "pagina_origen": pagina_origen,
                    "teorica_practica": teorica_practica_fichero,
                }
            )

        _, insertadas, duplicadas = publicar_importacion(
            conexion,
            raiz=RAIZ,
            ruta_pdf=ruta,
            hash_sha256=hash_sha256,
            tipo_importacion=fuente,
            paginas_totales=paginas_totales,
            registros=registros,
            omitidas_previas=omitidas_extraccion,
        )
        return "PROCESADO", insertadas, duplicadas
    except Exception as exc:
        registrar_importacion_fallida(
            conexion,
            raiz=RAIZ,
            ruta_pdf=ruta,
            hash_sha256=hash_sha256,
            tipo_importacion=fuente,
            paginas_totales=paginas_totales,
            errores=1,
            mensaje=str(exc),
        )
        raise


def main() -> int:
    configurar_log()

    if not RUTA_DB.exists():
        raise FileNotFoundError(f"No existe la base de datos: {RUTA_DB}")

    pdfs = sorted(
        ruta
        for carpeta in ("modelo", "apoyo")
        for ruta in (CARPETA_EXAMENES / carpeta).glob("*.pdf")
    )

    total_insertadas = 0
    total_duplicadas = 0
    total_omitidos = 0
    total_saltados = 0
    total_excluidos = 0

    with sqlite3.connect(RUTA_DB) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")
        asegurar_columna_teorica_practica(conexion)

        for ruta in pdfs:
            logging.info("Procesando: %s", ruta.relative_to(RAIZ))

            if ruta.name.casefold() in PDF_EXCLUIDOS:
                total_excluidos += 1
                logging.info(
                    "%s | EXCLUIDO | PDF escaneado excluido por criterio "
                    "expreso; no se aplica OCR ni IA visual",
                    ruta.name,
                )
                continue

            try:
                estado, insertadas, duplicadas = importar_pdf(conexion, ruta)
                if estado == "SALTADO":
                    total_saltados += 1
                    logging.info("%s | ya importado; omitido", ruta.name)
                    continue
                total_insertadas += insertadas
                total_duplicadas += duplicadas

                logging.info(
                    "%s | tipo=%s | insertadas=%d | ya existentes=%d",
                    ruta.name,
                    "PRACTICA" if es_examen_practico(ruta) else "SIN_FORZAR",
                    insertadas,
                    duplicadas,
                )

            except Exception as exc:
                conexion.rollback()
                total_omitidos += 1
                logging.error("%s | OMITIDO | %s", ruta.name, exc)

    print()
    print(f"PDF encontrados: {len(pdfs)}")
    print(f"PDF ya importados: {total_saltados}")
    print(f"PDF excluidos: {total_excluidos}")
    print(f"PDF omitidos: {total_omitidos}")
    print(f"Preguntas insertadas: {total_insertadas}")
    print(f"Preguntas ya existentes: {total_duplicadas}")
    print(f"Log: {RUTA_LOG}")
    return 1 if total_omitidos else 0


if __name__ == "__main__":
    raise SystemExit(main())