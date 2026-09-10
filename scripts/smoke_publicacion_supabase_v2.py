"""Smoke SOLO LECTURA del soporte de publicación Supabase V2.

No importa el cargador PostgreSQL consolidado: este smoke valida el contrato y
el código fuente del envoltorio sin exigir psycopg en el entorno local.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import tempfile
from pathlib import Path

import publicacion_v2_contrato as contrato

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
SQL = RAIZ / "scripts" / "sql" / "contenidos_constructor_v2.sql"
LEGACY = RAIZ / "scripts" / "actualizar_contenidos_supabase.py"
CARGADOR_V2 = RAIZ / "scripts" / "actualizar_contenidos_supabase_v2.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def crear_sqlite_tablas(path: Path, tablas: tuple[str, ...]) -> None:
    con = sqlite3.connect(path)
    try:
        for tabla in tablas:
            con.execute(f'CREATE TABLE "{tabla}" (id INTEGER PRIMARY KEY)')
        con.commit()
    finally:
        con.close()


def resolver_tablas(path: Path) -> tuple[list[str], bool]:
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        return contrato.resolver_tablas_publicables(con)
    finally:
        con.close()


def validar_ddl() -> None:
    texto = SQL.read_text(encoding="utf-8")
    normal = re.sub(r"\s+", " ", texto.lower())
    for tabla in contrato.TABLAS_V2:
        if f"create table if not exists contenidos.{tabla}" not in normal:
            raise RuntimeError(f"DDL sin CREATE TABLE para {tabla}")
    if len(re.findall(r"create table if not exists contenidos\.", normal)) != 6:
        raise RuntimeError("El DDL no contiene exactamente 6 CREATE TABLE V2.")
    prohibidos = (" drop table ", " truncate ", " insert into ", " update ", " delete from ")
    acolchado = " " + normal + " "
    if any(p in acolchado for p in prohibidos):
        raise RuntimeError("El DDL contiene una operación de datos/destructiva prohibida.")
    if normal.count(" id bigint primary key") != 6:
        raise RuntimeError("Los 6 IDs V2 deben ser bigint PRIMARY KEY sin identity.")
    if "generated" in normal or "serial" in normal:
        raise RuntimeError("El DDL V2 no debe crear identities/secuencias.")
    if "timestamptz" not in normal:
        raise RuntimeError("El DDL V2 no sigue el patrón timestamptz de contenidos.")


def validar_cargador_v2_sin_importar() -> None:
    fuente = CARGADOR_V2.read_text(encoding="utf-8")
    if "import actualizar_contenidos_supabase as legacy" not in fuente:
        raise RuntimeError("El envoltorio V2 no reutiliza el cargador consolidado.")
    if "legacy.TABLAS_ORDEN = list(tablas)" not in fuente:
        raise RuntimeError("El envoltorio V2 no aplica el contrato resuelto.")
    if "legacy.TABLAS_ORDEN = original" not in fuente:
        raise RuntimeError("El envoltorio V2 no restaura el contrato consolidado.")


def main() -> int:
    for ruta in (DB, SQL, LEGACY, CARGADOR_V2):
        if not ruta.is_file():
            raise RuntimeError(f"Falta archivo requerido: {ruta}")
    antes_db = sha256(DB)
    antes_legacy = sha256(LEGACY)
    validar_ddl()
    validar_cargador_v2_sin_importar()

    with tempfile.TemporaryDirectory(prefix="smoke_publicacion_v2_") as td:
        td = Path(td)
        legado = td / "legado.sqlite3"
        v2 = td / "v2.sqlite3"
        parcial = td / "parcial.sqlite3"
        crear_sqlite_tablas(legado, contrato.TABLAS_BASE)
        crear_sqlite_tablas(v2, tuple(set(contrato.TABLAS_BASE) | set(contrato.TABLAS_V2)))
        crear_sqlite_tablas(parcial, tuple(set(contrato.TABLAS_BASE) | set(contrato.TABLAS_V2[:-1])))

        tablas_legado, es_v2_legado = resolver_tablas(legado)
        tablas_v2, es_v2 = resolver_tablas(v2)
        if es_v2_legado or tuple(tablas_legado) != contrato.TABLAS_BASE:
            raise RuntimeError("El contrato legado fue alterado.")
        if not es_v2 or tuple(tablas_v2) != contrato.TABLAS_ORDEN_V2:
            raise RuntimeError("No se resolvieron las 22 tablas V2.")
        try:
            resolver_tablas(parcial)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("No se bloqueó un esquema V2 parcial.")

    if sha256(LEGACY) != antes_legacy:
        raise RuntimeError("El cargador consolidado cambió durante el smoke.")
    if sha256(DB) != antes_db:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("=" * 78)
    print("SMOKE PUBLICACION SUPABASE V2: OK")
    print("=" * 78)
    print("DDL PostgreSQL V2................ 6 TABLAS · OK")
    print("IDs PostgreSQL................... BIGINT SIN IDENTITY · OK")
    print("Contrato legacy.................. 16 TABLAS · OK")
    print("Contrato V2...................... 22 TABLAS · OK")
    print("Esquema V2 parcial............... BLOQUEADO · OK")
    print("Cargador V2...................... CONTRATO VALIDADO SIN CONEXION")
    print("Dependencia psycopg en smoke..... NO")
    print("Cargador consolidado............. SIN CAMBIOS")
    print("BD real.......................... SIN CAMBIOS")
    print("Supabase......................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
