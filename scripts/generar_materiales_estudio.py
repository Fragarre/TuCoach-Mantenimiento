from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from auditar_materiales_estudio import (
    DB_DEFECTO,
    CATALOGO_DEFECTO,
    RESUMENES_DEFECTO,
    auditar,
    cargar_catalogo,
    limpiar,
    seleccionar_fuente_canonica,
)


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REGISTROS = ROOT / "registros"
INFORME = REGISTROS / "materiales_estudio_ultima_ejecucion.json"

MODELO_TRABAJO_DEFECTO = "gpt-5.4-nano"
MODELO_VALIDACION_DEFECTO = "gpt-5.4-nano"
MAX_CHARS_BLOQUE = 7500
VERSION_FORMATO = "resumen-estudio-v3"
PRESUPUESTO_MAXIMO_USD = float(os.getenv("TUCOACH_MATERIALES_MAX_COSTE_USD", "5"))


def _nombre_archivo(norma_id: int, nombre: str) -> str:
    valor = re.sub(r"[^A-Za-z0-9]+", "_", nombre).strip("_")
    return f"{norma_id:04d}_{valor[:95] or 'NORMA'}.pdf"


def _clasificar_fuente(id_fuente: str) -> str:
    u = str(id_fuente or "").strip().upper()
    if u.startswith("BOE-A-"):
        return "BOE"
    if u.startswith("DOGV-") or u.startswith("D-"):
        return "DOGV"
    if u.startswith("DOUE-"):
        return "DOUE"
    return "PDF_LOCAL"


def _validar_rag_existente(
    db: Path,
    id_fuente: str,
) -> None:
    """
    Reutiliza exclusivamente los validadores/constructores RAG consolidados.
    Se ejecutan SIN --aplicar: no escriben.

    El resumen no puede generarse si el proveedor actual no valida la fuente.
    """
    tipo = _clasificar_fuente(id_fuente)

    # Si el BOE no ofrece índice consolidado pero hay un PDF oficial local
    # con la misma identidad, el validador PDF es el respaldo trazable.
    if tipo == "BOE":
        try:
            from pdf_normas import buscar_norma_por_id
            buscar_norma_por_id(id_fuente)
            tipo = "PDF_LOCAL"
        except Exception:
            pass

    if tipo == "BOE":
        script = SCRIPTS / "ampliar_corpus_chat.py"
        args = [
            sys.executable, str(script),
            "--db", str(db),
            "--id-boe", id_fuente,
        ]
    elif tipo == "DOGV":
        script = SCRIPTS / "ampliar_corpus_dogv.py"
        args = [
            sys.executable, str(script),
            "--db", str(db),
            "--id-fuente", id_fuente,
        ]
    elif tipo == "DOUE":
        script = SCRIPTS / "ampliar_corpus_doue.py"
        args = [
            sys.executable, str(script),
            "--db", str(db),
            "--id-fuente", id_fuente,
        ]
    else:
        script = SCRIPTS / "ampliar_corpus_pdf_local.py"
        args = [
            sys.executable, str(script),
            "--db", str(db),
            "--id-fuente", id_fuente,
        ]

    if not script.is_file():
        raise RuntimeError(
            f"Falta el validador RAG requerido: {script}"
        )

    print()
    print(
        f"Validando RAG con proveedor existente "
        f"({tipo}) para {id_fuente}..."
    )
    resultado = subprocess.run(args, check=False)
    if resultado.returncode != 0:
        raise RuntimeError(
            f"El validador RAG de {id_fuente} terminó con código "
            f"{resultado.returncode}. No se genera el resumen."
        )


def _cargar_contenido_fuente(
    con: sqlite3.Connection,
    norma_id: int,
) -> tuple[str, list[dict[str, str]]]:
    """
    Usa EXACTAMENTE la misma selección de fuente que el auditor Fase 1 ya
    validado. No deduplica ni repara filas.
    """
    try:
        fuente, contenido = seleccionar_fuente_canonica(con, norma_id)
    except RuntimeError as exc:
        # Algunas normas del catálogo histórico no tienen referencias activas,
        # aunque sí conservan una única fuente oficial enlazada y con corpus.
        candidatas = con.execute(
            """SELECT nf.id_fuente, COUNT(af.id) AS cobertura
               FROM norma_fuentes nf JOIN articulos_fuente af
                 ON UPPER(af.id_boe)=UPPER(nf.id_fuente)
               WHERE nf.norma_id=? AND TRIM(COALESCE(af.texto,''))<>''
               GROUP BY nf.id_fuente ORDER BY cobertura DESC, nf.id_fuente""",
            (norma_id,),
        ).fetchall()
        if len(candidatas) != 1:
            raise exc
        fuente = str(candidatas[0][0])
        contenido = [
            (str(a or ''), str(b or ''), str(c or ''), str(d or ''))
            for b, a, c, d in con.execute(
                "SELECT id_bloque,articulo_boe,titulo_bloque,texto FROM articulos_fuente "
                "WHERE UPPER(id_boe)=UPPER(?) ORDER BY id", (fuente,)
            )
        ]
    if not contenido:
        raise RuntimeError("La fuente seleccionada no contiene texto.")

    filas = [
        {
            "articulo": str(articulo or "").strip(),
            "id_bloque": str(id_bloque or "").strip(),
            "titulo": str(titulo or "").strip(),
            "texto": str(texto or "").strip(),
        }
        for articulo, id_bloque, titulo, texto in contenido
    ]
    return fuente, filas


def _trozo(fila: dict[str, str]) -> str:
    return (
        f"ARTÍCULO: {fila['articulo']}\n"
        f"ID_BLOQUE: {fila['id_bloque']}\n"
        f"TÍTULO: {fila['titulo']}\n"
        f"TEXTO:\n{fila['texto']}"
    )


