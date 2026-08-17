"""Funciones comunes para publicar importaciones de preguntas.

Este módulo utiliza exclusivamente las tablas y columnas ya existentes en la
base de datos de OpoCoach. El criterio de duplicado es único: igualdad exacta
de enunciado y de las cuatro opciones. No interviene ningún otro campo.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CAMPOS_DUPLICADO = (
    "enunciado",
    "opcion_a",
    "opcion_b",
    "opcion_c",
    "opcion_d",
)

CAMPOS_INSERCION = (
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
    "importacion_fichero_id",
    "pagina_origen",
    "teorica_practica",
)


def ahora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def calcular_sha256(ruta: Path) -> str:
    resumen = hashlib.sha256()
    with ruta.open("rb") as fichero:
        while bloque := fichero.read(1024 * 1024):
            resumen.update(bloque)
    return resumen.hexdigest()


def ruta_relativa_proyecto(ruta: Path, raiz: Path) -> str:
    try:
        return ruta.resolve().relative_to(raiz.resolve()).as_posix()
    except ValueError:
        return str(ruta.resolve())


def buscar_importacion(
    conexion: sqlite3.Connection,
    hash_sha256: str,
    tipo_fuente: str,
) -> sqlite3.Row | None:
    return conexion.execute(
        """
        SELECT *
        FROM importaciones_ficheros
        WHERE hash_sha256 = ?
          AND tipo_fuente = ?
        LIMIT 1
        """,
        (hash_sha256, tipo_fuente),
    ).fetchone()


def buscar_importacion_fichero(
    conexion: sqlite3.Connection,
    *,
    hash_sha256: str,
    tipo_fuente: str,
    ruta_pdf: Path,
    raiz: Path,
) -> sqlite3.Row | None:
    por_hash = buscar_importacion(conexion, hash_sha256, tipo_fuente)
    if por_hash is not None:
        return por_hash
    return conexion.execute(
        """
        SELECT *
        FROM importaciones_ficheros
        WHERE ruta_relativa = ?
          AND tipo_fuente = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (ruta_relativa_proyecto(ruta_pdf, raiz), tipo_fuente),
    ).fetchone()


def debe_omitirse(
    importacion: sqlite3.Row | None,
    forzar: bool,
    hash_sha256: str,
) -> bool:
    if importacion is None or forzar:
        return False
    estado = str(importacion["estado"] or "").strip().upper()
    reimportar = int(importacion["reimportar"] or 0) == 1
    mismo_contenido = str(importacion["hash_sha256"]) == hash_sha256
    return mismo_contenido and estado == "COMPLETADO" and not reimportar


def buscar_pregunta_duplicada(
    conexion: sqlite3.Connection,
    registro: dict[str, Any],
) -> sqlite3.Row | None:
    return conexion.execute(
        """
        SELECT id, importacion_fichero_id, pagina_origen, respuesta_correcta
        FROM lote_preguntas
        WHERE enunciado = :enunciado
          AND opcion_a = :opcion_a
          AND opcion_b = :opcion_b
          AND opcion_c = :opcion_c
          AND opcion_d = :opcion_d
        ORDER BY id
        LIMIT 1
        """,
        registro,
    ).fetchone()


def _insertar_pregunta(
    conexion: sqlite3.Connection,
    registro: dict[str, Any],
) -> int:
    columnas = ", ".join(CAMPOS_INSERCION)
    parametros = ", ".join(f":{campo}" for campo in CAMPOS_INSERCION)
    cursor = conexion.execute(
        f"INSERT INTO lote_preguntas ({columnas}) VALUES ({parametros})",
        registro,
    )
    return int(cursor.lastrowid)


