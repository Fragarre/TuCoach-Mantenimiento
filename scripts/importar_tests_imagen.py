"""
===============================================================================
Proyecto : OpoCoach
Tipo     : Importación de preguntas desde PDF-imagen
Archivo  : importar_tests_imagen.py
Ubicación:
    scripts/importar_tests_imagen.py

OBJETIVO
--------
Importar en `lote_preguntas` las preguntas contenidas en PDF de
`data_preguntas`, manteniendo trazabilidad de cada fichero mediante
`importaciones_ficheros`.

MODOS DE EJECUCIÓN
------------------
1. Importar todos los PDF de `data_preguntas`:

       python scripts/importar_tests_imagen.py

2. Importar únicamente un PDF concreto:

       python scripts/importar_tests_imagen.py --pdf C2_4.pdf

   También se admite una ruta relativa o absoluta:

       python scripts/importar_tests_imagen.py --pdf data_preguntas/C2_4.pdf

3. Forzar la reimportación de un PDF ya registrado:

       python scripts/importar_tests_imagen.py --pdf C2_4.pdf --forzar

   `--forzar` solo se admite junto con `--pdf`.

TRAZABILIDAD E IDEMPOTENCIA
---------------------------
1. Antes de abrir o enviar páginas a la IA se calcula el SHA-256 del PDF.

2. Cada PDF queda registrado en `importaciones_ficheros` con:
   - ruta relativa;
   - nombre;
   - hash SHA-256;
   - tipo de fuente;
   - estado;
   - páginas totales, insertadas, omitidas y con error;
   - fechas de inicio y fin;
   - último error.

3. Un fichero con el mismo hash y estado `COMPLETADO` se salta por completo:
   no se abre, no se recorren sus páginas y no se llama a la IA.

4. Un fichero se vuelve a procesar cuando:
   - se usa `--forzar`;
   - su fila tiene `reimportar = 1`;
   - no está completado;
   - o su contenido ha cambiado y, por tanto, tiene otro hash.

5. La extracción y validación se completan antes de modificar la base. La
   sustitución de una importación se realiza en una única transacción. Si
   falla, se conservan las preguntas anteriores y `reimportar` queda en 1.

6. Cada pregunta nueva guarda:
   - `importacion_fichero_id`;
   - `pagina_origen`.

7. La comprobación de pregunta duplicada se mantiene para evitar duplicar en
   el banco una pregunta idéntica que ya aparezca en otro fichero.

CRITERIOS DE IMPORTACIÓN
------------------------
1. Cada página puede contener varias preguntas y fragmentos de preguntas.

2. Los fragmentos consecutivos se reúnen antes de validar e insertar cada
   pregunta. El pie azul se captura de forma obligatoria para las jurídicas.

2. `origen_oposicion` se obtiene de los dos primeros caracteres del fichero:
   A1, A2, C1 o C2.

3. `tipo_fuente` es siempre `tests`.

4. Preguntas jurídicas:
   - solo se importan si el pie identifica expresamente norma y
     artículo/apartado;
   - no se deducen datos jurídicos ausentes.

5. Preguntas de informática:
   - si el pie contiene clasificación, se usa;
   - si no hay pie, se clasifica por el tema informático;
   - se importan sin tipo de norma, nombre de norma ni artículo.

6. Otras preguntas no jurídicas no se importan.

7. La respuesta correcta se obtiene exclusivamente del recuadro verde.

8. Un error no detiene los demás PDF, pero rechaza íntegramente el PDF
   afectado para impedir importaciones parciales.

9. La IA se llama mediante la utilidad existente `openai_api.py`.

10. El coste se registra en:
        registros/coste_ia.csv

11. El script exige que ya existan:
    - `lote_preguntas`;
    - `importaciones_ficheros`;
    - las columnas `importacion_fichero_id` y `pagina_origen`.

    El script no crea ni altera tablas.

DEPENDENCIAS
------------
    pip install pymupdf openai python-dotenv

===============================================================================
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
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


# =============================================================================
# RUTAS Y CONSTANTES
# =============================================================================

RUTA_SCRIPT = Path(__file__).resolve()
RAIZ = RUTA_SCRIPT.parent.parent

CARPETA_PDF = RAIZ / "data_preguntas"
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
RUTA_LOG = RAIZ / "logs" / "importar_tests_imagen.log"
RUTA_COSTES = RAIZ / "registros" / "coste_ia.csv"

TIPO_FUENTE = "tests"
MODELO = "gpt-5.4-nano"
OPERACION_IA = "importar_tests_imagen_multipregunta"

VERSION_SCRIPT = "2026-08-07-pdf-png-gofullpage-segmentado-v9"

# Las capturas GoFullPage pueden medir decenas de miles de píxeles de alto.
# Se dividen en tramos visuales antes de enviarlas a la IA para conservar
# un tamaño de texto legible. PyMuPDF representa los PNG a 72 dpi; en las
# capturas actuales 3000 puntos equivalen aproximadamente a 4000 píxeles.
ALTURA_TRAMO_PNG = 3000.0
MARCADOR_PROGRESO = "PROGRESO_IMPORTACION_IMAGEN|"

@dataclass(frozen=True)
class EntradaFuente:
    """Una importación lógica: un PDF o un grupo ordenado de capturas PNG."""

    ruta_traza: Path
    nombre_logico: str
    archivos: tuple[Path, ...]
    tipo: str  # "PDF" o "PNG"


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
            "Importa un PDF/PNG concreto o todos los PDF y grupos PNG de data_preguntas."
        )
    )
    parser.add_argument(
        "--pdf",
        help=(
            "Nombre o ruta del PDF/PNG que se desea procesar. "
            "Si se omite, se procesa toda la carpeta data_preguntas."
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
# SELECCIÓN DE PDF Y CAPTURAS PNG GOFULLPAGE
# =============================================================================

def _descomponer_nombre_png(ruta: Path) -> tuple[str, int]:
    """Devuelve (nombre lógico, orden) para nombre.png, nombre-2.png, ..."""
    coincidencia = re.fullmatch(r"(.+?)(?:-(\d+))?", ruta.stem)
    if coincidencia is None:
        return ruta.stem, 1
    base = coincidencia.group(1)
    sufijo = coincidencia.group(2)
    return base, int(sufijo) if sufijo is not None else 1


def _agrupar_pngs(rutas: list[Path]) -> list[EntradaFuente]:
    grupos: dict[str, list[tuple[int, Path]]] = {}
    for ruta in rutas:
        base, orden = _descomponer_nombre_png(ruta)
        grupos.setdefault(base.casefold(), []).append((orden, ruta.resolve()))

    entradas: list[EntradaFuente] = []
    for elementos in grupos.values():
        elementos.sort(key=lambda item: (item[0], item[1].name.casefold()))
        archivos = tuple(ruta for _, ruta in elementos)
        base, _ = _descomponer_nombre_png(archivos[0])
        entradas.append(
            EntradaFuente(
                ruta_traza=archivos[0],
                nombre_logico=f"{base}.png",
                archivos=archivos,
                tipo="PNG",
            )
        )

    entradas.sort(key=lambda entrada: entrada.nombre_logico.casefold())
    return entradas


def resolver_entrada_indicada(valor: str) -> EntradaFuente:
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
        if not resuelto.is_file():
            continue

        sufijo = resuelto.suffix.lower()
        if sufijo == ".pdf":
            return EntradaFuente(
                ruta_traza=resuelto,
                nombre_logico=resuelto.name,
                archivos=(resuelto,),
                tipo="PDF",
            )
        if sufijo == ".png":
            base, _ = _descomponer_nombre_png(resuelto)
            hermanos = [
                ruta.resolve()
                for ruta in resuelto.parent.iterdir()
                if ruta.is_file()
                and ruta.suffix.lower() == ".png"
                and _descomponer_nombre_png(ruta)[0].casefold() == base.casefold()
            ]
            grupos = _agrupar_pngs(hermanos)
            if grupos:
                return grupos[0]

        raise ValueError(
            f"El archivo indicado no es PDF ni PNG: {resuelto}"
        )

    rutas = "\n".join(f"  - {ruta.resolve()}" for ruta in candidatos)
    raise FileNotFoundError(
        f"No se encuentra el archivo indicado: {valor}\n"
        f"Rutas comprobadas:\n{rutas}"
    )


def obtener_entradas(valor_indicado: str | None) -> list[EntradaFuente]:
    if valor_indicado:
        return [resolver_entrada_indicada(valor_indicado)]

    if not CARPETA_PDF.is_dir():
        raise FileNotFoundError(
            f"No existe la carpeta de entrada: {CARPETA_PDF}"
        )

    pdfs = sorted(
        (
            ruta.resolve()
            for ruta in CARPETA_PDF.iterdir()
            if ruta.is_file() and ruta.suffix.lower() == ".pdf"
        ),
        key=lambda ruta: ruta.name.casefold(),
    )
    pngs = [
        ruta.resolve()
        for ruta in CARPETA_PDF.iterdir()
        if ruta.is_file() and ruta.suffix.lower() == ".png"
    ]

    entradas = [
        EntradaFuente(
            ruta_traza=ruta,
            nombre_logico=ruta.name,
            archivos=(ruta,),
            tipo="PDF",
        )
        for ruta in pdfs
    ]
    entradas.extend(_agrupar_pngs(pngs))
    entradas.sort(key=lambda entrada: entrada.nombre_logico.casefold())

    if not entradas:
        logging.info(
            "SIN PDF/PNG PENDIENTES | carpeta=%s",
            CARPETA_PDF,
        )

    return entradas


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
# IDENTIFICACIÓN DEL FICHERO
# =============================================================================

def obtener_origen(nombre_archivo: str) -> str:
    # Admite tanto A1_01.pdf como nombres de GoFullPage del tipo
    # screencapture-...-Simulacro-A1-GVA-....png
    coincidencia = re.search(
        r"(?<![A-Z0-9])(A1|A2|C1|C2)(?![A-Z0-9])",
        nombre_archivo,
        re.IGNORECASE,
    )
    if coincidencia is None:
        raise ValueError(
            "No se puede identificar A1, A2, C1 o C2 en el nombre: "
            f"{nombre_archivo}"
        )
    return coincidencia.group(1).upper()


def calcular_sha256_entrada(entrada: EntradaFuente) -> str:
    digest = hashlib.sha256()
    # La versión de segmentación forma parte del hash lógico de los PNG.
    # Así, las capturas ya procesadas por la versión defectuosa (que enviaba
    # un GoFullPage completo a la IA) se vuelven a procesar una sola vez.
    if entrada.tipo == "PNG":
        digest.update(b"gofullpage-segmentado-v9\0")
    for ruta in entrada.archivos:
        digest.update(ruta.name.encode("utf-8"))
        digest.update(b"\0")
        with ruta.open("rb") as fichero:
            while bloque := fichero.read(1024 * 1024):
                digest.update(bloque)
        digest.update(b"\0")
    return digest.hexdigest()


def renderizar_pagina(pagina: fitz.Page) -> bytes:
    matriz = fitz.Matrix(2.0, 2.0)
    pixmap = pagina.get_pixmap(matrix=matriz, alpha=False)
    return pixmap.tobytes("png")


def construir_entrada_ia(
    imagen_png: bytes,
    numero_pagina: int,
) -> list[dict]:
    imagen_b64 = base64.b64encode(imagen_png).decode("ascii")

    instrucciones = f"""
