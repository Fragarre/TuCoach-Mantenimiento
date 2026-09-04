from __future__ import annotations

import argparse
import json
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

MODELO_TRABAJO_DEFECTO = "gpt-5.4-mini"
MODELO_VALIDACION_DEFECTO = "gpt-5.4-mini"
MAX_CHARS_BLOQUE = 7500
VERSION_FORMATO = "resumen-estudio-v1"


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
    fuente, contenido = seleccionar_fuente_canonica(con, norma_id)
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
    from openai_api import seleccionar_fragmento_json
    return seleccionar_fragmento_json(
        prompt=prompt,
        modelo=modelo,
        operacion=operacion,
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
- No hagas comparaciones, clasificaciones ni categorías doctrinales.
- No formules "diferencias" salvo que la propia fuente contraste expresamente
  dos regímenes.
- No conviertas excepciones en reglas generales.
- No completes con conocimientos externos.
- No reformules de manera que añada sujetos, órganos, requisitos, efectos,
  plazos o condiciones no presentes.
- Si una fila es fragmentaria, auxiliar o dudosa, extrae solo lo inequívoco.
- Los plazos deben reproducirse con su contexto material.
- Las referencias deben ser artículo principal o subapartado si aparece claro.
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
            + "\nCorrígelos utilizando exclusivamente los hechos validados.\n"
        )

    return f"""
Actúas como editor de materiales de estudio de OpoCoach.

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
- Solo crea una diferencia si los propios hechos validados permiten contrastar
  claramente dos regímenes.
- Mantén las referencias de artículos.
- Los plazos deben salir exclusivamente de hechos de categoría PLAZO.
- Si dudas entre una formulación más elegante y una más fiel, elige la más fiel.
- No redactes artículo por artículo: organiza por materias, pero mantén
  trazabilidad jurídica.
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
    return [limpiar(y) for y in x if limpiar(y)]


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
    )
    _validar_estructura_hechos(propuesta)

    revision = _llamar_json(
        _prompt_validar_hechos(norma, bloque, propuesta),
        modelo_validacion,
        "material_estudio_validar_hechos",
    )

    if revision.get("valido") is True:
        return propuesta["hechos"]

    errores = _errores(revision)
    propuesta2 = _llamar_json(
        _prompt_extraer_hechos(norma, bloque, errores),
        modelo_trabajo,
        "material_estudio_reextraer_hechos",
    )
    _validar_estructura_hechos(propuesta2)

    revision2 = _llamar_json(
        _prompt_validar_hechos(norma, bloque, propuesta2),
        modelo_validacion,
        "material_estudio_revalidar_hechos",
    )
    if revision2.get("valido") is not True:
        raise RuntimeError(
            "Extracción jurídica rechazada tras reintento: "
            + " | ".join(
                _errores(revision2)
                or errores
                or ["sin detalle"]
            )
        )

    return propuesta2["hechos"]


def _procesar_final(
    norma: str,
    hechos_validados: list[dict],
    modelo_trabajo: str,
    modelo_validacion: str,
) -> dict:
    final = _llamar_json(
        _prompt_sintesis_desde_hechos(norma, hechos_validados),
        modelo_trabajo,
        "material_estudio_sintesis_final",
        max_output_tokens=8192,
    )

    revision = _llamar_json(
        _prompt_validar_sintesis(norma, hechos_validados, final),
        modelo_validacion,
        "material_estudio_validar_final",
        max_output_tokens=4096,
    )

    if revision.get("valido") is True:
        return final

    errores = _errores(revision)
    final2 = _llamar_json(
        _prompt_sintesis_desde_hechos(
            norma,
            hechos_validados,
            errores,
        ),
        modelo_trabajo,
        "material_estudio_resintesis_final",
        max_output_tokens=8192,
    )

    revision2 = _llamar_json(
        _prompt_validar_sintesis(norma, hechos_validados, final2),
        modelo_validacion,
        "material_estudio_revalidar_final",
        max_output_tokens=4096,
    )

    if revision2.get("valido") is not True:
        raise RuntimeError(
            "Síntesis final rechazada tras reintento: "
            + " | ".join(
                _errores(revision2)
                or errores
                or ["sin detalle"]
            )
        )

    return final2


def _p(v: Any) -> str:
    return escape(limpiar(v))


def generar_pdf(ruta: Path, norma: str, r: dict) -> None:
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle(
        "TituloOC", parent=estilos["Title"],
        fontName="Helvetica-Bold", fontSize=17, leading=21,
        alignment=TA_CENTER, spaceAfter=6,
    )
    h1 = ParagraphStyle(
        "H1OC", parent=estilos["Heading1"],
        fontName="Helvetica-Bold", fontSize=13, leading=16,
        spaceBefore=7, spaceAfter=5,
    )
    h2 = ParagraphStyle(
        "H2OC", parent=estilos["Heading2"],
        fontName="Helvetica-Bold", fontSize=10.3, leading=13,
        spaceBefore=4, spaceAfter=3,
    )
    cuerpo = ParagraphStyle(
        "CuerpoOC", parent=estilos["BodyText"],
        fontName="Helvetica", fontSize=9.2, leading=12.2,
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
        canvas.setFont("Helvetica", 8)
        canvas.drawString(17*mm, 9*mm, "OpoCoach · Resumen para estudiar")
        canvas.drawRightString(A4[0]-17*mm, 9*mm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(ruta), pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm,
        topMargin=17*mm, bottomMargin=16*mm,
    )

    story = [
        Paragraph("OPOCOACH", titulo),
        Paragraph("RESUMEN PARA ESTUDIAR", h1),
        Paragraph(_p(norma), titulo),
        Paragraph(
            "Resumen temático elaborado exclusivamente a partir del texto "
            "completo almacenado en el corpus OpoCoach.",
            pequeno,
        ),
        Spacer(1, 3*mm),
        Paragraph("1. Cómo usar este resumen", h1),
        Paragraph(_p(r.get("introduccion")), cuerpo),
        Paragraph("2. Mapa general", h1),
    ]

    data = [[
        Paragraph("Bloque", celda_h),
        Paragraph("Artículos", celda_h),
        Paragraph("Contenido", celda_h),
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
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),
        ("LEFTPADDING",(0,0),(-1,-1),3),
        ("RIGHTPADDING",(0,0),(-1,-1),3),
        ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story.append(t)

    numero = 3
    for s in r.get("secciones") or []:
        story.append(Paragraph(
            f"{numero}. {_p(s.get('titulo'))} "
            f"(arts. {_p(s.get('articulos'))})",
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
            ("GRID",(0,0),(-1,-1),0.3,colors.grey),
            ("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),
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

    numero += 1
    story.append(Paragraph(f"{numero}. Diferencias y puntos de confusión", h1))
    for x in r.get("diferencias") or []:
        story.append(Paragraph(
            f"<b>{_p(x.get('conceptos'))}:</b> {_p(x.get('explicacion'))}",
            cuerpo,
        ))

    numero += 1
    story.append(Paragraph(f"{numero}. Cierre de repaso", h1))
    story.append(Paragraph(_p(r.get("cierre")), cuerpo))
    story.append(Paragraph(
        "Para preguntas de literalidad debe acudirse al texto completo "
        "disponible en OpoCoach.",
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
    if bloqueantes:
        print("ERROR: existen incidencias previas que deben resolverse.")
        for x in bloqueantes:
            print(f"  {x['norma_id']} | {x['estado']} | {x['norma']}")
        return 2

    pendientes = [
        x for x in filas
        if x["estado"] in {"NUEVA", "DESACTUALIZADO"}
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

            hechos_validados: list[dict] = []
            for i, bloque in enumerate(bloques, 1):
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
