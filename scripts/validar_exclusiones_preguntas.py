"""Valida en solo lectura la coherencia de preguntas_exclusiones."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DB_DEFECTO, type=Path)
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        raise RuntimeError(f"No existe la base: {db}")
    conexion = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA query_only=ON")
    try:
        tabla = conexion.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='preguntas_exclusiones'"
        ).fetchone()
        if tabla is None:
            raise RuntimeError("No existe preguntas_exclusiones.")
        total = int(
            conexion.execute("SELECT COUNT(*) FROM preguntas_exclusiones").fetchone()[0]
        )
        incluidas = [
            dict(fila)
            for fila in conexion.execute(
                """
                SELECT bp.id AS banco_pregunta_id, bp.convocatoria_id,
                       bp.pregunta_id, pe.clasificacion, pe.estado
                FROM banco_preguntas AS bp
                JOIN preguntas_exclusiones AS pe ON pe.pregunta_id=bp.pregunta_id
                WHERE bp.estado='INCLUIDA'
                  AND pe.estado IN ('CUARENTENA','RETIRADA')
                ORDER BY bp.pregunta_id, bp.convocatoria_id
                """
            )
        ]
        revisiones = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas AS bp
                JOIN preguntas_exclusiones AS pe ON pe.pregunta_id=bp.pregunta_id
                WHERE bp.estado='REVISION'
                  AND pe.estado IN ('CUARENTENA','RETIRADA')
                """
            ).fetchone()[0]
        )
        fk = [tuple(fila) for fila in conexion.execute("PRAGMA foreign_key_check")]
        integridad = conexion.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conexion.close()
    resultado = {
        "modo": "SOLO_LECTURA",
        "base": str(db),
        "exclusiones": total,
        "vinculos_revision": revisiones,
        "exclusiones_incorrectamente_incluidas": incluidas,
        "foreign_key_check": fk,
        "integrity_check": integridad,
        "valido": not incluidas and not fk and integridad == "ok",
    }
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0 if resultado["valido"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
