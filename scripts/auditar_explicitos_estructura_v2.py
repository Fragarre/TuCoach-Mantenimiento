"""
Constructor Temario V2 - auditoría de delimitación estructural literal en temas JURIDICO_EXPLICITO.

SOLO LECTURA.

Objetivo:
- comprobar, dentro de los temas clasificados como JURIDICO_EXPLICITO, cuáles
  contienen delimitadores estructurales literales del tipo Título/Capítulo/
  Sección/Preámbulo/Disposiciones comunes;
- separar esos casos de los que sólo citan norma y describen materias;
- NO resolver artículos;
- NO consultar temario.csv ni temario_referencias;
- NO modificar BD, corpus ni bancos.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance
import resolver_normas_temario_v2 as resolver


PATRONES_ESTRUCTURA = (
    ("PREAMBULO", re.compile(r"\bPre[aá]mbulo\b", re.I)),
    ("TITULO", re.compile(r"\bT[ií]tulo\s+(?:Preliminar|[IVXLCDM]+|\d+)\b", re.I)),
    ("CAPITULO", re.compile(r"\bCap[ií]tulo\s+(?:[IVXLCDM]+|\d+)\b", re.I)),
    ("SECCION", re.compile(r"\bSecci[oó]n\s+(?:\d+\.?ª?|[IVXLCDM]+)\b", re.I)),
    ("DISPOSICIONES_COMUNES", re.compile(r"\bDisposiciones\s+comunes\b", re.I)),
)


def detectar_estructura(texto: str) -> list[dict]:
    hallazgos: list[dict] = []
    for tipo, patron in PATRONES_ESTRUCTURA:
        for m in patron.finditer(texto):
            hallazgos.append({
                "tipo": tipo,
                "texto": m.group(0),
                "inicio": m.start(),
            })
    hallazgos.sort(key=lambda x: x["inicio"])
    return hallazgos


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audita delimitadores estructurales literales en temas JURIDICO_EXPLICITO, en solo lectura."
    )
    ap.add_argument("--pdf", required=True, help="PDF oficial del temario")
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF {pdf}")

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    explicitos = []
    for tema in temas:
        clase, _ = alcance.clasificar(tema)
        if clase == "JURIDICO_EXPLICITO":
            explicitos.append(tema)

    con_estructura = 0
    solo_norma_materia = 0

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA EXPLICITOS / ESTRUCTURA - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"Temas JURIDICO_EXPLICITO......... {len(explicitos)}")
    print()

    for tema in explicitos:
        citas = resolver.detectar_citas(tema.texto)
        estructura_literal = detectar_estructura(tema.texto)

        if estructura_literal:
            clase_salida = "NORMA_Y_ESTRUCTURA_LITERAL"
            con_estructura += 1
        else:
            clase_salida = "NORMA_EXPLICITA_SIN_ESTRUCTURA_LITERAL"
            solo_norma_materia += 1

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | {clase_salida}")
        if citas:
            print("  normas literales: " + "; ".join(c.texto for c in citas))
        else:
            print("  normas literales: NINGUNA DETECTADA")
        if estructura_literal:
            print("  estructura literal: " + "; ".join(
                f"{x['tipo']}={x['texto']}" for x in estructura_literal
            ))
        else:
            print("  estructura literal: NINGUNA")
        print(f"  texto: {tema.texto}")
        print()

    print("Resumen:")
    print(f"  Explícitos totales...................... {len(explicitos)}")
    print(f"  Norma + estructura literal.............. {con_estructura}")
    print(f"  Norma explícita sin estructura literal.. {solo_norma_materia}")
    print()
    print("No se ha consultado temario_referencias.")
    print("No se ha consultado temario.csv.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
