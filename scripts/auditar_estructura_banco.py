"""
===============================================================================
Proyecto : OpoCoach
Tipo     : Auditoría previa a la generación del banco de preguntas
Archivo  : auditar_estructura_banco.py
Ubicación:
    scripts/auditar_estructura_banco.py

OBJETIVO
--------
Inspeccionar, sin modificar la base de datos, las tablas relacionadas con la
generación del banco de preguntas:

    - lote_preguntas
    - temario_referencias
    - comparison
    - banco_preguntas

El script muestra:

    - existencia de cada tabla
    - número de registros
    - columnas y tipos
    - claves primarias
    - valores por defecto
    - índices
    - claves foráneas

SEGURIDAD
---------
Este script es exclusivamente de lectura.

No crea tablas.
No modifica registros.
No elimina datos.
No crea copias de seguridad porque no realiza escrituras.

===============================================================================
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"

TABLAS_OBJETIVO = (
    "lote_preguntas",
    "temario_referencias",
    "comparison",
    "banco_preguntas",
)


def tabla_existe(conexion: sqlite3.Connection, nombre: str) -> bool:
    fila = conexion.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (nombre,),
    ).fetchone()

    return fila is not None


def contar_registros(conexion: sqlite3.Connection, tabla: str) -> int:
    consulta = f'SELECT COUNT(*) FROM "{tabla}"'
    return int(conexion.execute(consulta).fetchone()[0])


def obtener_columnas(
    conexion: sqlite3.Connection,
    tabla: str,
) -> list[sqlite3.Row]:
    return conexion.execute(
        f'PRAGMA table_info("{tabla}")'
    ).fetchall()


def obtener_indices(
    conexion: sqlite3.Connection,
    tabla: str,
) -> list[sqlite3.Row]:
    return conexion.execute(
        f'PRAGMA index_list("{tabla}")'
    ).fetchall()


def obtener_columnas_indice(
    conexion: sqlite3.Connection,
    indice: str,
) -> list[sqlite3.Row]:
    return conexion.execute(
        f'PRAGMA index_info("{indice}")'
    ).fetchall()


def obtener_claves_foraneas(
    conexion: sqlite3.Connection,
    tabla: str,
) -> list[sqlite3.Row]:
    return conexion.execute(
        f'PRAGMA foreign_key_list("{tabla}")'
    ).fetchall()


def mostrar_columnas(columnas: list[sqlite3.Row]) -> None:
    if not columnas:
        print("  Sin columnas detectadas.")
        return

    print("  Columnas:")
    for columna in columnas:
        nombre = columna["name"]
        tipo = columna["type"] or "(sin tipo declarado)"
        obligatorio = "SÍ" if columna["notnull"] else "NO"
        primaria = "SÍ" if columna["pk"] else "NO"
        defecto = columna["dflt_value"]

        print(
            f"    - {nombre}"
            f" | tipo={tipo}"
            f" | NOT NULL={obligatorio}"
            f" | PK={primaria}"
            f" | defecto={defecto}"
        )


def mostrar_indices(
    conexion: sqlite3.Connection,
    indices: list[sqlite3.Row],
) -> None:
    if not indices:
        print("  Índices: ninguno")
        return

    print("  Índices:")
    for indice in indices:
        nombre = indice["name"]
        unico = "SÍ" if indice["unique"] else "NO"
        origen = indice["origin"]

        columnas = obtener_columnas_indice(conexion, nombre)
        nombres_columnas = [
            fila["name"]
            for fila in columnas
            if fila["name"] is not None
        ]

        print(
            f"    - {nombre}"
            f" | UNIQUE={unico}"
            f" | origen={origen}"
            f" | columnas={', '.join(nombres_columnas) or '(ninguna)'}"
        )


def mostrar_claves_foraneas(
    claves: list[sqlite3.Row],
) -> None:
    if not claves:
        print("  Claves foráneas: ninguna")
        return

    print("  Claves foráneas:")
    for clave in claves:
        print(
            f"    - {clave['from']} -> "
            f"{clave['table']}.{clave['to']}"
            f" | ON UPDATE={clave['on_update']}"
            f" | ON DELETE={clave['on_delete']}"
        )


def main() -> int:
    if not RUTA_DB.is_file():
        print(f"No existe la base de datos: {RUTA_DB}")
        return 1

    print()
    print("=" * 78)
    print("AUDITORÍA DE LA ESTRUCTURA DEL BANCO DE PREGUNTAS")
    print("=" * 78)
    print(f"Base de datos: {RUTA_DB}")
    print("Modo: SOLO LECTURA")
    print("=" * 78)

    with sqlite3.connect(RUTA_DB) as conexion:
        conexion.row_factory = sqlite3.Row

        for tabla in TABLAS_OBJETIVO:
            print()
            print("-" * 78)
            print(f"TABLA: {tabla}")
            print("-" * 78)

            if not tabla_existe(conexion, tabla):
                print("  Estado: NO EXISTE")
                continue

            print("  Estado: EXISTE")
            print(f"  Registros: {contar_registros(conexion, tabla)}")

            columnas = obtener_columnas(conexion, tabla)
            indices = obtener_indices(conexion, tabla)
            claves = obtener_claves_foraneas(conexion, tabla)

            mostrar_columnas(columnas)
            mostrar_indices(conexion, indices)
            mostrar_claves_foraneas(claves)

    print()
    print("=" * 78)
    print("AUDITORÍA FINALIZADA")
    print("No se ha modificado la base de datos.")
    print("=" * 78)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
