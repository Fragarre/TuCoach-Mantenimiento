"""
==============================================================================
Proyecto : OpoCoach-Mantenimiento
Archivo  : scripts/comun.py

Objetivo:
    Utilidades compartidas para evitar que cada script del proyecto
    reimplemente su propia versión (a veces ligeramente distinta) de las
    mismas operaciones básicas: resolver la raíz del proyecto, conectar a
    la base SQLite en modo solo lectura, ejecutar subprocesos decodificando
    su salida de forma robusta, y calcular el hash SHA-256 de un fichero.

Estado:
    Fichero nuevo y puramente aditivo. Ningún script existente lo usa
    todavía; la migración de cada script se hace de uno en uno, validando
    con validacion_completa.py después de cada cambio.

No modifica BD. No tiene efectos secundarios al importarse.
==============================================================================
"""

from __future__ import annotations

import hashlib
import locale
import os
import sqlite3
import subprocess
from pathlib import Path


def raiz_proyecto() -> Path:
    """
    Devuelve la raíz de OpoCoach-Mantenimiento (la carpeta que contiene
    'scripts/', 'db/', etc.), a partir de la ubicación de este fichero.
    """
    return Path(__file__).resolve().parent.parent


def conectar_sqlite_solo_lectura(
    ruta: Path,
    *,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """
    Abre una base SQLite en modo estrictamente solo lectura.

    Combina las dos variantes que ya existían por separado en el proyecto:
    URI con 'mode=ro' (como en sincronizar_bancos.py / validacion_completa.py)
    y 'PRAGMA query_only = ON' + row_factory (como en actualizar_contenidos_
    supabase.py / publicar_contenidos_web.py), para que cualquier intento de
    escritura falle de dos formas independientes.
    """
    uri = ruta.resolve().as_uri() + "?mode=ro"
    conexion = sqlite3.connect(uri, uri=True)
    if row_factory:
        conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA query_only = ON")
    return conexion


def decodificar_salida(datos: bytes) -> str:
    """
    Decodifica la salida de un subproceso, con reintento robusto si no es
    UTF-8. Usa la codificación preferida del sistema en vez de asumir una
    fija (cp1252), para que funcione igual en cualquier configuración.
    """
    if not datos:
        return ""
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        return datos.decode(locale.getpreferredencoding(False), errors="replace")


def ejecutar_subproceso(
    comando: list[str],
    *,
    cwd: Path | None = None,
) -> tuple[int, str]:
    """
    Ejecuta un comando forzando UTF-8 en el hijo, capturando stdout+stderr
    combinados y decodificándolos de forma robusta.

    Devuelve (codigo_salida, salida_decodificada).
    """
    entorno = os.environ.copy()
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["PYTHONUTF8"] = "1"

    resultado = subprocess.run(
        comando,
        cwd=cwd or raiz_proyecto(),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=entorno,
    )
    return resultado.returncode, decodificar_salida(resultado.stdout or b"")


def sha256_archivo(ruta: Path) -> str:
    """Calcula el hash SHA-256 de un fichero, leyéndolo por bloques."""
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()
