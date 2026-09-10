"""Actualización SQLite -> Supabase compatible con Constructor Temario V2.

Capa aditiva sobre actualizar_contenidos_supabase.py. No cambia el cargador
consolidado: resuelve 16 tablas para snapshots legacy o 22 para V2 completo.
Un esquema V2 parcial queda bloqueado antes de conectar a PostgreSQL.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import actualizar_contenidos_supabase as legacy
import publicacion_v2_contrato as contrato

RAIZ = Path(__file__).resolve().parent.parent


def resolver_tablas(snapshot: Path) -> tuple[list[str], bool]:
    uri = snapshot.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        return contrato.resolver_tablas_publicables(con)
    finally:
        con.close()


def actualizar(
    snapshot: Path,
    informe_salida: Path,
    ruta_env: Path,
    informe_publicacion: Path | None = None,
    confirmar: bool = True,
) -> bool:
    snapshot = snapshot.resolve()
    if not snapshot.is_file():
        raise RuntimeError(f"No existe el snapshot: {snapshot}")
    tablas, es_v2 = resolver_tablas(snapshot)

    # Las funciones del cargador consolidado consultan TABLAS_ORDEN en tiempo
    # de ejecución. Se modifica únicamente el global del módulo importado en
    # este proceso; no se altera ningún archivo consolidado.
    original = legacy.TABLAS_ORDEN
    legacy.TABLAS_ORDEN = list(tablas)
    try:
        legacy.actualizar(
            snapshot,
            informe_salida,
            ruta_env,
            informe_publicacion=informe_publicacion,
            confirmar=confirmar,
        )
    finally:
        legacy.TABLAS_ORDEN = original
    return es_v2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--env", type=Path, required=True)
    ap.add_argument(
        "--informe",
        type=Path,
        default=RAIZ / "publicaciones_web" / "actualizacion_supabase_v2.json",
    )
    ap.add_argument("--informe-publicacion", type=Path)
    ap.add_argument("--si", action="store_true", help="No pedir confirmación interactiva.")
    args = ap.parse_args()
    try:
        es_v2 = actualizar(
            args.snapshot,
            args.informe,
            args.env,
            informe_publicacion=args.informe_publicacion,
            confirmar=not args.si,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("La transacción no se ha confirmado.")
        return 1
    print(f"Contrato utilizado................ {'V2 · 22 TABLAS' if es_v2 else 'LEGACY · 16 TABLAS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
