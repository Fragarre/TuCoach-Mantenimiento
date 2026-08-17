"""
OpoCoach - Construcción del catálogo de normas

Objetivo:
    Crear y completar la tabla 'normas' a partir de las denominaciones
    existentes en 'lote_preguntas' y 'temario_referencias'.

Comportamiento:
    - Lee las normas de ambas tablas.
    - Genera una clave común mediante normalizador_normas.py.
    - Inserta únicamente las normas que todavía no existen.
    - No modifica lote_preguntas ni temario_referencias.

Uso:
    python scripts\\construir_catalogo_normas.py
"""

from pathlib import Path
import sqlite3

from normalizador_normas import normalizar_norma


RUTA_BD = Path(__file__).resolve().parent.parent / "db" / "oposiciones.sqlite3"


def main() -> None:
    if not RUTA_BD.exists():
        raise FileNotFoundError(f"No existe la base de datos: {RUTA_BD}")

    with sqlite3.connect(RUTA_BD) as conexion:
        cursor = conexion.cursor()

        # Se leen primero las preguntas porque suelen contener
        # la denominación más completa de la norma.
        filas = cursor.execute(
            """
            SELECT DISTINCT nombre_norma_normalizado
            FROM lote_preguntas
            WHERE nombre_norma_normalizado IS NOT NULL
              AND TRIM(nombre_norma_normalizado) <> ''

            UNION ALL

            SELECT DISTINCT nombre_norma_normalizada
            FROM temario_referencias
            WHERE nombre_norma_normalizada IS NOT NULL
              AND TRIM(nombre_norma_normalizada) <> ''
            """
        ).fetchall()

        catalogo = {}

        for (nombre,) in filas:
            clave = normalizar_norma(nombre)

            if clave and clave not in catalogo:
                catalogo[clave] = nombre.strip()

        creadas = 0

        for clave, nombre_canonico in sorted(catalogo.items()):
            cursor.execute(
                """
                INSERT OR IGNORE INTO normas (
                    nombre_canonico,
                    clave_normalizada
                )
                VALUES (?, ?)
                """,
                (nombre_canonico, clave),
            )

            if cursor.rowcount == 1:
                creadas += 1

        conexion.commit()

        total = cursor.execute(
            "SELECT COUNT(*) FROM normas"
        ).fetchone()[0]

    print("Catálogo de normas construido.")
    print(f"Normas detectadas: {len(catalogo)}")
    print(f"Normas nuevas:      {creadas}")
    print(f"Total en catálogo:  {total}")


if __name__ == "__main__":
    main()