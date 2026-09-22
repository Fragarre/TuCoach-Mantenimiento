"""Sincroniza un artículo concreto con la redacción consolidada oficial del BOE.

Por defecto solo muestra la diferencia. ``--aplicar`` crea una copia de la
base y actualiza exactamente una fila, dentro de una transacción.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from boe_api import obtener_articulo  # noqa: E402


def limpio(valor: object | None) -> str:
    return " ".join(str(valor or "").split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id-boe", required=True)
    ap.add_argument("--norma", required=True)
    ap.add_argument("--articulo", required=True)
    ap.add_argument("--db", default=str(ROOT / "db" / "oposiciones.sqlite3"))
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()
    db = Path(args.db).resolve()
    oficial = obtener_articulo(args.norma, args.articulo)
    nuevo = limpio(oficial.texto)
    with sqlite3.connect(db) as con:
        filas = con.execute(
            "SELECT id, texto FROM articulos_fuente WHERE UPPER(id_boe)=UPPER(?) "
            "AND REPLACE(LOWER(articulo_boe), ',', '.')=?",
            (args.id_boe, args.articulo.replace(",", ".").lower()),
        ).fetchall()
    if len(filas) != 1:
        raise RuntimeError(f"Se esperaba una fila para el artículo; encontradas: {len(filas)}")
    fila_id, anterior = filas[0]
    if limpio(anterior) == nuevo:
        print("SIN_CAMBIOS: la fila ya coincide con el BOE consolidado.")
        return 0
    print(f"DIFERENCIA: artículo {args.articulo}, fuente oficial {oficial.id_bloque}.")
    print(f"Longitud BD: {len(limpio(anterior))}; BOE: {len(nuevo)}")
    if not args.aplicar:
        print("SOLO_VALIDAR: use --aplicar para sincronizar esta única fila.")
        return 0
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"{db.stem}_antes_boe_{args.id_boe}_{args.articulo}_{marca}{db.suffix}")
    shutil.copy2(db, backup)
    with sqlite3.connect(db) as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE articulos_fuente SET id_bloque=?, titulo_bloque=?, texto=?, "
            "hash_texto=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (oficial.id_bloque, oficial.titulo_bloque, nuevo,
             hashlib.sha256(nuevo.encode()).hexdigest(), fila_id),
        )
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign_key_check ha fallado")
        con.commit()
    print(f"APLICADO: backup {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
