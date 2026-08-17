"""
OpoCoach - Auditoría exhaustiva del corpus jurídico del temario.

Este script NO modifica la base de datos.

Revisa, cuando las tablas y columnas necesarias existen:

- integridad SQLite
- referencias pendientes
- referencias completadas sin artículo enlazado
- referencias pendientes que ya tienen artículo enlazado
- enlaces rotos hacia articulos_fuente
- artículos vacíos o sospechosamente breves
- duplicados en articulos_fuente
- duplicados en resoluciones_boe
- resoluciones rotas
- artículos huérfanos
- normas sin ningún artículo completado
- inconsistencias entre temario_referencias y resoluciones_boe
- valores vacíos en campos esenciales
- distribución general del corpus

Uso desde la raíz del proyecto:

    python scripts/auditar_corpus_temario.py

También puede indicarse otra base de datos:

    python scripts/auditar_corpus_temario.py --db ruta/a/oposiciones.sqlite3

Salida:

    auditorias/temario_AAAAMMDD_HHMMSS/
        informe_auditoria_temario.txt
        resumen.json
        *.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from boe_api import (
    texto_articulo_suficiente,
)


RAIZ = Path(__file__).resolve().parent.parent
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita el corpus jurídico del temario sin modificar la base de datos."
    )
    parser.add_argument(
        "--db",
        default=str(DB_PREDETERMINADA),
        help="Ruta de la base de datos SQLite.",
    )
    return parser.parse_args()


def existe_tabla(conexion: sqlite3.Connection, nombre: str) -> bool:
    fila = conexion.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (nombre,),
    ).fetchone()
    return fila is not None


def columnas_tabla(
    conexion: sqlite3.Connection,
    nombre: str,
) -> set[str]:
    if not existe_tabla(conexion, nombre):
        return set()

    return {
        str(fila["name"])
        for fila in conexion.execute(f"PRAGMA table_info({nombre})")
    }


def filas_a_dicts(filas: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(fila) for fila in filas]


def guardar_csv(
    ruta: Path,
    filas: list[dict[str, Any]],
) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)

    if not filas:
        ruta.write_text("", encoding="utf-8-sig")
        return

    columnas: list[str] = []
    vistas: set[str] = set()

    for fila in filas:
        for clave in fila:
            if clave not in vistas:
                vistas.add(clave)
                columnas.append(clave)

    with ruta.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as archivo:
        escritor = csv.DictWriter(
            archivo,
            fieldnames=columnas,
            extrasaction="ignore",
        )
        escritor.writeheader()
        escritor.writerows(filas)


def valor_texto(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor)


def ejecutar_consulta(
    conexion: sqlite3.Connection,
    sql: str,
    parametros: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    return filas_a_dicts(conexion.execute(sql, parametros).fetchall())


def encabezado(titulo: str) -> list[str]:
    return ["", titulo, "-" * len(titulo)]


def main() -> None:
    args = argumentos()
    ruta_db = Path(args.db).resolve()

    if not ruta_db.exists():
        raise FileNotFoundError(
            f"No existe la base de datos: {ruta_db}"
        )

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = RAIZ / "auditorias" / f"temario_{marca}"
    carpeta.mkdir(parents=True, exist_ok=True)

    resumen: dict[str, Any] = {
        "base_datos": str(ruta_db),
        "fecha_auditoria": datetime.now().isoformat(timespec="seconds"),
        "tablas_detectadas": [],
        "comprobaciones_omitidas": [],
        "metricas": {},
        "incidencias": {},
    }

    lineas: list[str] = [
        "AUDITORÍA EXHAUSTIVA DEL CORPUS JURÍDICO DEL TEMARIO",
        "=" * 70,
        f"Base de datos: {ruta_db}",
        f"Fecha: {resumen['fecha_auditoria']}",
        "La auditoría es de solo lectura.",
    ]

    with sqlite3.connect(f"file:{ruta_db}?mode=ro", uri=True) as conexion:
        conexion.row_factory = sqlite3.Row

        tablas = [
            str(fila["name"])
            for fila in conexion.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        resumen["tablas_detectadas"] = tablas

        columnas = {
            tabla: columnas_tabla(conexion, tabla)
            for tabla in tablas
        }

        lineas.extend(encabezado("1. Estructura detectada"))
        lineas.append(f"Tablas: {len(tablas)}")
        for tabla in tablas:
            lineas.append(
                f"- {tabla}: {len(columnas[tabla])} columnas"
            )

        # ------------------------------------------------------------
        # Integridad SQLite
        # ------------------------------------------------------------
        lineas.extend(encabezado("2. Integridad SQLite"))

        integrity = conexion.execute("PRAGMA integrity_check").fetchall()
        integrity_values = [str(fila[0]) for fila in integrity]
        resumen["metricas"]["integrity_check"] = integrity_values

        if integrity_values == ["ok"]:
            lineas.append("PRAGMA integrity_check: OK")
        else:
            lineas.append("PRAGMA integrity_check: INCIDENCIAS")
            lineas.extend(f"- {x}" for x in integrity_values)

        foreign_keys = filas_a_dicts(
            conexion.execute("PRAGMA foreign_key_check").fetchall()
        )
        guardar_csv(carpeta / "foreign_key_check.csv", foreign_keys)
        resumen["incidencias"]["foreign_key_check"] = len(foreign_keys)
        lineas.append(
            f"PRAGMA foreign_key_check: {len(foreign_keys)} incidencias"
        )

        # ------------------------------------------------------------
        # temario_referencias
        # ------------------------------------------------------------
        if "temario_referencias" in tablas:
            cols_ref = columnas["temario_referencias"]
            total_ref = conexion.execute(
                "SELECT COUNT(*) FROM temario_referencias"
            ).fetchone()[0]
            resumen["metricas"]["temario_referencias_total"] = total_ref

            lineas.extend(encabezado("3. Referencias del temario"))
            lineas.append(f"Total de referencias: {total_ref}")

            if "estado" in cols_ref:
                por_estado = ejecutar_consulta(
                    conexion,
                    """
                    SELECT COALESCE(estado, '<NULL>') AS estado,
                           COUNT(*) AS total
                    FROM temario_referencias
                    GROUP BY COALESCE(estado, '<NULL>')
                    ORDER BY total DESC, estado
                    """
                )
                guardar_csv(carpeta / "referencias_por_estado.csv", por_estado)
                resumen["metricas"]["referencias_por_estado"] = por_estado

                for fila in por_estado:
                    lineas.append(
                        f"- {fila['estado']}: {fila['total']}"
                    )

                pendientes = ejecutar_consulta(
                    conexion,
                    """
                    SELECT *
                    FROM temario_referencias
                    WHERE estado = 'PENDIENTE'
                    ORDER BY
                        COALESCE(nombre_norma_normalizada, ''),
                        COALESCE(articulo_solicitado, ''),
                        id
                    """
                )
                guardar_csv(carpeta / "referencias_pendientes.csv", pendientes)
                resumen["incidencias"]["referencias_pendientes"] = len(pendientes)
                lineas.append(
                    f"Referencias aún PENDIENTE: {len(pendientes)}"
                )
            else:
                resumen["comprobaciones_omitidas"].append(
                    "temario_referencias.estado no existe"
                )

            if {
                "estado",
                "articulo_fuente_id",
            }.issubset(cols_ref):
                completadas_sin_articulo = ejecutar_consulta(
                    conexion,
                    """
                    SELECT *
                    FROM temario_referencias
                    WHERE estado = 'COMPLETADO'
                      AND articulo_fuente_id IS NULL
                    ORDER BY id
                    """
                )
                guardar_csv(
                    carpeta / "completadas_sin_articulo.csv",
                    completadas_sin_articulo,
                )
                resumen["incidencias"][
                    "completadas_sin_articulo"
                ] = len(completadas_sin_articulo)

                pendientes_con_articulo = ejecutar_consulta(
                    conexion,
                    """
                    SELECT *
                    FROM temario_referencias
                    WHERE estado = 'PENDIENTE'
                      AND articulo_fuente_id IS NOT NULL
                    ORDER BY id
                    """
                )
                guardar_csv(
                    carpeta / "pendientes_con_articulo.csv",
                    pendientes_con_articulo,
                )
                resumen["incidencias"][
                    "pendientes_con_articulo"
                ] = len(pendientes_con_articulo)

                lineas.append(
                    "COMPLETADO sin articulo_fuente_id: "
                    f"{len(completadas_sin_articulo)}"
                )
                lineas.append(
                    "PENDIENTE con articulo_fuente_id: "
                    f"{len(pendientes_con_articulo)}"
                )

            campos_esenciales = [
                campo
                for campo in (
                    "nombre_norma_normalizada",
                    "articulo_solicitado",
                )
                if campo in cols_ref
            ]

            if campos_esenciales:
                condiciones = " OR ".join(
                    f"{campo} IS NULL OR TRIM(CAST({campo} AS TEXT)) = ''"
                    for campo in campos_esenciales
                )
                esenciales_vacios = ejecutar_consulta(
                    conexion,
                    f"""
                    SELECT *
                    FROM temario_referencias
                    WHERE {condiciones}
                    ORDER BY id
                    """
                )
                guardar_csv(
                    carpeta / "referencias_campos_esenciales_vacios.csv",
                    esenciales_vacios,
                )
                resumen["incidencias"][
                    "referencias_campos_esenciales_vacios"
                ] = len(esenciales_vacios)
                lineas.append(
                    "Referencias con campos esenciales vacíos: "
                    f"{len(esenciales_vacios)}"
                )

            if {
                "articulo_fuente_id",
            }.issubset(cols_ref) and "articulos_fuente" in tablas:
                referencias_rotas = ejecutar_consulta(
                    conexion,
                    """
                    SELECT tr.*
                    FROM temario_referencias tr
                    LEFT JOIN articulos_fuente af
                      ON af.id = tr.articulo_fuente_id
                    WHERE tr.articulo_fuente_id IS NOT NULL
                      AND af.id IS NULL
                    ORDER BY tr.id
                    """
                )
                guardar_csv(
                    carpeta / "referencias_enlace_roto.csv",
                    referencias_rotas,
                )
                resumen["incidencias"][
                    "referencias_enlace_roto"
                ] = len(referencias_rotas)
                lineas.append(
                    "Referencias con enlace roto: "
                    f"{len(referencias_rotas)}"
                )

            if {
                "nombre_norma_normalizada",
                "estado",
            }.issubset(cols_ref):
                normas_sin_completados = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        nombre_norma_normalizada,
                        COUNT(*) AS referencias_totales,
                        SUM(
                            CASE
                                WHEN estado = 'COMPLETADO' THEN 1
                                ELSE 0
                            END
                        ) AS referencias_completadas,
                        SUM(
                            CASE
                                WHEN estado = 'PENDIENTE' THEN 1
                                ELSE 0
                            END
                        ) AS referencias_pendientes
                    FROM temario_referencias
                    GROUP BY nombre_norma_normalizada
                    HAVING SUM(
                        CASE
                            WHEN estado = 'COMPLETADO' THEN 1
                            ELSE 0
                        END
                    ) = 0
                    ORDER BY referencias_totales DESC,
                             nombre_norma_normalizada
                    """
                )
                guardar_csv(
                    carpeta / "normas_sin_referencias_completadas.csv",
                    normas_sin_completados,
                )
                resumen["incidencias"][
                    "normas_sin_referencias_completadas"
                ] = len(normas_sin_completados)
                lineas.append(
                    "Normas sin ninguna referencia completada: "
                    f"{len(normas_sin_completados)}"
                )
        else:
            resumen["comprobaciones_omitidas"].append(
                "No existe la tabla temario_referencias"
            )

        # ------------------------------------------------------------
        # articulos_fuente
        # ------------------------------------------------------------
        if "articulos_fuente" in tablas:
            cols_art = columnas["articulos_fuente"]
            total_art = conexion.execute(
                "SELECT COUNT(*) FROM articulos_fuente"
            ).fetchone()[0]
            resumen["metricas"]["articulos_fuente_total"] = total_art

            lineas.extend(encabezado("4. Artículos fuente"))
            lineas.append(f"Total de artículos fuente: {total_art}")

            if "texto" in cols_art:
                articulos_vacios = ejecutar_consulta(
                    conexion,
                    """
                    SELECT *
                    FROM articulos_fuente
                    WHERE texto IS NULL
                       OR TRIM(texto) = ''
                    ORDER BY id
                    """
                )
                guardar_csv(
                    carpeta / "articulos_texto_vacio.csv",
                    articulos_vacios,
                )
                resumen["incidencias"]["articulos_texto_vacio"] = len(
                    articulos_vacios
                )

                articulos_breves = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        id,
                        id_boe,
                        id_bloque,
                        articulo_boe,
                        titulo_bloque,
                        LENGTH(TRIM(texto)) AS longitud_texto,
                        texto
                    FROM articulos_fuente
                    WHERE texto IS NOT NULL
                      AND LENGTH(TRIM(texto)) < 80
                    ORDER BY longitud_texto, id
                    """
                )
                guardar_csv(
                    carpeta / "articulos_texto_breve.csv",
                    articulos_breves,
                )
                resumen["incidencias"]["articulos_texto_breve"] = len(
                    articulos_breves
                )

                todos_articulos_texto = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        id,
                        id_boe,
                        id_bloque,
                        articulo_boe,
                        titulo_bloque,
                        LENGTH(TRIM(texto)) AS longitud_texto,
                        texto
                    FROM articulos_fuente
                    WHERE texto IS NOT NULL
                    ORDER BY id
                    """
                )
                articulos_incompletos = [
                    fila
                    for fila in todos_articulos_texto
                    if not texto_articulo_suficiente(
                        fila.get("texto"),
                        fila.get("titulo_bloque"),
                    )
                ]
                guardar_csv(
                    carpeta / "articulos_texto_incompleto.csv",
                    articulos_incompletos,
                )
                resumen["incidencias"]["articulos_texto_incompleto"] = len(
                    articulos_incompletos
                )

                lineas.append(
                    f"Artículos con texto vacío: {len(articulos_vacios)}"
                )
                lineas.append(
                    "Artículos manifiestamente incompletos "
                    f"(vacío o solo encabezado): {len(articulos_incompletos)}"
                )
                lineas.append(
                    "Artículos con menos de 80 caracteres "
                    f"(solo aviso): {len(articulos_breves)}"
                )
            if {"id_boe", "id_bloque"}.issubset(cols_art):
                duplicados_identidad = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        id_boe,
                        id_bloque,
                        COUNT(*) AS repeticiones,
                        GROUP_CONCAT(id) AS ids
                    FROM articulos_fuente
                    GROUP BY id_boe, id_bloque
                    HAVING COUNT(*) > 1
                    ORDER BY repeticiones DESC, id_boe, id_bloque
                    """
                )
                guardar_csv(
                    carpeta / "duplicados_articulos_identidad.csv",
                    duplicados_identidad,
                )
                resumen["incidencias"][
                    "duplicados_articulos_identidad"
                ] = len(duplicados_identidad)
                lineas.append(
                    "Duplicados por id_boe + id_bloque: "
                    f"{len(duplicados_identidad)}"
                )

            if "hash_texto" in cols_art:
                duplicados_hash = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        hash_texto,
                        COUNT(*) AS repeticiones,
                        GROUP_CONCAT(id) AS ids,
                        GROUP_CONCAT(
                            COALESCE(id_boe, '') || ' / ' ||
                            COALESCE(id_bloque, ''),
                            ' | '
                        ) AS fuentes
                    FROM articulos_fuente
                    WHERE hash_texto IS NOT NULL
                      AND TRIM(hash_texto) <> ''
                    GROUP BY hash_texto
                    HAVING COUNT(*) > 1
                    ORDER BY repeticiones DESC, hash_texto
                    """
                )
                guardar_csv(
                    carpeta / "duplicados_articulos_hash.csv",
                    duplicados_hash,
                )
                resumen["incidencias"][
                    "duplicados_articulos_hash"
                ] = len(duplicados_hash)
                lineas.append(
                    "Grupos con el mismo hash de texto: "
                    f"{len(duplicados_hash)}"
                )

            if (
                "temario_referencias" in tablas
                and "articulo_fuente_id"
                in columnas["temario_referencias"]
            ):
                tiene_resoluciones = (
                    "resoluciones_boe" in tablas
                    and "articulo_fuente_id"
                    in columnas["resoluciones_boe"]
                )

                if tiene_resoluciones:
                    articulos_huerfanos = ejecutar_consulta(
                        conexion,
                        """
                        SELECT af.*
                        FROM articulos_fuente af
                        LEFT JOIN temario_referencias tr
                          ON tr.articulo_fuente_id = af.id
                        LEFT JOIN resoluciones_boe rb
                          ON rb.articulo_fuente_id = af.id
                        WHERE tr.id IS NULL
                          AND rb.id IS NULL
                        ORDER BY af.id
                        """
                    )
                else:
                    articulos_huerfanos = ejecutar_consulta(
                        conexion,
                        """
                        SELECT af.*
                        FROM articulos_fuente af
                        LEFT JOIN temario_referencias tr
                          ON tr.articulo_fuente_id = af.id
                        WHERE tr.id IS NULL
                        ORDER BY af.id
                        """
                    )

                guardar_csv(
                    carpeta / "articulos_huerfanos.csv",
                    articulos_huerfanos,
                )
                resumen["incidencias"]["articulos_huerfanos"] = len(
                    articulos_huerfanos
                )
                lineas.append(
                    f"Artículos huérfanos: {len(articulos_huerfanos)}"
                )
        else:
            resumen["comprobaciones_omitidas"].append(
                "No existe la tabla articulos_fuente"
            )

        # ------------------------------------------------------------
        # resoluciones_boe
        # ------------------------------------------------------------
        if "resoluciones_boe" in tablas:
            cols_res = columnas["resoluciones_boe"]
            total_res = conexion.execute(
                "SELECT COUNT(*) FROM resoluciones_boe"
            ).fetchone()[0]
            resumen["metricas"]["resoluciones_boe_total"] = total_res

            lineas.extend(encabezado("5. Resoluciones normativas"))
            lineas.append(f"Total de resoluciones: {total_res}")

            clave_resolucion = {
                "nombre_norma_normalizada",
                "articulo_solicitado_normalizado",
            }

            if clave_resolucion.issubset(cols_res):
                duplicados_resolucion = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        nombre_norma_normalizada,
                        articulo_solicitado_normalizado,
                        COUNT(*) AS repeticiones,
                        GROUP_CONCAT(id) AS ids,
                        GROUP_CONCAT(
                            COALESCE(CAST(articulo_fuente_id AS TEXT), ''),
                            ','
                        ) AS articulos_fuente_ids
                    FROM resoluciones_boe
                    GROUP BY
                        nombre_norma_normalizada,
                        articulo_solicitado_normalizado
                    HAVING COUNT(*) > 1
                    ORDER BY repeticiones DESC,
                             nombre_norma_normalizada,
                             articulo_solicitado_normalizado
                    """
                )
                guardar_csv(
                    carpeta / "duplicados_resoluciones.csv",
                    duplicados_resolucion,
                )
                resumen["incidencias"][
                    "duplicados_resoluciones"
                ] = len(duplicados_resolucion)
                lineas.append(
                    "Resoluciones duplicadas por norma + artículo: "
                    f"{len(duplicados_resolucion)}"
                )

            if (
                "articulo_fuente_id" in cols_res
                and "articulos_fuente" in tablas
            ):
                resoluciones_rotas = ejecutar_consulta(
                    conexion,
                    """
                    SELECT rb.*
                    FROM resoluciones_boe rb
                    LEFT JOIN articulos_fuente af
                      ON af.id = rb.articulo_fuente_id
                    WHERE rb.articulo_fuente_id IS NULL
                       OR af.id IS NULL
                    ORDER BY rb.id
                    """
                )
                guardar_csv(
                    carpeta / "resoluciones_enlace_roto.csv",
                    resoluciones_rotas,
                )
                resumen["incidencias"][
                    "resoluciones_enlace_roto"
                ] = len(resoluciones_rotas)
                lineas.append(
                    "Resoluciones con enlace roto: "
                    f"{len(resoluciones_rotas)}"
                )

            if (
                "temario_referencias" in tablas
                and {
                    "nombre_norma_normalizada",
                    "articulo_solicitado",
                    "articulo_fuente_id",
                    "estado",
                }.issubset(columnas["temario_referencias"])
                and {
                    "nombre_norma_normalizada",
                    "articulo_solicitado_normalizado",
                    "articulo_fuente_id",
                }.issubset(cols_res)
            ):
                completadas_sin_resolucion = ejecutar_consulta(
                    conexion,
                    """
                    SELECT tr.*
                    FROM temario_referencias tr
                    LEFT JOIN resoluciones_boe rb
                      ON rb.nombre_norma_normalizada =
                         tr.nombre_norma_normalizada
                     AND rb.articulo_solicitado_normalizado =
                         TRIM(CAST(tr.articulo_solicitado AS TEXT))
                    WHERE tr.estado = 'COMPLETADO'
                      AND rb.id IS NULL
                    ORDER BY tr.id
                    """
                )
                guardar_csv(
                    carpeta / "completadas_sin_resolucion.csv",
                    completadas_sin_resolucion,
                )
                resumen["incidencias"][
                    "completadas_sin_resolucion"
                ] = len(completadas_sin_resolucion)

                desacuerdos = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        tr.id AS referencia_id,
                        tr.nombre_norma_normalizada,
                        tr.articulo_solicitado,
                        tr.articulo_fuente_id AS referencia_articulo_fuente_id,
                        rb.id AS resolucion_id,
                        rb.articulo_fuente_id AS resolucion_articulo_fuente_id
                    FROM temario_referencias tr
                    JOIN resoluciones_boe rb
                      ON rb.nombre_norma_normalizada =
                         tr.nombre_norma_normalizada
                     AND rb.articulo_solicitado_normalizado =
                         TRIM(CAST(tr.articulo_solicitado AS TEXT))
                    WHERE tr.articulo_fuente_id IS NOT
                          rb.articulo_fuente_id
                    ORDER BY tr.id
                    """
                )
                guardar_csv(
                    carpeta / "desacuerdo_referencia_resolucion.csv",
                    desacuerdos,
                )
                resumen["incidencias"][
                    "desacuerdo_referencia_resolucion"
                ] = len(desacuerdos)

                lineas.append(
                    "Referencias COMPLETADO sin resolución: "
                    f"{len(completadas_sin_resolucion)}"
                )
                lineas.append(
                    "Desacuerdos de articulo_fuente_id entre "
                    f"referencia y resolución: {len(desacuerdos)}"
                )
        else:
            resumen["comprobaciones_omitidas"].append(
                "No existe la tabla resoluciones_boe"
            )

        # ------------------------------------------------------------
        # Distribuciones útiles
        # ------------------------------------------------------------
        lineas.extend(encabezado("6. Distribución del corpus"))

        if "articulos_fuente" in tablas:
            cols_art = columnas["articulos_fuente"]

            if "id_boe" in cols_art:
                por_fuente = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        COALESCE(id_boe, '<NULL>') AS id_boe,
                        COUNT(*) AS articulos
                    FROM articulos_fuente
                    GROUP BY COALESCE(id_boe, '<NULL>')
                    ORDER BY articulos DESC, id_boe
                    """
                )
                guardar_csv(
                    carpeta / "articulos_por_fuente.csv",
                    por_fuente,
                )
                resumen["metricas"]["articulos_por_fuente"] = por_fuente
                lineas.append(
                    f"Fuentes normativas distintas: {len(por_fuente)}"
                )

        if "temario_referencias" in tablas:
            cols_ref = columnas["temario_referencias"]

            if "nombre_norma_normalizada" in cols_ref:
                por_norma = ejecutar_consulta(
                    conexion,
                    """
                    SELECT
                        COALESCE(
                            nombre_norma_normalizada,
                            '<NULL>'
                        ) AS nombre_norma_normalizada,
                        COUNT(*) AS referencias,
                        SUM(
                            CASE
                                WHEN estado = 'COMPLETADO'
                                THEN 1 ELSE 0
                            END
                        ) AS completadas,
                        SUM(
                            CASE
                                WHEN estado = 'PENDIENTE'
                                THEN 1 ELSE 0
                            END
                        ) AS pendientes
                    FROM temario_referencias
                    GROUP BY COALESCE(
                        nombre_norma_normalizada,
                        '<NULL>'
                    )
                    ORDER BY referencias DESC,
                             nombre_norma_normalizada
                    """
                )
                guardar_csv(
                    carpeta / "referencias_por_norma.csv",
                    por_norma,
                )
                resumen["metricas"]["referencias_por_norma"] = por_norma
                lineas.append(
                    f"Normas distintas en referencias: {len(por_norma)}"
                )

    # ------------------------------------------------------------
    # Clasificación final
    # ------------------------------------------------------------
    incidencia_totales = {
        clave: valor
        for clave, valor in resumen["incidencias"].items()
        if isinstance(valor, int)
    }

    criticas = {
        "foreign_key_check",
        "referencias_pendientes",
        "completadas_sin_articulo",
        "pendientes_con_articulo",
        "referencias_enlace_roto",
        "articulos_texto_vacio",
        "articulos_texto_incompleto",
        "duplicados_articulos_identidad",
        "duplicados_resoluciones",
        "resoluciones_enlace_roto",
        "completadas_sin_resolucion",
        "desacuerdo_referencia_resolucion",
        "referencias_campos_esenciales_vacios",
    }

    total_criticas = sum(
        cantidad
        for clave, cantidad in incidencia_totales.items()
        if clave in criticas
    )

    total_avisos = sum(
        cantidad
        for clave, cantidad in incidencia_totales.items()
        if clave not in criticas
    )

    resumen["resultado"] = {
        "incidencias_criticas": total_criticas,
        "avisos": total_avisos,
        "estado": (
            "CORRECTO"
            if total_criticas == 0
            else "REVISAR"
        ),
    }

    lineas.extend(encabezado("7. Resultado final"))
    lineas.append(
        f"Incidencias críticas: {total_criticas}"
    )
    lineas.append(f"Avisos: {total_avisos}")
    lineas.append(
        f"Estado general: {resumen['resultado']['estado']}"
    )

    if resumen["comprobaciones_omitidas"]:
        lineas.append("")
        lineas.append("Comprobaciones omitidas:")
        for texto in resumen["comprobaciones_omitidas"]:
            lineas.append(f"- {texto}")

    lineas.append("")
    lineas.append(f"Carpeta de resultados: {carpeta}")

    informe = carpeta / "informe_auditoria_temario.txt"
    informe.write_text(
        "\n".join(lineas) + "\n",
        encoding="utf-8-sig",
    )

    (carpeta / "resumen.json").write_text(
        json.dumps(
            resumen,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n".join(lineas[-12:]))
    print()
    print(f"Informe completo: {informe}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperación cancelada.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(
            f"\nERROR: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)