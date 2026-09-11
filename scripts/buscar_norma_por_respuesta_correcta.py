#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Busca norma y artículo a partir del texto de la respuesta correcta
de preguntas PENDIENTES.

El script:
- lee lote_preguntas;
- obtiene el texto de la opción correcta;
- formula una pregunta directa a la IA;
- guarda el resultado en CSV;
- no modifica la base de datos.

Uso:
    python scripts\\buscar_norma_por_respuesta_correcta.py
    python scripts\\buscar_norma_por_respuesta_correcta.py --limite 20
    python scripts\\buscar_norma_por_respuesta_correcta.py --id 5119
    python scripts\\buscar_norma_por_respuesta_correcta.py --modelo gpt-5.4-nano
    python scripts\\buscar_norma_por_respuesta_correcta.py --db db\\oposiciones.sqlite3
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import shutil
import sys
from datetime import datetime
from pathlib import Path

from openai_api import seleccionar_fragmento


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
MODELO_DEFECTO = "gpt-5.4-nano"


def limpiar(valor: object) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).strip().split())


def validar_estructura(conexion: sqlite3.Connection) -> None:
    tabla = conexion.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'lote_preguntas'
        """
    ).fetchone()

    if tabla is None:
        raise RuntimeError("No existe la tabla lote_preguntas.")

    columnas = {
        fila["name"]
        for fila in conexion.execute("PRAGMA table_info(lote_preguntas)")
    }

    obligatorias = {
        "id",
        "enunciado",
        "opcion_a",
        "opcion_b",
        "opcion_c",
        "opcion_d",
        "respuesta_correcta",
        "tipo_clasificacion",
    }

    faltan = sorted(obligatorias - columnas)
    if faltan:
        raise RuntimeError(
            "Faltan columnas obligatorias en lote_preguntas: "
            + ", ".join(faltan)
        )


def texto_respuesta_correcta(fila: sqlite3.Row) -> tuple[str, str]:
    letra = limpiar(fila["respuesta_correcta"]).upper()

    columnas = {
        "A": "opcion_a",
        "B": "opcion_b",
        "C": "opcion_c",
        "D": "opcion_d",
    }

    columna = columnas.get(letra)
    if columna is None:
        raise ValueError(
            f"Respuesta correcta no válida: {fila['respuesta_correcta']!r}"
        )

    texto = limpiar(fila[columna])
    if not texto:
        raise ValueError(
            f"La opción correcta {letra} no contiene texto."
        )

    return letra, texto


def construir_prompt(texto: str) -> str:
    return (
        "¿Qué artículo de qué norma de la legislación española contiene "
        "textualmente o casi textualmente el siguiente texto?\n\n"
        "Si encuentras más de una coincidencia, descarta todas"
        "Devuelve únicamente: norma y artículo.\n\n"
        "Si no puedes identificarlo con mucha seguridad, "
        "responde 'NO ENCONTRADO'.\n\n"
        f"Texto: «{texto}»"
    )


def escribir_csv(ruta: Path, filas: list[dict]) -> None:
    columnas = [
        "pregunta_id",
        "enunciado",
        "letra_correcta",
        "texto_respuesta_correcta",
        "respuesta_ia",
        "estado",
        "error",
    ]

    with ruta.open("w", newline="", encoding="utf-8-sig") as fichero:
        escritor = csv.DictWriter(fichero, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(filas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Busca norma y artículo mediante la respuesta correcta "
            "de preguntas PENDIENTES."
        )
    )
    parser.add_argument(
        "--db",
        default=str(DB_DEFECTO),
        help=f"Base SQLite. Por defecto: {DB_DEFECTO}",
    )
    parser.add_argument(
        "--id",
        type=int,
        help="Procesa una única pregunta.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=20,
        help="Máximo de preguntas. Por defecto: 20.",
    )
    parser.add_argument(
        "--modelo",
        default=MODELO_DEFECTO,
        help=f"Modelo. Por defecto: {MODELO_DEFECTO}",
    )
    args = parser.parse_args()

    if args.id is not None and args.id <= 0:
        raise ValueError("--id debe ser mayor que cero.")

    if args.limite is not None and args.limite <= 0:
        raise ValueError("--limite debe ser mayor que cero.")

    db = Path(args.db).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")

    carpeta = RAIZ / "auditorias" / "buscar_norma_respuesta_correcta"

    if carpeta.exists():
        shutil.rmtree(carpeta)

    carpeta.mkdir(parents=True, exist_ok=True)

    resultados: list[dict] = []
    encontradas = 0
    no_encontradas = 0
    errores = 0

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA query_only = ON")
        validar_estructura(conexion)

        sql = """
            SELECT
                id,
                enunciado,
                opcion_a,
                opcion_b,
                opcion_c,
                opcion_d,
                respuesta_correcta
            FROM lote_preguntas
            WHERE tipo_clasificacion = 'PENDIENTE'
        """
        parametros: list[object] = []

        if args.id is not None:
            sql += " AND id = ?"
            parametros.append(args.id)

        sql += " ORDER BY id"

        if args.id is None and args.limite is not None:
            sql += " LIMIT ?"
            parametros.append(args.limite)

        preguntas = conexion.execute(
            sql,
            tuple(parametros),
        ).fetchall()

    if args.id is not None and not preguntas:
        raise RuntimeError(
            f"La pregunta {args.id} no existe o no está PENDIENTE."
        )

    print("BÚSQUEDA DE NORMA POR RESPUESTA CORRECTA")
    print("=" * 70)
    print(f"Base: {db}")
    print(f"Modelo: {args.modelo}")
    print(f"Preguntas: {len(preguntas)}")
    print("La base de datos no será modificada.")

    for posicion, fila in enumerate(preguntas, start=1):
        pregunta_id = int(fila["id"])
        enunciado = limpiar(fila["enunciado"])

        print(f"\n[{posicion}/{len(preguntas)}] Pregunta {pregunta_id}")

        registro = {
            "pregunta_id": pregunta_id,
            "enunciado": enunciado,
            "letra_correcta": "",
            "texto_respuesta_correcta": "",
            "respuesta_ia": "",
            "estado": "",
            "error": "",
        }

        try:
            letra, texto = texto_respuesta_correcta(fila)
            registro["letra_correcta"] = letra
            registro["texto_respuesta_correcta"] = texto

            prompt = construir_prompt(texto)

            respuesta = seleccionar_fragmento(
                prompt=prompt,
                modelo=args.modelo,
                operacion="buscar_norma_respuesta_correcta",
            )

            respuesta = limpiar(respuesta)
            registro["respuesta_ia"] = respuesta

            if respuesta.upper() == "NO ENCONTRADO":
                registro["estado"] = "NO_ENCONTRADO"
                no_encontradas += 1
            else:
                registro["estado"] = "PROPUESTA"
                encontradas += 1

            print(f"Respuesta IA: {respuesta}")

        except Exception as exc:
            registro["estado"] = "ERROR"
            registro["error"] = f"{exc.__class__.__name__}: {exc}"
            errores += 1
            print(f"ERROR: {registro['error']}", file=sys.stderr)

        resultados.append(registro)

    ruta_csv = carpeta / "resultados.csv"
    escribir_csv(ruta_csv, resultados)

    resumen = [
        "BÚSQUEDA DE NORMA POR RESPUESTA CORRECTA",
        "=" * 60,
        f"Modelo: {args.modelo}",
        f"Preguntas procesadas: {len(resultados)}",
        f"Propuestas: {encontradas}",
        f"No encontradas: {no_encontradas}",
        f"Errores: {errores}",
        "",
        "La base de datos no ha sido modificada.",
        f"Resultados: {ruta_csv}",
    ]

    ruta_resumen = carpeta / "resumen.txt"
    ruta_resumen.write_text(
        "\n".join(resumen) + "\n",
        encoding="utf-8-sig",
    )

    print()
    print("\n".join(resumen))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nProceso interrumpido por el usuario.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(
            f"\nERROR: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
