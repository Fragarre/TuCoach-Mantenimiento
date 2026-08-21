"""
OpoCoach-Mantenimiento
Prepara las fuentes oficiales DOUE para TUE y TFUE.

NO abre ni modifica SQLite.

Uso:
    python scripts/preparar_fuentes_doue.py
"""

from __future__ import annotations

from pathlib import Path
import requests

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "fuentes_normativas"

FUENTES = (
    (
        "TUE",
        "https://www.boe.es/doue/2010/083/Z00013-00046.pdf",
        "TUE_2010.pdf",
    ),
    (
        "TFUE",
        "https://www.boe.es/doue/2010/083/Z00047-00199.pdf",
        "TFUE_2010.pdf",
    ),
)


def descargar(url: str) -> bytes:
    r = requests.get(
        url,
        timeout=120,
        headers={"User-Agent": "OpoCoach-Mantenimiento/1.0"},
    )
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"La URL no devolvió PDF: {url}")
    return r.content


def main() -> int:
    DESTINO.mkdir(parents=True, exist_ok=True)
    print("=" * 78)
    print("PREPARAR FUENTES DOUE - SIN MODIFICAR SQLITE")
    print("=" * 78)
    print(f"Destino: {DESTINO}")

    errores = 0
    for nombre, url, archivo in FUENTES:
        ruta = DESTINO / archivo
        print("-" * 78)
        print(f"{nombre} | {archivo}")
        if ruta.exists() and ruta.stat().st_size > 0:
            print(f"Estado: YA EXISTE ({ruta.stat().st_size:,} bytes)")
            continue
        try:
            contenido = descargar(url)
            tmp = ruta.with_suffix(ruta.suffix + ".tmp")
            tmp.write_bytes(contenido)
            tmp.replace(ruta)
            print(f"Descargado: {len(contenido):,} bytes")
            print("Estado: OK")
        except Exception as exc:
            errores += 1
            print(f"ERROR: {exc.__class__.__name__}: {exc}")

    print("-" * 78)
    print("SQLite no ha sido abierto ni modificado.")
    print("RESULTADO:", "OK" if errores == 0 else f"{errores} ERROR(ES)")
    return 0 if errores == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
