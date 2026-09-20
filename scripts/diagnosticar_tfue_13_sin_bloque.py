import re
import requests
from bs4 import BeautifulSoup

URL = "https://eur-lex.europa.eu/legal-content/ES/TXT/HTML/?uri=CELEX:02016E/TXT-20250315"
OBJETIVOS = ["67", "105", "106", "128", "129", "249", "282", "289", "291", "293", "298", "300", "302"]

r = requests.get(URL, timeout=30)
r.raise_for_status()
soup = BeautifulSoup(r.text, "html.parser")

print("=" * 100)
print("DIAGNÓSTICO EUR-LEX TFUE — SOLO LECTURA")
print("=" * 100)
print("HTTP:", r.status_code)
print("HTML:", len(r.text), "caracteres")
print()

for numero in OBJETIVOS:
    patron = re.compile(rf"^\s*Artículo\s+{re.escape(numero)}(?:\s*bis)?\.?\s*$", re.IGNORECASE)
    encontrados = []
    for elem in soup.find_all(True):
        texto = " ".join(elem.get_text(" ", strip=True).split())
        if patron.fullmatch(texto):
            encontrados.append(elem)

    print(f"ART {numero}: coincidencias={len(encontrados)}")
    for i, elem in enumerate(encontrados[:5], 1):
        padre = elem.parent
        print(
            f"  {i}. tag={elem.name} class={elem.get('class', [])} id={elem.get('id', '')!r} "
            f"| parent={getattr(padre, 'name', None)} class_parent={padre.get('class', []) if padre else []}"
        )
    print()

print("BD MODIFICADA: NO")
