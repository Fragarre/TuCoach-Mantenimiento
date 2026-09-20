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


def main():
    if not DB.exists():
        raise FileNotFoundError(f"No existe la base de datos: {DB}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    try:
        duplicados = con.execute(
            """
            SELECT id_boe, articulo_boe, COUNT(*) AS n
            FROM articulos_fuente
            WHERE id_boe IS NOT NULL
              AND articulo_boe IS NOT NULL
            GROUP BY id_boe, articulo_boe
            HAVING COUNT(*) > 1
            ORDER BY id_boe, articulo_boe
            """
        ).fetchall()

        por_boe = {}
        for d in duplicados:
            por_boe.setdefault(d["id_boe"], []).append(d)

        resumen = {
            "UNA_COINCIDE": 0,
            "TODAS_COINCIDEN": 0,
            "NINGUNA_COINCIDE": 0,
            "VARIAS_COINCIDEN": 0,
            "SIN_BLOQUE": 0,
            "SIN_TEXTO_BOE": 0,
            "ERROR_BOE": 0,
        }

        print("=" * 100)
        print("AUDITORÍA GENERAL DE DUPLICADOS VS FUENTE OFICIAL VIGENTE — SOLO LECTURA")
        print("=" * 100)
        print(f"BD.....................: {DB}")
        print(f"Fuentes BOE............: {len(por_boe)}")
        print(f"Grupos duplicados......: {len(duplicados)}")
        print()

        auditados = 0

        for id_boe, grupos in por_boe.items():
            es_eurlex = id_boe in EURLEX
            articulos_eurlex = None

            if es_eurlex:
                try:
                    articulos_eurlex = obtener_articulos_eurlex(id_boe)
                except Exception as exc:
                    for d in grupos:
                        resumen["ERROR_BOE"] += 1
                        auditados += 1
                        print(
                            f"{id_boe} | ART {d['articulo_boe']}: ERROR_BOE | "
                            f"EURLEX | ERROR={exc}"
                        )
                    continue
                indice = None
            else:
                try:
                    indice = obtener_indice(id_boe)
                except Exception as exc:
                    for d in grupos:
                        resumen["ERROR_BOE"] += 1
                        auditados += 1
                        print(
                            f"{id_boe} | ART {d['articulo_boe']}: ERROR_BOE | "
                            f"INDICE | ERROR={exc}"
                        )
                    continue

            for d in grupos:
                articulo = str(d["articulo_boe"])
                filas = con.execute(
                    """
                    SELECT id, id_bloque, texto
                    FROM articulos_fuente
                    WHERE id_boe = ? AND articulo_boe = ?
                    ORDER BY id
                    """,
                    (id_boe, articulo),
                ).fetchall()

                auditados += 1

                if es_eurlex:
                    dato_eurlex = articulos_eurlex.get(articulo)
                    if not dato_eurlex:
                        resumen["SIN_BLOQUE"] += 1
                        print(
                            f"{id_boe} | ART {articulo}: SIN_BLOQUE | "
                            f"EURLEX | BD={[fila['id'] for fila in filas]}"
                        )
                        continue
                    oficial, fecha_vigencia = dato_eurlex
                    id_bloque_oficial = "EURLEX"
                else:
                    id_bloque_oficial = indice.get(articulo)
                    if not id_bloque_oficial:
                        resumen["SIN_BLOQUE"] += 1
                        print(
                            f"{id_boe} | ART {articulo}: SIN_BLOQUE | "
                            f"BD={[fila['id'] for fila in filas]}"
                        )
                        continue

                    try:
                        oficial, fecha_vigencia = obtener_texto_vigente(
                            id_boe, id_bloque_oficial
                        )
                    except Exception as exc:
                        resumen["ERROR_BOE"] += 1
                        print(
                            f"{id_boe} | ART {articulo}: ERROR_BOE | "
                            f"BLOQUE={id_bloque_oficial} | ERROR={exc}"
                        )
                        continue

                if not oficial:
                    resumen["SIN_TEXTO_BOE"] += 1
                    print(
                        f"{id_boe} | ART {articulo}: SIN_TEXTO_BOE | "
                        f"BLOQUE={id_bloque_oficial}"
                    )
                    continue

                coincidencias = [
                    fila["id"]
                    for fila in filas
                    if normalizar(fila["texto"]) == oficial
                ]

                if len(coincidencias) == len(filas):
                    estado = "TODAS_COINCIDEN"
                elif len(coincidencias) == 1:
                    estado = "UNA_COINCIDE"
                elif len(coincidencias) == 0:
                    estado = "NINGUNA_COINCIDE"
                else:
                    estado = "VARIAS_COINCIDEN"

                resumen[estado] += 1

                print(
                    f"{id_boe} | ART {articulo}: {estado} | "
                    f"BOE={id_bloque_oficial} | "
                    f"VIGENCIA={fecha_vigencia} | "
                    f"BD={[fila['id'] for fila in filas]} | "
                    f"COINCIDE={coincidencias}"
                )

        print()
        print("=" * 100)
        print("RESUMEN")
        print("=" * 100)
        for estado, cantidad in resumen.items():
            print(f"{estado:<22}: {cantidad}")
        print()
        print(f"TOTAL AUDITADO..........: {auditados}")
        print("BD MODIFICADA...........: NO")

    finally:
        con.close()


if __name__ == "__main__":
    main()
