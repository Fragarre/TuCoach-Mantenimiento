"""
Modificación manual de preguntas de lote_preguntas.

Uso:
    python scripts/modificar_pregunta_manual.py
    python scripts/modificar_pregunta_manual.py --id 123
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
REGISTRO_PREDETERMINADO = RAIZ / "registros" / "modificaciones_manual_lote_preguntas.csv"

CAMPOS_EDITABLES = [
    "enunciado",
    "opcion_a",
    "opcion_b",
    "opcion_c",
    "opcion_d",
    "respuesta_correcta",
    "tipo_clasificacion",
    "tipo_norma",
    "nombre_norma",
    "articulo",
    "tema_no_juridico",
    "origen_oposicion",
    "tipo_fuente",
    "pagina_origen",
    "norma_id_normalizada",
    "articulo_normalizado",
    "teorica_practica",
    "tipo_norma_normalizado",
    "nombre_norma_normalizado",
]

CAMPOS_OBLIGATORIOS = {
    "enunciado",
    "opcion_a",
    "opcion_b",
    "opcion_c",
    "opcion_d",
    "respuesta_correcta",
    "tipo_clasificacion",
    "tipo_fuente",
}

CAMPOS_ENTEROS = {"pagina_origen", "norma_id_normalizada"}

VALORES_CONTROLADOS = {
    "respuesta_correcta": {"A", "B", "C", "D"},
    "tipo_clasificacion": {"JURIDICA", "INFORMATICA", "PENDIENTE"},
    "teorica_practica": {"TEORICA", "PRACTICA"},
}


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Modifica manualmente una pregunta de lote_preguntas."
    )
    parser.add_argument("--db", default=str(DB_PREDETERMINADA))
    parser.add_argument("--id", type=int)
    parser.add_argument("--registro", default=str(REGISTRO_PREDETERMINADO))
    return parser.parse_args()


def mostrar(valor: Any) -> str:
    return "NULL" if valor is None else str(valor)


def leer_pregunta(
    conexion: sqlite3.Connection,
    pregunta_id: int,
) -> sqlite3.Row | None:
    conexion.row_factory = sqlite3.Row
    return conexion.execute(
        "SELECT * FROM lote_preguntas WHERE id = ?",
        (pregunta_id,),
    ).fetchone()


def mostrar_pregunta(fila: sqlite3.Row) -> None:
    print("\n" + "=" * 78)
    print(f"PREGUNTA ID {fila['id']}")
    print("=" * 78)

    for campo in fila.keys():
        print(f"{campo}:")
        print(f"  {mostrar(fila[campo])}")

    print("=" * 78)


def pedir_id() -> int:
    while True:
        valor = input("ID de la pregunta: ").strip()
        if valor.isdigit() and int(valor) > 0:
            return int(valor)
        print("El ID debe ser un entero mayor que cero.")


def listar_campos(fila: sqlite3.Row) -> None:
    print("\nCampos editables:")
    for numero, campo in enumerate(CAMPOS_EDITABLES, start=1):
        print(f"{numero:2}. {campo}: {mostrar(fila[campo])}")
    print(" 0. Terminar selección")


def convertir(campo: str, entrada: str) -> Any:
    if entrada.upper() == "NULL":
        if campo in CAMPOS_OBLIGATORIOS:
            raise ValueError(f"{campo} no admite NULL.")
        return None

    if campo in CAMPOS_ENTEROS:
        if not entrada.isdigit():
            raise ValueError(f"{campo} debe ser un entero o NULL.")
        return int(entrada)

    if campo in VALORES_CONTROLADOS:
        valor = entrada.strip().upper()
        permitidos = VALORES_CONTROLADOS[campo]
        if valor not in permitidos:
            raise ValueError(
                f"Valores permitidos: {', '.join(sorted(permitidos))}."
            )
        return valor

    valor = entrada.strip()
    if campo in CAMPOS_OBLIGATORIOS and not valor:
        raise ValueError(f"{campo} no puede quedar vacío.")
    return valor


def pedir_cambios(fila: sqlite3.Row) -> dict[str, Any]:
    cambios: dict[str, Any] = {}

    while True:
        listar_campos(fila)
        seleccion = input("\nNúmero del campo a modificar: ").strip()

        if seleccion == "0":
            return cambios

        if not seleccion.isdigit():
            print("Selección no válida.")
            continue

        indice = int(seleccion) - 1
        if indice < 0 or indice >= len(CAMPOS_EDITABLES):
            print("Selección no válida.")
            continue

        campo = CAMPOS_EDITABLES[indice]
        actual = cambios.get(campo, fila[campo])

        print(f"\nCampo: {campo}")
        print(f"Valor actual: {mostrar(actual)}")
        print("Escriba NULL para borrar un campo opcional.")
        entrada = input("Nuevo valor: ")

        try:
            nuevo = convertir(campo, entrada)
        except ValueError as error:
            print(f"ERROR: {error}")
            continue

        if nuevo == fila[campo]:
            cambios.pop(campo, None)
            print("No hay cambio respecto al valor original.")
        else:
            cambios[campo] = nuevo
            print("Cambio añadido.")


def mostrar_cambios(
    fila: sqlite3.Row,
    cambios: dict[str, Any],
) -> None:
    print("\n" + "=" * 78)
    print("CAMBIOS PROPUESTOS")
    print("=" * 78)

    for campo, nuevo in cambios.items():
        print(f"{campo}:")
        print(f"  ANTES:   {mostrar(fila[campo])}")
        print(f"  DESPUÉS: {mostrar(nuevo)}")

    print("=" * 78)


def confirmar() -> bool:
    respuesta = input(
        "¿Guardar estos cambios en lote_preguntas? [s/N]: "
    ).strip().lower()
    return respuesta in {"s", "si", "sí"}


def actualizar(
    conexion: sqlite3.Connection,
    pregunta_id: int,
    cambios: dict[str, Any],
) -> None:
    asignaciones = ", ".join(f"{campo} = ?" for campo in cambios)
    valores = [*cambios.values(), pregunta_id]

    cursor = conexion.execute(
        f"UPDATE lote_preguntas SET {asignaciones} WHERE id = ?",
        valores,
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            f"Se esperaba modificar una pregunta y se modificaron "
            f"{cursor.rowcount}."
        )


def registrar(
    ruta: Path,
    pregunta_id: int,
    original: sqlite3.Row,
    cambios: dict[str, Any],
) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    existe = ruta.exists()

    anteriores = {campo: original[campo] for campo in cambios}

    with ruta.open("a", encoding="utf-8-sig", newline="") as fichero:
        escritor = csv.writer(fichero, delimiter=";")

        if not existe:
            escritor.writerow(
                [
                    "fecha_hora",
                    "pregunta_id",
                    "campos_modificados",
                    "valores_anteriores",
                    "valores_nuevos",
                ]
            )

        escritor.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                pregunta_id,
                ", ".join(cambios.keys()),
                json.dumps(anteriores, ensure_ascii=False, default=str),
                json.dumps(cambios, ensure_ascii=False, default=str),
            ]
        )


def main() -> int:
    args = argumentos()
    db = Path(args.db).expanduser().resolve()
    registro = Path(args.registro).expanduser().resolve()

    if not db.is_file():
        print(f"ERROR: no existe la base de datos: {db}")
        return 1

    pregunta_id = args.id or pedir_id()

    try:
        with sqlite3.connect(db) as conexion:
            fila = leer_pregunta(conexion, pregunta_id)

            if fila is None:
                print(f"No existe la pregunta ID {pregunta_id}.")
                return 1

            mostrar_pregunta(fila)
            cambios = pedir_cambios(fila)

            if not cambios:
                print("\nNo se ha definido ningún cambio.")
                return 0

            mostrar_cambios(fila, cambios)

            if not confirmar():
                print("\nOperación cancelada. No se ha modificado la base.")
                return 0

            try:
                conexion.execute("BEGIN")
                actualizar(conexion, pregunta_id, cambios)
                registrar(registro, pregunta_id, fila, cambios)
                conexion.commit()
            except Exception:
                conexion.rollback()
                raise

            print("\nPregunta modificada correctamente.")
            print(f"Registro: {registro}")

            fila_actualizada = leer_pregunta(conexion, pregunta_id)
            if fila_actualizada is not None:
                mostrar_pregunta(fila_actualizada)

    except (OSError, sqlite3.Error, RuntimeError) as error:
        print(f"ERROR: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
