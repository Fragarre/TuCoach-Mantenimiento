"""
===============================================================================
Proyecto : OpoCoach
Tipo     : Eliminación idempotente de preguntas duplicadas
Archivo  : depurar_preguntas.py
Ubicación:
    scripts/depurar_preguntas.py

OBJETIVO
--------
Eliminar de la tabla `lote_preguntas` las preguntas duplicadas.

CRITERIO DE DUPLICADO
---------------------
Dos preguntas se consideran duplicadas únicamente cuando son idénticos:

    - enunciado
    - opcion_a
    - opcion_b
    - opcion_c
    - opcion_d

No se atiende a ningún otro campo.

PROCEDIMIENTO
-------------
1. Localiza todos los grupos de preguntas duplicadas.
2. Conserva siempre el registro con menor ID.
3. Elimina el resto.
4. Antes de modificar la base crea una copia de seguridad.
5. Si no existen duplicados no modifica la base ni crea copia.

IDEMPOTENCIA
------------
Puede ejecutarse tantas veces como se desee.
Si no existen duplicados no realiza ninguna modificación.

BASE DE DATOS
-------------
Requiere la tabla:

    lote_preguntas

No crea ni modifica tablas.

===============================================================================
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
CARPETA_COPIAS = RAIZ / "db" / "copias_seguridad"


def crear_copia_seguridad() -> Path:
    CARPETA_COPIAS.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = CARPETA_COPIAS / f"oposiciones_antes_duplicados_{fecha}.sqlite3"
    shutil.copy2(RUTA_DB, destino)
    return destino


def main() -> int:
    if not RUTA_DB.is_file():
        print(f"No existe la base de datos: {RUTA_DB}")
        return 1

    with sqlite3.connect(RUTA_DB) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")

        duplicados = conexion.execute(
            """
            SELECT
                MIN(id) AS id_conservado,
                COUNT(*) AS cantidad,
                enunciado,
                opcion_a,
                opcion_b,
                opcion_c,
                opcion_d
            FROM lote_preguntas
            GROUP BY
                enunciado,
                opcion_a,
                opcion_b,
                opcion_c,
                opcion_d
            HAVING COUNT(*) > 1
            ORDER BY id_conservado
            """
        ).fetchall()

        if not duplicados:
            print()
            print("=" * 65)
            print("DEPURACIÓN DE PREGUNTAS DUPLICADAS")
            print("=" * 65)
            print("Grupos duplicados:             0")
            print("Registros eliminados:          0")
            print("Copia de seguridad:            no necesaria")
            return 0

        ids_eliminar: list[int] = []

        for grupo in duplicados:
            filas = conexion.execute(
                """
                SELECT id
                FROM lote_preguntas
                WHERE enunciado = ?
                  AND opcion_a = ?
                  AND opcion_b = ?
                  AND opcion_c = ?
                  AND opcion_d = ?
                ORDER BY id
                """,
                (
                    grupo["enunciado"],
                    grupo["opcion_a"],
                    grupo["opcion_b"],
                    grupo["opcion_c"],
                    grupo["opcion_d"],
                ),
            ).fetchall()

            ids_eliminar.extend(fila["id"] for fila in filas[1:])

        copia = crear_copia_seguridad()

        try:
            conexion.executemany(
                "DELETE FROM lote_preguntas WHERE id = ?",
                [(registro_id,) for registro_id in ids_eliminar],
            )
            conexion.commit()
        except Exception:
            conexion.rollback()
            raise

    print()
    print("=" * 65)
    print("DEPURACIÓN DE PREGUNTAS DUPLICADAS")
    print("=" * 65)
    print(f"Grupos duplicados:             {len(duplicados)}")
    print(f"Registros eliminados:          {len(ids_eliminar)}")
    print(f"Copia de seguridad:            {copia.relative_to(RAIZ)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
