"""Constructor Temario V2: auditoría de índices oficiales. SOLO LECTURA.

Comprueba si los HTML oficiales cacheados conservan jerarquía Título/Capítulo/
Sección y su asociación con artículos. No consulta temario_referencias y no modifica
BD, corpus, CSV ni bancos.
"""
from __future__ import annotations
import argparse, html, re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CACHE = RAIZ / "cache_localizador_normativa"

RE_TAG = re.compile(r"<[^>]+>")
RE_WS = re.compile(r"\s+")
RE_NIVEL = re.compile(r"^(T[IÍ]TULO|CAP[IÍ]TULO|SECCI[ÓO]N)\b", re.I)
RE_ART = re.compile(r"^Art[ií]culo\s+(.+?)(?:\.|$)", re.I)


def texto_html(s: str) -> str:
    return RE_WS.sub(" ", html.unescape(RE_TAG.sub(" ", s))).strip()


def extraer_indice(path: Path):
    bruto = path.read_text(encoding="utf-8", errors="replace")
    # El índice BOE/DOGV consolidado usa principalmente <p> para niveles y <li> para artículos.
    piezas = re.findall(r"<(p|li)\b[^>]*>(.*?)</\1>", bruto, flags=re.I | re.S)
    jerarquia = {"TÍTULO": None, "CAPÍTULO": None, "SECCIÓN": None}
    articulos = []
    niveles = []
    for _, cuerpo in piezas:
        t = texto_html(cuerpo)
        if not t:
            continue
        m = RE_NIVEL.match(t)
        if m:
            tipo = m.group(1).upper().replace("TITULO", "TÍTULO").replace("CAPITULO", "CAPÍTULO").replace("SECCION", "SECCIÓN")
            jerarquia[tipo] = t
            if tipo == "TÍTULO":
                jerarquia["CAPÍTULO"] = jerarquia["SECCIÓN"] = None
            elif tipo == "CAPÍTULO":
                jerarquia["SECCIÓN"] = None
            niveles.append((tipo, t))
            continue
        a = RE_ART.match(t)
        if a:
            articulos.append({"articulo": a.group(1).strip(), **jerarquia})
    return niveles, articulos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id-fuente", action="append", required=True,
                    help="Ej. BOE-A-2015-10566; puede repetirse")
    args = ap.parse_args()
    print("="*78)
    print("CONSTRUCTOR TEMARIO V2 - ESTRUCTURA DE ÍNDICES OFICIALES - SOLO LECTURA")
    print("="*78)
    for fuente in args.id_fuente:
        path = CACHE / fuente / "indice.html"
        print(f"\nFUENTE {fuente}")
        print(f"  archivo: {path}")
        if not path.exists():
            print("  NO DISPONIBLE EN CACHE")
            continue
        niveles, arts = extraer_indice(path)
        con_titulo = sum(bool(a["TÍTULO"]) for a in arts)
        con_cap = sum(bool(a["CAPÍTULO"]) for a in arts)
        con_sec = sum(bool(a["SECCIÓN"]) for a in arts)
        print(f"  niveles estructurales: {len(niveles)}")
        print(f"  artículos detectados: {len(arts)}")
        print(f"  artículos con título: {con_titulo}/{len(arts)}")
        print(f"  artículos con capítulo: {con_cap}/{len(arts)}")
        print(f"  artículos con sección: {con_sec}/{len(arts)}")
        print("  muestra:")
        for a in arts[:12]:
            ruta = " > ".join(x for x in (a['TÍTULO'], a['CAPÍTULO'], a['SECCIÓN']) if x)
            print(f"    Artículo {a['articulo']} | {ruta or '-'}")
    print("\nNo se ha consultado temario_referencias.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
