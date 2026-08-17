"""
===============================================================================
Proyecto : OpoCoach
Tipo     : Buscador reutilizable de preguntas jurídicas
Archivo  : buscador_preguntas.py
Ubicación:
    scripts/buscador_preguntas.py

OBJETIVO
--------
Contener en un único lugar el algoritmo de vinculación entre:

    - una referencia jurídica del temario
    - las preguntas jurídicas de lote_preguntas

Este módulo será reutilizado por:

    - previsualizar_banco.py
    - generar_banco.py

REGLA DE VINCULACIÓN
--------------------
Para una referencia formada por:

    nombre_norma_normalizada = N
    articulo_solicitado      = A

se incluyen las preguntas que cumplan:

    nombre_norma_normalizado = N

y además:

    articulo_normalizado = A
        -> tipo_vinculacion = EXACTA

o bien:

    articulo_normalizado empieza por A + "."
        -> tipo_vinculacion = SUBARTICULO

Ejemplo:

    referencia solicitada: 14

    se incluyen:
        14       -> EXACTA
        14.1     -> SUBARTICULO
        14.2     -> SUBARTICULO
        14.2.a   -> SUBARTICULO

    no se incluyen:
        140
        141
        15

SEGURIDAD
---------
Este módulo es exclusivamente de lectura.

No crea tablas.
No modifica registros.
No elimina datos.

===============================================================================
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RAIZ = Path(__file__).resolve().parent.parent
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"

TIPO_EXACTA = "EXACTA"
TIPO_SUBARTICULO = "SUBARTICULO"
METODO_NORMA_ARTICULO = "NORMA_ARTICULO"

PATRON_ARTICULO = re.compile(
    r"^\d+(?:\.\d+)*(?:\.[a-z])?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PreguntaVinculada:
    pregunta_id: int
    nombre_norma_normalizado: str
    articulo_normalizado: str
    tipo_vinculacion: str
    metodo_vinculacion: str = METODO_NORMA_ARTICULO


def normalizar_entrada_texto(valor: str) -> str:
    return " ".join(valor.strip().split())


def validar_nombre_norma(nombre_norma: str) -> str:
    nombre = normalizar_entrada_texto(nombre_norma)

    if not nombre:
        raise ValueError("El nombre normalizado de la norma no puede estar vacío.")

    return nombre


def validar_articulo(articulo: str) -> str:
    valor = normalizar_entrada_texto(articulo).lower()

    if not valor:
        raise ValueError("El artículo solicitado no puede estar vacío.")

    if PATRON_ARTICULO.fullmatch(valor) is None:
        raise ValueError(
            "Formato de artículo no válido. "
            "Se esperan valores como 14, 14.2 o 14.2.a."
        )

    if valor.split(".", 1)[0] == "0":
        raise ValueError("El artículo no puede comenzar por 0.")

    return valor


def buscar_preguntas(
    conexion: sqlite3.Connection,
    nombre_norma_normalizada: str,
    articulo_solicitado: str,
) -> list[PreguntaVinculada]:
    """
    Devuelve todas las preguntas jurídicas vinculables con una referencia.

    La conexión debe apuntar a una base que contenga lote_preguntas.
    La función no realiza ninguna escritura.
    """
    norma = validar_nombre_norma(nombre_norma_normalizada)
    articulo = validar_articulo(articulo_solicitado)
    prefijo_subarticulo = articulo + ".%"

    filas = conexion.execute(
        """
        SELECT
            id AS pregunta_id,
            nombre_norma_normalizado,
            articulo_normalizado,
            CASE
                WHEN articulo_normalizado = ?
                    THEN ?
                ELSE ?
            END AS tipo_vinculacion
        FROM lote_preguntas
        WHERE tipo_clasificacion = 'JURIDICA'
          AND nombre_norma_normalizado = ?
          AND articulo_normalizado IS NOT NULL
          AND (
                articulo_normalizado = ?
                OR articulo_normalizado LIKE ?
          )
        ORDER BY
            CASE
                WHEN articulo_normalizado = ? THEN 0
                ELSE 1
            END,
            articulo_normalizado,
            id
        """,
        (
            articulo,
            TIPO_EXACTA,
            TIPO_SUBARTICULO,
            norma,
            articulo,
            prefijo_subarticulo,
            articulo,
        ),
    ).fetchall()

    return [
        PreguntaVinculada(
            pregunta_id=int(fila["pregunta_id"]),
            nombre_norma_normalizado=str(
                fila["nombre_norma_normalizado"]
            ),
            articulo_normalizado=str(fila["articulo_normalizado"]),
            tipo_vinculacion=str(fila["tipo_vinculacion"]),
        )
        for fila in filas
    ]


def contar_por_tipo(
    preguntas: Iterable[PreguntaVinculada],
) -> dict[str, int]:
    resultado = {
        TIPO_EXACTA: 0,
        TIPO_SUBARTICULO: 0,
    }

    for pregunta in preguntas:
        resultado[pregunta.tipo_vinculacion] += 1

    return resultado


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Busca preguntas jurídicas por norma normalizada y artículo. "
            "No modifica la base de datos."
        )
    )
    parser.add_argument(
        "nombre_norma",
        help="Nombre normalizado exacto de la norma.",
    )
    parser.add_argument(
        "articulo",
        help="Artículo solicitado, por ejemplo 14 o 14.2.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=RUTA_DB,
        help=f"Ruta de la base de datos. Por defecto: {RUTA_DB}",
    )
    parser.add_argument(
        "--mostrar",
        action="store_true",
        help="Muestra los identificadores y artículos encontrados.",
    )
    return parser


def main() -> int:
    parser = crear_parser()
    args = parser.parse_args()

    ruta_db = args.db.resolve()

    if not ruta_db.is_file():
        print(f"No existe la base de datos: {ruta_db}")
        return 1

    try:
        with sqlite3.connect(ruta_db) as conexion:
            conexion.row_factory = sqlite3.Row

            preguntas = buscar_preguntas(
                conexion=conexion,
                nombre_norma_normalizada=args.nombre_norma,
                articulo_solicitado=args.articulo,
            )
    except (sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    conteos = contar_por_tipo(preguntas)

    print()
    print("=" * 72)
    print("BÚSQUEDA DE PREGUNTAS POR NORMA Y ARTÍCULO")
    print("=" * 72)
    print(f"Norma........................ {validar_nombre_norma(args.nombre_norma)}")
    print(f"Artículo solicitado.......... {validar_articulo(args.articulo)}")
    print(f"Coincidencias exactas........ {conteos[TIPO_EXACTA]}")
    print(f"Coincidencias subartículo.... {conteos[TIPO_SUBARTICULO]}")
    print(f"Total........................ {len(preguntas)}")
    print("Modo......................... SOLO LECTURA")

    if args.mostrar and preguntas:
        print()
        print("Preguntas encontradas:")
        for pregunta in preguntas:
            print(
                f"  id={pregunta.pregunta_id}"
                f" | artículo={pregunta.articulo_normalizado}"
                f" | vinculación={pregunta.tipo_vinculacion}"
            )

    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
