"""
OpoCoach - Crear o actualizar el banco de preguntas de una convocatoria.

Este mantenimiento sirve para las dos operaciones:

1. Crear el banco de una convocatoria nueva.
2. Incorporar al banco ya existente las nuevas preguntas disponibles
   en lote_preguntas.

REGLAS
------

Origen único:
    lote_preguntas

lote_preguntas:
    - no se modifica;
    - no se eliminan registros;
    - no se copian sus propiedades al banco.

El banco incorpora las preguntas por referencia:

    banco_preguntas.pregunta_id -> lote_preguntas.id

El punto y la parte del temario quedan disponibles mediante:

    banco_preguntas_temas.tema_id -> temario_temas.id
    temario_temas.parte
    temario_temas.numero_tema
    temario_temas.titulo

Preguntas jurídicas:
    lote_preguntas.norma_id_normalizada
    lote_preguntas.articulo_normalizado

    coinciden con:

    temario_referencias.norma_id
    temario_referencias.articulo_solicitado normalizado

Preguntas no jurídicas:
    lote_preguntas.tema_no_juridico

    coincide con:

    equivalencias_temas_no_juridicos.tema_no_juridico

SEGURIDAD
---------

Antes de escribir se detiene todo el proceso si:

- la convocatoria no existe;
- no tiene exactamente un temario;
- una misma norma + artículo aparece en más de un punto del temario;
- una misma categoría no jurídica aparece en más de un punto del temario;
- existe una incoherencia en el banco actual;
- faltan tablas o columnas necesarias.

La ejecución sin --guardar es exclusivamente de revisión.

EJEMPLOS
--------

Revisión:

    python scripts\\mantener_banco_preguntas.py --convocatoria-id 1

Guardar:

    python scripts\\mantener_banco_preguntas.py --convocatoria-id 1 --guardar

Por código:

    python scripts\\mantener_banco_preguntas.py --codigo C1-01_58_26

Base alternativa:

    python scripts\\mantener_banco_preguntas.py ^
        --db db\\oposiciones.sqlite3 ^
        --convocatoria-id 1 ^
        --guardar
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"

TIPO_JURIDICA = "JURIDICA"
TIPO_NO_JURIDICA = "NO_JURIDICA"

CLASIFICACION_JURIDICA = "JURIDICA"
CLASIFICACION_NO_JURIDICA = "INFORMATICA"

ESTADO_INCLUIDA = "INCLUIDA"

METODO_JURIDICO = "NORMA_ID_ARTICULO"
METODO_NO_JURIDICO = "TEMA_NO_JURIDICO_EXPLICITO"


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crea o actualiza el banco de preguntas de una convocatoria "
            "a partir de lote_preguntas."
        )
    )
    parser.add_argument(
        "--db",
        default=str(DB_DEFECTO),
        help=f"Base SQLite. Por defecto: {DB_DEFECTO}",
    )

    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "--convocatoria-id",
        type=int,
        help="Identificador numérico de la convocatoria.",
    )
    grupo.add_argument(
        "--codigo",
        help="Código exacto de la convocatoria.",
    )

    parser.add_argument(
        "--guardar",
        action="store_true",
        help="Guarda las nuevas vinculaciones. Sin esta opción solo informa.",
    )
    return parser


def normalizar_articulo(valor: Any) -> str | None:
    """
    Devuelve el artículo principal en formato comparable.

    Ejemplos:
        24          -> 24
        Art. 24     -> 24
        24.1        -> 24
        25.1.b      -> 25
        14 bis      -> 14 bis
        14-bis      -> 14 bis
    """
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
    """
    Normalización conservadora para el cruce no jurídico.

    Solo elimina espacios exteriores y unifica espacios interiores.
    No cambia mayúsculas, acentos ni significado.
    """
    if valor is None:
        return ""
    return " ".join(str(valor).strip().split())


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
        "temarios": {"id", "convocatoria_id", "nombre"},
        "temario_temas": {
            "id",
            "temario_id",
            "parte",
            "numero_tema",
            "titulo",
            "tipo_contenido",
        },
        "temario_referencias": {
            "id",
            "tema_id",
            "norma_id",
            "articulo_solicitado",
        },
        "equivalencias_temas_no_juridicos": {
            "id",
            "tema_no_juridico",
            "tema_id",
        },
        "lote_preguntas": {
            "id",
            "tipo_clasificacion",
            "norma_id_normalizada",
            "articulo_normalizado",
            "tema_no_juridico",
            "tipo_norma_normalizado",
            "nombre_norma_normalizado",
            "estado_vigencia",
        },
        "banco_preguntas": {
            "id",
            "convocatoria_id",
            "pregunta_id",
            "tipo_vinculacion",
            "estado",
            "metodo_vinculacion",
            "motivo_revision",
        },
        "banco_preguntas_temas": {
            "id",
            "banco_pregunta_id",
            "tema_id",
            "es_principal",
        },
    }

    errores: list[str] = []

    for tabla, columnas_necesarias in requeridas.items():
        columnas = columnas_tabla(conexion, tabla)

        if not columnas:
            errores.append(f"No existe la tabla {tabla}.")
            continue

        faltantes = sorted(columnas_necesarias - columnas)
        if faltantes:
            errores.append(
                f"En {tabla} faltan columnas: {', '.join(faltantes)}."
            )

    if errores:
        raise RuntimeError(" ".join(errores))


def buscar_convocatoria(
    conexion: sqlite3.Connection,
    convocatoria_id: int | None,
    codigo: str | None,
) -> sqlite3.Row:
    if convocatoria_id is not None:
        fila = conexion.execute(
            """
            SELECT *
            FROM convocatorias
            WHERE id = ?
            """,
            (convocatoria_id,),
        ).fetchone()
    else:
        fila = conexion.execute(
            """
            SELECT *
            FROM convocatorias
            WHERE codigo = ?
            """,
            (codigo.strip(),),
        ).fetchone()

    if fila is None:
        identificador = (
            f"id={convocatoria_id}"
            if convocatoria_id is not None
            else f"código={codigo!r}"
        )
        raise RuntimeError(
            f"No existe una convocatoria con {identificador}."
        )

    return fila


def buscar_temario(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
) -> sqlite3.Row:
    filas = conexion.execute(
        """
        SELECT id, convocatoria_id, nombre
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


