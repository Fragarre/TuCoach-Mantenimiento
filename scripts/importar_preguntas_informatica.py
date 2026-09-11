"""
===============================================================================
Proyecto : OpoCoach
Tipo     : Importación de preguntas de informática sin solucionario
Archivo  : importar_preguntas_informatica.py
Ubicación:
    scripts/importar_preguntas_informatica.py

OBJETIVO
--------
Importar en `lote_preguntas` preguntas procedentes de PDF almacenados en
`data_informatica`.

Los PDF contienen:
    - enunciado;
    - cuatro opciones A, B, C y D;
    - normalmente no contienen la respuesta correcta.

La respuesta y la clasificación temática se obtienen mediante la utilidad
existente `openai_api.py`. Una pregunta solo se importa cuando la respuesta es
única, inequívoca y la IA declara confianza alta. Si existe alguna pregunta
dudosa, defectuosa o incompleta, el PDF completo se rechaza y se registra.

MODOS DE EJECUCIÓN
------------------
1. Procesar todos los PDF de `data_informatica`:

       python scripts/importar_preguntas_informatica.py

2. Procesar un único PDF:

       python scripts/importar_preguntas_informatica.py \
           --pdf preguntas_informatica_001.pdf

   También se admite una ruta relativa o absoluta:

       python scripts/importar_preguntas_informatica.py \
           --pdf data_informatica/preguntas_informatica_001.pdf

3. Forzar la reimportación de un PDF ya completado:

       python scripts/importar_preguntas_informatica.py \
           --pdf preguntas_informatica_001.pdf --forzar

   `--forzar` solo se admite junto con `--pdf`.

TRAZABILIDAD E IDEMPOTENCIA
---------------------------
1. Se calcula el SHA-256 del PDF antes de llamar a la IA.

2. Cada PDF se registra en `importaciones_ficheros` con:
   - ruta relativa;
   - nombre;
   - hash;
   - tipo de fuente;
   - estado;
   - páginas totales;
   - preguntas insertadas, omitidas y con error;
   - fechas de inicio y fin;
   - último error.

3. Un PDF con el mismo hash y estado `COMPLETADO` se salta por completo:
   no se extrae, no se analiza y no consume API.

4. Se reprocesa cuando:
   - se usa `--forzar`;
   - `reimportar = 1`;
   - el estado no es `COMPLETADO`;
   - o el contenido ha cambiado y tiene otro hash.

5. La extracción y la validación terminan antes de modificar preguntas. La
   publicación se realiza en una única transacción. Si falla, se conservan
   las preguntas anteriores y `reimportar` queda en 1.

6. Cada pregunta importada guarda:
   - `importacion_fichero_id`;
   - `pagina_origen`.

CRITERIOS DE IMPORTACIÓN
------------------------
1. `tipo_clasificacion = "INFORMATICA"`.

2. `tipo_norma`, `nombre_norma`, `articulo` y `origen_oposicion` quedan NULL.

3. `tipo_fuente = "informatica"`.

4. La clasificación temática se guarda en `tema_no_juridico`.

5. Categorías preferentes:
   - HARDWARE
   - SISTEMA_OPERATIVO
   - OFIMATICA_WORD
   - OFIMATICA_EXCEL
   - OUTLOOK
   - TEAMS
   - MICROSOFT_365
   - NAVEGADORES
   - SEGURIDAD
   - INTELIGENCIA_ARTIFICIAL
   - REDES

6. Se intenta usar la categoría más general que siga siendo informativa.

7. Solo se importa cuando:
   - existen enunciado y cuatro opciones;
   - hay una única respuesta correcta;
   - la letra devuelta es A, B, C o D;
   - `dudosa = false`;
   - la confianza declarada es igual o superior a 0,95.

8. Si una pregunta es ambigua, depende de una versión/configuración no
   especificada, contiene opciones defectuosas o tiene más de una respuesta
   defendible, se rechaza el PDF completo.

9. El importador elimina duplicados internos y evita duplicar preguntas ya
   existentes en `lote_preguntas`.

10. La extracción del texto se hace localmente con PyMuPDF. La IA solo recibe
    preguntas ya separadas, en lotes pequeños.

11. El coste de IA se registra mediante `openai_api.py` en:
        registros/coste_ia.csv

BASE DE DATOS
-------------
El script exige que ya existan:
    - `lote_preguntas`;
    - `importaciones_ficheros`;
    - `importacion_fichero_id`;
    - `pagina_origen`.

El script no crea ni modifica tablas.

DEPENDENCIAS
------------
    pip install pymupdf openai python-dotenv

===============================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import re
import sqlite3
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Any, Iterable

import pymupdf as fitz
from importacion_preguntas_comun import (
    buscar_importacion_fichero,
    debe_omitirse,
    publicar_importacion,
    registrar_importacion_fallida,
)


# =============================================================================
# RUTAS Y CONSTANTES
# =============================================================================

RUTA_SCRIPT = Path(__file__).resolve()
RAIZ = RUTA_SCRIPT.parent.parent

CARPETA_PDF = RAIZ / "data_informatica"
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
RUTA_LOG = RAIZ / "logs" / "importar_preguntas_informatica.log"
RUTA_COSTES = RAIZ / "registros" / "coste_ia.csv"

TIPO_FUENTE = "informatica"
TIPO_CLASIFICACION = "INFORMATICA"
MODELO = "gpt-5.4-nano"
OPERACION_IA = "resolver_preguntas_informatica"

TAMANO_LOTE_IA = 8
CONFIANZA_MINIMA = 0.95

CATEGORIAS = {
    "HARDWARE",
    "SISTEMA_OPERATIVO",
    "OFIMATICA_WORD",
    "OFIMATICA_EXCEL",
    "OUTLOOK",
    "TEAMS",
    "MICROSOFT_365",
    "NAVEGADORES",
    "SEGURIDAD",
    "INTELIGENCIA_ARTIFICIAL",
    "REDES",
}

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


# =============================================================================
# ARGUMENTOS Y CONFIGURACIÓN
# =============================================================================

def leer_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Importa preguntas sin respuesta desde uno o todos los PDF "
            "de data_informatica."
        )
    )
    parser.add_argument(
        "--pdf",
        help=(
            "Nombre o ruta del único PDF que se desea procesar. "
            "Si se omite, se procesa toda la carpeta data_informatica."
        ),
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help=(
            "Reimporta el PDF aunque ya figure como completado. "
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


# =============================================================================
# SELECCIÓN DE PDF
# =============================================================================

def resolver_pdf_indicado(valor: str) -> Path:
    entrada = Path(valor).expanduser()
    candidatos: list[Path] = []

    if entrada.is_absolute():
        candidatos.append(entrada)
    else:
        candidatos.append(RAIZ / entrada)
        candidatos.append(CARPETA_PDF / entrada)

    vistos: set[Path] = set()

    for candidato in candidatos:
        resuelto = candidato.resolve()

        if resuelto in vistos:
            continue
        vistos.add(resuelto)

        if resuelto.is_file():
            if resuelto.suffix.lower() != ".pdf":
                raise ValueError(
                    f"El archivo indicado no es un PDF: {resuelto}"
                )
            return resuelto

    rutas = "\n".join(f"  - {ruta.resolve()}" for ruta in candidatos)
    raise FileNotFoundError(
        f"No se encuentra el PDF indicado: {valor}\n"
        f"Rutas comprobadas:\n{rutas}"
    )


def obtener_pdfs(pdf_indicado: str | None) -> list[Path]:
    if pdf_indicado:
        return [resolver_pdf_indicado(pdf_indicado)]

    if not CARPETA_PDF.is_dir():
        raise FileNotFoundError(
            f"No existe la carpeta de entrada: {CARPETA_PDF}"
        )

    pdfs = sorted(
        (
            ruta.resolve()
            for ruta in CARPETA_PDF.iterdir()
            if (
                ruta.is_file()
                and ruta.suffix.lower() == ".pdf"
                and ruta.name.casefold() != "temario_informatica.pdf"
            )
        ),
        key=lambda ruta: ruta.name.casefold(),
    )

    if not pdfs:
        logging.info("SIN PDF PENDIENTES | carpeta=%s", CARPETA_PDF)

    return pdfs


# =============================================================================
# VALIDACIÓN DE BASE DE DATOS
# =============================================================================

def columnas_tabla(
    conexion: sqlite3.Connection,
    tabla: str,
) -> set[str]:
    return {
        fila[1]
        for fila in conexion.execute(f"PRAGMA table_info({tabla})")
    }


def validar_base_datos(conexion: sqlite3.Connection) -> None:
    tablas = {
        fila[0]
        for fila in conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    for tabla in ("lote_preguntas", "importaciones_ficheros"):
        if tabla not in tablas:
            raise RuntimeError(f"No existe la tabla {tabla}.")

    faltantes_lote = (
        COLUMNAS_LOTE_ESPERADAS
        - columnas_tabla(conexion, "lote_preguntas")
    )
    faltantes_importacion = (
        COLUMNAS_IMPORTACION_ESPERADAS
        - columnas_tabla(conexion, "importaciones_ficheros")
    )

    if faltantes_lote:
        raise RuntimeError(
            "Faltan columnas en lote_preguntas: "
            + ", ".join(sorted(faltantes_lote))
        )

    if faltantes_importacion:
        raise RuntimeError(
            "Faltan columnas en importaciones_ficheros: "
            + ", ".join(sorted(faltantes_importacion))
        )


# =============================================================================
# TRAZABILIDAD DE FICHEROS
# =============================================================================

def calcular_sha256(ruta: Path) -> str:
    digest = hashlib.sha256()

    with ruta.open("rb") as fichero:
        while bloque := fichero.read(1024 * 1024):
            digest.update(bloque)

    return digest.hexdigest()


def limpiar_texto(valor: Any) -> str:
    if valor is None:
        return ""

    texto = str(valor)
    texto = texto.replace("\u00ad", "")
    texto = texto.replace("\xa0", " ")
    texto = re.sub(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]?", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def extraer_texto_con_paginas(
    documento: fitz.Document,
) -> tuple[str, list[int]]:
    """
    Devuelve el texto completo y el desplazamiento inicial de cada página.
    """
    partes: list[str] = []
    inicios: list[int] = []
    desplazamiento = 0

    for pagina in documento:
        inicios.append(desplazamiento)
        texto_pagina = pagina.get_text("text")
        partes.append(texto_pagina)
        desplazamiento += len(texto_pagina) + 1

    return "\n".join(partes), inicios


def pagina_para_posicion(
    posicion: int,
    inicios_paginas: list[int],
) -> int:
    return bisect_right(inicios_paginas, posicion)


def separar_opciones(
    cuerpo: str,
) -> tuple[str, str, str, str, str] | None:
    """
    Separa enunciado y opciones a), b), c), d).
    """
    patron = re.compile(
        r"(?is)^(.*?)"
        r"\ba\)\s*(.*?)"
        r"\bb\)\s*(.*?)"
        r"\bc\)\s*(.*?)"
        r"\bd\)\s*(.*)$"
    )
    coincidencia = patron.match(cuerpo.strip())

    if coincidencia is None:
        return None

    valores = tuple(
        limpiar_texto(coincidencia.group(indice))
        for indice in range(1, 6)
    )

    if any(not valor for valor in valores):
        return None

    return valores


def extraer_preguntas(
    documento: fitz.Document,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Extrae preguntas numeradas del texto completo del PDF.

    El patrón admite:
        1
        ¿Pregunta...?

    y:
        100Pregunta...
    """
    texto, inicios_paginas = extraer_texto_con_paginas(documento)

    patron_inicio = re.compile(
        r"(?m)^\s*(\d{1,4})\s*(?=(?:¿|[A-ZÁÉÍÓÚÜÑ]))"
    )
    coincidencias = list(patron_inicio.finditer(texto))

    preguntas: list[dict[str, Any]] = []
    incidencias: list[str] = []

    for indice, coincidencia in enumerate(coincidencias):
        numero = int(coincidencia.group(1))
        inicio_cuerpo = coincidencia.end()
        fin_cuerpo = (
            coincidencias[indice + 1].start()
            if indice + 1 < len(coincidencias)
            else len(texto)
        )
        cuerpo = texto[inicio_cuerpo:fin_cuerpo].strip()
        pagina = pagina_para_posicion(
            coincidencia.start(),
            inicios_paginas,
        )

        separada = separar_opciones(cuerpo)

        if separada is None:
            incidencias.append(
                f"número_visible={numero} | página={pagina} | "
                "no se detectaron enunciado y cuatro opciones"
            )
            continue

        enunciado, opcion_a, opcion_b, opcion_c, opcion_d = separada

        preguntas.append(
            {
                "numero_visible": numero,
                "pagina_origen": pagina,
                "enunciado": enunciado,
                "opcion_a": opcion_a,
                "opcion_b": opcion_b,
                "opcion_c": opcion_c,
                "opcion_d": opcion_d,
            }
        )

    numeros_detectados = {int(m.group(1)) for m in coincidencias}
    if numeros_detectados:
        primero = min(numeros_detectados)
        ultimo = max(numeros_detectados)
        for numero in range(primero, ultimo + 1):
            if numero not in numeros_detectados:
                incidencias.append(
                    f"número_visible={numero} | pregunta sin contenido extraíble"
                )

    return preguntas, incidencias


