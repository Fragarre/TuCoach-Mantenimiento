import re
import sqlite3
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from pathlib import Path

DB = Path("db/oposiciones.sqlite3")
API_BASE = "https://www.boe.es/datosabiertos/api/legislacion-consolidada/id"
HEADERS = {"Accept": "application/xml"}

EURLEX = {
    "DOUE-C-2010-083-TUE": (
        "https://eur-lex.europa.eu/legal-content/ES/TXT/HTML/?uri=CELEX:02016M/TXT-20250315",
        "2025-03-15",
    ),
    "DOUE-C-2010-083-TFUE": (
        "https://eur-lex.europa.eu/legal-content/ES/TXT/HTML/?uri=CELEX:02016E/TXT-20250315",
        "2025-03-15",
    ),
}


def normalizar(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def texto_version(version):
    partes = []
    for elemento in version.iter():
        clase = elemento.attrib.get("class", "")
        if clase == "articulo" or clase == "parrafo" or clase.startswith("parrafo_"):
            texto = "".join(elemento.itertext())
            texto = normalizar(texto)
            if texto:
                partes.append(texto)
    return normalizar(" ".join(partes))


NUMEROS_ARTICULO = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "primero": 1, "segundo": 2, "tercero": 3, "cuarto": 4, "quinto": 5,
    "sexto": 6, "séptimo": 7, "septimo": 7, "octavo": 8, "noveno": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciséis": 16, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
    "veintidós": 22, "veintidos": 22, "veintitrés": 23, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiséis": 26,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "treinta y uno": 31,
    "treinta y dos": 32, "treinta y tres": 33, "treinta y cuatro": 34,
    "treinta y cinco": 35, "treinta y seis": 36, "treinta y siete": 37,
    "treinta y ocho": 38, "treinta y nueve": 39, "cuarenta": 40,
    "cuarenta y uno": 41, "cuarenta y dos": 42, "cuarenta y tres": 43,
    "cuarenta y cuatro": 44, "cuarenta y cinco": 45, "cuarenta y seis": 46,
    "cuarenta y siete": 47, "cuarenta y ocho": 48, "cuarenta y nueve": 49,
    "cincuenta": 50, "cincuenta y uno": 51, "cincuenta y dos": 52,
    "cincuenta y tres": 53, "cincuenta y cuatro": 54,
    "cincuenta y cinco": 55, "cincuenta y seis": 56,
    "cincuenta y siete": 57, "cincuenta y ocho": 58,
    "cincuenta y nueve": 59, "sesenta": 60, "sesenta y uno": 61,
    "sesenta y dos": 62, "sesenta y tres": 63, "sesenta y cuatro": 64,
    "sesenta y cinco": 65, "sesenta y seis": 66, "sesenta y siete": 67,
    "sesenta y ocho": 68, "sesenta y nueve": 69, "setenta": 70,
    "setenta y uno": 71, "setenta y dos": 72, "setenta y tres": 73,
    "setenta y cuatro": 74, "setenta y cinco": 75, "setenta y seis": 76,
    "setenta y siete": 77, "setenta y ocho": 78, "setenta y nueve": 79,
    "ochenta": 80, "ochenta y uno": 81,
    "décimo": 10, "decimo": 10,
    "decimoprimero": 11, "décimo primero": 11, "decimo primero": 11,
    "decimosegundo": 12, "décimo segundo": 12, "decimo segundo": 12,
    "decimotercero": 13, "décimo tercero": 13, "decimo tercero": 13,
    "decimocuarto": 14, "décimo cuarto": 14, "decimo cuarto": 14,
    "decimoquinto": 15, "décimo quinto": 15, "decimo quinto": 15,
    "decimosexto": 16, "décimo sexto": 16, "decimo sexto": 16,
    "decimoséptimo": 17, "decimoseptimo": 17, "décimo séptimo": 17, "decimo septimo": 17,
    "decimoctavo": 18, "décimo octavo": 18, "decimo octavo": 18,
    "decimonoveno": 19, "décimo noveno": 19, "decimo noveno": 19,
    "vigésimo": 20, "vigesimo": 20,
    "vigésimo primero": 21, "vigesimo primero": 21,
}


def numero_desde_titulo(titulo):
    titulo = normalizar(titulo)
    m = re.fullmatch(r"(?:Artículo|Art\.?)\s+(\d+)\.?", titulo, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.fullmatch(r"Artículo\s+(.+?)\.?", titulo, re.IGNORECASE)
    if not m:
        return None

    nombre = m.group(1).strip().lower()
    numero = NUMEROS_ARTICULO.get(nombre)
    return str(numero) if numero is not None else None


def obtener_indice(id_boe):
    url = f"{API_BASE}/{id_boe}/texto/indice"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)

    resultado = {}
    for bloque in root.findall(".//bloque"):
        titulo = (bloque.findtext("titulo") or "").strip()
        id_bloque = (bloque.findtext("id") or "").strip()
        numero = numero_desde_titulo(titulo)
        if numero and id_bloque:
            resultado[numero] = id_bloque

    return resultado