def publicar_importacion(
    conexion: sqlite3.Connection,
    *,
    raiz: Path,
    ruta_pdf: Path,
    hash_sha256: str,
    tipo_importacion: str,
    paginas_totales: int,
    registros: Iterable[dict[str, Any]],
    omitidas_previas: int = 0,
) -> tuple[int, int, int]:
    """Publica un PDF completo en una única transacción.

    Si la publicación falla, la transacción se revierte y las preguntas de una
    importación anterior permanecen intactas.
    """
    preparados = [dict(registro) for registro in registros]
    importacion = buscar_importacion_fichero(
        conexion,
        hash_sha256=hash_sha256,
        tipo_fuente=tipo_importacion,
        ruta_pdf=ruta_pdf,
        raiz=raiz,
    )

    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.execute("BEGIN IMMEDIATE")
    try:
        if importacion is None:
            cursor = conexion.execute(
                """
                INSERT INTO importaciones_ficheros (
                    ruta_relativa, nombre_fichero, hash_sha256, tipo_fuente,
                    estado, paginas_totales, paginas_insertadas,
                    paginas_omitidas, paginas_error, fecha_inicio, fecha_fin,
                    reimportar, ultimo_error
                )
                VALUES (?, ?, ?, ?, 'EN_PROCESO', ?, 0, 0, 0, ?, NULL, 0, NULL)
                """,
                (
                    ruta_relativa_proyecto(ruta_pdf, raiz),
                    ruta_pdf.name,
                    hash_sha256,
                    tipo_importacion,
                    paginas_totales,
                    ahora_iso(),
                ),
            )
            importacion_id = int(cursor.lastrowid)
        else:
            importacion_id = int(importacion["id"])

            vinculadas_banco = conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas bp
                JOIN lote_preguntas lp ON lp.id = bp.pregunta_id
                WHERE lp.importacion_fichero_id = ?
                """,
                (importacion_id,),
            ).fetchone()[0]

            if int(vinculadas_banco) > 0:
                raise RuntimeError(
                    "Reimportación detenida: la importación anterior tiene "
                    f"{int(vinculadas_banco)} pregunta(s) ya utilizadas en bancos. "
                    "No se elimina ninguna pregunta. Revise primero esas vinculaciones "
                    "mediante el mantenimiento de bancos."
                )

            conexion.execute(
                "DELETE FROM lote_preguntas WHERE importacion_fichero_id = ?",
                (importacion_id,),
            )
            conexion.execute(
                """
                UPDATE importaciones_ficheros
                SET ruta_relativa = ?, nombre_fichero = ?, hash_sha256 = ?,
                    estado = 'EN_PROCESO', paginas_totales = ?,
                    paginas_insertadas = 0, paginas_omitidas = 0,
                    paginas_error = 0, fecha_inicio = ?, fecha_fin = NULL,
                    reimportar = 0, ultimo_error = NULL
                WHERE id = ?
                """,
                (
                    ruta_relativa_proyecto(ruta_pdf, raiz),
                    ruta_pdf.name,
                    hash_sha256,
                    paginas_totales,
                    ahora_iso(),
                    importacion_id,
                ),
            )

        insertadas = 0
        duplicadas = 0
        for registro in preparados:
            registro["importacion_fichero_id"] = importacion_id
            registro.setdefault("pagina_origen", None)
            registro.setdefault("teorica_practica", None)
            if buscar_pregunta_duplicada(conexion, registro) is not None:
                duplicadas += 1
                continue
            _insertar_pregunta(conexion, registro)
            insertadas += 1

        omitidas = int(omitidas_previas) + duplicadas
        conexion.execute(
            """
            UPDATE importaciones_ficheros
            SET estado = 'COMPLETADO', paginas_insertadas = ?,
                paginas_omitidas = ?, paginas_error = 0, fecha_fin = ?,
                reimportar = 0, ultimo_error = NULL
            WHERE id = ?
            """,
            (insertadas, omitidas, ahora_iso(), importacion_id),
        )
        conexion.commit()
        return importacion_id, insertadas, duplicadas
    except Exception:
        conexion.rollback()
        raise


def registrar_importacion_fallida(
    conexion: sqlite3.Connection,
    *,
    raiz: Path,
    ruta_pdf: Path,
    hash_sha256: str,
    tipo_importacion: str,
    paginas_totales: int | None,
    errores: int,
    mensaje: str,
) -> int:
    """Registra el fallo sin eliminar preguntas importadas anteriormente."""
    importacion = buscar_importacion_fichero(
        conexion,
        hash_sha256=hash_sha256,
        tipo_fuente=tipo_importacion,
        ruta_pdf=ruta_pdf,
        raiz=raiz,
    )
    conexion.execute("BEGIN IMMEDIATE")
    try:
        if importacion is None:
            cursor = conexion.execute(
                """
                INSERT INTO importaciones_ficheros (
                    ruta_relativa, nombre_fichero, hash_sha256, tipo_fuente,
                    estado, paginas_totales, paginas_insertadas,
                    paginas_omitidas, paginas_error, fecha_inicio, fecha_fin,
                    reimportar, ultimo_error
                )
                VALUES (?, ?, ?, ?, 'ERROR', ?, 0, 0, ?, ?, ?, 1, ?)
                """,
                (
                    ruta_relativa_proyecto(ruta_pdf, raiz),
                    ruta_pdf.name,
                    hash_sha256,
                    tipo_importacion,
                    paginas_totales,
                    max(1, int(errores)),
                    ahora_iso(),
                    ahora_iso(),
                    mensaje[:4000],
                ),
            )
            importacion_id = int(cursor.lastrowid)
        else:
            importacion_id = int(importacion["id"])
            conexion.execute(
                """
                UPDATE importaciones_ficheros
                SET ruta_relativa = ?, nombre_fichero = ?, hash_sha256 = ?,
                    estado = 'ERROR',
                    paginas_totales = ?, paginas_error = ?, fecha_fin = ?,
                    reimportar = 1, ultimo_error = ?
                WHERE id = ?
                """,
                (
                    ruta_relativa_proyecto(ruta_pdf, raiz),
                    ruta_pdf.name,
                    hash_sha256,
                    paginas_totales,
                    max(1, int(errores)),
                    ahora_iso(),
                    mensaje[:4000],
                    importacion_id,
                ),
            )
        conexion.commit()
        return importacion_id
    except Exception:
        conexion.rollback()
        raise