def escribir_csv(
    ruta: Path,
    filas: Iterable[dict[str, Any]],
) -> None:
    datos = list(filas)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    if not datos:
        ruta.write_text("", encoding="utf-8-sig")
        return

    columnas: list[str] = []
    vistas: set[str] = set()

    for fila in datos:
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
        escritor.writerows(datos)


def cargar_referencias_juridicas(
    conexion: sqlite3.Connection,
    temario_id: int,
) -> tuple[
    dict[tuple[int, str], dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    Devuelve:
    - mapa único norma_id + artículo -> punto del temario;
    - referencias sin norma o artículo válido;
    - combinaciones repetidas en varios puntos.
    """
    por_clave: dict[
        tuple[int, str],
        dict[int, dict[str, Any]],
    ] = defaultdict(dict)

    invalidas: list[dict[str, Any]] = []

    filas = conexion.execute(
        """
        SELECT
            tr.id AS referencia_id,
            tr.tema_id,
            tr.norma_id,
            tr.articulo_solicitado,
            tr.nombre_norma_csv,
            tr.nombre_norma_normalizada,
            tt.parte,
            tt.numero_tema,
            tt.titulo,
            tt.tipo_contenido
        FROM temario_referencias AS tr
        JOIN temario_temas AS tt
          ON tt.id = tr.tema_id
        WHERE tt.temario_id = ?
        ORDER BY
            tr.norma_id,
            tr.articulo_solicitado,
            tt.parte,
            tt.numero_tema,
            tr.id
        """,
        (temario_id,),
    ).fetchall()

    for fila_sql in filas:
        fila = dict(fila_sql)
        norma_id = fila["norma_id"]
        articulo = normalizar_articulo(
            fila["articulo_solicitado"]
        )

        if norma_id is None or articulo is None:
            invalidas.append(
                {
                    **fila,
                    "articulo_normalizado_proceso": articulo,
                    "motivo": (
                        "NORMA_ID_NULA"
                        if norma_id is None
                        else "ARTICULO_NO_NORMALIZABLE"
                    ),
                }
            )
            continue

        clave = (int(norma_id), articulo)
        por_clave[clave][int(fila["tema_id"])] = {
            **fila,
            "articulo_normalizado_proceso": articulo,
        }

    duplicadas: list[dict[str, Any]] = []
    mapa: dict[tuple[int, str], dict[str, Any]] = {}

    for clave, temas in sorted(por_clave.items()):
        candidatos = list(temas.values())

        if len(candidatos) > 1:
            duplicadas.append(
                {
                    "norma_id": clave[0],
                    "articulo": clave[1],
                    "numero_puntos": len(candidatos),
                    "tema_ids": " | ".join(
                        str(fila["tema_id"])
                        for fila in candidatos
                    ),
                    "puntos": " | ".join(
                        (
                            f"{fila['parte']} - "
                            f"{fila['numero_tema']} - "
                            f"{fila['titulo']}"
                        )
                        for fila in candidatos
                    ),
                }
            )
        elif candidatos:
            mapa[clave] = candidatos[0]

    return mapa, invalidas, duplicadas


def cargar_equivalencias_no_juridicas(
    conexion: sqlite3.Connection,
    temario_id: int,
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    Devuelve:
    - mapa único categoría -> punto del temario;
    - equivalencias con categoría vacía;
    - categorías asignadas a varios puntos.
    """
    por_categoria: dict[
        str,
        dict[int, dict[str, Any]],
    ] = defaultdict(dict)

    invalidas: list[dict[str, Any]] = []

    filas = conexion.execute(
        """
        SELECT
            e.id AS equivalencia_id,
            e.tema_no_juridico,
            e.tema_id,
            tt.parte,
            tt.numero_tema,
            tt.titulo,
            tt.tipo_contenido
        FROM equivalencias_temas_no_juridicos AS e
        JOIN temario_temas AS tt
          ON tt.id = e.tema_id
        WHERE tt.temario_id = ?
        ORDER BY
            e.tema_no_juridico,
            tt.parte,
            tt.numero_tema,
            e.id
        """,
        (temario_id,),
    ).fetchall()

    for fila_sql in filas:
        fila = dict(fila_sql)
        categoria = normalizar_categoria(
            fila["tema_no_juridico"]
        )

        if not categoria:
            invalidas.append(
                {
                    **fila,
                    "motivo": "CATEGORIA_VACIA",
                }
            )
            continue

        por_categoria[categoria][int(fila["tema_id"])] = {
            **fila,
            "categoria_normalizada_proceso": categoria,
        }

    duplicadas: list[dict[str, Any]] = []
    mapa: dict[str, dict[str, Any]] = {}

    for categoria, temas in sorted(por_categoria.items()):
        candidatos = list(temas.values())

        if len(candidatos) > 1:
            duplicadas.append(
                {
                    "tema_no_juridico": categoria,
                    "numero_puntos": len(candidatos),
                    "tema_ids": " | ".join(
                        str(fila["tema_id"])
                        for fila in candidatos
                    ),
                    "puntos": " | ".join(
                        (
                            f"{fila['parte']} - "
                            f"{fila['numero_tema']} - "
                            f"{fila['titulo']}"
                        )
                        for fila in candidatos
                    ),
                }
            )
        elif candidatos:
            mapa[categoria] = candidatos[0]

    return mapa, invalidas, duplicadas


def cargar_existentes(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
) -> dict[int, dict[str, Any]]:
    filas = conexion.execute(
        """
        SELECT
            bp.id AS banco_pregunta_id,
            bp.pregunta_id,
            bp.tipo_vinculacion,
            bp.estado,
            bp.metodo_vinculacion,
            COUNT(DISTINCT bpt.tema_id) AS numero_temas,
            MIN(bpt.tema_id) AS tema_id
        FROM banco_preguntas AS bp
        LEFT JOIN banco_preguntas_temas AS bpt
          ON bpt.banco_pregunta_id = bp.id
        WHERE bp.convocatoria_id = ?
        GROUP BY
            bp.id,
            bp.pregunta_id,
            bp.tipo_vinculacion,
            bp.estado,
            bp.metodo_vinculacion
        ORDER BY bp.pregunta_id
        """,
        (convocatoria_id,),
    ).fetchall()

    return {
        int(fila["pregunta_id"]): dict(fila)
        for fila in filas
    }


def validar_banco_actual(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    temario_id: int,
) -> list[dict[str, Any]]:
    incidencias: list[dict[str, Any]] = []

    for fila in conexion.execute(
        """
        SELECT id AS banco_pregunta_id, pregunta_id
        FROM banco_preguntas
        WHERE convocatoria_id = ?
          AND convocatoria_parte_id IS NULL
        """,
        (convocatoria_id,),
    ):
        incidencias.append(
            {
                "tipo": "PARTE_CONVOCATORIA_NULA",
                **dict(fila),
            }
        )

    for fila in conexion.execute(
        """
        SELECT
            pregunta_id,
            COUNT(*) AS apariciones,
            GROUP_CONCAT(id) AS banco_ids
        FROM banco_preguntas
        WHERE convocatoria_id = ?
        GROUP BY pregunta_id
        HAVING COUNT(*) > 1
        """,
        (convocatoria_id,),
    ):
        incidencias.append(
            {
                "tipo": "PREGUNTA_DUPLICADA_EN_BANCO",
                **dict(fila),
            }
        )

    for fila in conexion.execute(
        """
        SELECT
            bp.id AS banco_pregunta_id,
            bp.pregunta_id,
            COUNT(DISTINCT bpt.tema_id) AS numero_temas,
            GROUP_CONCAT(DISTINCT bpt.tema_id) AS tema_ids
        FROM banco_preguntas AS bp
        LEFT JOIN banco_preguntas_temas AS bpt
          ON bpt.banco_pregunta_id = bp.id
        WHERE bp.convocatoria_id = ?
        GROUP BY bp.id, bp.pregunta_id
        HAVING COUNT(DISTINCT bpt.tema_id) <> 1
        """,
        (convocatoria_id,),
    ):
        incidencias.append(
            {
                "tipo": "PREGUNTA_SIN_UNICO_PUNTO",
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
                "tipo": "PUNTO_DE_OTRO_TEMARIO",
                **dict(fila),
            }
        )

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

    if not convocatoria_admite_practica(conexion, convocatoria_id):
        for fila in conexion.execute(
            """
            SELECT bp.id AS banco_pregunta_id,bp.pregunta_id,bp.convocatoria_parte_id,lp.teorica_practica
            FROM banco_preguntas bp JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
            WHERE bp.convocatoria_id=?
              AND UPPER(TRIM(COALESCE(lp.teorica_practica,'')))='PRACTICA'
            ORDER BY bp.id
            """,(convocatoria_id,),
        ):
            incidencias.append({"tipo":"PRACTICA_NO_ADMITIDA_POR_CONVOCATORIA",**dict(fila)})

    return incidencias


def convocatoria_admite_practica(conexion: sqlite3.Connection, convocatoria_id: int) -> bool:
    """PRACTICA exige una regla explícita de la convocatoria."""
    return conexion.execute(
        """
        SELECT 1 FROM convocatoria_parte_reglas r
        JOIN convocatoria_partes cp ON cp.id=r.convocatoria_parte_id
        WHERE cp.convocatoria_id=?
          AND UPPER(TRIM(COALESCE(r.teorica_practica,'')))='PRACTICA'
        LIMIT 1
        """,(convocatoria_id,),
    ).fetchone() is not None


def seleccionar_juridicas(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    referencias: dict[tuple[int, str], dict[str, Any]],
    existentes: dict[int, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    resultado = {
        "nuevas": [],
        "ya_existentes": [],
        "sin_normalizacion": [],
        "fuera_temario": [],
    }

    filas = conexion.execute(
        """
        SELECT
            id,
            enunciado,
            tipo_norma,
            nombre_norma,
            articulo,
            norma_id_normalizada,
            articulo_normalizado,
            origen_oposicion,
            tipo_fuente,
            pagina_origen,
            teorica_practica,
            tipo_norma_normalizado,
            nombre_norma_normalizado,
            estado_vigencia
        FROM lote_preguntas
        WHERE tipo_clasificacion = ?
        ORDER BY id
        """,
        (CLASIFICACION_JURIDICA,),
    ).fetchall()

    admite_practica = convocatoria_admite_practica(conexion, convocatoria_id)

    for fila_sql in filas:
        pregunta = dict(fila_sql)
        pregunta_id = int(pregunta["id"])

        if str(pregunta.get("teorica_practica") or "").strip().upper() == "PRACTICA" and not admite_practica:
            resultado["fuera_temario"].append({**pregunta,"motivo":"PRACTICA_NO_ADMITIDA_POR_CONVOCATORIA"})
            continue

        # FILTRO_APTITUD_JURIDICA_VIGENCIA_NORMALIZACION_V1
        estado_vigencia = str(pregunta.get("estado_vigencia") or "").strip().upper()

        campos_faltantes = []
        if not str(pregunta.get("tipo_norma_normalizado") or "").strip():
            campos_faltantes.append("tipo_norma_normalizado")
        if not str(pregunta.get("nombre_norma_normalizado") or "").strip():
            campos_faltantes.append("nombre_norma_normalizado")
        if pregunta.get("norma_id_normalizada") is None:
            campos_faltantes.append("norma_id_normalizada")
        if not str(pregunta.get("articulo_normalizado") or "").strip():
            campos_faltantes.append("articulo_normalizado")

        if estado_vigencia.startswith("OBSOLETA"):
            resultado["sin_normalizacion"].append({
                **pregunta,
                "motivo": "ESTADO_VIGENCIA_OBSOLETO",
            })
            continue

        if campos_faltantes:
            resultado["sin_normalizacion"].append({
                **pregunta,
                "motivo": "NORMALIZACION_INCOMPLETA:" + ",".join(campos_faltantes),
            })
            continue

        if pregunta_id in existentes:
            resultado["ya_existentes"].append(
                {
                    **pregunta,
                    "banco_pregunta_id": existentes[pregunta_id][
                        "banco_pregunta_id"
                    ],
                }
            )
            continue

        norma_id = pregunta["norma_id_normalizada"]
        articulo = normalizar_articulo(
            pregunta["articulo_normalizado"]
        )

        if norma_id is None or articulo is None:
            resultado["sin_normalizacion"].append(
                {
                    **pregunta,
                    "articulo_normalizado_proceso": articulo,
                    "motivo": (
                        "NORMA_ID_NULA"
                        if norma_id is None
                        else "ARTICULO_NO_NORMALIZABLE"
                    ),
                }
            )
            continue

        clave = (int(norma_id), articulo)
        punto = referencias.get(clave)

        base = {
            **pregunta,
            "articulo_normalizado_proceso": articulo,
        }

        if punto is None:
            resultado["fuera_temario"].append(base)
            continue

        resultado["nuevas"].append(
            {
                **base,
                "tema_id": punto["tema_id"],
                "parte": punto["parte"],
                "numero_tema": punto["numero_tema"],
                "titulo_tema": punto["titulo"],
                "tipo_vinculacion_banco": TIPO_JURIDICA,
                "metodo_vinculacion": METODO_JURIDICO,
            }
        )

    return resultado


def seleccionar_no_juridicas(
    conexion: sqlite3.Connection,
    equivalencias: dict[str, dict[str, Any]],
    existentes: dict[int, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    resultado = {
        "nuevas": [],
        "ya_existentes": [],
        "sin_categoria": [],
        "fuera_temario": [],
    }

    filas = conexion.execute(
        """
        SELECT
            id,
            enunciado,
            tema_no_juridico,
            origen_oposicion,
            tipo_fuente,
            pagina_origen,
            teorica_practica
        FROM lote_preguntas
        WHERE tipo_clasificacion = ?
        ORDER BY id
        """,
        (CLASIFICACION_NO_JURIDICA,),
    ).fetchall()

    for fila_sql in filas:
        pregunta = dict(fila_sql)
        pregunta_id = int(pregunta["id"])

        if pregunta_id in existentes:
            resultado["ya_existentes"].append(
                {
                    **pregunta,
                    "banco_pregunta_id": existentes[pregunta_id][
                        "banco_pregunta_id"
                    ],
                }
            )
            continue

        categoria = normalizar_categoria(
            pregunta["tema_no_juridico"]
        )

        if not categoria:
            resultado["sin_categoria"].append(
                {
                    **pregunta,
                    "motivo": "TEMA_NO_JURIDICO_VACIO",
                }
            )
            continue

        punto = equivalencias.get(categoria)

        base = {
            **pregunta,
            "categoria_normalizada_proceso": categoria,
        }

        if punto is None:
            resultado["fuera_temario"].append(base)
            continue

        resultado["nuevas"].append(
            {
                **base,
                "tema_id": punto["tema_id"],
                "parte": punto["parte"],
                "numero_tema": punto["numero_tema"],
                "titulo_tema": punto["titulo"],
                "tipo_vinculacion_banco": TIPO_NO_JURIDICA,
                "metodo_vinculacion": METODO_NO_JURIDICO,
            }
        )

    return resultado


def resolver_parte_convocatoria(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    pregunta: dict[str, Any],
    tema: dict[str, Any],
) -> tuple[int | None, str | None]:
    """
    Resuelve la parte mediante las reglas explícitas ya almacenadas
    en convocatoria_parte_reglas. No introduce ningún criterio nuevo.
    """
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
            pregunta.get("teorica_practica"),
            pregunta.get("tema_no_juridico"),
        ),
    ).fetchall()

    if not filas:
        return None, "SIN_REGLA_PARTE"

    prioridad = int(filas[0]["prioridad"])
    partes = sorted(
        {
            int(fila["convocatoria_parte_id"])
            for fila in filas
            if int(fila["prioridad"]) == prioridad
        }
    )

    if len(partes) != 1:
        return None, "REGLAS_PARTE_AMBIGUAS"

    return partes[0], None


def insertar_nuevas(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    juridicas: list[dict[str, Any]],
    no_juridicas: list[dict[str, Any]],
) -> None:
    nuevas = [
        *juridicas,
        *no_juridicas,
    ]

    # Resolver todas las partes antes de iniciar la transacción.
    # Se reutilizan exclusivamente las reglas existentes en
    # convocatoria_parte_reglas.
    preparadas: list[tuple[dict[str, Any], int]] = []
    for fila in nuevas:
        tema_sql = conexion.execute(
            """
            SELECT parte, tipo_contenido
            FROM temario_temas
            WHERE id = ?
            """,
            (int(fila["tema_id"]),),
        ).fetchone()
        if tema_sql is None:
            raise RuntimeError(
                f"No existe el tema {fila['tema_id']} para la pregunta {fila['id']}."
            )

        tema = dict(tema_sql)
        parte_id, error_parte = resolver_parte_convocatoria(
            conexion,
            convocatoria_id,
            fila,
            tema,
        )
        if error_parte is not None or parte_id is None:
            raise RuntimeError(
                f"No se puede asignar parte a la pregunta {fila['id']}: "
                f"{error_parte or 'PARTE_NO_RESUELTA'}"
            )
        preparadas.append((fila, parte_id))

    conexion.execute("BEGIN IMMEDIATE")

    for fila, parte_id in preparadas:
        cursor = conexion.execute(
            """
            INSERT INTO banco_preguntas (
                convocatoria_id,
                pregunta_id,
                convocatoria_parte_id,
                tipo_vinculacion,
                estado,
                metodo_vinculacion,
                motivo_revision
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                convocatoria_id,
                int(fila["id"]),
                parte_id,
                fila["tipo_vinculacion_banco"],
                ESTADO_INCLUIDA,
                fila["metodo_vinculacion"],
            ),
        )

        banco_pregunta_id = int(cursor.lastrowid)

        conexion.execute(
            """
            INSERT INTO banco_preguntas_temas (
                banco_pregunta_id,
                tema_id,
                es_principal
            )
            VALUES (?, ?, 1)
            """,
            (
                banco_pregunta_id,
                int(fila["tema_id"]),
            ),
        )

    errores_fk = conexion.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if errores_fk:
        raise RuntimeError(
            f"Errores de claves externas: {errores_fk}"
        )

    integridad = conexion.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integridad != "ok":
        raise RuntimeError(
            f"integrity_check devolvió: {integridad}"
        )

    conexion.commit()


def auditar_resultado(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    temario_id: int,
) -> dict[str, Any]:
    total = int(
        conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
            """,
            (convocatoria_id,),
        ).fetchone()[0]
    )

    juridicas = int(
        conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
              AND tipo_vinculacion = 'JURIDICA'
            """,
            (convocatoria_id,),
        ).fetchone()[0]
    )

    no_juridicas = int(
        conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
              AND tipo_vinculacion = 'NO_JURIDICA'
            """,
            (convocatoria_id,),
        ).fetchone()[0]
    )

    revision = int(
        conexion.execute(
            """
            SELECT COUNT(*)
            FROM banco_preguntas
            WHERE convocatoria_id = ?
              AND estado = 'REVISION'
            """,
            (convocatoria_id,),
        ).fetchone()[0]
    )

    incidencias = validar_banco_actual(
        conexion,
        convocatoria_id,
        temario_id,
    )

    temas_sin_preguntas = [
        dict(fila)
        for fila in conexion.execute(
            """
            SELECT
                tt.id AS tema_id,
                tt.parte,
                tt.numero_tema,
                tt.titulo,
                tt.tipo_contenido
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
                tt.titulo,
                tt.tipo_contenido
            HAVING COUNT(DISTINCT bp.id) = 0
            ORDER BY
                tt.parte,
                tt.numero_tema,
                tt.id
            """,
            (convocatoria_id, temario_id),
        )
    ]

    distribucion = [
        dict(fila)
        for fila in conexion.execute(
            """
            SELECT
                tt.id AS tema_id,
                tt.parte,
                tt.numero_tema,
                tt.titulo,
                tt.tipo_contenido,
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
                tt.titulo,
                tt.tipo_contenido
            ORDER BY
                tt.parte,
                tt.numero_tema,
                tt.id
            """,
            (convocatoria_id, temario_id),
        )
    ]

    return {
        "total": total,
        "juridicas": juridicas,
        "no_juridicas": no_juridicas,
        "revision": revision,
        "incidencias": incidencias,
        "temas_sin_preguntas": temas_sin_preguntas,
        "distribucion": distribucion,
    }


def guardar_informes(
    carpeta: Path,
    resumen: dict[str, Any],
    juridicas: dict[str, list[dict[str, Any]]],
    no_juridicas: dict[str, list[dict[str, Any]]],
    invalidas_juridicas: list[dict[str, Any]],
    duplicadas_juridicas: list[dict[str, Any]],
    invalidas_no_juridicas: list[dict[str, Any]],
    duplicadas_no_juridicas: list[dict[str, Any]],
    incidencias_banco_inicial: list[dict[str, Any]],
    auditoria_final: dict[str, Any] | None,
) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)

    escribir_csv(
        carpeta / "juridicas_nuevas.csv",
        juridicas["nuevas"],
    )
    escribir_csv(
        carpeta / "juridicas_ya_existentes.csv",
        juridicas["ya_existentes"],
    )
    escribir_csv(
        carpeta / "juridicas_sin_normalizacion.csv",
        juridicas["sin_normalizacion"],
    )
    escribir_csv(
        carpeta / "juridicas_fuera_temario.csv",
        juridicas["fuera_temario"],
    )

    escribir_csv(
        carpeta / "no_juridicas_nuevas.csv",
        no_juridicas["nuevas"],
    )
    escribir_csv(
        carpeta / "no_juridicas_ya_existentes.csv",
        no_juridicas["ya_existentes"],
    )
    escribir_csv(
        carpeta / "no_juridicas_sin_categoria.csv",
        no_juridicas["sin_categoria"],
    )
    escribir_csv(
        carpeta / "no_juridicas_fuera_temario.csv",
        no_juridicas["fuera_temario"],
    )

    escribir_csv(
        carpeta / "referencias_juridicas_invalidas.csv",
        invalidas_juridicas,
    )
    escribir_csv(
        carpeta / "referencias_juridicas_repetidas.csv",
        duplicadas_juridicas,
    )
    escribir_csv(
        carpeta / "equivalencias_no_juridicas_invalidas.csv",
        invalidas_no_juridicas,
    )
    escribir_csv(
        carpeta / "equivalencias_no_juridicas_repetidas.csv",
        duplicadas_no_juridicas,
    )
    escribir_csv(
        carpeta / "incidencias_banco_inicial.csv",
        incidencias_banco_inicial,
    )

    if auditoria_final is not None:
        escribir_csv(
            carpeta / "distribucion_final_por_tema.csv",
            auditoria_final["distribucion"],
        )
        escribir_csv(
            carpeta / "temas_sin_preguntas.csv",
            auditoria_final["temas_sin_preguntas"],
        )
        escribir_csv(
            carpeta / "incidencias_banco_final.csv",
            auditoria_final["incidencias"],
        )

    (carpeta / "resumen.json").write_text(
        json.dumps(
            resumen,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lineas = [
        "MANTENIMIENTO DEL BANCO DE PREGUNTAS",
        "=" * 68,
        f"Fecha: {resumen['fecha']}",
        f"Base de datos: {resumen['base_datos']}",
        f"Modo: {resumen['modo']}",
        f"Convocatoria ID: {resumen['convocatoria_id']}",
        f"Código: {resumen['convocatoria_codigo']}",
        f"Temario ID: {resumen['temario_id']}",
        f"Temario: {resumen['temario_nombre']}",
        "",
        "VALIDACIONES PREVIAS",
        f"Referencias jurídicas inválidas: {len(invalidas_juridicas)}",
        f"Norma + artículo repetidos: {len(duplicadas_juridicas)}",
        (
            "Equivalencias no jurídicas inválidas: "
            f"{len(invalidas_no_juridicas)}"
        ),
        (
            "Categorías no jurídicas repetidas: "
            f"{len(duplicadas_no_juridicas)}"
        ),
        (
            "Incidencias en el banco inicial: "
            f"{len(incidencias_banco_inicial)}"
        ),
        "",
        "PREGUNTAS JURÍDICAS",
        f"Nuevas incorporables: {len(juridicas['nuevas'])}",
        f"Ya existentes: {len(juridicas['ya_existentes'])}",
        (
            "Sin normalización suficiente: "
            f"{len(juridicas['sin_normalizacion'])}"
        ),
        f"Fuera del temario: {len(juridicas['fuera_temario'])}",
        "",
        "PREGUNTAS NO JURÍDICAS",
        f"Nuevas incorporables: {len(no_juridicas['nuevas'])}",
        f"Ya existentes: {len(no_juridicas['ya_existentes'])}",
        f"Sin categoría: {len(no_juridicas['sin_categoria'])}",
        f"Fuera del temario: {len(no_juridicas['fuera_temario'])}",
        "",
        (
            "Total nuevas incorporables: "
            f"{len(juridicas['nuevas']) + len(no_juridicas['nuevas'])}"
        ),
        "lote_preguntas no se modifica.",
    ]

    if auditoria_final is not None:
        lineas.extend(
            [
                "",
                "RESULTADO FINAL",
                f"Total banco: {auditoria_final['total']}",
                f"Jurídicas: {auditoria_final['juridicas']}",
                f"No jurídicas: {auditoria_final['no_juridicas']}",
                f"En revisión: {auditoria_final['revision']}",
                (
                    "Temas sin preguntas: "
                    f"{len(auditoria_final['temas_sin_preguntas'])}"
                ),
                (
                    "Incidencias finales: "
                    f"{len(auditoria_final['incidencias'])}"
                ),
            ]
        )

    if resumen.get("copia_seguridad"):
        lineas.append(
            f"Copia de seguridad: {resumen['copia_seguridad']}"
        )

    lineas.append(f"Informes: {carpeta}")

    (carpeta / "informe.txt").write_text(
        "\n".join(lineas) + "\n",
        encoding="utf-8-sig",
    )


def main() -> None:
    args = crear_parser().parse_args()

    if (
        args.convocatoria_id is not None
        and args.convocatoria_id <= 0
    ):
        raise ValueError(
            "--convocatoria-id debe ser mayor que cero."
        )

    if args.codigo is not None and not args.codigo.strip():
        raise ValueError("--codigo no puede estar vacío.")

    db = Path(args.db).resolve()

    if not db.exists():
        raise FileNotFoundError(
            f"No existe la base de datos: {db}"
        )

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = RAIZ / "auditorias" / "mantener_banco"

    if carpeta.exists():
        shutil.rmtree(carpeta)

    carpeta.mkdir(parents=True, exist_ok=True)

    copia_seguridad: Path | None = None
    auditoria_final: dict[str, Any] | None = None

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")

        validar_estructura(conexion)

        convocatoria = buscar_convocatoria(
            conexion,
            args.convocatoria_id,
            args.codigo,
        )
        convocatoria_id = int(convocatoria["id"])

        columnas_conv = columnas_tabla(conexion, "convocatorias")
        if (
            args.guardar
            and "activa" in columnas_conv
            and int(convocatoria["activa"] or 0) != 1
        ):
            raise RuntimeError(
                "La convocatoria está dada de baja. No se puede modificar su banco. "
                "Reactívela primero desde el menú de convocatorias."
            )

        temario = buscar_temario(
            conexion,
            convocatoria_id,
        )
        temario_id = int(temario["id"])

        (
            referencias_juridicas,
            invalidas_juridicas,
            duplicadas_juridicas,
        ) = cargar_referencias_juridicas(
            conexion,
            temario_id,
        )

        (
            equivalencias_no_juridicas,
            invalidas_no_juridicas,
            duplicadas_no_juridicas,
        ) = cargar_equivalencias_no_juridicas(
            conexion,
            temario_id,
        )

        incidencias_banco_inicial = validar_banco_actual(
            conexion,
            convocatoria_id,
            temario_id,
        )

        existentes = cargar_existentes(
            conexion,
            convocatoria_id,
        )

        juridicas = seleccionar_juridicas(
            conexion,
            convocatoria_id,
            referencias_juridicas,
            existentes,
        )

        no_juridicas = seleccionar_no_juridicas(
            conexion,
            equivalencias_no_juridicas,
            existentes,
        )

        bloqueos = (
            len(invalidas_juridicas)
            + len(duplicadas_juridicas)
            + len(invalidas_no_juridicas)
            + len(duplicadas_no_juridicas)
            + len(incidencias_banco_inicial)
        )

        if args.guardar and bloqueos == 0:
            copia_seguridad = db.with_name(
                f"{db.stem}_backup_antes_banco_{marca}{db.suffix}"
            )
            shutil.copy2(db, copia_seguridad)

            insertar_nuevas(
                conexion,
                convocatoria_id,
                juridicas["nuevas"],
                no_juridicas["nuevas"],
            )

            auditoria_final = auditar_resultado(
                conexion,
                convocatoria_id,
                temario_id,
            )

            if auditoria_final["incidencias"]:
                raise RuntimeError(
                    "La auditoría posterior detectó incidencias. "
                    "Revise la copia de seguridad y los informes."
                )
        else:
            auditoria_final = auditar_resultado(
                conexion,
                convocatoria_id,
                temario_id,
            )

    resumen = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "base_datos": str(db),
        "modo": "GUARDAR" if args.guardar else "SOLO_REVISION",
        "convocatoria_id": convocatoria_id,
        "convocatoria_codigo": convocatoria["codigo"],
        "temario_id": temario_id,
        "temario_nombre": temario["nombre"],
        "copia_seguridad": (
            str(copia_seguridad)
            if copia_seguridad is not None
            else None
        ),
        "bloqueos": bloqueos,
        "juridicas_nuevas": len(juridicas["nuevas"]),
        "no_juridicas_nuevas": len(no_juridicas["nuevas"]),
        "total_nuevas": (
            len(juridicas["nuevas"])
            + len(no_juridicas["nuevas"])
        ),
        "total_banco_final": auditoria_final["total"],
        "juridicas_banco_final": auditoria_final["juridicas"],
        "no_juridicas_banco_final": auditoria_final[
            "no_juridicas"
        ],
        "revision_banco_final": auditoria_final["revision"],
        "incidencias_finales": len(
            auditoria_final["incidencias"]
        ),
    }

    guardar_informes(
        carpeta=carpeta,
        resumen=resumen,
        juridicas=juridicas,
        no_juridicas=no_juridicas,
        invalidas_juridicas=invalidas_juridicas,
        duplicadas_juridicas=duplicadas_juridicas,
        invalidas_no_juridicas=invalidas_no_juridicas,
        duplicadas_no_juridicas=duplicadas_no_juridicas,
        incidencias_banco_inicial=incidencias_banco_inicial,
        auditoria_final=auditoria_final,
    )

    print("MANTENIMIENTO DEL BANCO DE PREGUNTAS")
    print(f"Convocatoria: {convocatoria_id} | {convocatoria['codigo']}")
    print(f"Temario: {temario_id} | {temario['nombre']}")
    print(f"Modo: {'GUARDAR' if args.guardar else 'SOLO REVISIÓN'}")
    print()

    print("Validaciones:")
    print(
        "  Norma + artículo repetidos: "
        f"{len(duplicadas_juridicas)}"
    )
    print(
        "  Categorías no jurídicas repetidas: "
        f"{len(duplicadas_no_juridicas)}"
    )
    print(
        "  Referencias o equivalencias inválidas: "
        f"{len(invalidas_juridicas) + len(invalidas_no_juridicas)}"
    )
    print(
        "  Incidencias en el banco actual: "
        f"{len(incidencias_banco_inicial)}"
    )
    print()

    print(f"Jurídicas nuevas: {len(juridicas['nuevas'])}")
    print(
        f"No jurídicas nuevas: {len(no_juridicas['nuevas'])}"
    )
    print(
        "Total nuevas: "
        f"{len(juridicas['nuevas']) + len(no_juridicas['nuevas'])}"
    )

    if args.guardar and bloqueos:
        print()
        print(
            "PROCESO DETENIDO: no se ha insertado ninguna pregunta."
        )
        print(
            "Revise los CSV de validación antes de volver a ejecutarlo."
        )
        print(f"Informes: {carpeta}")
        raise SystemExit(2)

    if args.guardar:
        print()
        print(f"Total final del banco: {auditoria_final['total']}")
        print(f"Jurídicas: {auditoria_final['juridicas']}")
        print(
            f"No jurídicas: {auditoria_final['no_juridicas']}"
        )
        print(f"En revisión: {auditoria_final['revision']}")
        print(
            f"Incidencias finales: "
            f"{len(auditoria_final['incidencias'])}"
        )
        print(f"Copia de seguridad: {copia_seguridad}")

    print(f"Informes: {carpeta}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "\nOperación cancelada.",
            file=sys.stderr,
        )
        raise SystemExit(130)
    except Exception as error:
        print(
            f"\nERROR: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)