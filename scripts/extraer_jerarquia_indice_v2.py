"""
Constructor Temario V2 - extracción estructural de índices oficiales.

SOLO LECTURA.

Lee los HTML ya cacheados en cache_localizador_normativa/<ID_FUENTE>/indice.html
y reconstruye, en orden documental, la jerarquía normativa asociada a cada artículo:
Título -> Capítulo -> Sección -> Artículo.

No consulta ni modifica la base de datos. No consulta temario_referencias.
No persiste resultados salvo que se indique --salida-json.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import comun

RAIZ = comun.raiz_proyecto()
CACHE = RAIZ / "cache_localizador_normativa"

RE_TAG = re.compile(r"<[^>]+>")
RE_WS = re.compile(r"\s+")
RE_ART = re.compile(r"^Artículo\s+(.+?)(?:\.|$)", re.I)
RE_TITULO = re.compile(r"^TÍTULO(?:\s+|$)", re.I)
RE_CAP = re.compile(r"^CAPÍTULO(?:\s+|$)", re.I)
RE_SEC = re.compile(r"^Secci[oó]n(?:\s+|$)", re.I)


def limpiar(fragmento: str) -> str:
    texto = RE_TAG.sub(" ", fragmento)
    texto = html.unescape(texto).replace("\xa0", " ")
    return RE_WS.sub(" ", texto).strip()


def extraer_enlaces_indice(texto_html: str) -> list[str]:
    # El índice oficial está formado por enlaces <a ...>texto</a>. Conservamos
    # estrictamente su orden de aparición en el documento.
    return [
        limpiar(m.group(1))
        for m in re.finditer(r"<a\b[^>]*>(.*?)</a>", texto_html, flags=re.I | re.S)
        if limpiar(m.group(1))
    ]


def reconstruir(id_fuente: str, ruta: Path) -> dict:
    bruto = ruta.read_text(encoding="utf-8", errors="replace")
    enlaces = extraer_enlaces_indice(bruto)

    titulo = None
    capitulo = None
    seccion = None
    articulos: list[dict] = []
    titulos: list[str] = []
    capitulos: list[str] = []
    secciones: list[str] = []

    def agregar_unico(lista: list[str], valor: str) -> None:
        if valor not in lista:
            lista.append(valor)

    for txt in enlaces:
        if RE_TITULO.match(txt):
            titulo = txt
            capitulo = None
            seccion = None
            agregar_unico(titulos, txt)
            continue
        if RE_CAP.match(txt):
            capitulo = txt
            seccion = None
            agregar_unico(capitulos, txt)
            continue
        if RE_SEC.match(txt):
            seccion = txt
            agregar_unico(secciones, txt)
            continue

        m = RE_ART.match(txt)
        if not m:
            continue
        etiqueta = m.group(1).strip()
        articulos.append({
            "orden_documental": len(articulos) + 1,
            "articulo": etiqueta,
            "epigrafe": txt,
            "titulo": titulo,
            "capitulo": capitulo,
            "seccion": seccion,
        })

    return {
        "id_fuente": id_fuente,
        "ruta": str(ruta),
        "titulos": titulos,
        "capitulos": capitulos,
        "secciones": secciones,
        "articulos": articulos,
        "resumen": {
            "titulos": len(titulos),
            "capitulos": len(capitulos),
            "secciones": len(secciones),
            "articulos": len(articulos),
            "articulos_con_titulo": sum(1 for a in articulos if a["titulo"]),
            "articulos_con_capitulo": sum(1 for a in articulos if a["capitulo"]),
            "articulos_con_seccion": sum(1 for a in articulos if a["seccion"]),
        },
    }


def imprimir(payloads: list[dict], muestras: int) -> None:
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - JERARQUIA DE INDICES OFICIALES - SOLO LECTURA")
    print("=" * 78)
    for p in payloads:
        r = p["resumen"]
        print(f"\nFUENTE {p['id_fuente']}")
        print(f"  Títulos........................ {r['titulos']}")
        print(f"  Capítulos...................... {r['capitulos']}")
        print(f"  Secciones...................... {r['secciones']}")
        print(f"  Artículos...................... {r['articulos']}")
        print(f"  Artículos con título........... {r['articulos_con_titulo']}")
        print(f"  Artículos con capítulo......... {r['articulos_con_capitulo']}")
        print(f"  Artículos con sección.......... {r['articulos_con_seccion']}")
        if p["articulos"]:
            print("  Muestra:")
            paso = max(1, len(p["articulos"]) // max(1, muestras))
            for a in p["articulos"][::paso][:muestras]:
                print(f"    Art. {a['articulo']}: {a['epigrafe']}")
                print(f"      T: {a['titulo'] or '-'}")
                print(f"      C: {a['capitulo'] or '-'}")
                print(f"      S: {a['seccion'] or '-'}")

    print("\nNo se ha consultado temario_referencias.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconstruye jerarquía legal desde índices HTML cacheados.")
    ap.add_argument("--id-fuente", action="append", required=True)
    ap.add_argument("--muestras", type=int, default=8)
    ap.add_argument("--salida-json")
    args = ap.parse_args()

    payloads = []
    for fid in dict.fromkeys(args.id_fuente):
        ruta = CACHE / fid / "indice.html"
        if not ruta.exists():
            print(f"AVISO: no existe {ruta}")
            continue
        payloads.append(reconstruir(fid, ruta))

    if not payloads:
        raise SystemExit("ERROR: no se encontró ninguna fuente solicitada.")

    imprimir(payloads, max(1, args.muestras))

    if args.salida_json:
        salida = Path(args.salida_json).expanduser().resolve()
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Salida JSON...................... {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
