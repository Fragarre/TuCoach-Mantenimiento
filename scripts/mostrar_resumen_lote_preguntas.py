"""
Muestra un resumen general de lote_preguntas.

Es una utilidad exclusivamente de lectura.

Uso:
    python scripts/mostrar_resumen_lote_preguntas.py

Base alternativa:
    python scripts/mostrar_resumen_lote_preguntas.py --db ruta/oposiciones.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFECTO))
    argumentos = parser.parse_args()

    db = Path(argumentos.db).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA query_only = ON")

        total = int(
            conexion.execute(
                "SELECT COUNT(*) FROM lote_preguntas"
            ).fetchone()[0]
        )

        juridicas = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM lote_preguntas
                WHERE tipo_clasificacion = 'JURIDICA'
                """
            ).fetchone()[0]
        )

        no_juridicas = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM lote_preguntas
                WHERE tipo_clasificacion = 'INFORMATICA'
                """
            ).fetchone()[0]
        )

        pendientes = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM lote_preguntas
                WHERE tipo_clasificacion = 'PENDIENTE'
                """
            ).fetchone()[0]
        )

        otros = total - juridicas - no_juridicas - pendientes

        ia = conexion.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN tipo_clasificacion = 'JURIDICA' THEN 1 ELSE 0 END) AS juridicas,
                SUM(CASE WHEN tipo_clasificacion <> 'JURIDICA' THEN 1 ELSE 0 END) AS no_juridicas,
                SUM(CASE
                    WHEN tipo_clasificacion = 'JURIDICA'
                     AND norma_id_normalizada IS NOT NULL
                     AND TRIM(COALESCE(nombre_norma_normalizado, '')) <> ''
                     AND TRIM(COALESCE(articulo_normalizado, '')) <> ''
                    THEN 1 ELSE 0 END
                ) AS juridicas_correctas,
                SUM(CASE
                    WHEN tipo_clasificacion = 'JURIDICA'
                     AND (
                         norma_id_normalizada IS NULL
                         OR TRIM(COALESCE(nombre_norma_normalizado, '')) = ''
                         OR TRIM(COALESCE(articulo_normalizado, '')) = ''
                     )
                    THEN 1 ELSE 0 END
                ) AS juridicas_sin_referencia
            FROM lote_preguntas
            WHERE LOWER(TRIM(COALESCE(tipo_fuente, ''))) = 'ia_generada'
            """
        ).fetchone()

    print()
    print("=" * 60)
    print("RESUMEN GENERAL DEL BANCO DE PREGUNTAS")
    print("=" * 60)
    print(f"Total de preguntas: {total}")
    print(f"Jurídicas:          {juridicas}")
    print(f"No jurídicas:       {no_juridicas}")
    print(f"Pendientes:         {pendientes}")

    if otros:
        print(f"Otras clasificaciones: {otros}")

    print("-" * 60)
    print("Preguntas generadas por IA:")
    print(f"  Total:                         {int(ia['total'] or 0)}")
    print(f"  Jurídicas:                     {int(ia['juridicas'] or 0)}")
    print(f"    Referencia jurídica correcta: {int(ia['juridicas_correctas'] or 0)}")
    print(f"    Sin referencia completa:      {int(ia['juridicas_sin_referencia'] or 0)}")
    print(f"  No jurídicas:                  {int(ia['no_juridicas'] or 0)}")
    print("    Norma/artículo:               no aplicable")

    print("=" * 60)
    print("Consulta de solo lectura.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            f"ERROR: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)