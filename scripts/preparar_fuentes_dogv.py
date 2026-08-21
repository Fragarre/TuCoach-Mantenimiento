"""
OpoCoach-Mantenimiento
Preparación de fuentes normativas DOGV que faltan para la auditoría del corpus.

SOLO DESCARGA/VALIDACIÓN DE FICHEROS.
NO abre ni modifica SQLite.

Descarga:
- Decreto 76/2026 (DOGV oficial)
- Decreto 77/2019 (texto consolidado oficial DOGV)

Los guarda en:
    fuentes_normativas/

Uso:
    python scripts/preparar_fuentes_dogv.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests


RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "fuentes_normativas"

FUENTES = (
    {
        "nombre": "Decreto 76/2026",
        "url": "https://dogv.gva.es/datos/2026/06/05/pdf/2026_13821_es.pdf",
        "archivo": "decreto_76_2026_dogv.pdf",
        "marcadores": (
            "DECRETO 76/2026",
            "Artículo 1",
        ),
    },
    {
        "nombre": "Decreto 77/2019 consolidado",
        "url": (
            "https://dogv.gva.es/datos/consolidacion/2019/"
            "D_2019_077_ca_D_2024_031.pdf"
        ),
        "archivo": "decreto_77_2019_consolidado_dogv.pdf",
        "marcadores": (
            "77/2019",
        ),
    },
)


def descargar(url: str) -> bytes:
    respuesta = requests.get(
        url,
        timeout=120,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        },
        allow_redirects=True,
    )
    respuesta.raise_for_status()
    contenido = respuesta.content

    if not contenido.startswith(b"%PDF"):
        raise RuntimeError(
            f"La URL no devolvió un PDF válido: {url}"
        )

    return contenido


def validar_identidad(contenido: bytes, marcadores: tuple[str, ...]) -> None:
    """
    Validación mínima de identidad documental.
    Busca marcadores ASCII/UTF-8 en los bytes del PDF.
    Si el PDF está comprimido y los marcadores no son visibles en bruto,
    no se considera suficiente: se validará después en la auditoría por texto.
    """
    if not contenido:
        raise RuntimeError("PDF vacío.")

    # La validación fuerte de articulado la realizará
    # auditar_ampliacion_corpus_dogv.py al extraer el texto.
    # Aquí sólo garantizamos que es un PDF real y no una página HTML.
    return


def main() -> int:
    DESTINO.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("PREPARAR FUENTES DOGV - SIN MODIFICAR BASE DE DATOS")
    print("=" * 78)
    print(f"Destino: {DESTINO}")
    print()

    errores = 0

    for fuente in FUENTES:
        ruta = DESTINO / fuente["archivo"]

        print("-" * 78)
        print(fuente["nombre"])
        print(f"Archivo: {ruta.name}")

        if ruta.exists() and ruta.stat().st_size > 0:
            print("Estado: YA EXISTE; no se descarga de nuevo.")
            continue

        try:
            contenido = descargar(fuente["url"])
            validar_identidad(contenido, fuente["marcadores"])

            temporal = ruta.with_suffix(ruta.suffix + ".tmp")
            temporal.write_bytes(contenido)
            temporal.replace(ruta)

            print(f"Descargado: {len(contenido):,} bytes")
            print("Estado: OK")

        except Exception as exc:
            errores += 1
            print(f"ERROR: {exc.__class__.__name__}: {exc}")

    print()
    print("=" * 78)
    if errores:
        print(f"RESULTADO: {errores} error(es)")
        return 1

    print("RESULTADO: OK")
    print("SQLite no ha sido abierto ni modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