# =============================================================================
# DEDUPLICACIÓN INTERNA
# =============================================================================

def clave_pregunta(pregunta: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(pregunta[campo])
        for campo in (
            "enunciado",
            "opcion_a",
            "opcion_b",
            "opcion_c",
            "opcion_d",
        )
    )


def quitar_duplicados_internos(
    preguntas: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vistas: dict[tuple[str, ...], dict[str, Any]] = {}
    unicas: list[dict[str, Any]] = []
    duplicadas: list[dict[str, Any]] = []

    for pregunta in preguntas:
        clave = clave_pregunta(pregunta)

        if clave in vistas:
            duplicada = dict(pregunta)
            duplicada["numero_original"] = vistas[clave]["numero_visible"]
            duplicadas.append(duplicada)
            continue

        vistas[clave] = pregunta
        unicas.append(pregunta)

    return unicas, duplicadas


# =============================================================================
# LLAMADA A IA
# =============================================================================

def dividir_lotes(
    elementos: list[dict[str, Any]],
    tamano: int,
) -> Iterable[list[dict[str, Any]]]:
    for inicio in range(0, len(elementos), tamano):
        yield elementos[inicio:inicio + tamano]


def construir_entrada_ia(
    lote: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    preguntas_json = [
        {
            "numero_visible": pregunta["numero_visible"],
            "enunciado": pregunta["enunciado"],
            "A": pregunta["opcion_a"],
            "B": pregunta["opcion_b"],
            "C": pregunta["opcion_c"],
            "D": pregunta["opcion_d"],
        }
        for pregunta in lote
    ]

    instrucciones = f"""
Analiza estas preguntas tipo test de informática.

Debes determinar:
1. si existe una única respuesta correcta;
2. la letra correcta;
3. una categoría temática general;
4. tu grado de confianza.

Devuelve exclusivamente un objeto JSON válido, sin Markdown:

{{
  "resultados": [
    {{
      "numero_visible": 1,
      "importar": true,
      "respuesta_correcta": "B",
      "tema": "SISTEMA_OPERATIVO",
      "confianza": 0.99,
      "dudosa": false,
      "motivo": "Respuesta única e inequívoca"
    }}
  ]
}}

Categorías permitidas:
{", ".join(sorted(CATEGORIAS))}

Reglas obligatorias:

- No inventes una respuesta.
- No corrijas silenciosamente el enunciado ni las opciones.
- `respuesta_correcta` solo puede ser A, B, C o D cuando `importar` sea true.
- Usa la categoría más general que siga siendo informativa.
- Si una pregunta es de Word, usa OFIMATICA_WORD.
- Si es de Excel, usa OFIMATICA_EXCEL.
- Si es de Windows o gestión de archivos, usa SISTEMA_OPERATIVO.
- Si es de componentes físicos, memoria o arquitectura, usa HARDWARE.
- Si es de Outlook, usa OUTLOOK.
- Si es de Teams, usa TEAMS.
- Si es de OneDrive, SharePoint o Microsoft 365 en general, usa MICROSOFT_365.
- Si es de Edge, cookies, caché o navegación, usa NAVEGADORES.
- Si trata de protección, amenazas, cifrado o control de privilegios,
  usa SEGURIDAD.
- Si trata de Copilot, prompts o modelos generativos,
  usa INTELIGENCIA_ARTIFICIAL.
- Si trata de protocolos, servidores o infraestructura de red, usa REDES.
- Si hay ambigüedad, más de una respuesta defendible, una opción correcta
  ausente, dependencia de versión/configuración no especificada o un defecto
  material en las opciones:
    importar = false
    dudosa = true
- Sé especialmente conservador con atajos de teclado dependientes del idioma,
  capacidades numéricas concretas y funciones que cambian entre versiones.
- La confianza debe ser un número entre 0 y 1.
- Solo marca `importar = true` cuando la respuesta sea inequívoca y la
  confianza sea al menos {CONFIANZA_MINIMA}.
- Devuelve exactamente un resultado por cada `numero_visible`.

Preguntas:
{json.dumps(preguntas_json, ensure_ascii=False)}
""".strip()

    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": instrucciones,
                }
            ],
        }
    ]


