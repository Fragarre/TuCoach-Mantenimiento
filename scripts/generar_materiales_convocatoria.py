"""Construye materiales por convocatoria de forma trazable e idempotente.

El extracto siempre reproduce los artículos enlazados por el temario; el
resumen se delega en el generador ya auditado, sólo cuando se solicita.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from auditar_materiales_estudio import (
    CATALOGO_DEFECTO, DB_DEFECTO, RESUMENES_DEFECTO, auditar,
    clave_articulo, limpiar, seleccionar_fuente_canonica, validar_estructura,
)
from generar_materiales_estudio import _validar_rag_existente


ROOT = Path(__file__).resolve().parent.parent
EXTRACTOS = ROOT / "materiales_estudio" / "extractos"
MANIFIESTO = EXTRACTOS / "catalogo_extractos.json"
VERSION_EXTRACTO = "extracto-convocatoria-v1"


def _slug(valor: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", valor).strip("_")[:90] or "NORMA"


def _hash(valor: object) -> str:
    return hashlib.sha256(json.dumps(valor, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _referencias(con: sqlite3.Connection, convocatoria_id: int, norma_id: int | None) -> list[sqlite3.Row]:
    sql = """
        SELECT n.id norma_id, n.nombre_canonico norma, af.id_boe fuente,
               af.articulo_boe articulo, af.titulo_bloque titulo, af.texto,
               tt.numero_tema tema, tt.titulo tema_titulo
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        JOIN normas n ON n.id=tr.norma_id
        LEFT JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE t.convocatoria_id=? AND tr.norma_id IS NOT NULL
        ORDER BY n.id, af.id, tt.numero_tema
    """
    params: list[object] = [convocatoria_id]
    if norma_id is not None:
        sql = sql.replace("ORDER BY", "AND n.id=? ORDER BY")
        params.append(norma_id)
    return con.execute(sql, params).fetchall()


def _extraer_normas(filas: list[sqlite3.Row]) -> dict[int, dict]:
    salida: dict[int, dict] = {}
    for f in filas:
        norma_id = int(f["norma_id"])
        item = salida.setdefault(norma_id, {"norma": limpiar(f["norma"]), "filas": [], "temas": set()})
        if not limpiar(f["fuente"]) or not limpiar(f["texto"]):
            raise RuntimeError(f"La norma {norma_id} contiene una referencia sin artículo fuente completo.")
        item["filas"].append({k: limpiar(f[k]) for k in ("fuente", "articulo", "titulo", "texto")})
        item["temas"].add(f"Tema {f['tema']}: {limpiar(f['tema_titulo'])}")
    for item in salida.values():
        vistos: set[tuple[str, str]] = set()
        articulos_unicos = []
        for fila in item["filas"]:
            clave = (fila["fuente"], fila["articulo"])
            if clave not in vistos:
                vistos.add(clave)
                articulos_unicos.append(fila)
        item["filas"] = articulos_unicos
        item["filas"].sort(key=lambda x: clave_articulo(x["articulo"]))
        item["temas"] = sorted(item["temas"])
    return salida


def _pdf_extracto(destino: Path, codigo: str, puesto: str, norma: str, temas: list[str], filas: list[dict]) -> None:
    estilos = getSampleStyleSheet()
    azul = colors.HexColor("#102B57")
    portada = ParagraphStyle("Portada", parent=estilos["Title"], fontName="Helvetica-Bold", fontSize=17, leading=21, alignment=TA_CENTER, textColor=azul, spaceAfter=8)
    h = ParagraphStyle("H", parent=estilos["Heading1"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=azul, spaceBefore=8, spaceAfter=4)
    cuerpo = ParagraphStyle("C", parent=estilos["BodyText"], fontName="Helvetica", fontSize=9.2, leading=12.2, spaceAfter=5)
    def pie(canvas, doc):
        canvas.saveState(); canvas.setFont("Helvetica", 8); canvas.setFillColor(azul)
        canvas.drawString(17*mm, 9*mm, f"Tu Coach · {codigo}")
        canvas.drawRightString(A4[0]-17*mm, 9*mm, f"Página {doc.page}"); canvas.restoreState()
    doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=17*mm, rightMargin=17*mm, topMargin=17*mm, bottomMargin=16*mm)
    story = [Paragraph("TU COACH", portada), Paragraph(escape(codigo), portada), Paragraph(escape(puesto), h), Spacer(1, 8*mm), Paragraph(escape(norma), portada), Paragraph("Extracto para esta oposición", h), Spacer(1, 12*mm), Paragraph("Incluye exclusivamente los artículos vinculados al temario de esta convocatoria.", cuerpo), Paragraph("Temas relacionados: " + escape("; ".join(temas)), cuerpo), PageBreak()]
    for f in filas:
        titulo = f["titulo"]
        if titulo.casefold().replace(" ", "").startswith(("artículo" + f["articulo"]).casefold().replace(" ", "")):
            titulo = ""
        encabezado = f"Artículo {escape(f['articulo'])}" + (f". {escape(titulo)}" if titulo else "")
        story += [Paragraph(encabezado, h), Paragraph(escape(f["texto"]).replace("\n", "<br/>"), cuerpo)]
    doc.build(story, onFirstPage=pie, onLaterPages=pie)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--codigo", required=True, help="Código exacto de convocatoria")
    p.add_argument("--norma-id", type=int)
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--validar-rag", action="store_true")
    p.add_argument("--generar-resumenes", action="store_true")
    args = p.parse_args()
    with sqlite3.connect(f"file:{DB_DEFECTO.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row; con.execute("PRAGMA query_only=ON"); validar_estructura(con)
        convocatoria = con.execute("SELECT id, puesto FROM convocatorias WHERE codigo=? AND activa=1", (args.codigo,)).fetchone()
        if convocatoria is None: raise RuntimeError("No existe una convocatoria activa con ese código.")
        normas = _extraer_normas(_referencias(con, int(convocatoria["id"]), args.norma_id))
        if not normas: raise RuntimeError("No hay referencias normalizadas para la selección.")
        for norma_id, item in normas.items():
            fuente, corpus = seleccionar_fuente_canonica(con, norma_id)
            articulos = {(x["fuente"], x["articulo"]) for x in item["filas"]}
            corpus_ids = {(fuente, limpiar(x[0])) for x in corpus}
            faltan = [(fuente, articulo) for fuente, articulo in articulos if (fuente, articulo) not in corpus_ids]
            if faltan: raise RuntimeError(f"La norma {norma_id} no está íntegra en el corpus para el temario: {faltan[:3]}")
            item["fuente_canonica"] = fuente
            item["hash"] = _hash({"version": VERSION_EXTRACTO, "filas": item["filas"]})
    print(f"Convocatoria: {args.codigo} | normas: {len(normas)} | modo: {'APLICAR' if args.aplicar else 'PLAN'}")
    for norma_id, item in normas.items(): print(f"{norma_id} | {len(item['filas'])} artículos | {item['norma']}")
    if not args.aplicar: return 0
    EXTRACTOS.mkdir(parents=True, exist_ok=True)
    catalogo = json.loads(MANIFIESTO.read_text(encoding="utf-8")) if MANIFIESTO.is_file() else []
    por_clave = {(x["codigo"], int(x["norma_id"])): x for x in catalogo}
    for norma_id, item in normas.items():
        clave = (args.codigo, norma_id); archivo = f"{args.codigo}_{norma_id:04d}_{_slug(item['norma'])}_extracto.pdf"; destino = EXTRACTOS / archivo
        actual = por_clave.get(clave)
        if actual and actual.get("hash") == item["hash"] and destino.is_file():
            print(f"SIN CAMBIOS: {archivo}"); continue
        if args.validar_rag: _validar_rag_existente(DB_DEFECTO, item["fuente_canonica"])
        with tempfile.NamedTemporaryFile(suffix=".pdf", dir=EXTRACTOS, delete=False) as tmp: temporal = Path(tmp.name)
        try:
            _pdf_extracto(temporal, args.codigo, str(convocatoria["puesto"]), item["norma"], item["temas"], item["filas"])
            if not temporal.read_bytes().startswith(b"%PDF"): raise RuntimeError("El extracto PDF no es válido.")
            temporal.replace(destino)
        finally: temporal.unlink(missing_ok=True)
        nuevo = {"codigo": args.codigo, "norma_id": norma_id, "norma": item["norma"], "archivo": archivo, "hash": item["hash"], "version": VERSION_EXTRACTO, "fuente_canonica": item["fuente_canonica"], "articulos": len(item["filas"]), "actualizado": datetime.now().isoformat(timespec="seconds")}
        if actual: catalogo.remove(actual)
        catalogo.append(nuevo); por_clave[clave] = nuevo; print(f"CREADO: {archivo}")
    MANIFIESTO.write_text(json.dumps(sorted(catalogo, key=lambda x: (x["codigo"], x["norma_id"])), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if args.generar_resumenes:
        estados, _ = auditar(DB_DEFECTO, CATALOGO_DEFECTO, RESUMENES_DEFECTO)
        pendientes = {
            int(fila["norma_id"])
            for fila in estados
            if fila["estado"] in {"NUEVA", "DESACTUALIZADO"}
        }
        ids = [norma_id for norma_id in normas if norma_id in pendientes]
        if not ids:
            print("SIN CAMBIOS: los resúmenes seleccionados ya están auditados.")
            return 0
        comando = [sys.executable, str(ROOT / "scripts" / "generar_materiales_estudio.py"), "--aplicar"]
        for norma_id in ids: comando += ["--norma-id", str(norma_id)]
        raise SystemExit(subprocess.run(comando, cwd=ROOT, check=False).returncode)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
        print("\n" + "=" * 78)
        print("MATERIALES POR CONVOCATORIA - OPERACIÓN DETENIDA")
        print("=" * 78)
        print(f"Motivo: {limpiar(error)}")
        print(
            "No se ha publicado ningún material incompleto. Corrija el origen "
            "indicado y vuelva a ejecutar primero el plan."
        )
        raise SystemExit(2)
    except Exception as error:
        print("\n" + "=" * 78)
        print("MATERIALES POR CONVOCATORIA - ERROR NO PREVISTO")
        print("=" * 78)
        print(f"Tipo: {type(error).__name__}")
        print(f"Motivo: {limpiar(error)}")
        print(
            "No se ha publicado ningún material en esta fase. Anote el mensaje "
            "y revise el registro de mantenimiento antes de reintentar."
        )
        raise SystemExit(1)
