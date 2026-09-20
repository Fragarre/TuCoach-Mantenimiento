import sys
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



import hashlib
import argparse
import shutil
from datetime import datetime

def hash_oficial(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()

def indices_unicos(con, tabla):
    salida = []
    for idx in con.execute(f"PRAGMA index_list({tabla})").fetchall():
        # seq, name, unique, origin, partial
        if idx[2]:
            nombre = idx[1]
            cols = [r[2] for r in con.execute(f"PRAGMA index_info({nombre})").fetchall()]
            if cols:
                salida.append((nombre, cols))
    return salida

def redirigir_fks(mem, tabla, fk, antiguos, canonico):
    fusionadas = 0
    actualizadas = 0
    uniques = indices_unicos(mem, tabla)
    for antiguo in antiguos:
        filas = mem.execute(f"SELECT * FROM {tabla} WHERE {fk}=?", (antiguo,)).fetchall()
        for fila in filas:
            d = dict(fila)
            conflicto = False
            for _, cols in uniques:
                if fk not in cols:
                    continue
                vals = []
                where = []
                for col in cols:
                    val = canonico if col == fk else d[col]
                    if val is None:
                        where.append(f"{col} IS NULL")
                    else:
                        where.append(f"{col}=?")
                        vals.append(val)
                q = f"SELECT id FROM {tabla} WHERE " + " AND ".join(where) + " AND id<>? LIMIT 1"
                vals.append(d["id"])
                if mem.execute(q, vals).fetchone():
                    conflicto = True
                    break
            if conflicto:
                mem.execute(f"DELETE FROM {tabla} WHERE id=?", (d["id"],))
                fusionadas += 1
            else:
                mem.execute(f"UPDATE {tabla} SET {fk}=? WHERE id=?", (canonico, d["id"]))
                actualizadas += 1
    return actualizadas, fusionadas

def main():
    if not DB.exists():
        raise FileNotFoundError(f"No existe la base de datos: {DB}")

    origen = sqlite3.connect(DB)
    origen.row_factory = sqlite3.Row
    mem = sqlite3.connect(":memory:")
    origen.backup(mem)
    mem.row_factory = sqlite3.Row

    duplicados = origen.execute("""
        SELECT id_boe, articulo_boe, COUNT(*) AS n
        FROM articulos_fuente
        WHERE id_boe IS NOT NULL AND articulo_boe IS NOT NULL
        GROUP BY id_boe, articulo_boe
        HAVING COUNT(*) > 1
        ORDER BY id_boe, articulo_boe
    """).fetchall()

    por_fuente = {}
    for d in duplicados:
        por_fuente.setdefault(d["id_boe"], []).append(d)

    # Verificar convención de hash sin asumirla.
    total_hash = origen.execute("SELECT COUNT(*) FROM articulos_fuente").fetchone()[0]
    sha_raw = origen.execute("SELECT id, texto, hash_texto FROM articulos_fuente").fetchall()
    coinc_hash = sum(1 for r in sha_raw if hash_oficial(r["texto"]) == r["hash_texto"])

    stats = {
        "grupos": 0, "canon_existente_oficial": 0, "canon_reescribir": 0,
        "articulos_eliminar": 0, "temario_actualizadas": 0, "temario_fusionadas": 0,
        "resol_actualizadas": 0, "resol_fusionadas": 0, "bloqueos": 0,
    }
    errores = []

    try:
        mem.execute("PRAGMA foreign_keys=ON")

        for id_boe, grupos in por_fuente.items():
            try:
                if id_boe in EURLEX:
                    oficiales = obtener_articulos_eurlex(id_boe)
                    indice = None
                else:
                    indice = obtener_indice(id_boe)
                    oficiales = None
            except Exception as exc:
                errores.append(f"{id_boe}: FUENTE: {exc}")
                stats["bloqueos"] += len(grupos)
                continue

            for g in grupos:
                art = str(g["articulo_boe"]).strip()
                filas = mem.execute("""
                    SELECT * FROM articulos_fuente
                    WHERE id_boe=? AND articulo_boe=?
                    ORDER BY id
                """, (id_boe, g["articulo_boe"])).fetchall()

                try:
                    if id_boe in EURLEX:
                        dato = oficiales.get(art)
                        if not dato:
                            raise RuntimeError("sin texto oficial")
                        oficial, fecha = dato
                        id_bloque = filas[0]["id_bloque"]
                    else:
                        id_bloque = indice.get(art)
                        if not id_bloque:
                            raise RuntimeError("sin bloque oficial")
                        oficial, fecha = obtener_texto_vigente(id_boe, id_bloque)
                        if not oficial:
                            raise RuntimeError("sin texto oficial")

                    exactas = [r for r in filas if normalizar(r["texto"]) == normalizar(oficial)]
                    canon = min(exactas, key=lambda r: r["id"]) if exactas else min(filas, key=lambda r: r["id"])
                    extras = [r["id"] for r in filas if r["id"] != canon["id"]]

                    if exactas:
                        stats["canon_existente_oficial"] += 1
                    else:
                        stats["canon_reescribir"] += 1

                    a, f = redirigir_fks(mem, "temario_referencias", "articulo_fuente_id", extras, canon["id"])
                    stats["temario_actualizadas"] += a
                    stats["temario_fusionadas"] += f
                    a, f = redirigir_fks(mem, "resoluciones_boe", "articulo_fuente_id", extras, canon["id"])
                    stats["resol_actualizadas"] += a
                    stats["resol_fusionadas"] += f

                    # Primero quitar copias para liberar UNIQUE(id_boe,id_bloque), después normalizar canónico.
                    for extra_id in extras:
                        mem.execute("DELETE FROM articulos_fuente WHERE id=?", (extra_id,))
                    stats["articulos_eliminar"] += len(extras)

                    mem.execute("""
                        UPDATE articulos_fuente
                        SET id_bloque=?, texto=?, hash_texto=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    """, (id_bloque, oficial, hash_oficial(oficial), canon["id"]))

                    stats["grupos"] += 1
                except Exception as exc:
                    stats["bloqueos"] += 1
                    errores.append(f"{id_boe} ART {art}: {type(exc).__name__}: {exc}")

        restantes = mem.execute("""
            SELECT COUNT(*) FROM (
                SELECT 1 FROM articulos_fuente
                GROUP BY id_boe, articulo_boe HAVING COUNT(*) > 1
            )
        """).fetchone()[0]

        fk_errors = mem.execute("PRAGMA foreign_key_check").fetchall()

        print("=" * 88)
        print("CONSOLIDADOR DUPLICADOS ARTÍCULOS — SOLO REVISIÓN / SIMULACIÓN EN MEMORIA")
        print("=" * 88)
        print(f"Grupos detectados................: {len(duplicados)}")
        print(f"Grupos simulados correctamente...: {stats['grupos']}")
        print(f"Bloqueos.........................: {stats['bloqueos']}")
        print(f"Canónico ya coincide oficial.....: {stats['canon_existente_oficial']}")
        print(f"Canónico a reescribir oficial....: {stats['canon_reescribir']}")
        print(f"Artículos duplicados a eliminar..: {stats['articulos_eliminar']}")
        print(f"Temario referencias redirigidas..: {stats['temario_actualizadas']}")
        print(f"Temario referencias fusionadas...: {stats['temario_fusionadas']}")
        print(f"Resoluciones redirigidas..........: {stats['resol_actualizadas']}")
        print(f"Resoluciones fusionadas...........: {stats['resol_fusionadas']}")
        print(f"Duplicados restantes simulación...: {restantes}")
        print(f"Errores FK tras simulación........: {len(fk_errors)}")
        print(f"Hash SHA256(texto) actual.........: {coinc_hash}/{total_hash}")
        aplicar = "--aplicar" in sys.argv

        if aplicar:
            if stats["bloqueos"] or restantes or fk_errors or stats["grupos"] != len(duplicados):
                raise RuntimeError("Aplicación bloqueada: la simulación no ha quedado íntegra.")

            backup = DB.with_name(
                DB.stem + "_backup_antes_consolidar_" +
                datetime.now().strftime("%Y%m%d_%H%M%S") + DB.suffix
            )
            origen.close()
            shutil.copy2(DB, backup)

            destino = sqlite3.connect(DB)
            try:
                mem.backup(destino)
                destino.commit()
                destino.row_factory = sqlite3.Row
                restantes_real = destino.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT 1 FROM articulos_fuente
                        GROUP BY id_boe, articulo_boe HAVING COUNT(*) > 1
                    )
                """).fetchone()[0]
                fk_real = destino.execute("PRAGMA foreign_key_check").fetchall()
                if restantes_real != 0 or fk_real:
                    raise RuntimeError(
                        f"Validación real falló: duplicados={restantes_real}, FK={len(fk_real)}"
                    )
            except Exception:
                destino.close()
                shutil.copy2(backup, DB)
                raise
            else:
                destino.close()

            print(f"BACKUP.............................: {backup}")
            print("BD ORIGINAL MODIFICADA............: SÍ")
            print("RESULTADO..........................: APLICACIÓN COMPLETADA")
        else:
            print("BD ORIGINAL MODIFICADA............: NO")

        if errores:
            print("\nBLOQUEOS (máximo 30):")
            for e in errores[:30]:
                print(" -", e)
            if len(errores) > 30:
                print(f" ... y {len(errores)-30} más")

    finally:
        mem.close()
        origen.close()

if __name__ == "__main__":
    main()
