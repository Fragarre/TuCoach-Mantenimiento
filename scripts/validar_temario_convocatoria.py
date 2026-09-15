from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Valida estructuralmente el temario de una convocatoria: identidad normativa, "
            "corpus completo e integridad SQLite. No contiene reglas materiales propias "
            "de ninguna convocatoria."
        )
    )
    p.add_argument("--codigo", required=True)
    p.add_argument("--db", default=str(DB_DEFECTO))
    args = p.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 2

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()

        conv = con.execute(
            "SELECT id,codigo FROM convocatorias WHERE codigo=?", (args.codigo,)
        ).fetchone()
        if conv is None:
            print(f"ERROR: no existe la convocatoria {args.codigo}")
            return 2

        temarios = con.execute(
            "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id",
            (int(conv["id"]),),
        ).fetchall()
        if len(temarios) != 1:
            print(f"ERROR: se esperaba un único temario y hay {len(temarios)}")
            return 2
        temario_id = int(temarios[0]["id"])

        refs = con.execute(
            """
            SELECT tt.parte,tt.numero_tema,tr.nombre_norma_normalizada,
                   tr.articulo_solicitado,tr.estado,tr.articulo_fuente_id,tr.norma_id
            FROM temario_referencias tr
            JOIN temario_temas tt ON tt.id=tr.tema_id
            WHERE tt.temario_id=?
            ORDER BY tt.parte,tt.numero_tema,tr.nombre_norma_normalizada,tr.articulo_solicitado
            """,
            (temario_id,),
        ).fetchall()

    sin_norma = [r for r in refs if r["norma_id"] is None]
    incompletas = [
        r for r in refs
        if str(r["estado"] or "").strip() != "COMPLETADO"
        or r["articulo_fuente_id"] is None
    ]

    print("=" * 78)
    print(f"VALIDACIÓN TEMARIO {args.codigo}")
    print("=" * 78)
    print(f"Referencias jurídicas:    {len(refs)}")
    print(f"Referencias sin norma_id: {len(sin_norma)}")
    print(f"Corpus incompleto:         {len(incompletas)}")
    print(f"SQLite integrity_check:    {integrity}")
    print(f"Foreign key check:         {len(fk)} incidencias")

    if sin_norma:
        print("\nREFERENCIAS SIN IDENTIDAD NORMATIVA:")
        for r in sin_norma[:50]:
            print(
                f"  - {r['parte']} {int(r['numero_tema']):02d} | "
                f"{r['nombre_norma_normalizada']} | art. {r['articulo_solicitado']}"
            )
    if incompletas:
        print("\nREFERENCIAS SIN CORPUS COMPLETO:")
        for r in incompletas[:50]:
            print(
                f"  - {r['parte']} {int(r['numero_tema']):02d} | "
                f"{r['nombre_norma_normalizada']} | art. {r['articulo_solicitado']} | "
                f"estado={r['estado']} | fuente={r['articulo_fuente_id']}"
            )

    if integrity != "ok" or fk or sin_norma or incompletas:
        print("\nRESULTADO: ERROR")
        return 1

    print("\nRESULTADO: OK - normalización, corpus e integridad correctos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
