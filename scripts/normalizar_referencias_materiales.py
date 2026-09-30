"""Relaciona las referencias de temario con el catálogo de normas.

Está diseñado para materiales de estudio: no altera textos ni artículos fuente.
Sólo completa ``temario_referencias.norma_id`` cuando la correspondencia con
``normas`` es inequívoca. En modo normal informa el plan; ``--aplicar`` crea
una copia de seguridad y actualiza exclusivamente las filas seleccionadas.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
CODIGOS_APOYO = ("Apoyo-A1-AYT", "Apoyo-A2-AYT", "Apoyo-C1-AYT", "Apoyo-C2-AYT")

# Variantes presentes en los CSV municipales cuyo identificador legal coincide
# de forma unívoca con la norma del catálogo. No se aplica ninguna aproximación
# léxica: una variante nueva deberá declararse aquí tras revisión.
ALIAS_A_CLAVE = {
    "ley organica 5/1982": "ley organica 5/1982 de 1 de julio",
    "ley organica 5/1985 de 19 de junio": "ley organica 5/1985",
    "real decreto 500/1990 de 20 de abril": "real decreto 500/1990",
    "real decreto legislativo 781/1986 de 18 de abril": "real decreto legislativo 781/1986",
}


def _candidatos(con: sqlite3.Connection, codigos: tuple[str, ...]) -> list[sqlite3.Row]:
    marcas = ",".join("?" for _ in codigos)
    return con.execute(
        f"""
        SELECT tr.id, tr.nombre_norma_normalizada, n.id AS norma_id
        FROM temarios t
        JOIN convocatorias c ON c.id=t.convocatoria_id
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        LEFT JOIN normas n ON n.clave_normalizada =
            CASE tr.nombre_norma_normalizada
                {' '.join(f"WHEN ? THEN ?" for _ in ALIAS_A_CLAVE)}
                ELSE tr.nombre_norma_normalizada
            END
        WHERE c.codigo IN ({marcas})
          AND tr.norma_id IS NULL
        ORDER BY tr.id
        """,
        tuple(x for par in ALIAS_A_CLAVE.items() for x in par) + codigos,
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_DEFECTO)
    parser.add_argument("--codigo", action="append", dest="codigos")
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    codigos = tuple(args.codigos or CODIGOS_APOYO)
    db = args.db.resolve()
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        filas = _candidatos(con, codigos)
    resueltas = [x for x in filas if x["norma_id"] is not None]
    pendientes = [x for x in filas if x["norma_id"] is None]
    print(f"Referencias pendientes: {len(filas)} | resolubles: {len(resueltas)} | sin correspondencia: {len(pendientes)}")
    if pendientes:
        print("SIN CORRESPONDENCIA:")
        for valor in sorted({str(x["nombre_norma_normalizada"]) for x in pendientes}):
            print(f" - {valor}")
    if not args.aplicar:
        return 0
    if pendientes:
        raise RuntimeError("No se aplica una normalización parcial: revise las referencias sin correspondencia.")
    copia = db.with_name(f"{db.stem}_antes_materiales_{datetime.now():%Y%m%d_%H%M%S}{db.suffix}")
    shutil.copy2(db, copia)
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.executemany("UPDATE temario_referencias SET norma_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND norma_id IS NULL", [(int(x["norma_id"]), int(x["id"])) for x in resueltas])
    print(f"ACTUALIZADAS: {len(resueltas)} | COPIA: {copia}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