def limpiar_json_respuesta(texto: str) -> dict[str, Any]:
    texto = texto.strip()

    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)

    datos = json.loads(texto)

    if not isinstance(datos, dict):
        raise ValueError("La IA no devolvió un objeto JSON.")

    return datos


def analizar_lote(
    utilidad_openai,
    lote: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    entrada = construir_entrada_ia(lote)

    respuesta, _ = utilidad_openai.llamar_responses(
        input_api=entrada,
        modelo=MODELO,
        operacion=OPERACION_IA,
    )

    datos = limpiar_json_respuesta(respuesta.output_text)
    resultados = datos.get("resultados")

    if not isinstance(resultados, list):
        raise ValueError(
            "La respuesta de IA no contiene una lista `resultados`."
        )

    por_numero: dict[int, dict[str, Any]] = {}

    for resultado in resultados:
        if not isinstance(resultado, dict):
            continue

        try:
            numero = int(resultado.get("numero_visible"))
        except (TypeError, ValueError):
            continue

        por_numero[numero] = resultado

    esperados = {int(p["numero_visible"]) for p in lote}
    recibidos = set(por_numero)

    if esperados != recibidos:
        faltan = sorted(esperados - recibidos)
        sobran = sorted(recibidos - esperados)
        raise ValueError(
            "La IA no devolvió exactamente los números esperados. "
            f"Faltan={faltan}; sobran={sobran}"
        )

    return por_numero


# =============================================================================
# VALIDACIÓN DE RESPUESTA Y PREPARACIÓN DE REGISTRO
# =============================================================================

def normalizar_categoria(valor: Any) -> str | None:
    categoria = limpiar_texto(valor).upper()
    categoria = categoria.replace("Á", "A")
    categoria = categoria.replace("É", "E")
    categoria = categoria.replace("Í", "I")
    categoria = categoria.replace("Ó", "O")
    categoria = categoria.replace("Ú", "U")
    categoria = re.sub(r"[^A-Z0-9]+", "_", categoria).strip("_")

    equivalencias = {
        "WORD": "OFIMATICA_WORD",
        "EXCEL": "OFIMATICA_EXCEL",
        "OFIMATICA": "MICROSOFT_365",
        "WINDOWS": "SISTEMA_OPERATIVO",
        "SISTEMAS_OPERATIVOS": "SISTEMA_OPERATIVO",
        "MICROSOFT_TEAMS": "TEAMS",
        "MICROSOFT_OUTLOOK": "OUTLOOK",
        "EDGE": "NAVEGADORES",
        "IA": "INTELIGENCIA_ARTIFICIAL",
        "INTELIGENCIA_ARTIFICIAL_GENERATIVA": "INTELIGENCIA_ARTIFICIAL",
        "CIBERSEGURIDAD": "SEGURIDAD",
    }
    categoria = equivalencias.get(categoria, categoria)

    return categoria if categoria in CATEGORIAS else None


def validar_decision_ia(
    resultado: dict[str, Any],
) -> tuple[bool, str, str | None, str | None, float]:
    importar = resultado.get("importar") is True
    dudosa = resultado.get("dudosa") is True
    motivo = limpiar_texto(resultado.get("motivo"))

    try:
        confianza = float(resultado.get("confianza"))
    except (TypeError, ValueError):
        confianza = 0.0

    letra = limpiar_texto(
        resultado.get("respuesta_correcta")
    ).upper()
    tema = normalizar_categoria(resultado.get("tema"))

    if not importar:
        return False, motivo or "La IA decidió no importar", None, tema, confianza

    if dudosa:
        return False, motivo or "Respuesta marcada como dudosa", None, tema, confianza

    if confianza < CONFIANZA_MINIMA:
        return (
            False,
            f"Confianza insuficiente: {confianza:.2f}",
            None,
            tema,
            confianza,
        )

    if letra not in {"A", "B", "C", "D"}:
        return False, "Letra de respuesta no válida", None, tema, confianza

    if tema is None:
        return False, "Categoría temática no válida", None, None, confianza

    return True, motivo, letra, tema, confianza


def preparar_registro(
    pregunta: dict[str, Any],
    respuesta_correcta: str,
    tema: str,
    importacion_id: int,
) -> dict[str, Any]:
    return {
        "enunciado": pregunta["enunciado"],
        "opcion_a": pregunta["opcion_a"],
        "opcion_b": pregunta["opcion_b"],
        "opcion_c": pregunta["opcion_c"],
        "opcion_d": pregunta["opcion_d"],
        "respuesta_correcta": respuesta_correcta,
        "tipo_clasificacion": TIPO_CLASIFICACION,
        "tipo_norma": None,
        "nombre_norma": None,
        "articulo": None,
        "tema_no_juridico": tema,
        "origen_oposicion": None,
        "tipo_fuente": TIPO_FUENTE,
        "importacion_fichero_id": importacion_id,
        "pagina_origen": pregunta["pagina_origen"],
    }


# =============================================================================
# DUPLICADOS E INSERCIÓN
# =============================================================================

def procesar_pdf(
    ruta_pdf: Path,
    conexion: sqlite3.Connection,
    utilidad_openai,
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
        "duplicadas_internas": 0,
        "insertadas": 0,
        "duplicadas_banco": 0,
        "omitidas": 0,
        "errores": 0,
    }

    if debe_omitirse(importacion, forzar, hash_sha256):
        logging.info(
            "FICHERO OMITIDO | %s | ya importado | id=%s | hash=%s",
            ruta_pdf.name,
            importacion["id"],
            hash_sha256[:12],
        )
        return "SALTADO", totales

    documento = fitz.open(ruta_pdf)

    try:
        paginas_totales = documento.page_count
        totales["paginas"] = paginas_totales

        logging.info(
            (
                "INICIO PDF | archivo=%s | páginas=%d | forzar=%s"
            ),
            ruta_pdf.name,
            paginas_totales,
            forzar,
        )

        preguntas_extraidas, incidencias = extraer_preguntas(documento)
        totales["extraidas"] = len(preguntas_extraidas)

        for incidencia in incidencias:
            totales["omitidas"] += 1
            totales["errores"] += 1
            logging.warning(
                "%s | EXTRACCIÓN OMITIDA | %s",
                ruta_pdf.name,
                incidencia,
            )

        if incidencias:
            raise RuntimeError(
                "Importación rechazada antes de llamar a la IA: "
                f"{len(incidencias)} preguntas no extraíbles"
            )

        if utilidad_openai is None:
            utilidad_openai = cargar_utilidad_openai()

        preguntas, duplicadas = quitar_duplicados_internos(
            preguntas_extraidas
        )
        totales["duplicadas_internas"] = len(duplicadas)
        totales["omitidas"] += len(duplicadas)

        for duplicada in duplicadas:
            logging.info(
                (
                    "%s | número %s | DUPLICADA INTERNA | "
                    "coincide con número %s"
                ),
                ruta_pdf.name,
                duplicada["numero_visible"],
                duplicada["numero_original"],
            )

        registros: list[dict[str, Any]] = []
        for numero_lote, lote in enumerate(
            dividir_lotes(preguntas, TAMANO_LOTE_IA),
            start=1,
        ):
            try:
                logging.info(
                    "%s | LOTE IA %d | preguntas=%s",
                    ruta_pdf.name,
                    numero_lote,
                    [p["numero_visible"] for p in lote],
                )
                resultados = analizar_lote(utilidad_openai, lote)

            except Exception as exc:
                totales["errores"] += len(lote)
                logging.exception(
                    "%s | LOTE IA %d | ERROR | %s",
                    ruta_pdf.name,
                    numero_lote,
                    exc,
                )
                continue

            for pregunta in lote:
                numero = int(pregunta["numero_visible"])
                resultado = resultados[numero]

                try:
                    (
                        valida,
                        motivo,
                        letra,
                        tema,
                        confianza,
                    ) = validar_decision_ia(resultado)

                    if not valida or letra is None or tema is None:
                        totales["omitidas"] += 1
                        totales["errores"] += 1
                        logging.warning(
                            (
                                "%s | número %d | página %d | OMITIDA | "
                                "confianza=%.2f | %s"
                            ),
                            ruta_pdf.name,
                            numero,
                            pregunta["pagina_origen"],
                            confianza,
                            motivo,
                        )
                        continue

                    registro = preparar_registro(
                        pregunta,
                        letra,
                        tema,
                        0,
                    )
                    registros.append(registro)

                    logging.info(
                        (
                            "%s | número %d | página %d | VALIDADA | "
                            "respuesta=%s | tema=%s | "
                            "confianza=%.2f"
                        ),
                        ruta_pdf.name,
                        numero,
                        pregunta["pagina_origen"],
                        letra,
                        tema,
                        confianza,
                    )

                except Exception as exc:
                    totales["errores"] += 1
                    logging.exception(
                        "%s | número %d | ERROR | %s",
                        ruta_pdf.name,
                        numero,
                        exc,
                    )

        if totales["errores"]:
            raise RuntimeError(
                f"Importación rechazada: {totales['errores']} errores"
            )

        importacion_id, insertadas, duplicadas = publicar_importacion(
            conexion,
            raiz=RAIZ,
            ruta_pdf=ruta_pdf,
            hash_sha256=hash_sha256,
            tipo_importacion=TIPO_FUENTE,
            paginas_totales=paginas_totales,
            registros=registros,
            omitidas_previas=totales["omitidas"],
        )
        totales["insertadas"] = insertadas
        totales["duplicadas_banco"] = duplicadas
        totales["omitidas"] += duplicadas

        logging.info(
            (
                "FIN PDF | archivo=%s | importacion_id=%d | "
                "extraídas=%d | insertadas=%d | duplicadas_internas=%d | "
                "duplicadas_banco=%d | omitidas=%d | errores=%d"
            ),
            ruta_pdf.name,
            importacion_id,
            totales["extraidas"],
            totales["insertadas"],
            totales["duplicadas_internas"],
            totales["duplicadas_banco"],
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


# =============================================================================
# PROCESO PRINCIPAL
# =============================================================================

def main() -> int:
    configurar_logging()
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
        "extraidas": 0,
        "duplicadas_internas": 0,
        "insertadas": 0,
        "duplicadas_banco": 0,
        "omitidas": 0,
        "errores": 0,
    }

    modo = "PDF CONCRETO" if args.pdf else "CARPETA COMPLETA"

    logging.info(
        "INICIO IMPORTACIÓN | modo=%s | archivos=%d | forzar=%s",
        modo,
        len(rutas_pdf),
        args.forzar,
    )

    try:
        with sqlite3.connect(RUTA_DB) as conexion:
            conexion.row_factory = sqlite3.Row
            validar_base_datos(conexion)

            for ruta_pdf in rutas_pdf:
                try:
                    resultado, totales = procesar_pdf(
                        ruta_pdf,
                        conexion,
                        None,
                        args.forzar,
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
                        "duplicadas_internas",
                        "insertadas",
                        "duplicadas_banco",
                        "omitidas",
                        "errores",
                    ):
                        globales[clave] += totales[clave]

                except Exception as exc:
                    globales["pdf_error"] += 1
                    logging.exception(
                        "ERROR DE FICHERO | %s | %s",
                        ruta_pdf,
                        exc,
                    )

    except Exception as exc:
        logging.exception("Error general: %s", exc)
        return 1

    print()
    print("=" * 78)
    print("RESUMEN GENERAL")
    print("=" * 78)
    print(f"Modo:                       {modo}")
    if args.pdf:
        print(f"PDF solicitado:             {rutas_pdf[0]}")
    print(f"PDF encontrados:            {globales['pdf_encontrados']}")
    print(f"PDF procesados:             {globales['pdf_procesados']}")
    print(f"PDF ya importados:          {globales['pdf_saltados']}")
    print(f"PDF con error general:      {globales['pdf_error']}")
    print(f"Páginas:                    {globales['paginas']}")
    print(f"Preguntas extraídas:        {globales['extraidas']}")
    print(f"Duplicadas internas:        {globales['duplicadas_internas']}")
    print(f"Preguntas insertadas:       {globales['insertadas']}")
    print(f"Duplicadas en el banco:     {globales['duplicadas_banco']}")
    print(f"Preguntas omitidas:         {globales['omitidas']}")
    print(f"Errores:                    {globales['errores']}")
    print(f"Log:                        {RUTA_LOG}")
    print(f"Costes IA:                  {RUTA_COSTES}")

    return 1 if globales["pdf_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
