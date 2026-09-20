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


def obtener_indice():
    url = f"{API}/indice"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.content)


def buscar_bloque_por_id(root, ids_candidatos):
    bloques = {}
    for bloque in root.findall(".//bloque"):
        id_bloque = (bloque.findtext("id") or "").strip()
        if id_bloque:
            bloques[id_bloque] = bloque

    for candidato in ids_candidatos:
        if candidato in bloques:
            return candidato
    return None


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

        indice_root = obtener_indice()

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

            ids_candidatos = [fila["id_bloque"] for fila in filas]
            id_bloque_oficial = buscar_bloque_por_id(
                indice_root, ids_candidatos
            )

            if not id_bloque_oficial:
                resumen["SIN_BLOQUE"] += 1
                print(
                    f"ART {articulo}: SIN_BLOQUE | "
                    f"BD={[fila['id'] for fila in filas]} | "
                    f"IDS={ids_candidatos}"
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