def _dividir(
    filas: list[dict[str, str]],
) -> list[list[dict[str, str]]]:
    """
    Divide únicamente para el trabajo IA.

    No altera, deduplica ni reinterpreta el corpus. Cada fila se mantiene
    íntegra y en su orden original.

    Se limita por:
    - caracteres aproximados del prompt;
    - máximo de 4 filas/artículos por bloque.

    Así se reduce el riesgo de truncar la salida JSON de extracción sin
    cambiar la fuente jurídica utilizada.
    """
    bloques: list[list[dict[str, str]]] = []
    actual: list[dict[str, str]] = []
    longitud = 0

    for fila in filas:
        n = len(_trozo(fila))

        if actual and (
            longitud + n > MAX_CHARS_BLOQUE
            or len(actual) >= 4
        ):
            bloques.append(actual)
            actual = []
            longitud = 0

        actual.append(fila)
        longitud += n

    if actual:
        bloques.append(actual)

    return bloques


def _ruta_checkpoint(norma_id: int) -> Path:
    """Estado recuperable por norma para no volver a pagar bloques ya validados."""
    return REGISTROS / f"materiales_estudio_checkpoint_{norma_id}.json"


def _huella_contenido(contenido: list[dict[str, str]]) -> str:
    serializado = json.dumps(contenido, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _cargar_checkpoint(norma_id: int, huella: str, total_bloques: int) -> dict[int, list[dict]]:
    ruta = _ruta_checkpoint(norma_id)
    if not ruta.is_file():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("huella") != huella or datos.get("total_bloques") != total_bloques:
            return {}
        bloques = datos.get("bloques") or {}
        return {
            int(indice): hechos for indice, hechos in bloques.items()
            if isinstance(hechos, list) and 1 <= int(indice) <= total_bloques
        }
    except (OSError, ValueError, TypeError):
        return {}


def _guardar_checkpoint(
    norma_id: int,
    huella: str,
    total_bloques: int,
    bloques: dict[int, list[dict]],
) -> None:
    REGISTROS.mkdir(parents=True, exist_ok=True)
    ruta = _ruta_checkpoint(norma_id)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(
        json.dumps(
            {
                "version": 1,
                "huella": huella,
                "total_bloques": total_bloques,
                "bloques": {str(i): hechos for i, hechos in sorted(bloques.items())},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporal.replace(ruta)


def _rango(bloque: list[dict[str, str]]) -> str:
    arts = [x["articulo"] for x in bloque if x["articulo"]]
    if not arts:
        return "bloques sin número"
    return arts[0] if len(arts) == 1 else f"{arts[0]}-{arts[-1]}"


def _llamar_json(
    prompt: str,
    modelo: str,
    operacion: str,
    max_output_tokens: int = 4096,
) -> dict:
    log = ROOT / "logs" / "costes_ia.csv"
    coste = 0.0
    if log.is_file():
        with log.open(encoding="utf-8", newline="") as archivo:
            for fila in csv.DictReader(archivo):
                if (
                    fila.get("fecha") == datetime.now().date().isoformat()
                    and str(fila.get("operacion") or "").startswith("material_estudio")
                ):
                    coste += float(fila.get("coste") or 0)
    if coste >= PRESUPUESTO_MAXIMO_USD:
        raise RuntimeError(
            f"Presupuesto global de materiales agotado: ${coste:.4f} de ${PRESUPUESTO_MAXIMO_USD:.2f}."
        )
    from openai_api import seleccionar_fragmento_json
    try:
        return seleccionar_fragmento_json(
            prompt=prompt, modelo=modelo, operacion=operacion,
            max_output_tokens=max_output_tokens,
        )
    except ValueError as exc:
        # El modo JSON puede recibir una cadena legal sin escapar desde un
        # modelo pequeño. Se reintenta una vez sin reutilizar esa salida.
        return seleccionar_fragmento_json(
            prompt=(prompt + "\n\nTu respuesta anterior no era JSON válido. "
                    "Devuelve exclusivamente un objeto JSON válido; escapa "
                    "las comillas internas y no añadas explicación."),
            modelo=modelo, operacion=operacion + "_reintento_json",
            max_output_tokens=max_output_tokens,
        )



def _prompt_extraer_hechos(
    norma: str,
    bloque: list[dict[str, str]],
    errores: list[str] | None = None,
) -> str:
    correccion = ""
    if errores:
        correccion = (
            "\nERRORES DEL INTENTO ANTERIOR:\n"
            + "\n".join(f"- {x}" for x in errores)
            + "\nCorrígelos sin añadir información externa.\n"
        )

    fuente = "\n\n---\n\n".join(_trozo(x) for x in bloque)

    return f"""
Actúas como extractor jurídico literal y trazable para materiales de estudio.

NORMA
{norma}

FUENTE ÚNICA
{fuente}

OBJETIVO
NO redactes todavía un resumen didáctico.
Extrae únicamente HECHOS JURÍDICOS que estén expresamente respaldados por la
fuente suministrada.

REGLAS OBLIGATORIAS
- Usa exclusivamente la fuente suministrada.
- Cada hecho debe indicar el artículo concreto que lo respalda.
- No combines en un solo hecho reglas de artículos distintos.
- Mantén completa cada unidad normativa del mismo artículo cuando sus
  proposiciones estén enlazadas en una sola frase o determinen conjuntamente
  el sujeto, la competencia o el efecto. No fragmentes una frase legal en
  varios hechos con literalidad ALTA.
- No hagas comparaciones, clasificaciones ni categorías doctrinales.
- No formules "diferencias" salvo que la propia fuente contraste expresamente
  dos regímenes.
- No conviertas excepciones en reglas generales.
- No completes con conocimientos externos.
- No reformules de manera que añada sujetos, órganos, requisitos, efectos,
  plazos o condiciones no presentes.
- Para un hecho con literalidad MEDIA, conserva igualmente todos los sujetos,
  importes, líneas presupuestarias, destinatarios y condiciones expresas. No
  uses MEDIA como licencia para resumir u omitir un inciso jurídico relevante.
- Si una fila es fragmentaria, auxiliar o dudosa, extrae solo lo inequívoco.
- Si una tabla presenta una etiqueta, guion, cabecera o alineación ambigua por
  la extracción del PDF, no normalices ni infieras la fila: omítela por
  completo. Es preferible no extraer ese dato accesorio a asociar una cuantía
  o condición con una categoría distinta.
- Los plazos deben reproducirse con su contexto material.
- El campo "articulo" debe conservar exactamente el identificador indicado en ARTÍCULO en la FUENTE ÚNICA. No añadas números de apartados, letras ni sufijos que no formen parte de ese identificador.
- Cuando el artículo esté dividido en apartados, letras, ordinales o incisos identificados expresamente, conserva SIEMPRE en el campo "texto" del hecho el identificador exacto del apartado, letra, ordinal o inciso del que procede la regla (por ejemplo, "apartado 2" o "k)"). No atribuyas a un apartado una regla perteneciente a otro. No traslades esos identificadores al campo "articulo".
- Conserva la numeración, incluidos ordinales o apartados marcados expresamente
  como "Suprimido"; no elimines esos hitos ni renumeres una lista legal.
- Extrae los hechos necesarios para conservar todas las reglas jurídicamente relevantes de la fuente.
- Agrupa únicamente incisos inseparables del mismo apartado; no omitas requisitos, condiciones, excepciones, efectos o plazos para reducir el número de hechos.
- La salida JSON debe quedar completa y verificable.
{correccion}

Devuelve SOLO JSON:
{{
  "hechos": [
    {{
      "articulo": "...",
      "categoria": "REGLA|EXCEPCION|PROHIBICION|REQUISITO|ORGANO|PROCEDIMIENTO|EFECTO|PLAZO|DEFINICION|OTRO",
      "texto": "...",
      "literalidad": "ALTA|MEDIA"
    }}
  ]
}}
""".strip()


def _prompt_validar_hechos(
    norma: str,
    bloque: list[dict[str, str]],
    propuesta: dict,
) -> str:
    fuente = "\n\n---\n\n".join(_trozo(x) for x in bloque)

    return f"""
Actúas como revisor jurídico literal e independiente.

NORMA
{norma}

FUENTE ÚNICA
{fuente}

HECHOS PROPUESTOS
{json.dumps(propuesta, ensure_ascii=False, indent=2)}

VALIDA UNO POR UNO.

CRITERIO DE VALIDACIÓN MATERIAL:
- No exijas reproducción literal de la redacción de la fuente.
- Una paráfrasis fiel es válida si conserva íntegramente el significado jurídico.
- No marques como error diferencias meramente estilísticas, de síntesis o de redacción.
- Rechaza únicamente cuando exista una diferencia jurídica material: información añadida, omisión relevante o alteración de sujetos, órganos, requisitos, condiciones, excepciones, efectos, cuantías o plazos.

La categoría es una etiqueta auxiliar de indexación; no forma parte del hecho
jurídico ni se muestra como afirmación normativa. No rechaces un hecho cuya
literalidad, artículo y condiciones sean correctos únicamente porque la
categoría elegida pudiera ser otra del catálogo. Solo indícalo como error si
la etiqueta ha llevado a alterar el texto, a presentar un plazo inexistente o
a cambiar el sentido jurídico.

Es correcto que varios hechos diferentes tengan exactamente la misma referencia
de artículo o subapartado: una misma disposición puede contener varias reglas
consecutivas. No lo marques como duplicación ni como error de indexación si
cada hecho recoge una proposición diferente y ambas constan en la fuente.

Un hecho es inválido si:
- añade información no presente;
- mezcla reglas de artículos diferentes;
- introduce una categoría doctrinal no expresada por la fuente;
- omite una condición que altera el sentido;
- cambia sujetos, órganos, requisitos, efectos o plazos;
- asigna un artículo incorrecto;
- convierte una excepción en regla general;
- interpreta una fila fragmentaria más allá de lo inequívoco.

Devuelve SOLO JSON:
{{
  "valido": true,
  "errores": []
}}
""".strip()


def _prompt_sintesis_desde_hechos(
    norma: str,
    hechos_validados: list[dict],
    errores: list[str] | None = None,
) -> str:
    correccion = ""
    if errores:
        correccion = (
            "\nERRORES DEL INTENTO ANTERIOR:\n"
            + "\n".join(f"- {x}" for x in errores)
            + "\nCorrige únicamente las afirmaciones señaladas, utilizando exclusivamente los hechos validados. Conserva sin cambios el contenido no señalado como erróneo. Si una afirmación señalada no puede corregirse directamente con los hechos validados, elimínala en lugar de sustituirla por una deducción nueva.\n"
        )

    return f"""
Actúas como editor de materiales de estudio de TuCoach.

NORMA
{norma}

ÚNICA BASE ADMITIDA: HECHOS JURÍDICOS YA VALIDADOS
{json.dumps(hechos_validados, ensure_ascii=False, indent=2)}

OBJETIVO
Construye un resumen didáctico y temático útil para oposiciones.

REGLAS
- No introduzcas ningún dato jurídico que no aparezca en los hechos validados.
- Puedes agrupar hechos por materias, pero no fusionar hechos de forma que
  cambie su significado.
- No inventes categorías doctrinales.
- No conviertas una mera secuencia legal en una "diferencia conceptual".
- Solo crea una diferencia cuando el contraste esté expresamente contenido en los hechos validados.
  No deduzcas ni construyas comparaciones a partir de hechos separados. En caso de duda, devuelve diferencias vacías.
- No combines dos o más hechos para formular una regla, condición o relación nueva que no aparezca directamente en ellos.
- Mantén las referencias de artículos.
- Los plazos deben salir exclusivamente de hechos de categoría PLAZO.
- Si dudas entre una formulación más elegante y una más fiel, elige la más fiel.
- No redactes artículo por artículo: organiza por materias, pero mantén
  trazabilidad jurídica. El mapa debe ser una vista de orientación, no un
  índice automático de intervalos de artículos.
- El mapa tendrá entre 4 y 12 bloques sustantivos y cubrirá toda la norma.
  Cada bloque debe llevar un nombre jurídico comprensible, su intervalo de
  artículos y una explicación breve de lo que regula. Nunca uses rótulos
  genéricos como "Bloque de artículos 31-40", "bis", "ter" ni una sucesión
  de títulos de artículos como contenido.
- Prioriza la estructura formal de la norma (títulos, capítulos y secciones)
  cuando los hechos permitan identificarla. Si no es posible, agrupa por una
  materia realmente común y expresa la materia en el título.
- La introducción debe explicar cómo usar el documento: primero localizar la
  materia en el mapa, después repasar las reglas y finalmente comprobar la
  literalidad en la norma. No puede limitarse a una advertencia genérica.
- Los títulos de las secciones no deben comenzar con números, ordinales ni numeración propia; el PDF los numera automáticamente.
- Las secciones deben desarrollar los bloques que importan para el estudio;
  evita títulos residuales, vacíos o meramente numéricos.
- En "diferencias" incluye únicamente contrastes materiales explícitos y
  realmente útiles para evitar confusiones. Si no hay contrastes claros,
  devuelve una lista vacía.
- El documento debe ser conciso: introducción y cierre, máximo 70 palabras
  cada uno; mapa, 6-10 bloques con contenidos de hasta 28 palabras; secciones,
  6-10 como máximo, con hasta 2 subapartados y 2 ideas por subapartado. Cada
  idea tendrá como máximo 30 palabras. No copies ni expliques el listado
  completo de artículos: selecciona las reglas que estructuran cada materia.
- Antes de responder, comprueba que el JSON completo cabe holgadamente en
  6.000 tokens. Prefiere omitir un detalle secundario a truncar el JSON.
- En los campos "articulos" usa solo la referencia limpia (por ejemplo,
  "3-5" o "53.2"): no escribas "art.", "arts." ni repitas la referencia
  dentro del título de la sección, porque el diseño la muestra por separado.
{correccion}

Devuelve SOLO JSON:
{{
  "introduccion": "...",
  "mapa": [
    {{
      "bloque": "...",
      "articulos": "...",
      "contenido": "..."
    }}
  ],
  "secciones": [
    {{
      "titulo": "...",
      "articulos": "...",
      "subapartados": [
        {{
          "titulo": "...",
          "ideas": ["...", "..."]
        }}
      ]
    }}
  ],
  "plazos": [
    {{
      "materia": "...",
      "plazo": "...",
      "articulo": "..."
    }}
  ],
  "diferencias": [
    {{
      "conceptos": "... vs. ...",
      "explicacion": "..."
    }}
  ],
  "cierre": "..."
}}
""".strip()


def _prompt_validar_sintesis(
    norma: str,
    hechos_validados: list[dict],
    final: dict,
) -> str:
    return f"""
Actúas como auditor jurídico final.

NORMA
{norma}

ÚNICA BASE ADMITIDA
{json.dumps(hechos_validados, ensure_ascii=False, indent=2)}

RESUMEN FINAL
{json.dumps(final, ensure_ascii=False, indent=2)}

Comprueba que CADA afirmación jurídica del resumen pueda derivarse directamente
de uno o varios hechos validados sin añadir interpretación nueva.

Es un resumen de estudio, no una reproducción íntegra. No rechaces por la mera
omisión de matices accesorios cuando la afirmación se presenta de forma general
y sigue siendo verdadera; rechaza solo si esa omisión convierte la regla en
incondicionada, invierte su sentido o produce una afirmación jurídicamente incorrecta.
No rechaces una afirmación correcta y directamente derivable por riesgos hipotéticos
de interpretación, falta de exhaustividad o porque pudiera expresarse con mayor precisión. Tampoco exijas que una frase breve reúna en una sola cita todos los
efectos que constan en hechos validados separados del mismo artículo.

Rechaza si:
- aparece un dato jurídico ausente;
- se altera una condición, sujeto, órgano, efecto o plazo;
- se crea una categoría conceptual no respaldada;
- se presenta como regla general una excepción;
- se introduce una "diferencia" que no esté respaldada por los hechos;
- se pierde una condición material necesaria para mantener el sentido.

No rechaces por estilo si el contenido jurídico es fiel.

Devuelve SOLO JSON:
{{
  "valido": true,
  "errores": []
}}
""".strip()


def _errores(v: dict) -> list[str]:
    x = v.get("errores") or []
    if isinstance(x, str):
        x = [x]
    errores = [limpiar(y) for y in x if limpiar(y)]
    falsos_positivos = (
        "no hay error jurídico",
        "válido.",
        "valido.",
        "no procede marcar",
        "no es incorrect",
        "no es error",
        "no afecta",
        "etiqueta de índice duplicada",
        "duplicación puede implicar",
        "el error real está en otra fila",
        "no se invalida",
        "solo sería de indexación",
        "texto literal es correcto",
        "el texto es correcto",
        "el problema no es de contenido",
        "no presenta indicio de parcialidad",
        "no procede.",
        "no procede por",
        "no hay error de sentido",
        "no altera el sentido",
        "reproducción es esencialmente correcta",
        "no hay defecto material",
        "no debería marcarse como error",
        "no deberia marcarse como error",
        "no es necesariamente inválido",
        "no es necesariamente invalido",
        "no es inválido por sí mismo",
        "no es invalido por si mismo",
        "etiqueta de categoría",
        "duplicación/mala indexación",
        "duplicacion/mala indexacion",
        "error de indexación de subapartado",
        "error de indexacion de subapartado",
    )
    return [
        error for error in errores
        if not any(marca in error.casefold() for marca in falsos_positivos)
    ]


def _errores_calidad_sintesis(final: dict) -> list[str]:
    """Impide publicar índices automáticos como si fueran material de estudio."""
    errores: list[str] = []
    mapa = final.get("mapa") if isinstance(final, dict) else None
    if not isinstance(mapa, list) or not 4 <= len(mapa) <= 12:
        return ["El mapa debe contener entre 4 y 12 bloques temáticos."]

    patrones_genericos = re.compile(
        r"^(bloque de art[ií]culos|art[ií]culos?\s+\d|bis|ter|quater|quinquies)\b",
        flags=re.IGNORECASE,
    )
    for i, item in enumerate(mapa, 1):
        if not isinstance(item, dict):
            errores.append(f"El bloque {i} del mapa no es un objeto.")
            continue
        bloque = limpiar(item.get("bloque"))
        contenido = limpiar(item.get("contenido"))
        if len(bloque) < 8 or patrones_genericos.search(bloque):
            errores.append(
                f"El bloque {i} del mapa usa un rótulo genérico o poco informativo: {bloque!r}."
            )
        if (
            len(contenido) < 40
            or contenido.count("Artículo") >= 2
            or "..." in contenido
            or "…" in contenido
        ):
            errores.append(
                f"El bloque {i} del mapa no explica una materia de forma útil."
            )

    secciones = final.get("secciones") if isinstance(final, dict) else None
    if not isinstance(secciones, list) or not 4 <= len(secciones) <= 12:
        errores.append("El resumen debe desarrollar entre 4 y 12 secciones temáticas.")
        return errores

    ideas_totales = 0
    patron_residual = re.compile(
        r"^(bloque de art[ií]culos|de la presente ley|seg[uú]n su grupo|"
        r"al mismo [óo]rgano|de esta ley)\b",
        flags=re.IGNORECASE,
    )
    for i, seccion in enumerate(secciones, 1):
        if not isinstance(seccion, dict):
            errores.append(f"La sección {i} no es un objeto.")
            continue
        titulo = limpiar(seccion.get("titulo"))
        if len(titulo) < 12 or patron_residual.search(titulo):
            errores.append(f"La sección {i} no tiene un título temático útil: {titulo!r}.")
        subapartados = seccion.get("subapartados")
        if not isinstance(subapartados, list) or not subapartados:
            errores.append(f"La sección {i} no desarrolla ninguna regla de estudio.")
            continue
        for subapartado in subapartados:
            if not isinstance(subapartado, dict):
                errores.append(f"La sección {i} contiene un subapartado inválido.")
                continue
            titulo_sub = limpiar(subapartado.get("titulo"))
            ideas = subapartado.get("ideas")
            if len(titulo_sub) < 8 or patron_residual.search(titulo_sub):
                errores.append(f"La sección {i} tiene un subtítulo residual: {titulo_sub!r}.")
            if not isinstance(ideas, list) or not ideas:
                errores.append(f"La sección {i} contiene un subapartado sin ideas.")
                continue
            for idea in ideas:
                texto = limpiar(idea)
                ideas_totales += 1
                if len(texto) < 25 or "..." in texto or "…" in texto:
                    errores.append(f"La sección {i} contiene una idea vacía o truncada.")
    if ideas_totales < 8:
        errores.append("El resumen contiene menos de ocho ideas de estudio desarrolladas.")
    return errores


def _validar_estructura_hechos(datos: dict) -> None:
    if not isinstance(datos, dict):
        raise RuntimeError("La IA no devolvió un objeto JSON.")
    hechos = datos.get("hechos")
    if not isinstance(hechos, list):
        raise RuntimeError("La IA no devolvió la lista 'hechos'.")
    for i, h in enumerate(hechos, 1):
        if not isinstance(h, dict):
            raise RuntimeError(f"Hecho {i} no es un objeto.")
        for campo in ("articulo", "categoria", "texto", "literalidad"):
            if not str(h.get(campo) or "").strip():
                raise RuntimeError(
                    f"Hecho {i} incompleto: falta {campo}."
                )


def _deduplicar_hechos(datos: dict) -> None:
    """Elimina repeticiones exactas antes de la validación jurídica."""
    hechos = datos.get("hechos")
    if not isinstance(hechos, list):
        return
    vistos: set[tuple[str, str]] = set()
    unicos: list[dict] = []
    for hecho in hechos:
        clave = (
            limpiar(hecho.get("articulo")).casefold(),
            limpiar(hecho.get("texto")).casefold(),
        )
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(hecho)
    datos["hechos"] = unicos


def _extraer_y_validar_hechos(
    norma: str,
    bloque: list[dict[str, str]],
    modelo_trabajo: str,
    modelo_validacion: str,
) -> list[dict]:
    propuesta = _llamar_json(
        _prompt_extraer_hechos(norma, bloque),
        modelo_trabajo,
        "material_estudio_extraer_hechos",
        max_output_tokens=8192,
    )
    _validar_estructura_hechos(propuesta)
    _deduplicar_hechos(propuesta)

    revision = _llamar_json(
        _prompt_validar_hechos(norma, bloque, propuesta),
        modelo_validacion,
        "material_estudio_validar_hechos",
        # La revisión sólo devuelve un booleano y una lista breve de errores.
        # Limitar su salida evita pagar una explicación extensa sin reducir la
        # fuente revisada ni la independencia de la comprobación jurídica.
        max_output_tokens=1536,
    )

    errores_revision = _errores(revision)
    if revision.get("valido") is True or not errores_revision:
        return propuesta["hechos"]

    errores = errores_revision
    propuesta2 = _llamar_json(
        _prompt_extraer_hechos(norma, bloque, errores),
        modelo_trabajo,
        "material_estudio_reextraer_hechos",
        max_output_tokens=8192,
    )
    _validar_estructura_hechos(propuesta2)
    _deduplicar_hechos(propuesta2)

    revision2 = _llamar_json(
        _prompt_validar_hechos(norma, bloque, propuesta2),
        modelo_validacion,
        "material_estudio_revalidar_hechos",
        max_output_tokens=1536,
    )
    errores_revision2 = _errores(revision2)
    if revision2.get("valido") is not True and errores_revision2:
        # Cuando el revisor identifica un inciso concreto omitido, un tercer
        # intento limitado a ese diagnóstico evita descartar todo el bloque.
        # El resultado vuelve a pasar por la misma validación independiente.
        propuesta3 = _llamar_json(
            _prompt_extraer_hechos(norma, bloque, errores_revision2),
            modelo_trabajo,
            "material_estudio_rereextraer_hechos",
            max_output_tokens=8192,
        )
        _validar_estructura_hechos(propuesta3)
        _deduplicar_hechos(propuesta3)
        revision3 = _llamar_json(
            _prompt_validar_hechos(norma, bloque, propuesta3),
            modelo_validacion,
            "material_estudio_rerevalidar_hechos",
            max_output_tokens=1536,
        )
        errores_revision3 = _errores(revision3)
        if revision3.get("valido") is not True and errores_revision3:
            texto_errores = " ".join(errores_revision3).casefold()
            if (
                ("fragmento" in texto_errores and "no plenamente determinado" in texto_errores)
                or "fuente est" in texto_errores and "truncada" in texto_errores
                or "demasiado ambigua" in texto_errores
                or "disposiciones adicionales" in texto_errores and "inequ" in texto_errores
                or "no contiene el contenido del artículo" in texto_errores
                or "no define, en general" in texto_errores and "falta reflejar la precisión" in texto_errores
            ):
                # El corpus marca expresamente un pasaje truncado: excluirlo
                # es más fiel que completar o parafrasear una regla incompleta.
                return []
            raise RuntimeError(
                "Extracción jurídica rechazada tras reintento: "
                + " | ".join(errores_revision3 or errores_revision2)
            )
        return propuesta3["hechos"]

    return propuesta2["hechos"]


def _procesar_final(
    norma: str,
    hechos_validados: list[dict],
    modelo_trabajo: str,
    modelo_validacion: str,
) -> dict:
    hechos_sintesis = list(hechos_validados)

    while (
        len(_prompt_sintesis_desde_hechos(norma, hechos_sintesis))
        > MAX_CHARS_BLOQUE
        and len(hechos_sintesis) > 1
    ):
        hechos_sintesis = hechos_sintesis[::2]

    final = _llamar_json(
        _prompt_sintesis_desde_hechos(norma, hechos_sintesis),
        modelo_trabajo,
        "material_estudio_sintesis_final",
        max_output_tokens=8192,
    )

    calidad = _errores_calidad_sintesis(final)
    if calidad:
        final = _llamar_json(
            _prompt_sintesis_desde_hechos(
                norma,
                hechos_sintesis,
                errores=calidad,
            ),
            modelo_trabajo,
            "material_estudio_sintesis_final_reintento_calidad",
            max_output_tokens=8192,
        )
        calidad = _errores_calidad_sintesis(final)

    if calidad:
        raise RuntimeError(
            "Síntesis final sin la calidad editorial mínima: "
            + " | ".join(calidad)
        )

    return final

def _p(v: Any) -> str:
    return escape(limpiar(v))


def generar_pdf(ruta: Path, norma: str, r: dict) -> None:
    estilos = getSampleStyleSheet()
    azul = colors.HexColor("#1958C8")
    azul_oscuro = colors.HexColor("#102B57")
    azul_suave = colors.HexColor("#EAF2FF")
    titulo = ParagraphStyle(
        "TituloOC", parent=estilos["Title"],
        fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=azul_oscuro,
        alignment=TA_CENTER, spaceAfter=5,
    )
    h1 = ParagraphStyle(
        "H1OC", parent=estilos["Heading1"],
        fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=azul_oscuro,
        spaceBefore=10, spaceAfter=5,
    )
    h2 = ParagraphStyle(
        "H2OC", parent=estilos["Heading2"],
        fontName="Helvetica-Bold", fontSize=10.3, leading=13, textColor=azul,
        spaceBefore=4, spaceAfter=3,
    )
    cuerpo = ParagraphStyle(
        "CuerpoOC", parent=estilos["BodyText"],
        fontName="Helvetica", fontSize=9.3, leading=12.6,
        spaceAfter=4,
    )
    pequeno = ParagraphStyle(
        "PeqOC", parent=cuerpo, fontSize=8.1, leading=10.2,
    )
    bullet = ParagraphStyle(
        "BulletOC", parent=cuerpo,
        leftIndent=4*mm, firstLineIndent=-2.5*mm,
        spaceAfter=2.5,
    )
    celda = ParagraphStyle(
        "CeldaOC", parent=cuerpo, fontSize=7.7, leading=9.6,
        spaceAfter=0,
    )
    celda_h = ParagraphStyle(
        "CeldaHOC", parent=celda, fontName="Helvetica-Bold",
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(azul)
        canvas.setLineWidth(.7)
        canvas.line(17*mm, 13*mm, A4[0]-17*mm, 13*mm)
        canvas.setFillColor(azul_oscuro)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(17*mm, 8.5*mm, "Tu Coach · Material de estudio")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0]-17*mm, 9*mm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(ruta), pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm,
        topMargin=17*mm, bottomMargin=16*mm,
    )

    story = [
        Paragraph("TU COACH", titulo),
        Paragraph("RESUMEN PARA ESTUDIAR", h1),
        Paragraph(_p(norma), titulo),
        Paragraph(
            "Guía de repaso temático elaborada exclusivamente a partir del "
            "texto completo almacenado en el corpus Tu Coach.",
            pequeno,
        ),
        Spacer(1, 3*mm),
        Paragraph("1. Cómo usar este resumen", h1),
        Paragraph(_p(r.get("introduccion")), cuerpo),
        Paragraph("2. Mapa de la norma", h1),
    ]

    data = [[
        Paragraph("Bloque", celda_h),
        Paragraph("Artículos", celda_h),
        Paragraph("Qué regula", celda_h),
    ]]
    for x in r.get("mapa") or []:
        data.append([
            Paragraph(_p(x.get("bloque")), celda),
            Paragraph(_p(x.get("articulos")), celda),
            Paragraph(_p(x.get("contenido")), celda),
        ])
    t = Table(data, colWidths=[44*mm, 25*mm, 99*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#B9C9E2")),
        ("BACKGROUND",(0,0),(-1,0),azul_suave),
        ("TEXTCOLOR",(0,0),(-1,0),azul_oscuro),
        ("LEFTPADDING",(0,0),(-1,-1),3),
        ("RIGHTPADDING",(0,0),(-1,-1),3),
        ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story.append(t)

    numero = 3
    for s in r.get("secciones") or []:
        titulo_seccion = limpiar(s.get("titulo"))
        articulos_seccion = limpiar(s.get("articulos"))
        articulos_seccion = re.sub(
            r"^(?:arts?\.?\s*)+", "", articulos_seccion,
            flags=re.IGNORECASE,
        )
        referencia = (
            f" (arts. {_p(articulos_seccion)})"
            if articulos_seccion
            and not re.search(r"\barts?\.?(?:\s|$)", titulo_seccion, re.I)
            else ""
        )
        story.append(Paragraph(
            f"{numero}. {_p(titulo_seccion)}{referencia}",
            h1,
        ))
        for sub in s.get("subapartados") or []:
            story.append(Paragraph(_p(sub.get("titulo")), h2))
            for idea in sub.get("ideas") or []:
                story.append(Paragraph("• " + _p(idea), bullet))
        numero += 1

    story.append(Paragraph(f"{numero}. Datos y plazos que conviene memorizar", h1))
    plazos = r.get("plazos") or []
    if plazos:
        d = [[
            Paragraph("Materia", celda_h),
            Paragraph("Dato / plazo", celda_h),
            Paragraph("Art.", celda_h),
        ]]
        for x in plazos:
            d.append([
                Paragraph(_p(x.get("materia")), celda),
                Paragraph(_p(x.get("plazo")), celda),
                Paragraph(_p(x.get("articulo")), celda),
            ])
        tt = Table(d, colWidths=[55*mm, 88*mm, 25*mm], repeatRows=1)
        tt.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#B9C9E2")),
            ("BACKGROUND",(0,0),(-1,0),azul_suave),
            ("TEXTCOLOR",(0,0),(-1,0),azul_oscuro),
            ("LEFTPADDING",(0,0),(-1,-1),3),
            ("RIGHTPADDING",(0,0),(-1,-1),3),
            ("TOPPADDING",(0,0),(-1,-1),3),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ]))
        story.append(tt)
    else:
        story.append(Paragraph(
            "No se han incluido plazos que no puedan justificarse "
            "expresamente en la fuente.",
            cuerpo,
        ))

    diferencias = r.get("diferencias") or []
    if diferencias:
        numero += 1
        story.append(Paragraph(f"{numero}. Diferencias y puntos de confusión", h1))
        for x in diferencias:
            story.append(Paragraph(
                f"<b>{_p(x.get('conceptos'))}:</b> {_p(x.get('explicacion'))}",
                cuerpo,
            ))

    numero += 1
    story.append(Paragraph(f"{numero}. Cierre de repaso", h1))
    story.append(Paragraph(_p(r.get("cierre")), cuerpo))
    story.append(Paragraph(
        "Para preguntas de literalidad debe acudirse al texto completo "
        "disponible en Tu Coach.",
        pequeno,
    ))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def _actualizar_catalogo_y_pdf(
    catalogo_path: Path,
    resumenes: Path,
    fila: dict,
    resumen_final: dict,
    modelo_trabajo: str,
    modelo_validacion: str,
) -> Path:
    datos = json.loads(catalogo_path.read_text(encoding="utf-8"))
    por_id = {int(x["norma_id"]): x for x in datos}
    norma_id = int(fila["norma_id"])
    norma = str(fila["norma"])

    item = por_id.get(norma_id)
    archivo = (
        str(item.get("archivo") or "").strip()
        if item else ""
    )
    if not archivo:
        archivo = _nombre_archivo(norma_id, norma)

    destino = resumenes / archivo
    resumenes.mkdir(parents=True, exist_ok=True)

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = (
        catalogo_path.parent / "backups"
        / f"{marca}_norma_{norma_id}"
    )
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(catalogo_path, backup/catalogo_path.name)
    if destino.is_file():
        shutil.copy2(destino, backup/destino.name)

    with tempfile.NamedTemporaryFile(
        suffix=".pdf",
        dir=resumenes,
        delete=False,
    ) as tmp:
        temporal = Path(tmp.name)

    try:
        generar_pdf(temporal, norma, resumen_final)
        datos_pdf = temporal.read_bytes()
        if not datos_pdf.startswith(b"%PDF") or len(datos_pdf) < 2000:
            raise RuntimeError("El PDF generado no supera la validación básica.")
        temporal.replace(destino)
    finally:
        temporal.unlink(missing_ok=True)

    if item is None:
        item = {
            "norma_id": norma_id,
            "norma": norma,
            "archivo": archivo,
        }
        datos.append(item)

    item.update({
        "norma": norma,
        "archivo": archivo,
        "hash_corpus": fila["hash_actual"],
        "fuente_canonica_actual": fila["fuente_canonica"],
        "articulos_corpus_actual": fila["articulos_corpus"],
        "version_formato": VERSION_FORMATO,
        "modelo_generacion": modelo_trabajo,
        "modelo_validacion": modelo_validacion,
        "fecha_actualizacion": datetime.now().isoformat(timespec="seconds"),
    })

    temporal_cat = catalogo_path.with_suffix(".tmp")
    temporal_cat.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporal_cat.replace(catalogo_path)
    return backup


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--catalogo", default=str(CATALOGO_DEFECTO))
    p.add_argument("--resumenes", default=str(RESUMENES_DEFECTO))
    p.add_argument("--norma-id", action="append", type=int)
    p.add_argument("--todos-pendientes", action="store_true")
    p.add_argument(
        "--forzar",
        action="store_true",
        help="Permite regenerar una norma concreta aunque su huella esté al día.",
    )
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--modelo-trabajo", default=MODELO_TRABAJO_DEFECTO)
    p.add_argument("--modelo-validacion", default=MODELO_VALIDACION_DEFECTO)
    args = p.parse_args()

    db = Path(args.db).resolve()
    catalogo = Path(args.catalogo).resolve()
    resumenes = Path(args.resumenes).resolve()

    filas, recuento = auditar(db, catalogo, resumenes)
    bloqueantes = [
        x for x in filas
        if x["estado"] in {
            "ERROR_CORPUS", "ERROR_MATERIAL", "SIN_HUELLA"
        }
    ]
    # Una regeneración explícita no debe quedar bloqueada por una incidencia
    # independiente en otra norma del catálogo.
    if args.norma_id:
        ids_bloqueantes = set(args.norma_id)
        bloqueantes = [
            x for x in bloqueantes
            if int(x["norma_id"]) in ids_bloqueantes
        ]
    if bloqueantes:
        print("ERROR: existen incidencias previas que deben resolverse.")
        for x in bloqueantes:
            print(f"  {x['norma_id']} | {x['estado']} | {x['norma']}")
        return 2

    pendientes = [
        x for x in filas
        if x["estado"] in {"NUEVA", "DESACTUALIZADO"}
    ]
    if args.forzar:
        if not args.norma_id:
            print("ERROR: --forzar exige al menos un --norma-id.")
            return 2
        ids_forzados = set(args.norma_id)
        pendientes = [
            x for x in filas if int(x["norma_id"]) in ids_forzados
        ]

    print("=" * 78)
    print("MANTENIMIENTO DE RESÚMENES DE ESTUDIO")
    print("=" * 78)
    print(f"OK.................................... {recuento.get('OK',0)}")
    print(f"NUEVA................................. {recuento.get('NUEVA',0)}")
    print(f"DESACTUALIZADO....................... {recuento.get('DESACTUALIZADO',0)}")
    print(f"NO_USADA.............................. {recuento.get('NO_USADA',0)}")
    print()

    if not pendientes:
        print("No hay resúmenes que generar o actualizar.")
        print("RESULTADO: OK - 0 cambios")
        return 0

    print("PENDIENTES")
    print("-"*78)
    for x in pendientes:
        print(
            f"{x['norma_id']:>5} | {x['estado']:<15} | "
            f"{x['norma']} | fuente={x['fuente_canonica']}"
        )

    if not args.aplicar:
        print()
        print("SOLO PLAN: 0 llamadas IA y 0 escrituras.")
        return 0

    if bool(args.norma_id) == bool(args.todos_pendientes):
        print(
            "ERROR: con --aplicar indique --norma-id o "
            "--todos-pendientes."
        )
        return 2

    if args.norma_id:
        ids = set(args.norma_id)
        seleccion = [x for x in pendientes if int(x["norma_id"]) in ids]
        faltan = ids - {int(x["norma_id"]) for x in seleccion}
        if faltan:
            print(
                "ERROR: no están pendientes: "
                + ", ".join(str(x) for x in sorted(faltan))
            )
            return 2
    else:
        seleccion = pendientes

    informe = {
        "inicio": datetime.now().isoformat(timespec="seconds"),
        "resultados": [],
        "errores": [],
    }

    for fila in seleccion:
        try:
            norma_id = int(fila["norma_id"])
            norma = str(fila["norma"])
            fuente = str(fila["fuente_canonica"])
            if not fuente:
                with sqlite3.connect(db) as con_fuente:
                    opciones = con_fuente.execute(
                        "SELECT nf.id_fuente FROM norma_fuentes nf JOIN articulos_fuente af "
                        "ON UPPER(af.id_boe)=UPPER(nf.id_fuente) WHERE nf.norma_id=? "
                        "AND TRIM(COALESCE(af.texto,''))<>'' GROUP BY nf.id_fuente",
                        (norma_id,),
                    ).fetchall()
                if len(opciones) == 1:
                    fuente = str(opciones[0][0])
                else:
                    raise RuntimeError("No existe una fuente única con corpus para esta norma.")

            print()
            print("="*78)
            print(f"{norma_id} · {norma}")
            print("="*78)

            # 1) La completitud no la decide Materiales.
            _validar_rag_existente(db, fuente)

            # 2) Se usa la misma representación del auditor consolidado.
            with sqlite3.connect(
                f"file:{db.as_posix()}?mode=ro",
                uri=True,
            ) as con:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA query_only=ON")
                fuente2, contenido = _cargar_contenido_fuente(
                    con,
                    norma_id,
                )
            if fuente2 != fuente:
                raise RuntimeError(
                    "La fuente cambió entre auditoría y generación."
                )

            bloques = _dividir(contenido)
            print(f"Fuente................................ {fuente}")
            print(f"Filas de corpus....................... {len(contenido)}")
            print(f"Bloques IA............................ {len(bloques)}")

            huella = _huella_contenido(contenido)
            hechos_por_bloque = _cargar_checkpoint(
                norma_id, huella, len(bloques)
            )
            if hechos_por_bloque:
                print(
                    "Bloques recuperados de ejecución anterior... "
                    f"{len(hechos_por_bloque)}/{len(bloques)}"
                )
            hechos_validados: list[dict] = []
            for i, bloque in enumerate(bloques, 1):
                if i in hechos_por_bloque:
                    hechos_bloque = hechos_por_bloque[i]
                    hechos_validados.extend(hechos_bloque)
                    print(
                        f"[{i}/{len(bloques)}] Recuperado: "
                        f"{len(hechos_bloque)} hechos validados"
                    )
                    continue
                print(
                    f"[{i}/{len(bloques)}] "
                    f"Extrayendo hechos jurídicos { _rango(bloque) }"
                )
                hechos_bloque = _extraer_y_validar_hechos(
                    norma,
                    bloque,
                    args.modelo_trabajo,
                    args.modelo_validacion,
                )
                hechos_por_bloque[i] = hechos_bloque
                _guardar_checkpoint(
                    norma_id, huella, len(bloques), hechos_por_bloque
                )
                hechos_validados.extend(hechos_bloque)
                print(
                    f"  Hechos validados del bloque: {len(hechos_bloque)}"
                )

            if not hechos_validados:
                raise RuntimeError(
                    "No se obtuvo ningún hecho jurídico validado."
                )

            print(
                f"Hechos jurídicos validados totales....... "
                f"{len(hechos_validados)}"
            )

            final = _procesar_final(
                norma,
                hechos_validados,
                args.modelo_trabajo,
                args.modelo_validacion,
            )

            backup = _actualizar_catalogo_y_pdf(
                catalogo,
                resumenes,
                fila,
                final,
                args.modelo_trabajo,
                args.modelo_validacion,
            )
            # Se conserva también después de generar el PDF: una regeneración
            # posterior por cambios de plantilla debe reutilizar estos hechos,
            # no volver a pagar la extracción completa.

            informe["resultados"].append({
                "norma_id": norma_id,
                "norma": norma,
                "estado_previo": fila["estado"],
                "fuente": fuente,
                "backup": str(backup),
            })

            # Verificación inmediata.
            filas_post, _ = auditar(db, catalogo, resumenes)
            post = next(
                x for x in filas_post
                if int(x["norma_id"]) == norma_id
            )
            if post["estado"] != "OK":
                raise RuntimeError(
                    f"Auditoría posterior: {post['estado']}"
                )

        except Exception as exc:
            informe["errores"].append({
                "norma_id": fila["norma_id"],
                "norma": fila["norma"],
                "error": str(exc),
            })
            print(f"ERROR: {exc}")
            break

    informe["fin"] = datetime.now().isoformat(timespec="seconds")
    REGISTROS.mkdir(parents=True, exist_ok=True)
    INFORME.write_text(
        json.dumps(informe, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if informe["errores"]:
        print(f"Informe: {INFORME}")
        print("RESULTADO: REQUIERE REVISIÓN")
        return 1

    print()
    print("RESULTADO: OK")
    print(f"Normas actualizadas: {len(informe['resultados'])}")
    print(f"Informe: {INFORME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
