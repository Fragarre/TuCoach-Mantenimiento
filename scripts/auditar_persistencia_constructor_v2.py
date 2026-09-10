"""
Auditoría de persistencia para Constructor Temario V2.

SOLO LECTURA. Inspecciona el esquema SQLite real antes de proponer cambios de
persistencia. No crea tablas, no altera datos y no ejecuta DDL.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import comun


RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
PALABRAS = ("pseudo", "fuente", "evidencia", "temario", "articulo", "corpus", "rag")


def columnas(con, tabla: str) -> list[dict]:
    return [dict(r) for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()]


def claves_externas(con, tabla: str) -> list[dict]:
    return [dict(r) for r in con.execute(f"PRAGMA foreign_key_list({tabla})").fetchall()]


def indices(con, tabla: str) -> list[dict]:
    return [dict(r) for r in con.execute(f"PRAGMA index_list({tabla})").fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Audita esquema SQLite para persistencia V2, sin modificar datos.")
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD: {db}")

    hash_antes = comun.sha256_archivo(db)
    with comun.conectar_sqlite_solo_lectura(db) as con:
        tablas = [
            str(r["name"])
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        relevantes = [
            t for t in tablas
            if any(p in t.lower() for p in PALABRAS)
        ]

        print("=" * 78)
        print("CONSTRUCTOR TEMARIO V2 - AUDITORIA PERSISTENCIA - SOLO LECTURA")
        print("=" * 78)
        print(f"BD............................... {db}")
        print(f"Tablas totales................... {len(tablas)}")
        print(f"Tablas relevantes................ {len(relevantes)}")
        print()

        for tabla in relevantes:
            print(f"TABLA {tabla}")
            print("  COLUMNAS")
            for c in columnas(con, tabla):
                print(
                    f"    {c['name']} | {c['type']} | notnull={c['notnull']} | "
                    f"pk={c['pk']} | default={c['dflt_value']}"
                )
            fks = claves_externas(con, tabla)
            print("  FOREIGN KEYS")
            if not fks:
                print("    -")
            for fk in fks:
                print(
                    f"    {fk['from']} -> {fk['table']}.{fk['to']} | "
                    f"on_update={fk['on_update']} | on_delete={fk['on_delete']}"
                )
            idxs = indices(con, tabla)
            print("  INDICES")
            if not idxs:
                print("    -")
            for idx in idxs:
                print(f"    {idx['name']} | unique={idx['unique']} | origin={idx['origin']}")
            total = con.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()["n"]
            print(f"  FILAS: {total}")
            print()

        nombres_v2 = [t for t in tablas if "pseudo" in t.lower()]
        print("RESUMEN")
        print(f"  Tablas con 'pseudo'............. {len(nombres_v2)}")
        if nombres_v2:
            for t in nombres_v2:
                print(f"    - {t}")
        else:
            print("    - ninguna")

    hash_despues = comun.sha256_archivo(db)
    if hash_antes != hash_despues:
        raise RuntimeError("La auditoría detectó modificación de la BD, lo que no está permitido.")

    print("Hash BD antes/después............. IGUAL")
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
