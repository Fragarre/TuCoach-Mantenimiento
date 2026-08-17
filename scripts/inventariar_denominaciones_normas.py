"""
OpoCoach - Inventario de denominaciones jurídicas en lote_preguntas.

No usa IA.
No modifica la base de datos.
Ignora norma_id_normalizada.

Uso:

    python scripts\\inventariar_denominaciones_normas.py

Base alternativa:

    python scripts\\inventariar_denominaciones_normas.py --db db\\oposiciones.sqlite3
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def normalizar_texto(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )
    texto = texto.lower()
    texto = re.sub(r"[_/\\.,;:()\[\]{}\-]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def escribir_csv(ruta: Path, filas: list[dict[str, Any]]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)

    if not filas:
        ruta.write_text("", encoding="utf-8-sig")
        return

    columnas: list[str] = []
    vistas: set[str] = set()

    for fila in filas:
        for columna in fila:
            if columna not in vistas:
                vistas.add(columna)
                columnas.append(columna)

    with ruta.open("w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(filas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFECTO))
    args = parser.parse_args()

    db = Path(args.db).resolve()
    if not db.exists():
        raise FileNotFoundError(f"No existe la base: {db}")

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = RAIZ / "auditorias" / f"denominaciones_normas_{marca}"
    carpeta.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row

        filas = conexion.execute(
            """
            SELECT
                id,
                tipo_norma,
                nombre_norma,
                articulo,
                tipo_fuente,
                origen_oposicion,
                fichero,
                pagina_origen
            FROM lote_preguntas
            WHERE tipo_clasificacion = 'JURIDICA'
            ORDER BY id
            """
        ).fetchall()

    agrupadas_exactas: dict[
        tuple[str, str], dict[str, Any]
    ] = {}

    agrupadas_normalizadas: dict[
        tuple[str, str], dict[str, Any]
    ] = {}

    variantes_por_normalizada: dict[
        tuple[str, str], set[tuple[str, str]]
    ] = defaultdict(set)

    sin_nombre: list[dict[str, Any]] = []

    for fila_sql in filas:
        fila = dict(fila_sql)

        tipo_original = "" if fila["tipo_norma"] is None else str(
            fila["tipo_norma"]
        ).strip()
        nombre_original = "" if fila["nombre_norma"] is None else str(
            fila["nombre_norma"]
        ).strip()

        tipo_normalizado = normalizar_texto(tipo_original)
        nombre_normalizado = normalizar_texto(nombre_original)

        if not nombre_original:
            sin_nombre.append(fila)

        clave_exacta = (tipo_original, nombre_original)

        if clave_exacta not in agrupadas_exactas:
            agrupadas_exactas[clave_exacta] = {
                "tipo_norma": tipo_original,
                "nombre_norma": nombre_original,
                "tipo_norma_normalizado": tipo_normalizado,
                "nombre_norma_normalizado": nombre_normalizado,
                "numero_preguntas": 0,
                "id_minimo": fila["id"],
                "id_maximo": fila["id"],
            }

        grupo_exacto = agrupadas_exactas[clave_exacta]
        grupo_exacto["numero_preguntas"] += 1
        grupo_exacto["id_minimo"] = min(
            grupo_exacto["id_minimo"], fila["id"]
        )
        grupo_exacto["id_maximo"] = max(
            grupo_exacto["id_maximo"], fila["id"]
        )

        clave_normalizada = (tipo_normalizado, nombre_normalizado)

        if clave_normalizada not in agrupadas_normalizadas:
            agrupadas_normalizadas[clave_normalizada] = {
                "tipo_norma_normalizado": tipo_normalizado,
                "nombre_norma_normalizado": nombre_normalizado,
                "numero_preguntas": 0,
                "numero_variantes_exactas": 0,
            }

        agrupadas_normalizadas[clave_normalizada]["numero_preguntas"] += 1
        variantes_por_normalizada[clave_normalizada].add(clave_exacta)

    inventario_exacto = sorted(
        agrupadas_exactas.values(),
        key=lambda fila: (
            -fila["numero_preguntas"],
            fila["tipo_norma_normalizado"],
            fila["nombre_norma_normalizado"],
        ),
    )

    inventario_normalizado: list[dict[str, Any]] = []

    for clave, grupo in agrupadas_normalizadas.items():
        variantes = sorted(
            variantes_por_normalizada[clave],
            key=lambda x: (x[0].lower(), x[1].lower()),
        )

        grupo_salida = dict(grupo)
        grupo_salida["numero_variantes_exactas"] = len(variantes)
        grupo_salida["variantes_exactas"] = " | ".join(
            f"tipo_norma={tipo!r}; nombre_norma={nombre!r}"
            for tipo, nombre in variantes
        )
        inventario_normalizado.append(grupo_salida)

    inventario_normalizado.sort(
        key=lambda fila: (
            -fila["numero_preguntas"],
            fila["tipo_norma_normalizado"],
            fila["nombre_norma_normalizado"],
        )
    )

    # Ejemplos por denominación exacta, sin alterar ni interpretar datos.
    ejemplos: list[dict[str, Any]] = []
    contador_ejemplos: dict[tuple[str, str], int] = defaultdict(int)

    for fila_sql in filas:
        fila = dict(fila_sql)
        tipo_original = "" if fila["tipo_norma"] is None else str(
            fila["tipo_norma"]
        ).strip()
        nombre_original = "" if fila["nombre_norma"] is None else str(
            fila["nombre_norma"]
        ).strip()
        clave = (tipo_original, nombre_original)

        if contador_ejemplos[clave] >= 3:
            continue

        contador_ejemplos[clave] += 1
        ejemplos.append(
            {
                "tipo_norma": tipo_original,
                "nombre_norma": nombre_original,
                "pregunta_id": fila["id"],
                "articulo": fila["articulo"],
                "tipo_fuente": fila["tipo_fuente"],
                "origen_oposicion": fila["origen_oposicion"],
                "fichero": fila["fichero"],
                "pagina_origen": fila["pagina_origen"],
            }
        )

    escribir_csv(
        carpeta / "inventario_denominaciones_exactas.csv",
        inventario_exacto,
    )
    escribir_csv(
        carpeta / "inventario_denominaciones_normalizadas.csv",
        inventario_normalizado,
    )
    escribir_csv(
        carpeta / "ejemplos_por_denominacion.csv",
        ejemplos,
    )
    escribir_csv(
        carpeta / "preguntas_sin_nombre_norma.csv",
        sin_nombre,
    )

    resumen = [
        "INVENTARIO DE DENOMINACIONES JURÍDICAS",
        "=" * 60,
        f"Base de datos: {db}",
        "",
        f"Preguntas jurídicas: {len(filas)}",
        f"Denominaciones exactas distintas: {len(inventario_exacto)}",
        (
            "Denominaciones normalizadas distintas: "
            f"{len(inventario_normalizado)}"
        ),
        f"Preguntas sin nombre_norma: {len(sin_nombre)}",
        "",
        "No se ha modificado la base de datos.",
        "norma_id_normalizada se ha ignorado completamente.",
        "",
        "Archivos generados:",
        "- inventario_denominaciones_exactas.csv",
        "- inventario_denominaciones_normalizadas.csv",
        "- ejemplos_por_denominacion.csv",
        "- preguntas_sin_nombre_norma.csv",
    ]

    (carpeta / "informe.txt").write_text(
        "\n".join(resumen) + "\n",
        encoding="utf-8-sig",
    )

    print("\n".join(resumen))
    print(f"\nInformes: {carpeta}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"\nERROR: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)