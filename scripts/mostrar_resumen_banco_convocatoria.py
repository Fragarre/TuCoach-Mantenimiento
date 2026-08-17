"""
Muestra el número de preguntas del banco de una convocatoria.

Es una utilidad exclusivamente de lectura.

Uso:
    python scripts/mostrar_resumen_banco_convocatoria.py --convocatoria-id 1

Base alternativa:
    python scripts/mostrar_resumen_banco_convocatoria.py \
        --convocatoria-id 1 \
        --db ruta/oposiciones.sqlite3
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
    parser.add_argument(
        "--convocatoria-id",
        type=int,
        required=True,
    )
    argumentos = parser.parse_args()

    if argumentos.convocatoria_id <= 0:
        raise ValueError(
            "El ID de convocatoria debe ser mayor que cero."
        )

    db = Path(argumentos.db).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA query_only = ON")

        convocatoria = conexion.execute(
            """
            SELECT id, codigo, puesto, numero, anio
            FROM convocatorias
            WHERE id = ?
            """,
            (argumentos.convocatoria_id,),
        ).fetchone()

        if convocatoria is None:
            raise RuntimeError(
                "No existe la convocatoria "
                f"{argumentos.convocatoria_id}."
            )

        total = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas
                WHERE convocatoria_id = ?
                """,
                (argumentos.convocatoria_id,),
            ).fetchone()[0]
        )

        juridicas = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas
                WHERE convocatoria_id = ?
                  AND tipo_vinculacion = 'JURIDICA'
                """,
                (argumentos.convocatoria_id,),
            ).fetchone()[0]
        )

        no_juridicas = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas
                WHERE convocatoria_id = ?
                  AND tipo_vinculacion = 'NO_JURIDICA'
                """,
                (argumentos.convocatoria_id,),
            ).fetchone()[0]
        )

        revision = int(
            conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas
                WHERE convocatoria_id = ?
                  AND estado = 'REVISION'
                """,
                (argumentos.convocatoria_id,),
            ).fetchone()[0]
        )

        ia = conexion.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN lp.tipo_clasificacion = 'JURIDICA' THEN 1 ELSE 0 END) AS juridicas,
                SUM(CASE WHEN lp.tipo_clasificacion <> 'JURIDICA' THEN 1 ELSE 0 END) AS no_juridicas,
                SUM(CASE
                    WHEN lp.tipo_clasificacion = 'JURIDICA'
                     AND lp.norma_id_normalizada IS NOT NULL
                     AND TRIM(COALESCE(lp.nombre_norma_normalizado, '')) <> ''
                     AND TRIM(COALESCE(lp.articulo_normalizado, '')) <> ''
                    THEN 1 ELSE 0 END
                ) AS juridicas_correctas,
                SUM(CASE
                    WHEN lp.tipo_clasificacion = 'JURIDICA'
                     AND (
                         lp.norma_id_normalizada IS NULL
                         OR TRIM(COALESCE(lp.nombre_norma_normalizado, '')) = ''
                         OR TRIM(COALESCE(lp.articulo_normalizado, '')) = ''
                     )
                    THEN 1 ELSE 0 END
                ) AS juridicas_sin_referencia
            FROM banco_preguntas bp
            JOIN lote_preguntas lp ON lp.id = bp.pregunta_id
            WHERE bp.convocatoria_id = ?
              AND LOWER(TRIM(COALESCE(lp.tipo_fuente, ''))) = 'ia_generada'
            """,
            (argumentos.convocatoria_id,),
        ).fetchone()

    print()
    print("=" * 60)
    print("RESUMEN DEL BANCO DE UNA CONVOCATORIA")
    print("=" * 60)
    print(f"Convocatoria ID: {convocatoria['id']}")
    print(f"Código:          {convocatoria['codigo']}")
    print(f"Puesto:          {convocatoria['puesto']}")
    print(f"Número:          {convocatoria['numero']}")
    print(f"Año:             {convocatoria['anio']}")
    print("-" * 60)
    print(f"Total de preguntas: {total}")
    print(f"Jurídicas:          {juridicas}")
    print(f"No jurídicas:       {no_juridicas}")
    print(f"En revisión:        {revision}")
    print("-" * 60)
    print("Preguntas IA incluidas en este banco:")
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
    