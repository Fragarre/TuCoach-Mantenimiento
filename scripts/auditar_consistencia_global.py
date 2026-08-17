#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
auditar_consistencia_global.py

Auditoría global y exclusivamente de lectura de:

1. lote_preguntas
2. banco_preguntas
3. banco_preguntas_temas
4. correspondencia entre el banco real y el banco que debería resultar
   de aplicar las reglas actuales de incorporación.

No modifica ninguna tabla.

REGLAS DEL BANCO ESPERADO
-------------------------

Preguntas jurídicas:
    lote_preguntas.tipo_clasificacion = 'JURIDICA'
    y cruce por:
        norma_id_normalizada
        articulo_normalizado
    contra:
        temario_referencias.norma_id
        temario_referencias.articulo_solicitado

Preguntas no jurídicas:
    lote_preguntas.tipo_clasificacion = 'INFORMATICA'
    y cruce por:
        tema_no_juridico
    contra:
        equivalencias_temas_no_juridicos.tema_no_juridico

Solo se consideran incorporables las vinculaciones inequívocas a un único
punto del temario.

SALIDAS
-------

Genera una carpeta en auditorias/auditoria_consistencia_global_YYYYMMDD_HHMMSS
con:

- resumen_general.txt
- lote_clasificaciones.csv
- lote_juridicas_sin_normalizacion.csv
- lote_juridicas_fuera_temario.csv
- lote_no_juridicas_sin_categoria.csv
- lote_no_juridicas_fuera_temario.csv
- lote_duplicados_enunciado.csv
- banco_incidencias.csv
- banco_esperado.csv
- banco_real.csv
- banco_faltantes.csv
- banco_sobrantes.csv
- banco_tema_incorrecto.csv

USO
---

    python scripts\\auditar_consistencia_global.py --convocatoria-id 1

o, si solo existe una convocatoria:

    python scripts\\auditar_consistencia_global.py
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


def normalizar_articulo(valor: Any) -> str | None:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )
    texto = texto.lower().strip()
    texto = re.sub(r"^(articulo|art)\.?\s*", "", texto)

    coincidencia = re.match(
        r"^(\d+)(?:[\s.\-]*(bis|ter|quater|quinquies))?",
        texto,
    )
    if coincidencia is None:
        return None

    numero = str(int(coincidencia.group(1)))
    sufijo = coincidencia.group(2)
    return f"{numero} {sufijo}" if sufijo else numero


def normalizar_categoria(valor: Any) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).strip().split())


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
        escritor = csv.DictWriter(
            archivo,
            fieldnames=columnas,
            extrasaction="ignore",
        )
        escritor.writeheader()
        escritor.writerows(filas)


