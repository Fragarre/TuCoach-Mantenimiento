"""Preparación de publicación Web compatible con Constructor Temario V2.

Capa aditiva: no modifica el publicador consolidado. Detecta si la SQLite contiene
el esquema V2 completo y ejecuta la validación correspondiente antes de delegar
la creación del snapshot en publicar_contenidos_web.py.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

import publicar_contenidos_web as legacy
import publicacion_v2_contrato as contrato

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SALIDA_DEFECTO = RAIZ / "publicaciones_web"
VALIDADOR_LEGACY = RAIZ / "scripts" / "validacion_completa.py"
VALIDADOR_V2 = RAIZ / "scripts" / "validacion_completa_v2.py"


def detectar_v2(db: Path) -> bool:
    uri = db.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        _, es_v2 = contrato.resolver_tablas_publicables(con)
        return es_v2
    finally:
        con.close()


def ejecutar_validador(db: Path, es_v2: bool) -> None:
    script = VALIDADOR_V2 if es_v2 else VALIDADOR_LEGACY
    cmd = [sys.executable, str(script)]
    if es_v2:
        cmd += ["--db", str(db.resolve())]
    r = subprocess.run(cmd, cwd=RAIZ, check=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"{script.name} ha terminado con error. Publicación cancelada."
        )


def preparar(db: Path, salida: Path) -> tuple[Path, Path, Path, bool]:
    db = db.resolve()
    if not db.is_file():
        raise RuntimeError(f"No existe la BD: {db}")
    es_v2 = detectar_v2(db)
    ejecutar_validador(db, es_v2)
    snapshot, informe_json, informe_txt = legacy.preparar_publicacion(
        db,
        salida,
        ejecutar_validacion_externa=False,
    )
    return snapshot, informe_json, informe_txt, es_v2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    ap.add_argument("--salida", type=Path, default=SALIDA_DEFECTO)
    args = ap.parse_args()
    try:
        snapshot, informe_json, informe_txt, es_v2 = preparar(args.db, args.salida)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    print("=" * 78)
    print("PUBLICACION WEB V2 PREPARADA Y VALIDADA")
    print("=" * 78)
    print(f"Contrato......................... {'V2 · 22 TABLAS' if es_v2 else 'LEGACY · 16 TABLAS'}")
    print(f"Snapshot......................... {snapshot}")
    print(f"Informe JSON..................... {informe_json}")
    print(f"Informe TXT...................... {informe_txt}")
    print("Supabase......................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