Analiza la imagen de la página {numero_pagina} de un test que puede contener
varias preguntas y también fragmentos de preguntas iniciadas en la página
anterior o continuadas en la siguiente.

Debes transcribir únicamente lo que se ve. No completes, no deduzcas y no
corrijas contenidos jurídicos.

El PIE AZUL situado debajo de cada pregunta es fundamental. Debes copiarlo
literalmente y usar exclusivamente ese pie para obtener la norma y el
artículo/apartado. No confundas el pie azul con el encabezado de materia que
aparece sobre un grupo de preguntas.

Devuelve exclusivamente un objeto JSON válido, sin Markdown, con esta forma:

{{
  "fragmentos": [
    {{
      "numero_visible": "",
      "continuacion_de_pagina_anterior": false,
      "enunciado_fragmento": "",
      "opcion_a_fragmento": "",
      "opcion_b_fragmento": "",
      "opcion_c_fragmento": "",
      "opcion_d_fragmento": "",
      "respuesta_correcta": "",
      "clase": "JURIDICA",
      "pie_literal": "",
      "tipo_norma": "",
      "nombre_norma": "",
      "articulo_apartado": "",
      "tema_informatica": ""
    }}
  ]
}}

Reglas obligatorias:

1. Devuelve un elemento por cada pregunta completa o fragmento de pregunta que
   aparezca en la página, respetando estrictamente el orden vertical.