def existe_tabla(conexion: sqlite3.Connection, tabla: str) -> bool:
    return (
        conexion.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = ?
            """,
            (tabla,),
        ).fetchone()
        is not None
    )


def columnas_tabla(
    conexion: sqlite3.Connection,
    tabla: str,
) -> set[str]:
    if not existe_tabla(conexion, tabla):
        return set()

    return {
        str(fila["name"])
        for fila in conexion.execute(f"PRAGMA table_info({tabla})")
    }


def validar_estructura(conexion: sqlite3.Connection) -> None:
    requeridas = {
        "convocatorias": {"id", "codigo"},
        "temarios": {"id", "convocatoria_id"},
        "temario_temas": {
            "id",
            "temario_id",
            "parte",
            "numero_tema",
            "titulo",
        },
        "temario_referencias": {
            "tema_id",
            "norma_id",
            "articulo_solicitado",
        },
        "equivalencias_temas_no_juridicos": {
            "tema_no_juridico",
            "tema_id",
        },
        "lote_preguntas": {
            "id",
            "enunciado",
            "tipo_clasificacion",
            "norma_id_normalizada",
            "articulo_normalizado",
            "tema_no_juridico",
        },
        "banco_preguntas": {
            "id",
            "convocatoria_id",
            "pregunta_id",
            "tipo_vinculacion",
            "estado",
            "metodo_vinculacion",
        },
        "banco_preguntas_temas": {
            "banco_pregunta_id",
            "tema_id",
            "es_principal",
        },
    }

    errores: list[str] = []

    for tabla, necesarias in requeridas.items():
        columnas = columnas_tabla(conexion, tabla)
        if not columnas:
            errores.append(f"No existe la tabla {tabla}.")
            continue

        faltantes = sorted(necesarias - columnas)
        if faltantes:
            errores.append(
                f"En {tabla} faltan columnas: {', '.join(faltantes)}."
            )

    if errores:
        raise RuntimeError(" ".join(errores))


def seleccionar_convocatoria(
    conexion: sqlite3.Connection,
    convocatoria_id: int | None,
) -> sqlite3.Row:
    if convocatoria_id is None:
        filas = conexion.execute(
            "SELECT * FROM convocatorias ORDER BY id"
        ).fetchall()

        if len(filas) != 1:
            raise RuntimeError(
                "Indica --convocatoria-id: "
                f"hay {len(filas)} convocatorias."
            )
        return filas[0]

    fila = conexion.execute(
        "SELECT * FROM convocatorias WHERE id = ?",
        (convocatoria_id,),
    ).fetchone()

    if fila is None:
        raise RuntimeError(
            f"No existe la convocatoria {convocatoria_id}."
        )
    return fila


def seleccionar_temario(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
) -> sqlite3.Row:
    filas = conexion.execute(
        """
        SELECT *
        FROM temarios
        WHERE convocatoria_id = ?
        ORDER BY id
        """,
        (convocatoria_id,),
    ).fetchall()

    if len(filas) != 1:
        raise RuntimeError(
            "La convocatoria debe tener exactamente un temario. "
            f"Encontrados: {len(filas)}."
        )

    return filas[0]


def cargar_referencias_juridicas(
    conexion: sqlite3.Connection,
    temario_id: int,
) -> tuple[
    dict[tuple[int, str], dict[str, Any]],
    list[dict[str, Any]],
]:
    por_clave: dict[
        tuple[int, str],
        dict[int, dict[str, Any]],
    ] = defaultdict(dict)

    for fila in conexion.execute(
        """
        SELECT
            tr.tema_id,
            tr.norma_id,
            tr.articulo_solicitado,
            tt.parte,
            tt.numero_tema,
            tt.titulo
        FROM temario_referencias AS tr
        JOIN temario_temas AS tt
          ON tt.id = tr.tema_id
        WHERE tt.temario_id = ?
        ORDER BY tr.norma_id, tr.articulo_solicitado, tt.id
        """,
        (temario_id,),
    ):
        norma_id = fila["norma_id"]
        articulo = normalizar_articulo(
            fila["articulo_solicitado"]
        )

        if norma_id is None or articulo is None:
            continue

        por_clave[(int(norma_id), articulo)][int(fila["tema_id"])] = {
            **dict(fila),
            "articulo_comparable": articulo,
        }

    mapa: dict[tuple[int, str], dict[str, Any]] = {}
    ambiguas: list[dict[str, Any]] = []

    for clave, temas in sorted(por_clave.items()):
        candidatos = list(temas.values())

        if len(candidatos) == 1:
            mapa[clave] = candidatos[0]
        else:
            ambiguas.append(
                {
                    "norma_id": clave[0],
                    "articulo": clave[1],
                    "numero_temas": len(candidatos),
                    "tema_ids": " | ".join(
                        str(fila["tema_id"])
                        for fila in candidatos
                    ),
                }
            )

    return mapa, ambiguas


def cargar_equivalencias_no_juridicas(
    conexion: sqlite3.Connection,
    temario_id: int,
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    por_categoria: dict[
        str,
        dict[int, dict[str, Any]],
    ] = defaultdict(dict)

    for fila in conexion.execute(
        """
        SELECT
            e.tema_no_juridico,
            e.tema_id,
            tt.parte,
            tt.numero_tema,
            tt.titulo
        FROM equivalencias_temas_no_juridicos AS e
        JOIN temario_temas AS tt
          ON tt.id = e.tema_id
        WHERE tt.temario_id = ?
        ORDER BY e.tema_no_juridico, tt.id
        """,
        (temario_id,),
    ):
        categoria = normalizar_categoria(
            fila["tema_no_juridico"]
        )
        if not categoria:
            continue

        por_categoria[categoria][int(fila["tema_id"])] = dict(fila)

    mapa: dict[str, dict[str, Any]] = {}
    ambiguas: list[dict[str, Any]] = []

    for categoria, temas in sorted(por_categoria.items()):
        candidatos = list(temas.values())

        if len(candidatos) == 1:
            mapa[categoria] = candidatos[0]
        else:
            ambiguas.append(
                {
                    "tema_no_juridico": categoria,
                    "numero_temas": len(candidatos),
                    "tema_ids": " | ".join(
                        str(fila["tema_id"])
                        for fila in candidatos
                    ),
                }
            )

    return mapa, ambiguas


def construir_banco_esperado(
    conexion: sqlite3.Connection,
    referencias_juridicas: dict[
        tuple[int, str],
        dict[str, Any],
    ],
    equivalencias_no_juridicas: dict[
        str,
        dict[str, Any],
    ],
) -> tuple[
    dict[int, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    esperado: dict[int, dict[str, Any]] = {}

    incidencias = {
        "juridicas_sin_normalizacion": [],
        "juridicas_fuera_temario": [],
        "no_juridicas_sin_categoria": [],
        "no_juridicas_fuera_temario": [],
    }

    for fila in conexion.execute(
        """
        SELECT
            id,
            enunciado,
            tipo_clasificacion,
            tipo_norma,
            nombre_norma,
            articulo,
            norma_id_normalizada,
            articulo_normalizado,
            tema_no_juridico,
            origen_oposicion,
            tipo_fuente,
            teorica_practica
        FROM lote_preguntas
        WHERE tipo_clasificacion IN ('JURIDICA', 'INFORMATICA')
        ORDER BY id
        """
    ):
        pregunta = dict(fila)
        pregunta_id = int(fila["id"])

        if fila["tipo_clasificacion"] == "JURIDICA":
            norma_id = fila["norma_id_normalizada"]
            articulo = normalizar_articulo(
                fila["articulo_normalizado"]
            )

            if norma_id is None or articulo is None:
                incidencias["juridicas_sin_normalizacion"].append(
                    {
                        **pregunta,
                        "articulo_comparable": articulo,
                    }
                )
                continue

            punto = referencias_juridicas.get(
                (int(norma_id), articulo)
            )

            if punto is None:
                incidencias["juridicas_fuera_temario"].append(
                    {
                        **pregunta,
                        "articulo_comparable": articulo,
                    }
                )
                continue

            esperado[pregunta_id] = {
                "pregunta_id": pregunta_id,
                "tipo_vinculacion": "JURIDICA",
                "tema_id": int(punto["tema_id"]),
                "parte": punto["parte"],
                "numero_tema": punto["numero_tema"],
                "titulo": punto["titulo"],
                "metodo_vinculacion": "NORMA_ID_ARTICULO",
            }

        elif fila["tipo_clasificacion"] == "INFORMATICA":
            categoria = normalizar_categoria(
                fila["tema_no_juridico"]
            )

            if not categoria:
                incidencias["no_juridicas_sin_categoria"].append(
                    pregunta
                )
                continue

            punto = equivalencias_no_juridicas.get(categoria)

            if punto is None:
                incidencias["no_juridicas_fuera_temario"].append(
                    {
                        **pregunta,
                        "categoria_comparable": categoria,
                    }
                )
                continue

            esperado[pregunta_id] = {
                "pregunta_id": pregunta_id,
                "tipo_vinculacion": "NO_JURIDICA",
                "tema_id": int(punto["tema_id"]),
                "parte": punto["parte"],
                "numero_tema": punto["numero_tema"],
                "titulo": punto["titulo"],
                "metodo_vinculacion": (
                    "TEMA_NO_JURIDICO_EXPLICITO"
                ),
            }

    return esperado, incidencias


def cargar_banco_real(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
) -> tuple[
    dict[int, dict[str, Any]],
    list[dict[str, Any]],
]:
    real: dict[int, dict[str, Any]] = {}
    incidencias: list[dict[str, Any]] = []

    filas = conexion.execute(
        """
        SELECT
            bp.id AS banco_pregunta_id,
            bp.pregunta_id,
            bp.tipo_vinculacion,
            bp.estado,
            bp.metodo_vinculacion,
            bp.motivo_revision,
            COUNT(DISTINCT bpt.tema_id) AS numero_temas,
            MIN(bpt.tema_id) AS tema_id,
            GROUP_CONCAT(DISTINCT bpt.tema_id) AS tema_ids
        FROM banco_preguntas AS bp
        LEFT JOIN banco_preguntas_temas AS bpt
          ON bpt.banco_pregunta_id = bp.id
        WHERE bp.convocatoria_id = ?
        GROUP BY
            bp.id,
            bp.pregunta_id,
            bp.tipo_vinculacion,
            bp.estado,
            bp.metodo_vinculacion,
            bp.motivo_revision
        ORDER BY bp.pregunta_id, bp.id
        """,
        (convocatoria_id,),
    ).fetchall()

    vistos: set[int] = set()

    for fila_sql in filas:
        fila = dict(fila_sql)
        pregunta_id = int(fila["pregunta_id"])
        numero_temas = int(fila["numero_temas"])

        if pregunta_id in vistos:
            incidencias.append(
                {
                    "tipo": "PREGUNTA_DUPLICADA_EN_BANCO",
                    **fila,
                }
            )
            continue

        vistos.add(pregunta_id)

        if numero_temas != 1:
            incidencias.append(
                {
                    "tipo": "PREGUNTA_SIN_UNICO_TEMA",
                    **fila,
                }
            )

        real[pregunta_id] = fila

    return real, incidencias


def auditar_incidencias_banco(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    temario_id: int,
) -> list[dict[str, Any]]:
    incidencias: list[dict[str, Any]] = []

    for fila in conexion.execute(
        """
        SELECT
            bp.id AS banco_pregunta_id,
            bp.pregunta_id,
            bp.tipo_vinculacion,
            lp.tipo_clasificacion
        FROM banco_preguntas AS bp
        JOIN lote_preguntas AS lp
          ON lp.id = bp.pregunta_id
        WHERE bp.convocatoria_id = ?
          AND (
                (
                    bp.tipo_vinculacion = 'JURIDICA'
                    AND lp.tipo_clasificacion <> 'JURIDICA'
                )
                OR
                (
                    bp.tipo_vinculacion = 'NO_JURIDICA'
                    AND lp.tipo_clasificacion <> 'INFORMATICA'
                )
          )
        """,
        (convocatoria_id,),
    ):
        incidencias.append(
            {
                "tipo": "TIPO_VINCULACION_INCOHERENTE",
                **dict(fila),
            }
        )

    for fila in conexion.execute(
        """
        SELECT
            bp.id AS banco_pregunta_id,
            bp.pregunta_id,
            bpt.tema_id,
            tt.temario_id AS temario_real
        FROM banco_preguntas AS bp
        JOIN banco_preguntas_temas AS bpt
          ON bpt.banco_pregunta_id = bp.id
        JOIN temario_temas AS tt
          ON tt.id = bpt.tema_id
        WHERE bp.convocatoria_id = ?
          AND tt.temario_id <> ?
        """,
        (convocatoria_id, temario_id),
    ):
        incidencias.append(
            {
                "tipo": "TEMA_DE_OTRO_TEMARIO",
                **dict(fila),
            }
        )

    return incidencias


def obtener_duplicados_lote(
    conexion: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return [
        dict(fila)
        for fila in conexion.execute(
            """
            SELECT
                TRIM(enunciado) AS enunciado,
                COUNT(*) AS apariciones,
                GROUP_CONCAT(id) AS pregunta_ids
            FROM lote_preguntas
            WHERE TRIM(COALESCE(enunciado, '')) <> ''
            GROUP BY TRIM(enunciado)
            HAVING COUNT(*) > 1
            ORDER BY apariciones DESC, enunciado
            """
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita lote_preguntas y compara el banco real "
            "con el banco esperado."
        )
    )
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument("--convocatoria-id", type=int)
    args = parser.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        raise FileNotFoundError(
            f"No existe la base de datos: {db}"
        )

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = (
        RAIZ
        / "auditorias"
        / f"auditoria_consistencia_global_{marca}"
    )
    carpeta.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA query_only = ON")
        conexion.execute("PRAGMA foreign_keys = ON")

        validar_estructura(conexion)

        convocatoria = seleccionar_convocatoria(
            conexion,
            args.convocatoria_id,
        )
        convocatoria_id = int(convocatoria["id"])

        temario = seleccionar_temario(
            conexion,
            convocatoria_id,
        )
        temario_id = int(temario["id"])

        clasificaciones = [
            dict(fila)
            for fila in conexion.execute(
                """
                SELECT
                    COALESCE(
                        NULLIF(TRIM(tipo_clasificacion), ''),
                        '(VACIA)'
                    ) AS tipo_clasificacion,
                    COUNT(*) AS total
                FROM lote_preguntas
                GROUP BY COALESCE(
                    NULLIF(TRIM(tipo_clasificacion), ''),
                    '(VACIA)'
                )
                ORDER BY total DESC, tipo_clasificacion
                """
            )
        ]

        referencias, referencias_ambiguas = (
            cargar_referencias_juridicas(
                conexion,
                temario_id,
            )
        )

        equivalencias, equivalencias_ambiguas = (
            cargar_equivalencias_no_juridicas(
                conexion,
                temario_id,
            )
        )

        esperado, incidencias_lote = construir_banco_esperado(
            conexion,
            referencias,
            equivalencias,
        )

        real, incidencias_real = cargar_banco_real(
            conexion,
            convocatoria_id,
        )

        incidencias_banco = [
            *incidencias_real,
            *auditar_incidencias_banco(
                conexion,
                convocatoria_id,
                temario_id,
            ),
        ]

        faltantes = [
            esperado[pregunta_id]
            for pregunta_id in sorted(
                set(esperado) - set(real)
            )
        ]

        sobrantes = [
            real[pregunta_id]
            for pregunta_id in sorted(
                set(real) - set(esperado)
            )
        ]

        tema_incorrecto: list[dict[str, Any]] = []

        for pregunta_id in sorted(set(real) & set(esperado)):
            fila_real = real[pregunta_id]
            fila_esperada = esperado[pregunta_id]

            tema_real = fila_real["tema_id"]
            tema_esperado = fila_esperada["tema_id"]
            tipo_real = fila_real["tipo_vinculacion"]
            tipo_esperado = fila_esperada["tipo_vinculacion"]

            if (
                tema_real != tema_esperado
                or tipo_real != tipo_esperado
            ):
                tema_incorrecto.append(
                    {
                        "pregunta_id": pregunta_id,
                        "banco_pregunta_id": fila_real[
                            "banco_pregunta_id"
                        ],
                        "tipo_real": tipo_real,
                        "tipo_esperado": tipo_esperado,
                        "tema_real": tema_real,
                        "tema_esperado": tema_esperado,
                    }
                )

        duplicados_lote = obtener_duplicados_lote(conexion)

        errores_fk = conexion.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        integridad = conexion.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        escribir_csv(
            carpeta / "lote_clasificaciones.csv",
            clasificaciones,
        )
        escribir_csv(
            carpeta / "lote_juridicas_sin_normalizacion.csv",
            incidencias_lote[
                "juridicas_sin_normalizacion"
            ],
        )
        escribir_csv(
            carpeta / "lote_juridicas_fuera_temario.csv",
            incidencias_lote[
                "juridicas_fuera_temario"
            ],
        )
        escribir_csv(
            carpeta / "lote_no_juridicas_sin_categoria.csv",
            incidencias_lote[
                "no_juridicas_sin_categoria"
            ],
        )
        escribir_csv(
            carpeta / "lote_no_juridicas_fuera_temario.csv",
            incidencias_lote[
                "no_juridicas_fuera_temario"
            ],
        )
        escribir_csv(
            carpeta / "lote_duplicados_enunciado.csv",
            duplicados_lote,
        )
        escribir_csv(
            carpeta / "banco_incidencias.csv",
            incidencias_banco,
        )
        escribir_csv(
            carpeta / "banco_esperado.csv",
            list(esperado.values()),
        )
        escribir_csv(
            carpeta / "banco_real.csv",
            list(real.values()),
        )
        escribir_csv(
            carpeta / "banco_faltantes.csv",
            faltantes,
        )
        escribir_csv(
            carpeta / "banco_sobrantes.csv",
            sobrantes,
        )
        escribir_csv(
            carpeta / "banco_tema_incorrecto.csv",
            tema_incorrecto,
        )
        escribir_csv(
            carpeta / "referencias_juridicas_ambiguas.csv",
            referencias_ambiguas,
        )
        escribir_csv(
            carpeta / "equivalencias_no_juridicas_ambiguas.csv",
            equivalencias_ambiguas,
        )

        resumen = [
            "AUDITORÍA DE CONSISTENCIA GLOBAL",
            "=" * 70,
            f"Base: {db}",
            f"Convocatoria ID: {convocatoria_id}",
            f"Código: {convocatoria['codigo']}",
            f"Temario ID: {temario_id}",
            "",
            "LOTE_PREGUNTAS",
            "-" * 70,
            (
                "Total: "
                + str(
                    conexion.execute(
                        "SELECT COUNT(*) FROM lote_preguntas"
                    ).fetchone()[0]
                )
            ),
            (
                "Duplicados exactos de enunciado: "
                f"{len(duplicados_lote)}"
            ),
            (
                "Jurídicas sin normalización suficiente: "
                f"{len(incidencias_lote['juridicas_sin_normalizacion'])}"
            ),
            (
                "Jurídicas fuera del temario: "
                f"{len(incidencias_lote['juridicas_fuera_temario'])}"
            ),
            (
                "No jurídicas sin categoría: "
                f"{len(incidencias_lote['no_juridicas_sin_categoria'])}"
            ),
            (
                "No jurídicas fuera del temario: "
                f"{len(incidencias_lote['no_juridicas_fuera_temario'])}"
            ),
            "",
            "TEMARIO",
            "-" * 70,
            (
                "Referencias jurídicas ambiguas: "
                f"{len(referencias_ambiguas)}"
            ),
            (
                "Equivalencias no jurídicas ambiguas: "
                f"{len(equivalencias_ambiguas)}"
            ),
            "",
            "BANCO",
            "-" * 70,
            f"Banco esperado: {len(esperado)}",
            f"Banco real: {len(real)}",
            f"Faltantes: {len(faltantes)}",
            f"Sobrantes: {len(sobrantes)}",
            (
                "Vinculación o tema incorrecto: "
                f"{len(tema_incorrecto)}"
            ),
            (
                "Otras incidencias del banco: "
                f"{len(incidencias_banco)}"
            ),
            "",
            "INTEGRIDAD",
            "-" * 70,
            f"Errores de claves externas: {len(errores_fk)}",
            f"PRAGMA integrity_check: {integridad}",
            "",
            "La auditoría no modifica ninguna tabla.",
        ]

        (carpeta / "resumen_general.txt").write_text(
            "\n".join(resumen) + "\n",
            encoding="utf-8-sig",
        )

        print("\n".join(resumen))
        print(f"\nInformes: {carpeta}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(
            "\nProceso interrumpido por el usuario.",
            file=sys.stderr,
        )
        raise SystemExit(130)
    except Exception as error:
        print(
            f"ERROR: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)
