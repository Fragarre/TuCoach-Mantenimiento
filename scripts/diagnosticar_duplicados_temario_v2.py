"""Diagnóstico SOLO LECTURA de temas duplicados en un PDF para Constructor Temario V2."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import extraer_estructura_temario_v2 as estructura


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    texto = estructura.extraer_texto_pdf(pdf)
    lineas = [limpiar(x) for x in texto.splitlines()]

    parte_actual = ""
    ocurrencias: dict[tuple[str, int], list[int]] = {}

    for i, linea in enumerate(lineas):
        if not linea:
            continue
        m_parte = estructura.PATRON_PARTE.match(linea)
        if m_parte:
            parte_actual = m_parte.group(1).upper()
            continue
        m_tema = estructura.PATRON_TEMA.match(linea)
        if m_tema and parte_actual:
            clave = (parte_actual, int(m_tema.group(1)))
            ocurrencias.setdefault(clave, []).append(i)

    duplicados = {k: v for k, v in ocurrencias.items() if len(v) > 1}

    print("=" * 78)
    print("DIAGNOSTICO DUPLICADOS TEMARIO V2 - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"Claves duplicadas................ {len(duplicados)}")

    for (parte, numero), posiciones in sorted(duplicados.items()):
        print()
        print(f"{parte}-{numero}: {len(posiciones)} apariciones")
        for n, pos in enumerate(posiciones, 1):
            print(f"  Aparición {n} | línea extraída {pos + 1}")
            inicio = max(0, pos - 3)
            fin = min(len(lineas), pos + 4)
            for j in range(inicio, fin):
                marca = ">>" if j == pos else "  "
                if lineas[j]:
                    print(f"    {marca} {j + 1:>5}: {lineas[j]}")

    print()
    print("BD............................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
