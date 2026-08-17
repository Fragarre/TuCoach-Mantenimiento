"""
Importa los PDF estructurados de ``data_academia_texto``.

Formato esperado:

* preguntas numeradas como ``01.-``;
* cuatro opciones ``a)`` a ``d)``;
* respuesta correcta subrayada o indicada expresamente en la corrección;
* explicación posterior, que se usa para identificar la norma y el artículo;
* preguntas marcadas como NULA, que se excluyen.

La explicación no se almacena. La clasificación TEORICA/PRACTICA queda sin
forzar para que la realice después ``enriquecer_preguntas.py --aplicar``.

La publicación utiliza ``importacion_preguntas_comun.py``: es atómica por PDF,
idempotente y aplica el criterio único de duplicado del proyecto.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pymupdf as fitz
from importacion_preguntas_comun import (
    buscar_importacion_fichero,
    calcular_sha256,
    debe_omitirse,
    publicar_importacion,
    registrar_importacion_fallida,
)


RUTA_SCRIPT = Path(__file__).resolve()
RAIZ = RUTA_SCRIPT.parent.parent
CARPETA_PDF = RAIZ / "data_academia_texto"
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
RUTA_LOG = RAIZ / "logs" / "importar_tests_academia_texto.log"
RUTA_COSTES = RAIZ / "registros" / "coste_ia.csv"

TIPO_FUENTE = "data_academia_texto"
MODELO = "gpt-5.4-nano"
OPERACION_IA = "resolver_norma_articulo_academia_texto"
VERSION_SCRIPT = "2026-08-06-v3-referencias-parciales"

RE_ORIGEN = re.compile(r"^(A1|A2|C1|C2)(?:\b|[_\-])", re.IGNORECASE)
RE_INICIO_PREGUNTA = re.compile(r"^\s*(\d{1,3})\.-\s*(.*)$")
RE_INICIO_OPCION = re.compile(r"^\s*([a-e])\)\s*(.*)$", re.IGNORECASE)
RE_NULA = re.compile(r"(?im)^\s*(?:ES\s+)?NULA\b")
RE_INICIO_EXPLICACION = re.compile(
    r"^\s*(?:"
    r"Respuesta\s*:|Explicaci[oó]n\s*:|Correcci[oó]n\s*:|"
    r"SOLUCI[ÓO]N\b|(?:ES\s+)?NULA\b|"
    r"La\s+respuesta\s+correcta\b|"
    r"La\s+[ABCD](?:\s+es\s+la\s+correcta|[.)]?)\s*$"
    r")",
    re.IGNORECASE,
)

PATRONES_RESPUESTA_EXPLICITA = (
    # La explicación se normaliza en una sola línea. Por eso la respuesta puede
    # aparecer después de rótulos como "Corrección:" o "Solución:" y no al
    # principio de la cadena.
    re.compile(
        r"(?i)(?:\bRespuesta\s+correcta|\bRespuesta)\s*[:.-]?\s*"
        r"(?:es\s+)?(?:la\s+)?([ABCD])(?=\s*[).,:;]|\s|$)"
    ),
    re.compile(
        r"(?i)\bLa\s+respuesta\s+correcta\s+es\s+(?:la\s+)?"
        r"([ABCD])(?=\s*[).,:;]|\s|$)"
    ),
    re.compile(
        r"(?i)\bOpci[oó]n\s+([ABCD])\s*\(?\s*Correcta\s*\)?\s*[:.-]?"
    ),
    re.compile(r"(?i)\bLa\s+([ABCD])\s+es\s+la\s+correcta\b"),
    re.compile(r"(?im)^\s*La\s+([ABCD])[.)]?\s*$"),
)

PATRON_ARTICULO_NORMA = re.compile(
    r"\bart(?:[íi]culo|s?\.)\s*"
    r"(\d+(?:\.\d+)*(?:\.[a-z])?)"
    r"(?:\s*(?:,|y)\s*\d+(?:\.\d+)*)?"
    r"[^\n.;:]{0,70}?"
    r"(?:de\s+la\s+|del\s+|de\s+)?"
    r"("
    r"Ley\s+Org[aá]nica\s+\d+/\d{4}|"
    r"Ley\s+\d+/\d{4}|"
    r"Real\s+Decreto(?:\s+Legislativo|-ley)?\s+\d+/\d{4}|"
    r"Decreto(?:\s+Legislativo|-ley)?\s+\d+/\d{4}"
    r")",
    re.IGNORECASE,
)

PATRON_MENCION_ARTICULO = re.compile(
    r"\bart(?:[íi]culos?|s?\.)\s*\d+",
    re.IGNORECASE,
)

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
    "teorica_practica",
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
class LineaPDF:
    pagina: int
    x0: float
    y0: float
    x1: float
    y1: float
    texto: str
    es_azul: bool


@dataclass(frozen=True)
class Subrayado:
    pagina: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class PreguntaTexto:
    numero: int
    pagina_origen: int
    enunciado: str
    opciones: dict[str, str]
    respuesta_correcta: str | None
    explicacion: str
    anulada: bool
    nombre_norma: str | None = None
    articulo: str | None = None


def leer_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Importa uno o todos los PDF de data_academia_texto."
        )
    )
    parser.add_argument(
        "--pdf",
        help="Nombre o ruta del único PDF que se desea procesar.",
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Reimporta el PDF indicado aunque figure como completado.",
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
    texto = re.sub(r"-\s*\n\s*(?=\w)", "", texto)
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
    entrada = Path(valor).expanduser()
    candidatos = (
        [entrada]
        if entrada.is_absolute()
        else [Path.cwd() / entrada, RAIZ / entrada, CARPETA_PDF / entrada]
    )
    for candidato in candidatos:
        ruta = candidato.resolve()
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
    pdfs = sorted(
        (ruta for ruta in CARPETA_PDF.iterdir() if ruta.suffix.lower() == ".pdf"),
        key=lambda ruta: ruta.name.casefold(),
    )
    if not pdfs:
        logging.info("SIN PDF PENDIENTES | carpeta=%s", CARPETA_PDF)
    return pdfs


def obtener_origen(nombre_pdf: str) -> str:
    coincidencia = RE_ORIGEN.match(Path(nombre_pdf).stem)
    if coincidencia is None:
        raise ValueError(
            "El nombre del PDF debe comenzar por A1, A2, C1 o C2: "
            f"{nombre_pdf}"
        )
    return coincidencia.group(1).upper()


def color_es_azul(color: int) -> bool:
    rojo = (int(color) >> 16) & 255
    verde = (int(color) >> 8) & 255
    azul = int(color) & 255
    return azul >= 150 and azul > rojo * 1.5 and azul > verde * 1.5


def extraer_lineas(documento: fitz.Document) -> list[LineaPDF]:
    lineas: list[LineaPDF] = []
    for indice in range(documento.page_count):
        pagina = documento.load_page(indice)
        datos = pagina.get_text("dict", sort=True)
        for bloque in datos.get("blocks", []):
            if bloque.get("type") != 0:
                continue
            for linea in bloque.get("lines", []):
                spans = linea.get("spans", [])
                texto = "".join(str(span.get("text", "")) for span in spans)
                texto = texto.strip()
                if not texto:
                    continue
                x0, y0, x1, y1 = linea["bbox"]
                colores = [int(span.get("color", 0)) for span in spans]
                es_azul = bool(colores) and all(
                    color_es_azul(color) for color in colores
                )
                lineas.append(
                    LineaPDF(
                        pagina=indice + 1,
                        x0=float(x0),
                        y0=float(y0),
                        x1=float(x1),
                        y1=float(y1),
                        texto=texto,
                        es_azul=es_azul,
                    )
                )
    return lineas


def extraer_subrayados(documento: fitz.Document) -> list[Subrayado]:
    resultado: list[Subrayado] = []
    for indice in range(documento.page_count):
        pagina = documento.load_page(indice)
        for dibujo in pagina.get_drawings():
            rectangulo = dibujo.get("rect")
            relleno = dibujo.get("fill")
            if rectangulo is None or relleno is None:
                continue
            if rectangulo.height > 1.5 or rectangulo.width < 8.0:
                continue
            if rectangulo.x0 < 95.0:
                continue
            if max(float(componente) for componente in relleno) > 0.08:
                continue
            resultado.append(
                Subrayado(
                    pagina=indice + 1,
                    x0=float(rectangulo.x0),
                    y0=float(rectangulo.y0),
                    x1=float(rectangulo.x1),
                    y1=float(rectangulo.y1),
                )
            )
    return resultado


def linea_inicia_explicacion(linea: LineaPDF) -> bool:
    return linea.es_azul or RE_INICIO_EXPLICACION.match(linea.texto) is not None


def unir_lineas(lineas: list[LineaPDF]) -> str:
    return limpiar_texto("\n".join(linea.texto for linea in lineas))


def opcion_subrayada(
    lineas_opcion: list[LineaPDF],
    subrayados: list[Subrayado],
) -> bool:
    for linea in lineas_opcion:
        for subrayado in subrayados:
            if subrayado.pagina != linea.pagina:
                continue
            if not (linea.y0 - 1.0 <= subrayado.y0 <= linea.y1 + 2.5):
                continue
            solapamiento = min(linea.x1, subrayado.x1) - max(
                linea.x0, subrayado.x0
            )
            if solapamiento >= 5.0:
                return True
    return False


def respuesta_explicita(explicacion: str) -> str | None:
    encontradas = {
        coincidencia.group(1).upper()
        for patron in PATRONES_RESPUESTA_EXPLICITA
        for coincidencia in patron.finditer(explicacion)
    }
    if len(encontradas) > 1:
        raise ValueError(
            "La corrección contiene varias respuestas explícitas: "
            + ", ".join(sorted(encontradas))
        )
    return next(iter(encontradas), None)


def construir_pregunta(
    bloque: list[LineaPDF],
    subrayados: list[Subrayado],
) -> PreguntaTexto:
    inicio = RE_INICIO_PREGUNTA.match(bloque[0].texto)
    if inicio is None:
        raise ValueError("Bloque sin número de pregunta")
    numero = int(inicio.group(1))

    indices_opciones = [
        indice
        for indice, linea in enumerate(bloque[1:], start=1)
        if RE_INICIO_OPCION.match(linea.texto) is not None
    ]
    if len(indices_opciones) < 4:
        raise ValueError(
            f"Pregunta {numero}: solo se localizaron "
            f"{len(indices_opciones)} opciones."
        )
    indices_opciones = indices_opciones[:4]

    inicio_explicacion: int | None = None
    for indice in range(indices_opciones[3] + 1, len(bloque)):
        if linea_inicia_explicacion(bloque[indice]):
            inicio_explicacion = indice
            break
    if inicio_explicacion is None:
        inicio_explicacion = len(bloque)

    letras_detectadas = [
        str(RE_INICIO_OPCION.match(bloque[indice].texto).group(1)).lower()
        for indice in indices_opciones
    ]
    if letras_detectadas != list("abcd"):
        logging.warning(
            "Pregunta %d: rótulos %s; las opciones se asignan por posición",
            numero,
            "/".join(letras_detectadas),
        )

    primera = bloque[0]
    primera_sin_numero = inicio.group(2)
    lineas_enunciado = [
        LineaPDF(
            pagina=primera.pagina,
            x0=primera.x0,
            y0=primera.y0,
            x1=primera.x1,
            y1=primera.y1,
            texto=primera_sin_numero,
            es_azul=primera.es_azul,
        ),
        *bloque[1:indices_opciones[0]],
    ]

    opciones: dict[str, str] = {}
    opciones_lineas: dict[str, list[LineaPDF]] = {}
    for posicion, letra in enumerate("abcd"):
        desde = indices_opciones[posicion]
        hasta = (
            indices_opciones[posicion + 1]
            if posicion < 3
            else inicio_explicacion
        )
        lineas_opcion = list(bloque[desde:hasta])
        marcador = RE_INICIO_OPCION.match(lineas_opcion[0].texto)
        lineas_opcion[0] = LineaPDF(
            pagina=lineas_opcion[0].pagina,
            x0=lineas_opcion[0].x0,
            y0=lineas_opcion[0].y0,
            x1=lineas_opcion[0].x1,
            y1=lineas_opcion[0].y1,
            texto=marcador.group(2),
            es_azul=lineas_opcion[0].es_azul,
        )
        opciones[letra] = unir_lineas(lineas_opcion)
        opciones_lineas[letra] = lineas_opcion

    explicacion = unir_lineas(bloque[inicio_explicacion:])
    anulada = RE_NULA.search(explicacion) is not None

    explicita = respuesta_explicita(explicacion)
    marcadas = [
        letra.upper()
        for letra in "abcd"
        if opcion_subrayada(opciones_lineas[letra], subrayados)
    ]
    subrayada = marcadas[0] if len(marcadas) == 1 else None

    if not anulada:
        if len(marcadas) > 1:
            raise ValueError(
                f"Pregunta {numero}: varias opciones subrayadas: "
                + ", ".join(marcadas)
            )
        if explicita and subrayada and explicita != subrayada:
            raise ValueError(
                f"Pregunta {numero}: respuesta explícita {explicita} "
                f"distinta de la subrayada {subrayada}."
            )
        correcta = explicita or subrayada
        if correcta not in {"A", "B", "C", "D"}:
            raise ValueError(
                f"Pregunta {numero}: no se pudo identificar la respuesta correcta."
            )
    else:
        correcta = None

    enunciado = unir_lineas(lineas_enunciado)
    if not enunciado or any(not opciones[letra] for letra in "abcd"):
        raise ValueError(f"Pregunta {numero}: contenido incompleto.")

    return PreguntaTexto(
        numero=numero,
        pagina_origen=primera.pagina,
        enunciado=enunciado,
        opciones=opciones,
        respuesta_correcta=correcta,
        explicacion=explicacion,
        anulada=anulada,
    )


def extraer_preguntas(documento: fitz.Document) -> list[PreguntaTexto]:
    lineas = extraer_lineas(documento)
    subrayados = extraer_subrayados(documento)
    inicios = [
        indice
        for indice, linea in enumerate(lineas)
        if RE_INICIO_PREGUNTA.match(linea.texto) is not None
    ]
    if not inicios:
        raise ValueError("No se localizaron preguntas con formato NN.-")

    preguntas: list[PreguntaTexto] = []
    for posicion, inicio in enumerate(inicios):
        final = inicios[posicion + 1] if posicion + 1 < len(inicios) else len(lineas)
        preguntas.append(construir_pregunta(lineas[inicio:final], subrayados))

    for indice, pregunta in enumerate(preguntas):
        if pregunta.explicacion:
            continue
        if indice + 1 >= len(preguntas) or not preguntas[indice + 1].explicacion:
            raise ValueError(
                f"Pregunta {pregunta.numero}: no se localizó una explicación "
                "propia ni compartida con la pregunta siguiente."
            )
        pregunta.explicacion = preguntas[indice + 1].explicacion
        logging.info(
            "Pregunta %d: utiliza la explicación compartida situada tras "
            "la pregunta %d",
            pregunta.numero,
            preguntas[indice + 1].numero,
        )

    numeros = [pregunta.numero for pregunta in preguntas]
    if len(numeros) != len(set(numeros)):
        raise ValueError("Hay números de pregunta repetidos.")
    esperados = list(range(min(numeros), max(numeros) + 1))
    if numeros != esperados:
        raise ValueError(
            f"Secuencia de preguntas incompleta: {numeros}"
        )
    return preguntas


def referencia_inequivoca(explicacion: str) -> tuple[str, str] | None:
    menciones = list(PATRON_MENCION_ARTICULO.finditer(explicacion))
    parejas = list(PATRON_ARTICULO_NORMA.finditer(explicacion))
    if len(menciones) != 1 or len(parejas) != 1:
        return None
    pareja = parejas[0]
    if re.search(r"(?:,|\by\b)\s*\d+", pareja.group(0), re.IGNORECASE):
        return None
    articulo, norma = pareja.group(1), pareja.group(2)
    referencia = (limpiar_texto(norma), limpiar_texto(articulo))
    if all(referencia):
        return referencia
    return None


def cargar_utilidad_openai():
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    errores: list[str] = []
    for modulo in ("core.openai_api", "scripts.openai_api"):
        try:
            utilidad = importlib.import_module(modulo)
            utilidad.LOG_COSTES = RUTA_COSTES
            RUTA_COSTES.parent.mkdir(parents=True, exist_ok=True)
            return utilidad
        except ModuleNotFoundError as exc:
            errores.append(f"{modulo}: {exc}")
    raise ImportError(
        "No se encuentra openai_api.py en core ni en scripts.\n"
        + "\n".join(errores)
    )


def resolver_referencias_ia(
    preguntas: list[PreguntaTexto],
    utilidad_openai,
    tamano_lote: int = 6,
) -> dict[int, tuple[str, str]]:
    resultado: dict[int, tuple[str, str]] = {}
    for inicio in range(0, len(preguntas), tamano_lote):
        lote = preguntas[inicio:inicio + tamano_lote]
        datos_entrada = [
            {
                "numero": pregunta.numero,
                "enunciado": pregunta.enunciado,
                "opciones": pregunta.opciones,
                "respuesta_correcta": pregunta.respuesta_correcta,
                "explicacion": pregunta.explicacion,
            }
            for pregunta in lote
        ]
        prompt = """
