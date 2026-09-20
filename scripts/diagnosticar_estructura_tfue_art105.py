import requests
from bs4 import BeautifulSoup

URL = "https://eur-lex.europa.eu/legal-content/ES/TXT/HTML/?uri=CELEX:02016E/TXT-20250315"
ARTICULO = "105"

r = requests.get(URL, timeout=30)
r.raise_for_status()
soup = BeautifulSoup(r.text, "html.parser")

cabecera = None
for p in soup.select("p.title-article-norm"):
    if " ".join(p.get_text(" ", strip=True).split()) == f"Artículo {ARTICULO}":
        cabecera = p
        break

print("=" * 100)
print(f"ESTRUCTURA EUR-LEX TFUE — ARTÍCULO {ARTICULO} — SOLO LECTURA")
print("=" * 100)

if cabecera is None:
    print("CABECERA: NO ENCONTRADA")
else:
    print("CABECERA:", cabecera.name, cabecera.get("class"), cabecera.get("id"))
    print("PADRE:", cabecera.parent.name, cabecera.parent.get("class"))
    print("\nSIGUIENTES ELEMENTOS HERMANOS DE LA CABECERA:")
    for i, elem in enumerate(cabecera.find_next_siblings()[:20], 1):
        texto = " ".join(elem.get_text(" ", strip=True).split())
        print(f"{i:02d}. tag={elem.name} class={elem.get('class', [])} texto={texto[:220]!r}")

    print("\nSIGUIENTES ELEMENTOS HERMANOS DEL CONTENEDOR PADRE:")
    for i, elem in enumerate(cabecera.parent.find_next_siblings()[:10], 1):
        texto = " ".join(elem.get_text(" ", strip=True).split())
        print(f"{i:02d}. tag={elem.name} class={elem.get('class', [])} texto={texto[:220]!r}")

print("\nBD MODIFICADA: NO")
