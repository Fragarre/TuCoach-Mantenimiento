import re
import sqlite3
import requests
import xml.etree.ElementTree as ET
from pathlib import Path

DB = Path("db/oposiciones.sqlite3")
ID_BOE = "BOE-A-1982-17235"
API = (
    "https://www.boe.es/datosabiertos/api/"
    f"legislacion-consolidada/id/{ID_BOE}/texto"
)
HEADERS = {"Accept": "application/xml"}


def normalizar(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def texto_version(version):
    partes = []
    for elemento in version.iter():
        clase = elemento.attrib.get("class", "")
        if clase in {"articulo", "parrafo"}:
            texto = "".join(elemento.itertext())
            texto = normalizar(texto)
            if texto:
                partes.append(texto)
    return normalizar(" ".join(partes))


NUMEROS_ARTICULO = {
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
}


def numero_desde_titulo(titulo):
    titulo = normalizar(titulo)
    m = re.fullmatch(r"Artículo\s+(\d+)\.?", titulo, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.fullmatch(r"Artículo\s+(.+?)\.?", titulo, re.IGNORECASE)
    if not m:
        return None

    nombre = m.group(1).strip().lower()
    numero = NUMEROS_ARTICULO.get(nombre)
    return str(numero) if numero is not None else None


def obtener_indice():
    url = f"{API}/indice"
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


def obtener_texto_vigente(id_bloque):
    url = f"{API}/bloque/{id_bloque}"
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


def main():
    if not DB.exists():
        raise FileNotFoundError(f"No existe la base de datos: {DB}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    try:
        duplicados = con.execute(
            """
            SELECT articulo_boe
            FROM articulos_fuente
            WHERE id_boe = ?
            GROUP BY articulo_boe
            HAVING COUNT(*) > 1
            ORDER BY CAST(articulo_boe AS INTEGER)
            """,
            (ID_BOE,),
        ).fetchall()

        indice = obtener_indice()

        print("=" * 90)
        print("AUDITORÍA DUPLICADOS VS BOE VIGENTE — SOLO LECTURA")
        print("=" * 90)
        print(f"BD..............: {DB}")
        print(f"BOE.............: {ID_BOE}")
        print(f"Duplicados......: {len(duplicados)}")
        print()

        resumen = {
            "UNA_COINCIDE": 0,
            "AMBAS_COINCIDEN": 0,
            "NINGUNA_COINCIDE": 0,
            "SIN_BLOQUE": 0,
            "SIN_TEXTO_BOE": 0,
            "ERROR_BOE": 0,
        }

        for d in duplicados:
            articulo = str(d["articulo_boe"])

            filas = con.execute(
                """
                SELECT id, id_bloque, texto
                FROM articulos_fuente
                WHERE id_boe = ? AND articulo_boe = ?
                ORDER BY id
                """,
                (ID_BOE, articulo),
            ).fetchall()

            id_bloque_oficial = indice.get(articulo)

            if not id_bloque_oficial:
                resumen["SIN_BLOQUE"] += 1
                print(
                    f"ART {articulo}: SIN_BLOQUE | "
                    f"BD={[fila['id'] for fila in filas]}"
                )
                continue

            try:
                oficial, fecha_vigencia = obtener_texto_vigente(
                    id_bloque_oficial
                )
            except Exception as exc:
                resumen["ERROR_BOE"] += 1
                print(
                    f"ART {articulo}: ERROR_BOE | "
                    f"BLOQUE={id_bloque_oficial} | ERROR={exc}"
                )
                continue

            if not oficial:
                resumen["SIN_TEXTO_BOE"] += 1
                print(
                    f"ART {articulo}: SIN_TEXTO_BOE | "
                    f"BLOQUE={id_bloque_oficial}"
                )
                continue

            coincidencias = [
                fila["id"]
                for fila in filas
                if normalizar(fila["texto"]) == oficial
            ]

            if len(coincidencias) == 1:
                estado = "UNA_COINCIDE"
            elif len(coincidencias) == len(filas):
                estado = "AMBAS_COINCIDEN"
            else:
                estado = "NINGUNA_COINCIDE"

            resumen[estado] += 1

            print(
                f"ART {articulo}: {estado} | "
                f"BOE={id_bloque_oficial} | "
                f"VIGENCIA={fecha_vigencia} | "
                f"BD={[fila['id'] for fila in filas]} | "
                f"COINCIDE={coincidencias}"
            )

        print()
        print("=" * 90)
        print("RESUMEN")
        print("=" * 90)
        for estado, cantidad in resumen.items():
            print(f"{estado:<22}: {cantidad}")

        print()
        print(f"TOTAL AUDITADO........: {len(duplicados)}")
        print("BD MODIFICADA..........: NO")

    finally:
        con.close()


if __name__ == "__main__":
    main()