def obtener_texto_vigente(id_boe, id_bloque):
    url = f"{API_BASE}/{id_boe}/texto/bloque/{id_bloque}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    versiones = root.findall(".//version")

    if not versiones:
        return None, None

    vigente = max(
        versiones,
        key=lambda v: (
            v.attrib.get("fecha_vigencia", ""),
            v.attrib.get("fecha_publicacion", ""),
        ),
    )

    fecha_vigencia = vigente.attrib.get("fecha_vigencia", "")
    return texto_version(vigente), fecha_vigencia



def obtener_articulos_eurlex(id_doue):
    url, fecha_vigencia = EURLEX[id_doue]
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    resultado = {}
    for cabecera in soup.find_all("p", class_="title-article-norm"):
        titulo = normalizar(cabecera.get_text(" ", strip=True))
        m = re.fullmatch(r"Artículo\s+(\d+)(?:\s*bis)?\.?", titulo, re.IGNORECASE)
        if not m:
            continue

        numero = m.group(1)

        # El documento EUR-Lex incluye protocolos/anexos que vuelven a numerar
        # artículos desde 1. Conservamos la primera aparición: corresponde al
        # cuerpo principal del Tratado.
        if numero in resultado:
            continue

        partes = []
        for elem in cabecera.find_all_next():
            if elem is cabecera:
                continue
            clases = elem.get("class", []) if hasattr(elem, "get") else []
            if elem.name == "p" and "title-article-norm" in clases:
                break
            if elem.name in ("p", "div") and "norm" in clases:
                texto = normalizar(elem.get_text(" ", strip=True))
                if texto:
                    partes.append(texto)

        texto = normalizar(" ".join(partes))
        if texto:
            resultado[numero] = (texto, fecha_vigencia)

    return resultado


from difflib import SequenceMatcher
import unicodedata

def normalizar_comparacion_suave(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[“”«»\"'’]", "", s)
    s = re.sub(r"\s*([,.;:()])\s*", r"\1", s)
    return s

def ratio(a, b):
    return 100.0 * SequenceMatcher(
        None, normalizar_comparacion_suave(a), normalizar_comparacion_suave(b)
    ).ratio()

def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    grupos = cur.execute("""
        SELECT id_boe, articulo_boe, COUNT(*) n
        FROM articulos_fuente
        GROUP BY id_boe, articulo_boe
        HAVING COUNT(*) > 1
        ORDER BY id_boe, articulo_boe
    """).fetchall()

    cache_boe = {}
    cache_eur = {}
    filas = []

    for g in grupos:
        id_boe = g["id_boe"]
        art = str(g["articulo_boe"]).strip()
        regs = cur.execute("""
            SELECT id, texto FROM articulos_fuente
            WHERE id_boe=? AND articulo_boe=?
            ORDER BY id
        """, (id_boe, g["articulo_boe"])).fetchall()

        oficial = None
        if id_boe in EURLEX:
            if id_boe not in cache_eur:
                cache_eur[id_boe] = obtener_articulos_eurlex(id_boe)
            oficial = cache_eur[id_boe].get(art)
        else:
            if id_boe not in cache_boe:
                cache_boe[id_boe] = obtener_indice(id_boe)
            bloque = buscar_bloque(cache_boe[id_boe], art)
            if bloque:
                texto, fecha = obtener_texto_boe(id_boe, bloque)
                oficial = (texto, fecha)

        if not oficial or not oficial[0]:
            continue

        texto_oficial = oficial[0]
        exactas = [r["id"] for r in regs if normalizar(r["texto"]) == normalizar(texto_oficial)]
        if exactas:
            continue

        sims = [(r["id"], ratio(r["texto"], texto_oficial)) for r in regs]
        mejor = max(x[1] for x in sims)
        if mejor >= 99:
            tramo = ">=99"
        elif mejor >= 95:
            tramo = "95-99"
        elif mejor >= 90:
            tramo = "90-95"
        elif mejor >= 75:
            tramo = "75-90"
        elif mejor >= 50:
            tramo = "50-75"
        else:
            tramo = "<50"
        filas.append((id_boe, art, sims, mejor, tramo))

    orden = [">=99", "95-99", "90-95", "75-90", "50-75", "<50"]
    conteos = {k: 0 for k in orden}
    for _, _, _, _, tramo in filas:
        conteos[tramo] += 1

    print("=" * 100)
    print("ANÁLISIS DE 331 NINGUNA_COINCIDE — SIMILITUD — SOLO LECTURA")
    print("=" * 100)
    print("NINGUNA_COINCIDE analizadas:", len(filas))
    print()
    for k in orden:
        print(f"{k:8}: {conteos[k]}")
    print()
    print("DETALLE <95%:")
    for id_boe, art, sims, mejor, tramo in filas:
        if mejor < 95:
            datos = ", ".join(f"{i}:{s:.2f}%" for i, s in sims)
            print(f"{id_boe} | ART {art} | {tramo} | MEJOR={mejor:.2f}% | BD=[{datos}]")
    print()
    print("BD MODIFICADA: NO")
    con.close()

if __name__ == "__main__":
    main()