2. Si la parte superior contiene la continuación de una pregunta iniciada en
   la página anterior y su número no es visible:
   - deja "numero_visible" vacío;
   - usa "continuacion_de_pagina_anterior": true;
   - transcribe solo las partes visibles.

3. Si se ve el número de la pregunta, cópialo en "numero_visible" y usa
   "continuacion_de_pagina_anterior": false, aunque la pregunta continúe en la
   página siguiente.

4. Cada campo terminado en "_fragmento" debe contener solo el texto visible
   correspondiente. Si una opción está dividida entre dos páginas, copia en
   cada página únicamente el tramo visible; posteriormente el programa unirá
   ambos fragmentos.

5. "respuesta_correcta" debe ser exclusivamente la letra cuyo recuadro está
   relleno de verde. Si el recuadro verde no aparece en esta página, déjala
   vacía. No resuelvas la pregunta por conocimientos propios.

6. Copia en "pie_literal" el texto azul situado inmediatamente debajo de la
   pregunta. Si el pie no se ve en esta página, deja el campo vacío. No uses el
   encabezado de sección como pie.

7. "clase" solo puede ser "JURIDICA", "INFORMATICA" u "OTRA". Cuando solo
   aparezca un fragmento insuficiente para clasificarlo con seguridad, deja
   "clase" vacía; se completará al unir las páginas.

8. Para una pregunta jurídica:
   - "tipo_norma", "nombre_norma" y "articulo_apartado" deben proceder
     exclusivamente del pie azul;
   - no uses el enunciado, las opciones ni el encabezado para suplirlos;
   - conserva vacío cualquier dato que no aparezca expresamente en el pie.

9. Para informática:
   - si el pie azul contiene clasificación, cópiala en "tema_informatica";
   - si no existe pie, indica el tema informático visible;
   - deja vacíos tipo_norma, nombre_norma y articulo_apartado.

10. No incluyas botones, etiquetas naranjas, iconos de ayuda, puntuaciones,
    encabezados de la aplicación ni otros elementos ajenos a las preguntas.

11. Si la página no contiene ninguna pregunta ni fragmento, devuelve
    "fragmentos": [].
