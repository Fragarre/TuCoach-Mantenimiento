"""
Auditoría funcional del banco de preguntas.

Este script es de solo lectura:
- no modifica lote_preguntas;
- no modifica banco_preguntas;
- no modifica ninguna tabla.

Genera:
- resumen_general.txt
- distribucion_por_tema.csv
- temas_sin_preguntas.csv
- distribucion_por_origen.csv
- distribucion_teorica_practica.csv
- duplicados_banco.csv
- preguntas_vinculadas_a_varios_temas.csv

Uso:
    python scripts\\auditar_banco_preguntas.py

Convocatoria concreta:
    python scripts\\auditar_banco_preguntas.py --convocatoria-id 1

Base alternativa:
    python scripts\\auditar_banco_preguntas.py --db ruta\\oposiciones.sqlite3
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def escribir_csv(ruta: Path, filas: list[dict]) -> None:
    if not filas:
        ruta.write_text("", encoding="utf-8-sig")
        return

    columnas: list[str] = []
    for fila in filas:
        for columna in fila:
            if columna not in columnas:
                columnas.append(columna)

    with ruta.open("w", newline="", encoding="utf-8-sig") as fichero:
        escritor = csv.DictWriter(fichero, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(filas)


def consultar_diccionarios(
    conexion: sqlite3.Connection,
    sql: str,
    parametros: tuple = (),
) -> list[dict]:
    return [dict(fila) for fila in conexion.execute(sql, parametros)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument("--convocatoria-id", type=int)
    argumentos = parser.parse_args()

    db = Path(argumentos.db).resolve()
    if not db.exists():
        raise FileNotFoundError(f"No existe la base de datos: {db}")

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = RAIZ / "auditorias" / f"auditoria_banco_{marca}"
    carpeta.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA query_only = ON")
        conexion.execute("PRAGMA foreign_keys = ON")

        columnas_conv = {
            str(r["name"])
            for r in conexion.execute("PRAGMA table_info(convocatorias)")
        }
        if "activa" in columnas_conv:
            convocatorias = conexion.execute(
                "SELECT id FROM convocatorias WHERE activa = 1 ORDER BY id"
            ).fetchall()
        else:
            convocatorias = conexion.execute(
                "SELECT id FROM convocatorias ORDER BY id"
            ).fetchall()

        if argumentos.convocatoria_id is None:
            if len(convocatorias) != 1:
                raise RuntimeError(
                    "Indica --convocatoria-id: "
                    f"hay {len(convocatorias)} convocatorias."
                )
            convocatoria_id = int(convocatorias[0]["id"])
        else:
            convocatoria_id = argumentos.convocatoria_id

        convocatoria = conexion.execute(
            """
            SELECT *
            FROM convocatorias
            WHERE id = ?
            """,
            (convocatoria_id,),
        ).fetchone()

        if convocatoria is None:
            raise RuntimeError(
                f"No existe la convocatoria {convocatoria_id}."
            )

        temarios = conexion.execute(
            """
            SELECT id
            FROM temarios
            WHERE convocatoria_id = ?
            ORDER BY id
            """,
            (convocatoria_id,),
        ).fetchall()

        if len(temarios) != 1:
            raise RuntimeError(
                "La convocatoria debe tener exactamente un temario."
            )

        temario_id = int(temarios[0]["id"])

        total_banco = conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
            """,
            (convocatoria_id,),
        ).fetchone()[0]

        juridicas = conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
              AND tipo_vinculacion = 'JURIDICA'
            """,
            (convocatoria_id,),
        ).fetchone()[0]

        no_juridicas = conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
              AND tipo_vinculacion = 'NO_JURIDICA'
            """,
            (convocatoria_id,),
        ).fetchone()[0]

        revision = conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
              AND estado = 'REVISION'
            """,
            (convocatoria_id,),
        ).fetchone()[0]

        distribucion_temas = consultar_diccionarios(
            conexion,
            """
            SELECT
                tt.id AS tema_id,
                tt.parte,
                tt.numero_tema,
                tt.titulo,
                COUNT(DISTINCT bp.id) AS total_preguntas,
                COUNT(
                    DISTINCT CASE
                        WHEN bp.tipo_vinculacion = 'JURIDICA'
                        THEN bp.id
                    END
                ) AS juridicas,
                COUNT(
                    DISTINCT CASE
                        WHEN bp.tipo_vinculacion = 'NO_JURIDICA'
                        THEN bp.id
                    END
                ) AS no_juridicas
            FROM temario_temas AS tt
            LEFT JOIN banco_preguntas_temas AS bpt
              ON bpt.tema_id = tt.id
            LEFT JOIN banco_preguntas AS bp
              ON bp.id = bpt.banco_pregunta_id
             AND bp.convocatoria_id = ?
            WHERE tt.temario_id = ?
            GROUP BY
                tt.id,
                tt.parte,
                tt.numero_tema,
                tt.titulo
            ORDER BY
                tt.parte,
                tt.numero_tema,
                tt.id
            """,
            (convocatoria_id, temario_id),
        )

        temas_sin_preguntas = [
            fila
            for fila in distribucion_temas
            if int(fila["total_preguntas"]) == 0
        ]

        distribucion_origen = consultar_diccionarios(
            conexion,
            """
            SELECT
                COALESCE(lp.origen_oposicion, '(SIN ORIGEN)') AS origen,
                COUNT(*) AS total_preguntas,
                SUM(
                    CASE
                        WHEN bp.tipo_vinculacion = 'JURIDICA'
                        THEN 1 ELSE 0
                    END
                ) AS juridicas,
                SUM(
                    CASE
                        WHEN bp.tipo_vinculacion = 'NO_JURIDICA'
                        THEN 1 ELSE 0
                    END
                ) AS no_juridicas
            FROM banco_preguntas AS bp
            JOIN lote_preguntas AS lp
              ON lp.id = bp.pregunta_id
            WHERE bp.convocatoria_id = ?
            GROUP BY COALESCE(
                lp.origen_oposicion,
                '(SIN ORIGEN)'
            )
            ORDER BY total_preguntas DESC, origen
            """,
            (convocatoria_id,),
        )

        distribucion_tipo = consultar_diccionarios(
            conexion,
            """
            SELECT
                COALESCE(
                    NULLIF(TRIM(lp.teorica_practica), ''),
                    '(SIN CLASIFICAR)'
                ) AS teorica_practica,
                COUNT(*) AS total_preguntas,
                SUM(
                    CASE
                        WHEN bp.tipo_vinculacion = 'JURIDICA'
                        THEN 1 ELSE 0
                    END
                ) AS juridicas,
                SUM(
                    CASE
                        WHEN bp.tipo_vinculacion = 'NO_JURIDICA'
                        THEN 1 ELSE 0
                    END
                ) AS no_juridicas
            FROM banco_preguntas AS bp
            JOIN lote_preguntas AS lp
              ON lp.id = bp.pregunta_id
            WHERE bp.convocatoria_id = ?
            GROUP BY COALESCE(
                NULLIF(TRIM(lp.teorica_practica), ''),
                '(SIN CLASIFICAR)'
            )
            ORDER BY total_preguntas DESC, teorica_practica
            """,
            (convocatoria_id,),
        )

        duplicados_banco = consultar_diccionarios(
            conexion,
            """
            SELECT
                pregunta_id,
                COUNT(*) AS apariciones,
                GROUP_CONCAT(id) AS banco_ids
            FROM banco_preguntas
            WHERE convocatoria_id = ?
            GROUP BY pregunta_id
            HAVING COUNT(*) > 1
            ORDER BY apariciones DESC, pregunta_id
            """,
            (convocatoria_id,),
        )

        varios_temas = consultar_diccionarios(
            conexion,
            """
            SELECT
                bp.id AS banco_pregunta_id,
                bp.pregunta_id,
                bp.tipo_vinculacion,
                COUNT(DISTINCT bpt.tema_id) AS numero_temas,
                GROUP_CONCAT(
                    DISTINCT tt.numero_tema
                ) AS temas
            FROM banco_preguntas AS bp
            JOIN banco_preguntas_temas AS bpt
              ON bpt.banco_pregunta_id = bp.id
            JOIN temario_temas AS tt
              ON tt.id = bpt.tema_id
            WHERE bp.convocatoria_id = ?
            GROUP BY
                bp.id,
                bp.pregunta_id,
                bp.tipo_vinculacion
            HAVING COUNT(DISTINCT bpt.tema_id) > 1
            ORDER BY numero_temas DESC, bp.pregunta_id
            """,
            (convocatoria_id,),
        )

        enlaces_sin_tema = conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas AS bp
            LEFT JOIN banco_preguntas_temas AS bpt
              ON bpt.banco_pregunta_id = bp.id
            WHERE bp.convocatoria_id = ?
              AND bpt.banco_pregunta_id IS NULL
            """,
            (convocatoria_id,),
        ).fetchone()[0]

        enlaces_fuera_temario = conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas AS bp
            JOIN banco_preguntas_temas AS bpt
              ON bpt.banco_pregunta_id = bp.id
            JOIN temario_temas AS tt
              ON tt.id = bpt.tema_id
            WHERE bp.convocatoria_id = ?
              AND tt.temario_id <> ?
            """,
            (convocatoria_id, temario_id),
        ).fetchone()[0]

        errores_fk = conexion.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        escribir_csv(
            carpeta / "distribucion_por_tema.csv",
            distribucion_temas,
        )
        escribir_csv(
            carpeta / "temas_sin_preguntas.csv",
            temas_sin_preguntas,
        )
        escribir_csv(
            carpeta / "distribucion_por_origen.csv",
            distribucion_origen,
        )
        escribir_csv(
            carpeta / "distribucion_teorica_practica.csv",
            distribucion_tipo,
        )
        escribir_csv(
            carpeta / "duplicados_banco.csv",
            duplicados_banco,
        )
        escribir_csv(
            carpeta / "preguntas_vinculadas_a_varios_temas.csv",
            varios_temas,
        )

        resumen = [
            "AUDITORÍA FUNCIONAL DEL BANCO",
            "=" * 60,
            f"Convocatoria ID: {convocatoria_id}",
            f"Temario ID: {temario_id}",
            "",
            f"Total banco: {total_banco}",
            f"Jurídicas: {juridicas}",
            f"No jurídicas: {no_juridicas}",
            f"En revisión: {revision}",
            "",
            f"Temas del temario: {len(distribucion_temas)}",
            f"Temas sin preguntas: {len(temas_sin_preguntas)}",
            f"Preguntas repetidas en banco: {len(duplicados_banco)}",
            (
                "Preguntas vinculadas a varios temas: "
                f"{len(varios_temas)}"
            ),
            f"Preguntas sin tema asociado: {enlaces_sin_tema}",
            (
                "Vínculos con temas de otro temario: "
                f"{enlaces_fuera_temario}"
            ),
            f"Errores de claves externas: {len(errores_fk)}",
            "",
            "La auditoría no modifica ninguna tabla.",
        ]

        (carpeta / "resumen_general.txt").write_text(
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
            f"ERROR: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)