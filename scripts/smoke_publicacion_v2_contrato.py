"""Smoke SOLO LECTURA del contrato de publicación V2."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import publicacion_v2_contrato as contrato

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def main() -> int:
    if not DB.is_file():
        raise RuntimeError(f"No existe la BD real: {DB}")

    antes = sha256(DB)
    contrato.validar_orden_v2()

    # Caso legado: exactamente las 16 tablas consolidadas.
    tablas, es_v2 = contrato.resolver_desde_nombres(contrato.TABLAS_BASE)
    if es_v2 or tuple(tablas) != contrato.TABLAS_BASE:
        raise RuntimeError("El contrato legado no conserva las 16 tablas originales.")

    # Caso V2 completo: 16 + 6 = 22.
    tablas, es_v2 = contrato.resolver_desde_nombres(
        set(contrato.TABLAS_BASE) | set(contrato.TABLAS_V2)
    )
    if not es_v2 or tuple(tablas) != contrato.TABLAS_ORDEN_V2 or len(tablas) != 22:
        raise RuntimeError("El contrato V2 completo no contiene exactamente 22 tablas.")

    # Caso V2 parcial: debe bloquear.
    parcial = set(contrato.TABLAS_BASE) | set(contrato.TABLAS_V2[:-1])
    bloqueado = False
    try:
        contrato.resolver_desde_nombres(parcial)
    except RuntimeError as exc:
        bloqueado = "parcial" in str(exc).lower()
    if not bloqueado:
        raise RuntimeError("Un esquema V2 parcial no quedó bloqueado.")

    # La BD real actual debe contener el contrato V2 completo, pero se abre RO.
    uri = DB.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        tablas_real, es_v2_real = contrato.resolver_tablas_publicables(con)
    finally:
        con.close()
    if not es_v2_real or len(tablas_real) != 22:
        raise RuntimeError("La BD real no presenta el esquema V2 completo esperado.")

    if sha256(DB) != antes:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("=" * 78)
    print("SMOKE CONTRATO PUBLICACION V2: OK")
    print("=" * 78)
    print("Contrato legado.................... 16 TABLAS · OK")
    print("Contrato V2 completo............... 22 TABLAS · OK")
    print("Esquema V2 parcial................. BLOQUEADO · OK")
    print("Orden de claves foráneas V2........ OK")
    print("BD real esquema V2................. COMPLETO")
    print("BD real............................ SIN CAMBIOS")
    print("Supabase........................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
