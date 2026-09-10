"""
Constructor Temario V2 - auditoría de integración con lote_preguntas y bancos.

SOLO LECTURA. Inspecciona el esquema real necesario para decidir cómo representar
preguntas PSEUDO/MIXTA sin alterar la semántica consolidada de las preguntas
jurídicas REAL basadas en norma+artículo.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
TABLAS = (
    "lote_preguntas",
    "banco_preguntas",
    "banco_preguntas_temas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "temario_pseudoarticulo_respaldos",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFECTO))
    args = ap.parse_args()
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD: {db}")

    antes = sha256(db)
    uri = db.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        existentes = {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        print("=" * 78)
        print("CONSTRUCTOR TEMARIO V2 - AUDITORIA INTEGRACION PREGUNTAS - SOLO LECTURA")
        print("=" * 78)
        print(f"BD............................... {db}")

        for tabla in TABLAS:
            print(f"\nTABLA {tabla}")
            if tabla not in existentes:
                print("  NO EXISTE")
                continue
            print("  COLUMNAS")
            for r in con.execute(f"PRAGMA table_info({tabla})"):
                print(
                    f"    {r['name']} | {r['type']} | notnull={r['notnull']} | "
                    f"pk={r['pk']} | default={r['dflt_value']}"
                )
            print("  FOREIGN KEYS")
            fks = con.execute(f"PRAGMA foreign_key_list({tabla})").fetchall()
            if not fks:
                print("    -")
            for r in fks:
                print(
                    f"    {r['from']} -> {r['table']}.{r['to']} | "
                    f"on_update={r['on_update']} | on_delete={r['on_delete']}"
                )
            print("  INDICES")
            indices = con.execute(f"PRAGMA index_list({tabla})").fetchall()
            if not indices:
                print("    -")
            for idx in indices:
                nombre = str(idx['name'])
                cols = [str(x['name']) for x in con.execute(f"PRAGMA index_info('{nombre}')")]
                print(
                    f"    {nombre} | unique={idx['unique']} | origin={idx['origin']} | "
                    f"cols={','.join(cols)}"
                )
            n = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
            print(f"  FILAS: {n}")

        # Valores reales usados hoy: no asumimos que la documentación cubra todos.
        if "lote_preguntas" in existentes:
            print("\nVALORES lote_preguntas.tipo_clasificacion")
            for r in con.execute(
                "SELECT COALESCE(tipo_clasificacion,'<NULL>') v, COUNT(*) n "
                "FROM lote_preguntas GROUP BY tipo_clasificacion ORDER BY v"
            ):
                print(f"  {r['v']}: {r['n']}")
            print("\nVALORES lote_preguntas.criterio_inclusion_banco")
            cols = {str(x['name']) for x in con.execute("PRAGMA table_info(lote_preguntas)")}
            if "criterio_inclusion_banco" in cols:
                for r in con.execute(
                    "SELECT COALESCE(criterio_inclusion_banco,'<NULL>') v, COUNT(*) n "
                    "FROM lote_preguntas GROUP BY criterio_inclusion_banco ORDER BY v"
                ):
                    print(f"  {r['v']}: {r['n']}")

        if "banco_preguntas" in existentes:
            print("\nVALORES banco_preguntas.metodo_vinculacion")
            for r in con.execute(
                "SELECT COALESCE(metodo_vinculacion,'<NULL>') v, COUNT(*) n "
                "FROM banco_preguntas GROUP BY metodo_vinculacion ORDER BY v"
            ):
                print(f"  {r['v']}: {r['n']}")

    despues = sha256(db)
    print("\nHash BD antes/después............. " + ("IGUAL" if antes == despues else "DISTINTO"))
    if antes != despues:
        raise RuntimeError("La BD cambió durante auditoría SOLO LECTURA.")
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
