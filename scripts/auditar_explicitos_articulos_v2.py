"""
Constructor Temario V2 - auditoría de artículos literales en temas JURIDICO_EXPLICITO.

SOLO LECTURA.

Objetivo:
- extraer los temas del PDF oficial;
- seleccionar únicamente los clasificados como JURIDICO_EXPLICITO;
- comprobar si el texto oficial contiene referencias literales a artículo(s),
  sin inferir ningún alcance;
- distinguir norma explícita de alcance de artículos explícito.

No consulta temario_referencias, no usa temario.csv y no modifica datos.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import clasificar_alcance_juridico_v2 as alcance
import extraer_estructura_temario_v2 as estructura
import resolver_normas_temario_v2 as resolver


PATRONES_ARTICULO = (
    re.compile(
        r"\bart[ií]culos?\s+\d+[a-z]*(?:\s*(?:a|al|hasta|[-–—])\s*\d+[a-z]*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\barts?\.?\s+\d+[a-z]*(?:\s*(?:a|al|hasta|[-–—])\s*\d+[a-z]*)?",
        re.IGNORECASE,
    ),
)


def detectar_articulos_literales(texto: str) -> list[str]:
    encontrados: list[tuple[int, str]] = []
    for patron in PATRONES_ARTICULO:
        for m in patron.finditer(texto or ""):
            encontrados.append((m.start(), m.group(0)))

    resultado: list[str] = []
    vistos: set[str] = set()
    for _, literal in sorted(encontrados, key=lambda x: x[0]):
        clave = literal.casefold()
        if clave not in vistos:
            vistos.add(clave)
            resultado.append(literal)
    return resultado


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audita artículos/rangos literales en temas JURIDICO_EXPLICITO."
    )
    ap.add_argument("--pdf", required=True)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF {pdf}")

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    explicitos = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        if clase == "JURIDICO_EXPLICITO":
            explicitos.append((tema, evidencia))

    con_articulos = 0
    sin_articulos = 0

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA EXPLICITOS / ARTICULOS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"Temas JURIDICO_EXPLICITO......... {len(explicitos)}")
    print()

    for tema, _ in explicitos:
        citas_norma = resolver.detectar_citas(tema.texto)
        refs_articulo = detectar_articulos_literales(tema.texto)
        if refs_articulo:
            con_articulos += 1
            estado = "ARTICULOS_LITERALES"
        else:
            sin_articulos += 1
            estado = "SOLO_NORMA_EXPLICITA"

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | {estado}")
        print(f"  texto: {tema.texto}")
        print("  normas literales: " + (
            "; ".join(c.texto for c in citas_norma) if citas_norma else "NINGUNA"
        ))
        print("  artículos literales: " + (
            "; ".join(refs_articulo) if refs_articulo else "NINGUNO"
        ))
        print()

    print("Resumen:")
    print(f"  Explícitos totales.............. {len(explicitos)}")
    print(f"  Con artículos/rangos literales. {con_articulos}")
    print(f"  Sólo norma explícita............ {sin_articulos}")
    print()
    print("No se ha consultado temario_referencias.")
    print("No se ha consultado temario.csv.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
