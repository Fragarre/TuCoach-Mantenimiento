"""
TuCoach-Mantenimiento
Reparación controlada del corpus tras dos defectos de extracción.

A) PDF locales (pdf_normas.py): una remisión partida al inicio de línea
   ("artículo 10 del Decreto 42/2019...") se tomaba como encabezado del
   artículo 10. Consecuencias: texto de artículo sustituido por un fragmento
   ajeno y truncado del artículo anterior.

   Solo se repara un bloque ART-* cuando se demuestra que es fruto del defecto:
   su texto actual coincide con la extracción ANTIGUA y la extracción
   CORREGIDA produce otro texto. Cualquier otro bloque se deja intacto.

B) Numeración compuesta (Ley 20/2017 GVA: "1.3-1"): el resolvedor truncaba a
   "1" y enlazaba todas las referencias "1.x-y" a un único bloque.
   - Si el bloque correcto ya existe en articulos_fuente, se reenlaza.
   - Si no existe, la referencia vuelve a SIN_RESOLVER para que
     "Construir/validar corpus IA + RAG" la resuelva con el resolvedor corregido.
   - Se eliminan las resoluciones_boe erróneas (caché) de esas referencias.
   - Se corrige articulo_boe de bloques cuyo título es compuesto ("1.3-1") y
     se guardó truncado ("1").

Por defecto: SOLO LECTURA (vista previa).
--aplicar: copia de seguridad, transacción única, integrity_check y
foreign_key_check antes de confirmar.

Uso:
    python scripts/reparar_corpus_pdf_local.py
    python scripts/reparar_corpus_pdf_local.py --aplicar
    python scripts/reparar_corpus_pdf_local.py --aplicar --eliminar-inexistentes
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pdf_normas
from boe_api import BOEError, _titulo_corresponde_articulo, base_articulo

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"

# Fuentes de la BD cuyo PDF está inventariado con otro identificador.
ALIAS_PDF = {
    "LOCAL-PDF-REGLAMENTO-UE-2016-679": "DOUE-CELEX-32016R0679",
    "LOCAL-PDF-REAL-DECRETO-500-1990-DE-20-DE-ABRIL": "BOE-A-1990-9664",
}

PATRON_COMPUESTO = re.compile(r"^\d+\.\d+-\d+$")


def limpiar(valor: object | None) -> str:
    return " ".join(str(valor or "").split()).strip()


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


@dataclass
class Reparacion:
    af_id: int
    id_boe: str
    articulo: str
    referencias: int
    long_actual: int
    long_nueva: int
    inicio_actual: str
    inicio_nuevo: str
    titulo_nuevo: str
    texto_nuevo: str


def extraer(pdf: pdf_normas.PDFNorma, articulo: str, legado: bool):
    anterior = pdf_normas._ENCABEZADOS_LEGADO
    pdf_normas._ENCABEZADOS_LEGADO = legado
    try:
        return pdf_normas._bloque_articulo(pdf, articulo)
    except BOEError:
        return None
    finally:
        pdf_normas._ENCABEZADOS_LEGADO = anterior


def plan_pdf(con: sqlite3.Connection):
    reparaciones: list[Reparacion] = []
    inexistentes: list[tuple[int, str, str, int]] = []
    sin_pdf: dict[str, int] = {}
    revisados = 0

    filas = con.execute(
        """
        SELECT af.id, af.id_boe, af.id_bloque, af.articulo_boe, af.texto,
               (SELECT COUNT(*) FROM temario_referencias tr
                 WHERE tr.articulo_fuente_id = af.id) AS refs
        FROM articulos_fuente af
        WHERE af.id_bloque LIKE 'ART-%'
        ORDER BY af.id_boe, af.id
        """
    ).fetchall()

    pdfs: dict[str, pdf_normas.PDFNorma | None] = {}
    for fila in filas:
        id_boe = fila["id_boe"]
        if id_boe not in pdfs:
            try:
                pdfs[id_boe] = pdf_normas.buscar_norma_por_id(
                    ALIAS_PDF.get(id_boe, id_boe)
                )
            except BOEError:
                pdfs[id_boe] = None
        pdf = pdfs[id_boe]
        if pdf is None:
            sin_pdf[id_boe] = sin_pdf.get(id_boe, 0) + 1
            continue
        if pdf_normas.normalizar(pdf.ruta.stem).startswith("gen "):
            continue  # unidades doctrinales: no afectadas por el defecto

        revisados += 1
        articulo = str(fila["articulo_boe"])
        try:
            antiguo = extraer(pdf, articulo, legado=True)
            nuevo = extraer(pdf, articulo, legado=False)
        except BOEError:
            continue
        # Inequívoco: un artículo real nunca empieza por "artículo" en minúscula;
        # es una remisión que el extractor antiguo tomó como encabezado.
        es_remision = bool(re.match(r"art[ií]culo\b", str(fila["texto"]).lstrip()))
        if antiguo is None and not es_remision:
            continue
        if not es_remision and limpiar(fila["texto"]) != limpiar(antiguo[2]):
            continue  # el bloque no procede de la extracción defectuosa
        if nuevo is None:
            inexistentes.append(
                (int(fila["id"]), id_boe, articulo, int(fila["refs"]))
            )
            continue
        if limpiar(nuevo[2]) == limpiar(fila["texto"]):
            continue
        if es_remision and re.match(r"art[ií]culo\b", nuevo[2].lstrip()):
            continue  # no hay alternativa fiable
        reparaciones.append(
            Reparacion(
                af_id=int(fila["id"]),
                id_boe=id_boe,
                articulo=articulo,
                referencias=int(fila["refs"]),
                long_actual=len(fila["texto"]),
                long_nueva=len(nuevo[2]),
                inicio_actual=limpiar(fila["texto"])[:70],
                inicio_nuevo=limpiar(nuevo[2])[:70],
                titulo_nuevo=nuevo[1],
                texto_nuevo=nuevo[2],
            )
        )
    return revisados, reparaciones, inexistentes, sin_pdf


def plan_compuestos(con: sqlite3.Connection):
    reenlaces: list[tuple[int, int, str, str, int]] = []
    reinicios: list[tuple[int, str, str]] = []
    resoluciones: list[int] = []
    articulo_boe: list[tuple[int, str, str]] = []

    filas = con.execute(
        """
        SELECT tr.id, tr.articulo_solicitado, tr.articulo_fuente_id,
               af.id_boe, af.titulo_bloque
        FROM temario_referencias tr
        JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
        """
    ).fetchall()
    for fila in filas:
        base = base_articulo(fila["articulo_solicitado"])
        if not PATRON_COMPUESTO.match(base):
            continue
        if _titulo_corresponde_articulo(fila["titulo_bloque"], base):
            continue
        candidatos = [
            r["id"]
            for r in con.execute(
                "SELECT id, titulo_bloque FROM articulos_fuente WHERE id_boe=?",
                (fila["id_boe"],),
            )
            if _titulo_corresponde_articulo(r["titulo_bloque"], base)
        ]
        if len(candidatos) == 1:
            reenlaces.append(
                (int(fila["id"]), int(fila["articulo_fuente_id"]),
                 fila["id_boe"], base, int(candidatos[0]))
            )
        else:
            reinicios.append((int(fila["id"]), fila["id_boe"], base))

    for fila in con.execute(
        """
        SELECT rb.id, rb.articulo_solicitado_normalizado, af.titulo_bloque
        FROM resoluciones_boe rb
        JOIN articulos_fuente af ON af.id = rb.articulo_fuente_id
        """
    ):
        base = base_articulo(fila["articulo_solicitado_normalizado"])
        if PATRON_COMPUESTO.match(base) and not _titulo_corresponde_articulo(
            fila["titulo_bloque"], base
        ):
            resoluciones.append(int(fila["id"]))

    for fila in con.execute(
        "SELECT id, articulo_boe, titulo_bloque FROM articulos_fuente"
    ):
        m = re.match(
            r"^\s*Art[ií]culo\s+(\d+\.\d+-\d+)(?=$|[.\s])",
            fila["titulo_bloque"] or "",
        )
        if m and str(fila["articulo_boe"]).strip() != m.group(1):
            articulo_boe.append((int(fila["id"]), fila["articulo_boe"], m.group(1)))

    return reenlaces, reinicios, resoluciones, articulo_boe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument(
        "--eliminar-inexistentes",
        action="store_true",
        help="Con --aplicar, elimina bloques ART-* que solo existían por el "
        "defecto y que ninguna referencia del temario utiliza.",
    )
    args = parser.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1

    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        revisados, reparaciones, inexistentes, sin_pdf = plan_pdf(con)
        reenlaces, reinicios, resoluciones, art_boe = plan_compuestos(con)

    print("=" * 78)
    print("REPARACIÓN DEL CORPUS — " + ("APLICAR" if args.aplicar else "VISTA PREVIA"))
    print("=" * 78)
    print(f"Base: {db}")
    print("\nA) BLOQUES EXTRAÍDOS DE PDF LOCAL")
    print(f"Bloques ART-* revisados.............. {revisados}")
    print(f"Textos a reparar..................... {len(reparaciones)}")
    print(f"  de ellos usados por temarios....... {sum(1 for r in reparaciones if r.referencias)}")
    print(f"Artículos inexistentes (falsos)...... {len(inexistentes)}")
    for id_boe, n in sorted(sin_pdf.items()):
        print(f"  Sin PDF local localizable: {id_boe} ({n} bloques) — no se revisa")
    for r in reparaciones:
        marca = f"[{r.referencias} ref]" if r.referencias else "[RAG]"
        print(f"  {marca:8} {r.id_boe} art. {r.articulo}: {r.long_actual} -> {r.long_nueva} car.")
        print(f"           antes: {r.inicio_actual}")
        print(f"           ahora: {r.inicio_nuevo}")
    for af_id, id_boe, art, refs in inexistentes:
        print(f"  INEXISTENTE {id_boe} art. {art} (af {af_id}, referencias: {refs})")

    print("\nB) NUMERACIÓN COMPUESTA (1.x-y)")
    print(f"Referencias a reenlazar.............. {len(reenlaces)}")
    print(f"Referencias a re-resolver (BOE)...... {len(reinicios)}")
    print(f"resoluciones_boe erróneas a borrar... {len(resoluciones)}")
    print(f"articulo_boe a corregir.............. {len(art_boe)}")
    for ref, af_old, id_boe, base, af_new in reenlaces:
        print(f"  ref {ref}: {id_boe} art. {base}: af {af_old} -> {af_new}")
    for ref, id_boe, base in reinicios:
        print(f"  ref {ref}: {id_boe} art. {base}: sin bloque correcto -> SIN_RESOLVER")
    for af_id, viejo, nuevo in art_boe:
        print(f"  af {af_id}: articulo_boe {viejo!r} -> {nuevo!r}")

    total = len(reparaciones) + len(reenlaces) + len(reinicios) + len(resoluciones) + len(art_boe)
    if args.eliminar_inexistentes:
        total += sum(1 for x in inexistentes if x[3] == 0)
    if not args.aplicar:
        print("\nVISTA PREVIA: no se ha modificado la base.")
        return 0
    if total == 0:
        print("\nAPLICACIÓN: 0 cambios (idempotente).")
        return 0

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"{db.stem}_antes_reparar_corpus_{marca}{db.suffix}")
    shutil.copy2(db, backup)
    print(f"\nCopia de seguridad: {backup}")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        for r in reparaciones:
            con.execute(
                """UPDATE articulos_fuente
                   SET texto=?, hash_texto=?, titulo_bloque=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (r.texto_nuevo, sha(r.texto_nuevo), r.titulo_nuevo, r.af_id),
            )
        if args.eliminar_inexistentes:
            for af_id, _, _, refs in inexistentes:
                if refs == 0:
                    con.execute("DELETE FROM resoluciones_boe WHERE articulo_fuente_id=?", (af_id,))
                    con.execute("DELETE FROM articulos_fuente WHERE id=?", (af_id,))
        for rb_id in resoluciones:
            con.execute("DELETE FROM resoluciones_boe WHERE id=?", (rb_id,))
        for ref, _, _, _, af_new in reenlaces:
            con.execute(
                """UPDATE temario_referencias
                   SET articulo_fuente_id=?, estado='COMPLETADO', mensaje_error=NULL,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (af_new, ref),
            )
        for ref, _, base in reinicios:
            con.execute(
                """UPDATE temario_referencias
                   SET articulo_fuente_id=NULL, estado='SIN_RESOLVER',
                       mensaje_error=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (f"Reinicio: enlazada por error a otro artículo (numeración {base}).", ref),
            )
        for af_id, _, nuevo in art_boe:
            con.execute(
                "UPDATE articulos_fuente SET articulo_boe=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (nuevo, af_id),
            )
        integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if integridad != "ok" or fk:
            raise RuntimeError(f"integrity_check={integridad}; foreign_key_check={len(fk)}")
        con.commit()
    except Exception as exc:
        con.rollback()
        print(f"ERROR: se deshace la transacción: {exc}")
        return 2
    finally:
        con.close()

    print(f"APLICACIÓN: OK — {total} cambios.")
    if reinicios:
        print(
            "Siguiente paso: 7. HERRAMIENTAS AVANZADAS → 8. Construir/validar corpus "
            "IA + RAG para las convocatorias afectadas (requiere acceso al BOE)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
