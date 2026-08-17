"""
Enlaza las normas de lote_preguntas y temario_referencias
con el catálogo central de la tabla normas.

Actualiza únicamente:

- lote_preguntas.norma_id_normalizada
- temario_referencias.norma_id

La correspondencia se realiza mediante la clave normalizada
generada por normalizar_norma().
"""

from pathlib import Path
import sqlite3

from normalizador_normas import normalizar_norma


RUTA_BD = Path(__file__).resolve().parent.parent / "db" / "oposiciones.sqlite3"


def cargar_catalogo(
    conexion: sqlite3.Connection,
) -> dict[str, int]:
    filas = conexion.execute(
        """
        SELECT id, clave_normalizada
        FROM normas
        """
    ).fetchall()

    return {
        clave_normalizada: norma_id
        for norma_id, clave_normalizada in filas
    }


def enlazar_lote_preguntas(
    conexion: sqlite3.Connection,
    catalogo: dict[str, int],
) -> tuple[int, int]:
    filas = conexion.execute(
        """
        SELECT id, nombre_norma_normalizado, norma_id_normalizada
        FROM lote_preguntas
        WHERE nombre_norma_normalizado IS NOT NULL
          AND TRIM(nombre_norma_normalizado) <> ''
        """
    ).fetchall()

    actualizadas = 0
    sin_correspondencia = 0

    for pregunta_id, nombre_norma, norma_actual in filas:
        clave = normalizar_norma(nombre_norma)
        norma_id = catalogo.get(clave)

        if norma_id is None:
            sin_correspondencia += 1
            continue

        if norma_actual == norma_id:
            continue

        conexion.execute(
            """
            UPDATE lote_preguntas
            SET norma_id_normalizada = ?
            WHERE id = ?
            """,
            (norma_id, pregunta_id),
        )

        actualizadas += 1

    return actualizadas, sin_correspondencia


def enlazar_temario_referencias(
    conexion: sqlite3.Connection,
    catalogo: dict[str, int],
) -> tuple[int, int]:
    filas = conexion.execute(
        """
        SELECT id, nombre_norma_normalizada, norma_id
        FROM temario_referencias
        WHERE nombre_norma_normalizada IS NOT NULL
          AND TRIM(nombre_norma_normalizada) <> ''
        """
    ).fetchall()

    actualizadas = 0
    sin_correspondencia = 0

    for referencia_id, nombre_norma, norma_actual in filas:
        clave = normalizar_norma(nombre_norma)
        norma_id = catalogo.get(clave)

        if norma_id is None:
            sin_correspondencia += 1
            continue

        if norma_actual == norma_id:
            continue

        conexion.execute(
            """
            UPDATE temario_referencias
            SET norma_id = ?
            WHERE id = ?
            """,
            (norma_id, referencia_id),
        )

        actualizadas += 1

    return actualizadas, sin_correspondencia


def main() -> None:
    if not RUTA_BD.exists():
        raise FileNotFoundError(
            f"No existe la base de datos: {RUTA_BD}"
        )

    with sqlite3.connect(RUTA_BD) as conexion:
        catalogo = cargar_catalogo(conexion)

        if not catalogo:
            raise RuntimeError(
                "La tabla normas está vacía."
            )

        lote_actualizadas, lote_sin_correspondencia = (
            enlazar_lote_preguntas(conexion, catalogo)
        )

        temario_actualizadas, temario_sin_correspondencia = (
            enlazar_temario_referencias(conexion, catalogo)
        )

        conexion.commit()

    print("Lote de preguntas")
    print("-----------------")
    print(f"Actualizadas:          {lote_actualizadas}")
    print(
        f"Sin correspondencia:   "
        f"{lote_sin_correspondencia}"
    )

    print()

    print("Temario referencias")
    print("-------------------")
    print(f"Actualizadas:          {temario_actualizadas}")
    print(
        f"Sin correspondencia:   "
        f"{temario_sin_correspondencia}"
    )


if __name__ == "__main__":
    main()