Para cada pregunta, selecciona la única norma y el único artículo que
constituyen el fundamento jurídico principal de la opción correcta.

Reglas obligatorias:
- Usa exclusivamente el enunciado, las opciones y la explicación facilitados.
- La explicación puede citar referencias secundarias para descartar opciones;
  no las elijas si no fundamentan directamente la respuesta correcta.
- Si aparece una abreviatura inequívoca, devuelve la denominación legal con su
  número y año cuando pueda obtenerse del propio contexto.
- No inventes referencias.
- El artículo debe conservar apartados o letras relevantes, por ejemplo 45.1.b.
- Devuelve todas y solo las preguntas recibidas.

Devuelve un objeto JSON con esta forma exacta:
{"referencias": [{"numero": 1, "nombre_norma": "Ley 00/0000", "articulo": "1"}]}

PREGUNTAS:
""".strip() + "\n" + json.dumps(
            datos_entrada,
            ensure_ascii=False,
        )

        datos = utilidad_openai.seleccionar_fragmento_json(
            prompt=prompt,
            modelo=MODELO,
            operacion=OPERACION_IA,
        )
        referencias = datos.get("referencias") if isinstance(datos, dict) else None
        if not isinstance(referencias, list):
            raise ValueError("La IA no devolvió la lista 'referencias'.")

        esperados = {pregunta.numero for pregunta in lote}
        recibidos: set[int] = set()
        for referencia in referencias:
            if not isinstance(referencia, dict):
                raise ValueError("Una referencia IA no es un objeto JSON.")
            try:
                numero = int(referencia.get("numero"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Número de pregunta IA no válido.") from exc
            if numero not in esperados or numero in recibidos:
                raise ValueError(
                    f"Número inesperado o repetido en respuesta IA: {numero}"
                )
            norma = limpiar_texto(referencia.get("nombre_norma"))
            articulo = limpiar_texto(referencia.get("articulo"))
            recibidos.add(numero)
            if not norma or not articulo or not re.match(r"^\d+", articulo):
                logging.warning(
                    "Pregunta %d: referencia IA incompleta; se omitirá esta pregunta",
                    numero,
                )
                continue
            resultado[numero] = (norma, articulo)

        faltan = sorted(esperados - recibidos)
        for numero in faltan:
            logging.warning(
                "Pregunta %d: la IA no devolvió referencia; se omitirá esta pregunta",
                numero,
            )
    return resultado


def inferir_tipo_norma(nombre_norma: str) -> str | None:
    nombre = limpiar_texto(nombre_norma).casefold()
    reglas = (
        ("constitución", "CONSTITUCION"),
        ("ley orgánica", "LEY ORGANICA"),
        ("real decreto legislativo", "REAL DECRETO LEGISLATIVO"),
        ("real decreto-ley", "REAL DECRETO-LEY"),
        ("real decreto", "REAL DECRETO"),
        ("decreto legislativo", "DECRETO LEGISLATIVO"),
        ("decreto-ley", "DECRETO-LEY"),
        ("decreto", "DECRETO"),
        ("reglamento", "REGLAMENTO"),
        ("tratado", "TRATADO"),
        ("ley", "LEY"),
    )
    for comienzo, tipo in reglas:
        if nombre.startswith(comienzo):
            return tipo
    return None


def resolver_referencias(
    preguntas: list[PreguntaTexto],
    resolver_ambiguas: Callable[
        [list[PreguntaTexto]],
        dict[int, tuple[str, str]],
    ],
) -> None:
    ambiguas: list[PreguntaTexto] = []
    for pregunta in preguntas:
        if pregunta.anulada:
            continue
        referencia = referencia_inequivoca(pregunta.explicacion)
        if referencia is None:
            ambiguas.append(pregunta)
            continue
        pregunta.nombre_norma, pregunta.articulo = referencia
        logging.info(
            "Pregunta %d | referencia=%s | art. %s | método=%s",
            pregunta.numero,
            pregunta.nombre_norma,
            pregunta.articulo,
            "TEXTO",
        )

    if not ambiguas:
        return
    resueltas = resolver_ambiguas(ambiguas)
    for pregunta in ambiguas:
        referencia = resueltas.get(pregunta.numero)
        if referencia is None:
            logging.warning(
                "Pregunta %d | OMITIDA | no se pudo identificar norma y artículo",
                pregunta.numero,
            )
            continue
        pregunta.nombre_norma, pregunta.articulo = referencia
        logging.info(
            "Pregunta %d | referencia=%s | art. %s | método=IA",
            pregunta.numero,
            pregunta.nombre_norma,
            pregunta.articulo,
        )


def preparar_registro(
    pregunta: PreguntaTexto,
    origen: str,
) -> dict[str, Any]:
    if pregunta.anulada:
        raise ValueError("No se prepara una pregunta anulada.")
    if pregunta.respuesta_correcta not in {"A", "B", "C", "D"}:
        raise ValueError(
            f"Pregunta {pregunta.numero}: respuesta correcta no válida."
        )
    if not pregunta.nombre_norma or not pregunta.articulo:
        raise ValueError(
            f"Pregunta {pregunta.numero}: referencia jurídica incompleta."
        )
    return {
        "enunciado": limpiar_texto(pregunta.enunciado),
        "opcion_a": limpiar_texto(pregunta.opciones["a"]),
        "opcion_b": limpiar_texto(pregunta.opciones["b"]),
        "opcion_c": limpiar_texto(pregunta.opciones["c"]),
        "opcion_d": limpiar_texto(pregunta.opciones["d"]),
        "respuesta_correcta": pregunta.respuesta_correcta,
        "tipo_clasificacion": "JURIDICA",
        "tipo_norma": inferir_tipo_norma(pregunta.nombre_norma),
        "nombre_norma": limpiar_texto(pregunta.nombre_norma),
        "articulo": limpiar_texto(pregunta.articulo),
        "tema_no_juridico": None,
        "origen_oposicion": origen,
        "tipo_fuente": TIPO_FUENTE,
        "importacion_fichero_id": 0,
        "pagina_origen": pregunta.pagina_origen,
        "teorica_practica": None,
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
        "extraidas": 0,
        "anuladas": 0,
        "omitidas": 0,
        "insertadas": 0,
        "duplicadas": 0,
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

    documento: fitz.Document | None = None
    try:
        documento = fitz.open(ruta_pdf)
        totales["paginas"] = documento.page_count
        origen = obtener_origen(ruta_pdf.name)
        preguntas = extraer_preguntas(documento)
        totales["extraidas"] = len(preguntas)
        totales["anuladas"] = sum(pregunta.anulada for pregunta in preguntas)

        utilidad_openai = None

        def resolver_ambiguas(
            ambiguas: list[PreguntaTexto],
        ) -> dict[int, tuple[str, str]]:
            nonlocal utilidad_openai
            if utilidad_openai is None:
                utilidad_openai = cargar_utilidad_openai()
            return resolver_referencias_ia(ambiguas, utilidad_openai)

        resolver_referencias(preguntas, resolver_ambiguas)
        sin_referencia = [
            pregunta
            for pregunta in preguntas
            if not pregunta.anulada
            and (not pregunta.nombre_norma or not pregunta.articulo)
        ]
        totales["omitidas"] = len(sin_referencia)
        registros = [
            preparar_registro(pregunta, origen)
            for pregunta in preguntas
            if not pregunta.anulada
            and pregunta.nombre_norma
            and pregunta.articulo
        ]
        if not registros:
            raise ValueError("El PDF no contiene preguntas válidas.")

        _, insertadas, duplicadas = publicar_importacion(
            conexion,
            raiz=RAIZ,
            ruta_pdf=ruta_pdf,
            hash_sha256=hash_sha256,
            tipo_importacion=TIPO_FUENTE,
            paginas_totales=documento.page_count,
            registros=registros,
            omitidas_previas=totales["anuladas"] + totales["omitidas"],
        )
        totales["insertadas"] = insertadas
        totales["duplicadas"] = duplicadas
        logging.info(
            "FIN PDF | %s | extraídas=%d | anuladas=%d | omitidas=%d | insertadas=%d | "
            "duplicadas=%d",
            ruta_pdf.name,
            totales["extraidas"],
            totales["anuladas"],
            totales["omitidas"],
            insertadas,
            duplicadas,
        )
        return "PROCESADO", totales
    except Exception as exc:
        totales["errores"] += 1
        registrar_importacion_fallida(
            conexion,
            raiz=RAIZ,
            ruta_pdf=ruta_pdf,
            hash_sha256=hash_sha256,
            tipo_importacion=TIPO_FUENTE,
            paginas_totales=totales["paginas"] or None,
            errores=totales["errores"],
            mensaje=str(exc),
        )
        logging.exception("IMPORTACIÓN RECHAZADA | %s | %s", ruta_pdf, exc)
        return "ERROR", totales
    finally:
        if documento is not None:
            documento.close()


def main() -> int:
    configurar_logging()
    logging.info("VERSIÓN SCRIPT | %s | archivo=%s", VERSION_SCRIPT, RUTA_SCRIPT)
    args = leer_argumentos()

    if not RUTA_DB.is_file():
        logging.error("No existe la base de datos: %s", RUTA_DB)
        return 1
    try:
        pdfs = obtener_pdfs(args.pdf)
    except Exception as exc:
        logging.exception("Error de configuración: %s", exc)
        return 1

    globales = {
        "pdf_encontrados": len(pdfs),
        "pdf_procesados": 0,
        "pdf_saltados": 0,
        "pdf_error": 0,
        "paginas": 0,
        "extraidas": 0,
        "anuladas": 0,
        "omitidas": 0,
        "insertadas": 0,
        "duplicadas": 0,
        "errores": 0,
    }

    try:
        with sqlite3.connect(RUTA_DB) as conexion:
            conexion.row_factory = sqlite3.Row
            conexion.execute("PRAGMA foreign_keys = ON")
            validar_base_datos(conexion)
            for ruta_pdf in pdfs:
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
                    "extraidas",
                    "anuladas",
                    "omitidas",
                    "insertadas",
                    "duplicadas",
                    "errores",
                ):
                    globales[clave] += totales[clave]
    except Exception as exc:
        logging.exception("Error general: %s", exc)
        return 1

    print()
    print("=" * 76)
    print("RESUMEN IMPORTACIÓN ACADEMIA TEXTO")
    print("=" * 76)
    print(f"PDF encontrados:        {globales['pdf_encontrados']}")
    print(f"PDF procesados:         {globales['pdf_procesados']}")
    print(f"PDF ya importados:      {globales['pdf_saltados']}")
    print(f"PDF con error:          {globales['pdf_error']}")
    print(f"Páginas procesadas:     {globales['paginas']}")
    print(f"Preguntas extraídas:    {globales['extraidas']}")
    print(f"Preguntas anuladas:     {globales['anuladas']}")
    print(f"Preguntas omitidas:     {globales['omitidas']}")
    print(f"Preguntas insertadas:   {globales['insertadas']}")
    print(f"Preguntas duplicadas:   {globales['duplicadas']}")
    print(f"Errores:                {globales['errores']}")
    print(f"Log:                    {RUTA_LOG}")
    print("=" * 76)
    return 1 if globales["pdf_error"] or globales["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