""".strip()

    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": instrucciones},
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{imagen_b64}",
                },
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


def analizar_pagina(
    utilidad_openai,
    imagen_png: bytes,
    numero_pagina: int,
) -> list[dict[str, Any]]:
    entrada = construir_entrada_ia(imagen_png, numero_pagina)

    respuesta, _ = utilidad_openai.llamar_responses(
        input_api=entrada,
        modelo=MODELO,
        operacion=OPERACION_IA,
    )

    datos = limpiar_json_respuesta(respuesta.output_text)
    fragmentos = datos.get("fragmentos")

    if not isinstance(fragmentos, list):
        raise ValueError("La IA no devolvió una lista 'fragmentos'.")

    for indice, fragmento in enumerate(fragmentos, start=1):
        if not isinstance(fragmento, dict):
            raise ValueError(
                f"El fragmento {indice} de la página no es un objeto JSON."
            )

    return fragmentos


# =============================================================================
# NORMALIZACIÓN Y DECISIÓN DE IMPORTACIÓN
# =============================================================================

def texto_limpio(valor: Any) -> str:
    if valor is None:
        return ""

    return re.sub(r"\s+", " ", str(valor).strip())


def normalizar_tipo_norma(valor: str) -> str | None:
    valor = texto_limpio(valor)

    if not valor:
        return None

    valor = valor.upper()
    valor = re.sub(r"[^A-ZÁÉÍÓÚÜÑ0-9]+", "_", valor)
    return valor.strip("_") or None


def normalizar_fragmento(
    datos: dict[str, Any],
    numero_pagina: int,
    orden_pagina: int,
) -> dict[str, Any]:
    campos_texto = (
        "numero_visible",
        "enunciado_fragmento",
        "opcion_a_fragmento",
        "opcion_b_fragmento",
        "opcion_c_fragmento",
        "opcion_d_fragmento",
        "respuesta_correcta",
        "clase",
        "pie_literal",
        "tipo_norma",
        "nombre_norma",
        "articulo_apartado",
        "tema_informatica",
    )

    resultado = {
        campo: texto_limpio(datos.get(campo))
        for campo in campos_texto
    }
    resultado["continuacion_de_pagina_anterior"] = (
        datos.get("continuacion_de_pagina_anterior") is True
    )
    resultado["pagina"] = numero_pagina
    resultado["orden_pagina"] = orden_pagina
    resultado["respuesta_correcta"] = resultado["respuesta_correcta"].upper()
    resultado["clase"] = resultado["clase"].upper()

    if (
        resultado["respuesta_correcta"]
        and resultado["respuesta_correcta"] not in {"A", "B", "C", "D"}
    ):
        raise ValueError(
            f"Respuesta correcta no válida en página {numero_pagina}, "
            f"fragmento {orden_pagina}."
        )

    if resultado["clase"] not in {"", "JURIDICA", "INFORMATICA", "OTRA"}:
        raise ValueError(
            f"Clase no reconocida en página {numero_pagina}, "
            f"fragmento {orden_pagina}: {resultado['clase']!r}"
        )

    tiene_contenido = any(
        resultado[campo]
        for campo in (
            "numero_visible",
            "enunciado_fragmento",
            "opcion_a_fragmento",
            "opcion_b_fragmento",
            "opcion_c_fragmento",
            "opcion_d_fragmento",
            "respuesta_correcta",
            "pie_literal",
        )
    )
    if not tiene_contenido:
        raise ValueError(
            f"Fragmento vacío en página {numero_pagina}, "
            f"posición {orden_pagina}."
        )

    return resultado


def unir_texto(existente: str, nuevo: str) -> str:
    existente = texto_limpio(existente)
    nuevo = texto_limpio(nuevo)

    if not nuevo:
        return existente
    if not existente:
        return nuevo
    if nuevo == existente:
        return existente
    if nuevo in existente:
        return existente
    if existente in nuevo:
        return nuevo

    return f"{existente} {nuevo}".strip()


def crear_acumulador(
    fragmento: dict[str, Any],
) -> dict[str, Any]:
    return {
        "numero_visible": fragmento["numero_visible"],
        "enunciado": fragmento["enunciado_fragmento"],
        "opcion_a": fragmento["opcion_a_fragmento"],
        "opcion_b": fragmento["opcion_b_fragmento"],
        "opcion_c": fragmento["opcion_c_fragmento"],
        "opcion_d": fragmento["opcion_d_fragmento"],
        "respuesta_correcta": fragmento["respuesta_correcta"],
        "clase": fragmento["clase"],
        "pie_literal": fragmento["pie_literal"],
        "tipo_norma": fragmento["tipo_norma"],
        "nombre_norma": fragmento["nombre_norma"],
        "articulo_apartado": fragmento["articulo_apartado"],
        "tema_informatica": fragmento["tema_informatica"],
        "pagina_origen": fragmento["pagina"],
        "pagina_final": fragmento["pagina"],
    }


def agregar_fragmento(
    acumulador: dict[str, Any],
    fragmento: dict[str, Any],
) -> None:
    correspondencias = {
        "enunciado": "enunciado_fragmento",
        "opcion_a": "opcion_a_fragmento",
        "opcion_b": "opcion_b_fragmento",
        "opcion_c": "opcion_c_fragmento",
        "opcion_d": "opcion_d_fragmento",
        "pie_literal": "pie_literal",
    }

    for destino, origen in correspondencias.items():
        acumulador[destino] = unir_texto(
            acumulador.get(destino, ""),
            fragmento.get(origen, ""),
        )

    for campo in (
        "respuesta_correcta",
        "clase",
        "tipo_norma",
        "nombre_norma",
        "articulo_apartado",
        "tema_informatica",
    ):
        nuevo = texto_limpio(fragmento.get(campo))
        actual = texto_limpio(acumulador.get(campo))

        if nuevo and actual and nuevo != actual:
            raise ValueError(
                f"Datos incompatibles al unir la pregunta "
                f"{acumulador.get('numero_visible') or '(sin número)'}: "
                f"campo {campo}."
            )
        if nuevo:
            acumulador[campo] = nuevo

    acumulador["pagina_final"] = max(
        int(acumulador["pagina_final"]),
        int(fragmento["pagina"]),
    )


def reunir_preguntas(
    fragmentos_paginas: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Reconstruye las preguntas por orden documental.

    Una pregunta comienza cuando aparece un número visible y termina justo
    antes del siguiente número visible. Los fragmentos sin número marcados
    como continuación se añaden únicamente a la pregunta inmediatamente
    anterior. No se agrupan preguntas por un diccionario global de números.
    """
    preguntas: list[dict[str, Any]] = []
    errores: list[str] = []
    actual: dict[str, Any] | None = None

    for fragmento in fragmentos_paginas:
        numero = texto_limpio(fragmento["numero_visible"])
        es_continuacion = fragmento["continuacion_de_pagina_anterior"]

        try:
            if numero:
                actual = crear_acumulador(fragmento)
                preguntas.append(actual)
                continue

            if es_continuacion:
                if actual is None:
                    raise ValueError(
                        "Se encontró una continuación sin una pregunta "
                        "anterior a la que asociarla."
                    )
                agregar_fragmento(actual, fragmento)
                continue

            raise ValueError(
                "Fragmento sin número que no está marcado como continuación."
            )

        except Exception as exc:
            errores.append(
                f"Página {fragmento['pagina']}, fragmento "
                f"{fragmento['orden_pagina']}: {exc}"
            )

    return preguntas, errores


def numero_entero(valor: Any) -> int | None:
    texto = texto_limpio(valor)
    coincidencia = re.fullmatch(r"(\d+)", texto)
    return int(coincidencia.group(1)) if coincidencia else None


