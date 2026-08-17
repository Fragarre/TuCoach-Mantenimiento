from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def resolver_parte_convocatoria(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    pregunta: sqlite3.Row,
    tema: sqlite3.Row,
) -> tuple[int | None, str | None]:
    filas = conexion.execute(
        """
        SELECT DISTINCT r.convocatoria_parte_id, r.prioridad
        FROM convocatoria_parte_reglas AS r
        JOIN convocatoria_partes AS cp
          ON cp.id = r.convocatoria_parte_id
        WHERE cp.convocatoria_id = ?
          AND (r.temario_parte IS NULL OR UPPER(r.temario_parte) = UPPER(?))
          AND (r.tipo_contenido IS NULL OR UPPER(r.tipo_contenido) = UPPER(?))
          AND (r.teorica_practica IS NULL OR UPPER(r.teorica_practica) = UPPER(COALESCE(?, '')))
          AND (r.tema_no_juridico IS NULL OR UPPER(r.tema_no_juridico) = UPPER(COALESCE(?, '')))
        ORDER BY r.prioridad, r.convocatoria_parte_id
        """,
        (
            convocatoria_id,
            tema["parte"],
            tema["tipo_contenido"],
            pregunta["teorica_practica"],
            pregunta["tema_no_juridico"],
        ),
    ).fetchall()
    if not filas:
        return None, "SIN_REGLA_PARTE"
    prioridad = int(filas[0]["prioridad"])
    partes = sorted({
        int(fila["convocatoria_parte_id"])
        for fila in filas
        if int(fila["prioridad"]) == prioridad
    })
    if len(partes) != 1:
        return None, "REGLAS_PARTE_AMBIGUAS"
    return partes[0], None


def cargar_bancos(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            bp.id AS banco_pregunta_id,
            bp.convocatoria_id,
            bp.pregunta_id,
            bp.convocatoria_parte_id,
            lp.teorica_practica,
            lp.tema_no_juridico,
            tt.id AS tema_id,
            tt.parte,
            tt.tipo_contenido
        FROM banco_preguntas AS bp
        JOIN lote_preguntas AS lp
          ON lp.id = bp.pregunta_id
        JOIN banco_preguntas_temas AS bpt
          ON bpt.banco_pregunta_id = bp.id
         AND bpt.es_principal = 1
        JOIN temario_temas AS tt
          ON tt.id = bpt.tema_id
        ORDER BY bp.convocatoria_id, bp.id
        """
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rellena banco_preguntas.convocatoria_parte_id usando exclusivamente "
            "las reglas existentes en convocatoria_parte_reglas."
        )
    )
    parser.add_argument("--db", required=True, help="Ruta de oposiciones.sqlite3")
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        raise FileNotFoundError(db)

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")

        filas = cargar_bancos(con)
        actualizaciones: list[tuple[int, int]] = []
        errores: list[str] = []
        incorrectas: list[str] = []

        for fila in filas:
            parte_esperada, error = resolver_parte_convocatoria(
                con,
                int(fila["convocatoria_id"]),
                fila,
                fila,
            )
            if error or parte_esperada is None:
                errores.append(
                    f"banco_id={fila['banco_pregunta_id']} pregunta={fila['pregunta_id']} {error}"
                )
                continue

            parte_actual = fila["convocatoria_parte_id"]
            if parte_actual is None:
                actualizaciones.append((parte_esperada, int(fila["banco_pregunta_id"])))
            elif int(parte_actual) != parte_esperada:
                incorrectas.append(
                    f"banco_id={fila['banco_pregunta_id']} pregunta={fila['pregunta_id']} "
                    f"actual={parte_actual} esperada={parte_esperada}"
                )
                actualizaciones.append((parte_esperada, int(fila["banco_pregunta_id"])))

        print(f"Registros de banco revisados........ {len(filas)}")
        print(f"Partes nulas reparables............. {len(actualizaciones)}")
        print(f"Partes existentes incorrectas....... {len(incorrectas)}")
        print(f"Sin regla o regla ambigua............ {len(errores)}")

        if incorrectas:
            for linea in incorrectas[:20]:
                print("INCORRECTA |", linea)
        if errores:
            for linea in errores[:20]:
                print("ERROR |", linea)

        if errores:
            print("No se aplica ningún cambio mientras existan reglas sin resolver o ambiguas.")
            return 1

        if not args.aplicar:
            print("Modo revisión. La base no se ha modificado.")
            return 0

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    copia = db.with_name(f"{db.stem}_backup_antes_reparar_partes_{marca}{db.suffix}")
    shutil.copy2(db, copia)

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("BEGIN IMMEDIATE")
        con.executemany(
            """
            UPDATE banco_preguntas
            SET convocatoria_parte_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            actualizaciones,
        )
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
        nulos = con.execute(
            "SELECT COUNT(*) FROM banco_preguntas WHERE convocatoria_parte_id IS NULL"
        ).fetchone()[0]
        if fk or integridad != "ok" or nulos:
            con.rollback()
            raise RuntimeError(
                f"Validación posterior fallida: fk={len(fk)}, integrity={integridad}, nulos={nulos}"
            )
        con.commit()

    print(f"Copia de seguridad................... {copia}")
    print(f"Partes reparadas..................... {len(actualizaciones)}")
    print(f"  Incluye partes nulas o incorrectas.. {len(actualizaciones)}")
    print("Integridad SQLite.................... ok")
    print("Partes nulas restantes............... 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
