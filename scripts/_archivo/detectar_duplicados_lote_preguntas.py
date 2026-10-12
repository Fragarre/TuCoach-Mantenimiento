"""Detecta duplicados de lote_preguntas con criterio enunciado Y opciones.

Solo lectura sobre SQLite. Normaliza acentos, mayúsculas, puntuación y
espacios. Las cuatro opciones se comparan como conjunto, por lo que un cambio
de orden no impide detectar el mismo contenido.
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "db" / "oposiciones.sqlite3"


def normalizar(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    db = args.db.resolve()
    if not db.is_file():
        raise FileNotFoundError(db)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d,
                   respuesta_correcta, tipo_clasificacion, tipo_fuente,
                   origen_oposicion
            FROM lote_preguntas
            ORDER BY id
            """
        ).fetchall()

    groups: dict[tuple[str, tuple[str, str, str, str]], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        options = tuple(sorted(normalizar(row[field]) for field in (
            "opcion_a", "opcion_b", "opcion_c", "opcion_d"
        )))
        groups[(normalizar(row["enunciado"]), options)].append(row)

    matches = [group for group in groups.values() if len(group) > 1]
    with args.output.open("w", encoding="utf-8-sig", newline="") as out:
        fields = [
            "grupo", "id", "respuesta_correcta", "tipo_clasificacion",
            "tipo_fuente", "origen_oposicion", "enunciado", "opcion_a",
            "opcion_b", "opcion_c", "opcion_d",
        ]
        writer = csv.DictWriter(out, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for group_no, group in enumerate(matches, start=1):
            for row in group:
                writer.writerow({"grupo": group_no, **dict(row)})

    print(f"Preguntas revisadas: {len(rows)}")
    print(f"Grupos duplicados: {len(matches)}")
    print(f"Preguntas en grupos duplicados: {sum(map(len, matches))}")
    print(f"CSV: {args.output}")


if __name__ == "__main__":
    main()