def detectar_numeros_ausentes(
    preguntas: list[dict[str, Any]],
) -> list[int]:
    numeros = sorted(
        {numero for pregunta in preguntas
         if (numero := numero_entero(pregunta.get("numero_visible"))) is not None}
    )
    if len(numeros) < 2:
        return []
    return [
        numero
        for numero in range(numeros[0], numeros[-1] + 1)
        if numero not in numeros
    ]


def paginas_candidatas_para_numero(
    numero_objetivo: int,
    preguntas: list[dict[str, Any]],
    paginas_totales: int,
) -> list[int]:
    anteriores = []
    posteriores = []

    for pregunta in preguntas:
        numero = numero_entero(pregunta.get("numero_visible"))
        if numero is None:
            continue
        if numero < numero_objetivo:
            anteriores.append((numero, int(pregunta["pagina_final"])))
        elif numero > numero_objetivo:
            posteriores.append((numero, int(pregunta["pagina_origen"])))

    paginas: set[int] = set()
    if anteriores:
        _, pagina = max(anteriores, key=lambda item: item[0])
        paginas.update({pagina, pagina + 1})
    if posteriores:
        _, pagina = min(posteriores, key=lambda item: item[0])
        paginas.update({pagina - 1, pagina})

    return sorted(
        pagina for pagina in paginas if 1 <= pagina <= paginas_totales
    )


def construir_entrada_recuperacion(
    imagenes_paginas: list[tuple[int, bytes]],
    numero_pregunta: str,
) -> list[dict]:
    contenido: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": f"""
Las imágenes adjuntas son páginas consecutivas de un test. Extrae únicamente
la pregunta número {numero_pregunta}. Puede comenzar en una página y terminar
en la siguiente.

Debes copiar literalmente el enunciado completo, las opciones A, B, C y D, la
letra marcada por el recuadro verde y el pie azul situado debajo de esa
pregunta. El pie azul es obligatorio para obtener norma y artículo. No uses el
encabezado de materia ni conocimientos propios.

Devuelve exclusivamente JSON válido con esta forma:
{{
  "numero_visible": "{numero_pregunta}",
  "enunciado": "",
  "opcion_a": "",
  "opcion_b": "",
  "opcion_c": "",
  "opcion_d": "",
  "respuesta_correcta": "",
  "clase": "JURIDICA",
  "pie_literal": "",
  "tipo_norma": "",
  "nombre_norma": "",
  "articulo_apartado": "",
  "tema_informatica": ""
}}

