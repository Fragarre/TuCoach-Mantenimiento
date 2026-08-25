"""
OpoCoach-Mantenimiento - Edición segura de partes de una convocatoria.

Modifica exclusivamente convocatoria_partes.nombre, numero_preguntas y orden.
Conserva convocatoria_partes.id, por lo que no recrea partes ni rompe sus FK.

Reglas:
- Vista previa antes de escribir.
- La suma final de numero_preguntas debe coincidir con convocatorias.numero_preguntas.
- Los órdenes finales deben ser positivos y únicos.
- Si existen bloques de modelo para una parte, su suma debe coincidir con el nuevo
  numero_preguntas; de lo contrario no se permite guardar.
- Backup obligatorio antes de escribir.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
COPIAS = RAIZ / "db" / "copias_seguridad"


@dataclass(frozen=True)
class Parte:
    id: int
    nombre: str
    numero_preguntas: int
    orden: int


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def pedir_entero(mensaje: str, minimo: int = 0, maximo: int | None = None) -> int:
    while True:
        valor = input(mensaje).strip()
        if valor.isdigit():
            n = int(valor)
            if n >= minimo and (maximo is None or n <= maximo):
                return n
        if maximo is None:
            print(f"Debe ser un entero igual o mayor que {minimo}.")
        else:
            print(f"Debe ser un entero entre {minimo} y {maximo}.")


def pedir_si_no(mensaje: str) -> bool:
    while True:
        v = input(mensaje + " [s/N]: ").strip().lower()
        if not v:
            return False
        if v in {"s", "si", "sí"}:
            return True
        if v in {"n", "no"}:
            return False
        print("Responde S o N.")


def backup() -> Path:
    COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = COPIAS / f"{DB.stem}_antes_editar_partes_{marca}{DB.suffix}"
    shutil.copy2(DB, destino)
    return destino


def tabla_existe(con: sqlite3.Connection, nombre: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nombre,),
    ).fetchone() is not None


def convocatorias(con: sqlite3.Connection) -> list[sqlite3.Row]:
    columnas = {str(r["name"]) for r in con.execute("PRAGMA table_info(convocatorias)")}
    where = " WHERE activa=1" if "activa" in columnas else ""
    return con.execute(
        "SELECT id, codigo, puesto, numero_preguntas FROM convocatorias"
        + where + " ORDER BY id"
    ).fetchall()


def partes(con: sqlite3.Connection, convocatoria_id: int) -> list[Parte]:
    filas = con.execute(
        """
        SELECT id, nombre, numero_preguntas, orden
        FROM convocatoria_partes
        WHERE convocatoria_id=?
        ORDER BY orden, id
        """,
        (convocatoria_id,),
    ).fetchall()
    return [
        Parte(int(f["id"]), str(f["nombre"]), int(f["numero_preguntas"]), int(f["orden"]))
        for f in filas
    ]


def mostrar(lista: list[Parte]) -> None:
    print("\nPARTES")
    print("-" * 78)
    for i, p in enumerate(sorted(lista, key=lambda x: (x.orden, x.id)), 1):
        print(
            f"{i:>3}. id={p.id:<4} | orden={p.orden:<3} | "
            f"{p.nombre:<38} | preguntas={p.numero_preguntas}"
        )
    print("-" * 78)
    print(f"Total: {sum(p.numero_preguntas for p in lista)}")


def errores_finales(
    con: sqlite3.Connection,
    lista: list[Parte],
    total_convocatoria: int,
) -> list[str]:
    errores: list[str] = []

    if sum(p.numero_preguntas for p in lista) != total_convocatoria:
        errores.append(
            f"La suma de las partes es {sum(p.numero_preguntas for p in lista)}, "
            f"pero la convocatoria exige {total_convocatoria}."
        )

    ordenes = [p.orden for p in lista]
    if any(o <= 0 for o in ordenes):
        errores.append("Todos los órdenes deben ser mayores que cero.")
    if len(set(ordenes)) != len(ordenes):
        errores.append("No puede haber dos partes con el mismo orden.")

    if tabla_existe(con, "convocatoria_modelo_bloques"):
        for p in lista:
            fila = con.execute(
                """
                SELECT COUNT(*) AS n, COALESCE(SUM(cantidad), 0) AS total
                FROM convocatoria_modelo_bloques
                WHERE convocatoria_parte_id=?
                """,
                (p.id,),
            ).fetchone()
            if int(fila["n"]) and int(fila["total"]) != p.numero_preguntas:
                errores.append(
                    f"Parte '{p.nombre}' (id={p.id}): el modelo existente suma "
                    f"{int(fila['total'])} preguntas y la parte quedaría en "
                    f"{p.numero_preguntas}. Modifique primero el modelo o mantenga "
                    "el número actual."
                )

    return errores


def main() -> int:
    if not DB.is_file():
        print(f"ERROR: no existe la base:\n{DB}")
        return 1

    with conectar() as con:
        convs = convocatorias(con)
        if not convs:
            print("No existen convocatorias activas.")
            return 0

        print("=" * 78)
        print("EDITAR PARTES DE CONVOCATORIA")
        print("=" * 78)
        for i, c in enumerate(convs, 1):
            print(
                f"{i:>3}. [{c['id']}] {c['codigo']:<20} | "
                f"{c['puesto']:<35} | {c['numero_preguntas']} preguntas"
            )
        print("  0. Cancelar")
        idx = pedir_entero("Convocatoria: ", 0, len(convs))
        if idx == 0:
            return 0

        conv = convs[idx - 1]
        actuales = partes(con, int(conv["id"]))
        if not actuales:
            print("La convocatoria no tiene partes.")
            return 0

        trabajo = list(actuales)

        while True:
            mostrar(trabajo)
            print("\n1. Modificar nombre")
            print("2. Modificar número de preguntas")
            print("3. Modificar orden")
            print("4. Guardar cambios")
            print("0. Cancelar sin guardar")
            op = input("Opción: ").strip()

            if op == "0":
                print("No se ha modificado la base.")
                return 0

            if op in {"1", "2", "3"}:
                ordenadas = sorted(trabajo, key=lambda x: (x.orden, x.id))
                n = pedir_entero("Número de la parte: ", 1, len(ordenadas))
                elegida = ordenadas[n - 1]
                pos = next(i for i, p in enumerate(trabajo) if p.id == elegida.id)

                if op == "1":
                    nuevo = input(f"Nuevo nombre [{elegida.nombre}]: ").strip()
                    if not nuevo:
                        print("Sin cambios.")
                        continue
                    trabajo[pos] = replace(elegida, nombre=nuevo)

                elif op == "2":
                    nuevo = pedir_entero(
                        f"Nuevo número de preguntas [{elegida.numero_preguntas}]: ",
                        0,
                    )
                    trabajo[pos] = replace(elegida, numero_preguntas=nuevo)

                else:
                    nuevo = pedir_entero(
                        f"Nuevo orden [{elegida.orden}]: ",
                        1,
                    )
                    trabajo[pos] = replace(elegida, orden=nuevo)
                continue

            if op == "4":
                if trabajo == actuales:
                    print("No hay cambios que guardar.")
                    continue

                errores = errores_finales(
                    con, trabajo, int(conv["numero_preguntas"])
                )
                if errores:
                    print("\nNO SE PUEDE GUARDAR:")
                    for e in errores:
                        print(f"  - {e}")
                    continue

                print("\nVISTA PREVIA FINAL")
                mostrar(trabajo)
                if not pedir_si_no("¿Aplicar exactamente estos cambios?"):
                    continue

                copia = backup()
                con.execute("BEGIN IMMEDIATE")
                try:
                    for p in trabajo:
                        con.execute(
                            """
                            UPDATE convocatoria_partes
                            SET nombre=?, numero_preguntas=?, orden=?,
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=? AND convocatoria_id=?
                            """,
                            (
                                p.nombre,
                                p.numero_preguntas,
                                p.orden,
                                p.id,
                                int(conv["id"]),
                            ),
                        )
                    con.commit()
                except Exception:
                    con.rollback()
                    raise

                guardadas = partes(con, int(conv["id"]))
                if sorted(guardadas, key=lambda p: p.id) != sorted(trabajo, key=lambda p: p.id):
                    raise RuntimeError("La verificación posterior no coincide con los cambios solicitados.")

                print(f"\nCopia de seguridad: {copia}")
                print("Partes actualizadas correctamente. Se han conservado sus IDs.")
                return 0

            print("Opción no válida.")


if __name__ == "__main__":
    raise SystemExit(main())