Si la pregunta no aparece completa en las imágenes, deja vacíos únicamente
los campos realmente no visibles; no inventes nada.
""".strip(),
        }
    ]

    for numero_pagina, imagen_png in imagenes_paginas:
        contenido.append({
            "type": "input_text",
            "text": f"Página PDF {numero_pagina}",
        })
        contenido.append({
            "type": "input_image",
            "image_url": (
                "data:image/png;base64,"
                + base64.b64encode(imagen_png).decode("ascii")
            ),
        })

    return [{"role": "user", "content": contenido}]


def recuperar_pregunta(
    utilidad_openai,
    imagenes_paginas: list[tuple[int, bytes]],
    numero_pregunta: str,
) -> dict[str, Any]:
    entrada = construir_entrada_recuperacion(
        imagenes_paginas,
        numero_pregunta,
    )
    respuesta, _ = utilidad_openai.llamar_responses(
        input_api=entrada,
        modelo=MODELO,
        operacion=f"{OPERACION_IA}_recuperar",
    )
    datos = limpiar_json_respuesta(respuesta.output_text)

    campos = (
        "numero_visible", "enunciado", "opcion_a", "opcion_b",
        "opcion_c", "opcion_d", "respuesta_correcta", "clase",
        "pie_literal", "tipo_norma", "nombre_norma",
        "articulo_apartado", "tema_informatica",
    )
    resultado = {campo: texto_limpio(datos.get(campo)) for campo in campos}
    resultado["numero_visible"] = (
        resultado["numero_visible"] or texto_limpio(numero_pregunta)
    )
    resultado["respuesta_correcta"] = resultado["respuesta_correcta"].upper()
    resultado["clase"] = resultado["clase"].upper()
    resultado["pagina_origen"] = min(pagina for pagina, _ in imagenes_paginas)
    resultado["pagina_final"] = max(pagina for pagina, _ in imagenes_paginas)
    return resultado


def pregunta_necesita_recuperacion(pregunta: dict[str, Any]) -> bool:
    try:
        validar_pregunta_reunida(dict(pregunta))
        return False
    except ValueError:
        return True

def validar_pregunta_reunida(
    datos: dict[str, Any],
) -> dict[str, Any]:
    numero = datos.get("numero_visible") or "(sin número)"

    for campo in (
        "enunciado",
        "opcion_a",
        "opcion_b",
        "opcion_c",
        "opcion_d",
    ):
        if not texto_limpio(datos.get(campo)):
            raise ValueError(
                f"Pregunta {numero}: falta el campo {campo}."
            )

    if datos.get("respuesta_correcta") not in {"A", "B", "C", "D"}:
        raise ValueError(
            f"Pregunta {numero}: no se identificó el recuadro verde."
        )

    clase = texto_limpio(datos.get("clase")).upper()
    if clase not in {"JURIDICA", "INFORMATICA", "OTRA"}:
        raise ValueError(
            f"Pregunta {numero}: clase no reconocida: {clase!r}."
        )
    datos["clase"] = clase

    if clase == "JURIDICA":
        if not texto_limpio(datos.get("pie_literal")):
            raise ValueError(
                f"Pregunta {numero}: no se captó el pie azul."
            )
        if not texto_limpio(datos.get("nombre_norma")):
            raise ValueError(
                f"Pregunta {numero}: el pie azul no proporcionó la norma."
            )
        if not texto_limpio(datos.get("articulo_apartado")):
            raise ValueError(
                f"Pregunta {numero}: el pie azul no proporcionó el "
                "artículo/apartado."
            )

    return datos


def preparar_registro(
    datos: dict[str, Any],
    origen: str,
    importacion_id: int,
    pagina_origen: int,
) -> tuple[dict[str, Any] | None, str]:
    clase = datos["clase"]

    comunes = {
        "enunciado": datos["enunciado"],
        "opcion_a": datos["opcion_a"],
        "opcion_b": datos["opcion_b"],
        "opcion_c": datos["opcion_c"],
        "opcion_d": datos["opcion_d"],
        "respuesta_correcta": datos["respuesta_correcta"],
        "origen_oposicion": origen,
        "tipo_fuente": TIPO_FUENTE,
        "importacion_fichero_id": importacion_id,
        "pagina_origen": pagina_origen,
    }

    if clase == "JURIDICA":
        norma = datos["nombre_norma"]
        articulo = datos["articulo_apartado"]

        if not norma or not articulo:
            return None, (
                "Pregunta jurídica sin norma y artículo/apartado "
                "expresos en el pie"
            )

        return {
            **comunes,
            "tipo_clasificacion": "JURIDICA",
            "tipo_norma": normalizar_tipo_norma(datos["tipo_norma"]),
            "nombre_norma": norma,
            "articulo": articulo,
            "tema_no_juridico": None,
        }, ""

    if clase == "INFORMATICA":
        tema = datos["tema_informatica"]

        if not tema:
            return None, (
                "Pregunta de informática sin clasificación ni tema legible"
            )

        return {
            **comunes,
            "tipo_clasificacion": "INFORMATICA",
            "tipo_norma": None,
            "nombre_norma": None,
            "articulo": None,
            "tema_no_juridico": tema,
        }, ""

    return None, "Pregunta no jurídica y no informática"


# =============================================================================
# PREGUNTAS: DUPLICADOS E INSERCIÓN
# =============================================================================

def procesar_entrada(
    entrada: EntradaFuente,
    conexion: sqlite3.Connection,
    utilidad_openai,
    forzar: bool,
) -> tuple[str, dict[str, int]]:
    ruta_pdf = entrada.ruta_traza
    nombre_fuente = entrada.nombre_logico
    hash_sha256 = calcular_sha256_entrada(entrada)
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

    estado_registrado = ""
    if importacion is not None:
        estado_registrado = str(importacion["estado"] or "").strip().upper()

    logging.info(
        (
            "CONTROL IDEMPOTENCIA | archivo=%s | hash=%s | "
            "registro=%s | estado=%r | reimportar=%s | forzar=%s"
        ),
        ruta_pdf.name,
        hash_sha256[:12],
        None if importacion is None else importacion["id"],
        estado_registrado,
        bool(importacion is not None and int(importacion["reimportar"] or 0) == 1),
        forzar,
    )

    if debe_omitirse(importacion, forzar, hash_sha256):
        logging.info(
            (
                "FICHERO OMITIDO SIN ABRIR | %s | id=%s | "
                "estado=%s | hash=%s"
            ),
            ruta_pdf.name,
            importacion["id"],
            estado_registrado or "(VACÍO)",
            hash_sha256[:12],
        )
        return "SALTADO", totales

    documento: fitz.Document | None = None
    documentos_png: list[fitz.Document] = []
    tramos_png: list[tuple[int, fitz.Rect, int, int, int]] = []

    if entrada.tipo == "PDF":
        documento = fitz.open(ruta_pdf)
        paginas_totales = documento.page_count
    else:
        # Cada GoFullPage se abre como una imagen de una sola página y se corta
        # verticalmente en tramos de tamaño legible. No hay solapamiento: si una
        # pregunta cruza el corte, la lógica existente de fragmentos entre páginas
        # reconstruye su continuación.
        for indice_captura, ruta_png in enumerate(entrada.archivos, start=1):
            doc_png = fitz.open(ruta_png)
            documentos_png.append(doc_png)
            pagina_png = doc_png.load_page(0)
            rect = pagina_png.rect
            total_tramos = max(1, int((rect.height + ALTURA_TRAMO_PNG - 1) // ALTURA_TRAMO_PNG))
            for indice_tramo in range(total_tramos):
                y0 = indice_tramo * ALTURA_TRAMO_PNG
                y1 = min(rect.height, y0 + ALTURA_TRAMO_PNG)
                tramos_png.append((
                    indice_captura - 1,
                    fitz.Rect(rect.x0, y0, rect.x1, y1),
                    indice_captura,
                    indice_tramo + 1,
                    total_tramos,
                ))
        paginas_totales = len(tramos_png)

    def descripcion_fuente(numero_pagina: int) -> str:
        if entrada.tipo == "PNG":
            _, _, captura, tramo, total_tramos = tramos_png[numero_pagina - 1]
            return (
                f"{nombre_fuente} | captura {captura}/{len(entrada.archivos)} "
                f"| tramo {tramo}/{total_tramos}"
            )
        return f"{nombre_fuente} | página {numero_pagina}/{paginas_totales}"

    def obtener_imagen_fuente(numero_pagina: int) -> bytes:
        if entrada.tipo == "PNG":
            indice_doc, clip, _, _, _ = tramos_png[numero_pagina - 1]
            pagina_png = documentos_png[indice_doc].load_page(0)
            pixmap = pagina_png.get_pixmap(clip=clip, alpha=False)
            return pixmap.tobytes("png")
        assert documento is not None
        pagina = documento.load_page(numero_pagina - 1)
        return renderizar_pagina(pagina)

    try:
        origen = obtener_origen(nombre_fuente)

        logging.info(
            (
                "INICIO PDF | archivo=%s | ruta=%s | páginas=%d | "
                "origen=%s | forzar=%s"
            ),
            ruta_pdf.name,
            ruta_pdf,
            paginas_totales,
            origen,
            forzar,
        )

        fragmentos_totales: list[dict[str, Any]] = []

        # Fase 1: extracción visual de todas las páginas. No se inserta todavía,
        # porque una pregunta puede continuar en la página siguiente.
        for indice in range(paginas_totales):
            numero_pagina = indice + 1
            totales["paginas"] += 1

            try:
                print(
                    MARCADOR_PROGRESO
                    + f"Procesando imagen {numero_pagina}/{paginas_totales} - "
                    + descripcion_fuente(numero_pagina),
                    flush=True,
                )
                imagen = obtener_imagen_fuente(numero_pagina)
                fragmentos_brutos = analizar_pagina(
                    utilidad_openai,
                    imagen,
                    numero_pagina,
                )

                validos_pagina = 0
                for orden, fragmento_bruto in enumerate(
                    fragmentos_brutos,
                    start=1,
                ):
                    try:
                        fragmento = normalizar_fragmento(
                            fragmento_bruto,
                            numero_pagina,
                            orden,
                        )
                        fragmentos_totales.append(fragmento)
                        validos_pagina += 1
                    except Exception as exc:
                        totales["errores"] += 1
                        logging.exception(
                            "%s | página %d | fragmento %d | ERROR | %s",
                            ruta_pdf.name,
                            numero_pagina,
                            orden,
                            exc,
                        )

                logging.info(
                    "%s | página %d | FRAGMENTOS EXTRAÍDOS=%d",
                    ruta_pdf.name,
                    numero_pagina,
                    validos_pagina,
                )

            except Exception as exc:
                totales["errores"] += 1
                logging.exception(
                    "%s | página %d | ERROR DE EXTRACCIÓN | %s",
                    ruta_pdf.name,
                    numero_pagina,
                    exc,
                )

        # Fase 2: unión de fragmentos, incluida la continuidad entre páginas.
        preguntas_reunidas, errores_union = reunir_preguntas(
            fragmentos_totales
        )
        for error in errores_union:
            totales["errores"] += 1
            logging.error("%s | ERROR DE UNIÓN | %s", ruta_pdf.name, error)

        logging.info(
            "%s | PREGUNTAS REUNIDAS=%d | fragmentos=%d",
            ruta_pdf.name,
            len(preguntas_reunidas),
            len(fragmentos_totales),
        )

        # Fase 2B: recuperación selectiva. Solo se vuelve a consultar la IA
        # para números ausentes o preguntas incompletas. El resto no se toca.
        por_pagina_imagen: dict[int, bytes] = {}

        def obtener_imagen(numero_pagina: int) -> bytes:
            if numero_pagina not in por_pagina_imagen:
                por_pagina_imagen[numero_pagina] = obtener_imagen_fuente(numero_pagina)
            return por_pagina_imagen[numero_pagina]

        numeros_ausentes = detectar_numeros_ausentes(preguntas_reunidas)
        if numeros_ausentes:
            logging.warning(
                "%s | NÚMEROS AUSENTES=%s",
                ruta_pdf.name,
                ",".join(str(numero) for numero in numeros_ausentes),
            )

        for numero in numeros_ausentes:
            paginas = paginas_candidatas_para_numero(
                numero, preguntas_reunidas, paginas_totales
            )
            if not paginas:
                totales["errores"] += 1
                logging.error(
                    "%s | pregunta %d | RECUPERACIÓN IMPOSIBLE | "
                    "sin páginas candidatas",
                    ruta_pdf.name, numero,
                )
                continue
            try:
                recuperada = recuperar_pregunta(
                    utilidad_openai,
                    [(pagina, obtener_imagen(pagina)) for pagina in paginas],
                    str(numero),
                )
                validar_pregunta_reunida(recuperada)
                preguntas_reunidas.append(recuperada)
                logging.info(
                    "%s | pregunta %d | RECUPERADA | páginas=%s",
                    ruta_pdf.name, numero, paginas,
                )
            except Exception as exc:
                totales["errores"] += 1
                logging.exception(
                    "%s | pregunta %d | ERROR DE RECUPERACIÓN | %s",
                    ruta_pdf.name, numero, exc,
                )

        for indice, pregunta in enumerate(list(preguntas_reunidas)):
            if not pregunta_necesita_recuperacion(pregunta):
                continue

            numero = texto_limpio(pregunta.get("numero_visible"))
            paginas = list(range(
                max(1, int(pregunta["pagina_origen"]) - 1),
                min(paginas_totales, int(pregunta["pagina_final"]) + 1) + 1,
            ))
            try:
                recuperada = recuperar_pregunta(
                    utilidad_openai,
                    [(pagina, obtener_imagen(pagina)) for pagina in paginas],
                    numero,
                )
                validar_pregunta_reunida(recuperada)
                preguntas_reunidas[indice] = recuperada
                logging.info(
                    "%s | pregunta %s | RECUPERADA TRAS VALIDACIÓN | "
                    "páginas=%s",
                    ruta_pdf.name, numero, paginas,
                )
            except Exception as exc:
                logging.warning(
                    "%s | pregunta %s | NO RECUPERADA | %s",
                    ruta_pdf.name, numero, exc,
                )

        preguntas_reunidas.sort(
            key=lambda pregunta: (
                numero_entero(pregunta.get("numero_visible")) is None,
                numero_entero(pregunta.get("numero_visible")) or 0,
            )
        )

        # Fase 3: validación e inserción. Duplicados y estructura de la tabla
        # se mantienen exactamente como en el importador anterior.
        registros: list[dict[str, Any]] = []
        for pregunta in preguntas_reunidas:
            numero_visible = pregunta.get("numero_visible") or "(sin número)"
            pagina_origen = int(pregunta["pagina_origen"])
            pagina_final = int(pregunta["pagina_final"])

            try:
                datos = validar_pregunta_reunida(pregunta)

                registro, motivo = preparar_registro(
                    datos,
                    origen,
                    0,
                    pagina_origen,
                )

                if registro is None:
                    totales["omitidas"] += 1
                    logging.warning(
                        (
                            "%s | pregunta %s | páginas %d-%d | "
                            "OMITIDA | %s"
                        ),
                        ruta_pdf.name,
                        numero_visible,
                        pagina_origen,
                        pagina_final,
                        motivo,
                    )
                    continue

                registros.append(registro)
                logging.info(
                    (
                        "%s | pregunta %s | páginas %d-%d | VALIDADA | "
                        "clase=%s | pie=%s"
                    ),
                    ruta_pdf.name,
                    numero_visible,
                    pagina_origen,
                    pagina_final,
                    registro["tipo_clasificacion"],
                    datos.get("pie_literal", ""),
                )

            except Exception as exc:
                totales["errores"] += 1
                logging.exception(
                    (
                        "%s | pregunta %s | páginas %d-%d | "
                        "ERROR | %s"
                    ),
                    ruta_pdf.name,
                    numero_visible,
                    pagina_origen,
                    pagina_final,
                    exc,
                )

        # Los errores de extracción de preguntas concretas no rechazan el PDF.
        # Se publican de forma atómica todos los registros válidos obtenidos y
        # los errores parciales quedan reflejados en la trazabilidad y el log.
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
        totales["duplicadas"] = duplicadas
        totales["omitidas"] += duplicadas

        # publicar_importacion finaliza la fila como COMPLETADO. Conservamos
        # ese estado para mantener la idempotencia, pero registramos el número
        # de errores parciales detectados durante la extracción.
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
                        f"Importación completada con "
                        f"{totales['errores']} errores parciales de extracción"
                    ),
                    importacion_id,
                ),
            )
            conexion.commit()

        logging.info(
            (
                "FIN PDF | archivo=%s | importacion_id=%d | "
                "páginas=%d | insertadas=%d | duplicadas=%d | "
                "omitidas=%d | errores=%d"
            ),
            ruta_pdf.name,
            importacion_id,
            totales["paginas"],
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
        if documento is not None:
            documento.close()
        for documento_png in documentos_png:
            documento_png.close()


# =============================================================================
# PROCESO PRINCIPAL
# =============================================================================

def main() -> int:
    configurar_logging()
    logging.info("VERSIÓN SCRIPT | %s | archivo=%s", VERSION_SCRIPT, RUTA_SCRIPT)
    args = leer_argumentos()

    if not RUTA_DB.is_file():
        logging.error("No existe la base de datos: %s", RUTA_DB)
        return 1

    try:
        entradas = obtener_entradas(args.pdf)
        # No se carga la utilidad de IA si no hay ningún PDF que procesar.
        # Así una carpeta vacía termina correctamente incluso aunque la IA
        # no vaya a utilizarse en esta ejecución.
        utilidad_openai = cargar_utilidad_openai() if entradas else None
    except Exception as exc:
        logging.exception("Error de configuración: %s", exc)
        return 1

    globales = {
        "pdf_encontrados": len(entradas),
        "pdf_procesados": 0,
        "pdf_saltados": 0,
        "pdf_error": 0,
        "paginas": 0,
        "insertadas": 0,
        "duplicadas": 0,
        "omitidas": 0,
        "errores": 0,
    }

    modo = "PDF CONCRETO" if args.pdf else "CARPETA COMPLETA"

    logging.info(
        "INICIO IMPORTACIÓN | modo=%s | archivos=%d | forzar=%s",
        modo,
        len(entradas),
        args.forzar,
    )

    try:
        with sqlite3.connect(RUTA_DB) as conexion:
            conexion.row_factory = sqlite3.Row
            validar_base_datos(conexion)

            for entrada in entradas:
                try:
                    resultado, totales = procesar_entrada(
                        entrada,
                        conexion,
                        utilidad_openai,
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
                        "insertadas",
                        "duplicadas",
                        "omitidas",
                        "errores",
                    ):
                        globales[clave] += totales[clave]

                except Exception as exc:
                    globales["pdf_error"] += 1
                    logging.exception(
                        "ERROR DE FICHERO | %s | %s",
                        entrada.nombre_logico,
                        exc,
                    )

    except Exception as exc:
        logging.exception("Error general: %s", exc)
        return 1

    print()
    print("=" * 76)
    print("RESUMEN GENERAL")
    print("=" * 76)
    print(f"Modo:                   {modo}")
    if args.pdf:
        print(f"Archivo solicitado:     {entradas[0].nombre_logico}")
    print(f"Entradas encontradas:   {globales['pdf_encontrados']}")
    print(f"Entradas procesadas:    {globales['pdf_procesados']}")
    print(f"Entradas ya importadas: {globales['pdf_saltados']}")
    print(f"Entradas con error:     {globales['pdf_error']}")
    print(f"Páginas procesadas:     {globales['paginas']}")
    print(f"Preguntas insertadas:   {globales['insertadas']}")
    print(f"Preguntas duplicadas:   {globales['duplicadas']}")
    print(f"Preguntas omitidas:     {globales['omitidas']}")
    print(f"Errores extracción:     {globales['errores']}")
    print(f"Log:                    {RUTA_LOG}")
    print(f"Costes IA:              {RUTA_COSTES}")

    # Solo los errores generales de fichero deben detener el mantenimiento.
    # Los errores parciales de extracción ya se muestran y quedan registrados,
    # pero no invalidan las preguntas válidas publicadas.
    return 1 if globales["pdf_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())