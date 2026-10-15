"""Elimina cabeceras y pies de página de los PDF que quedaron dentro del texto de artículos.

Ejemplos de lo que se quita (solo líneas completas de cabecera, nunca texto legal):
  "4.5.2016" / "L 119/35" / "Diario Oficial de la Unión Europea" / "ES"   (DOUE)
  "ES 30.3.2010 Diario Oficial de la Unión Europea C 83/55"               (TFUE/TUE)
  "DO L de 26.9.2024" / "32/239" / "ELI: http://data.europa.eu/..."        (Reglamentos recientes)
  "Num. 7379 / 13.10.2014"                                                 (DOGV en PDF)
  "Núm. 143 Sábado 14 de junio de 2025 Sec. I. Pág. 77830 cve: BOE-A-... Verificable en https://www.boe.es"

Uso:
    python scripts/limpiar_cabeceras_corpus.py            # vista previa (no escribe)
    python scripts/limpiar_cabeceras_corpus.py --aplicar  # copia de seguridad + actualización

Actualiza ``texto`` y ``hash_texto`` (sha256) de articulos_fuente. Después hay que
regenerar los resúmenes afectados (menú 4 → 1) y publicar (menú 5).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
REGISTRO = RAIZ / "registros" / "limpieza_cabeceras_corpus.csv"

_T = r"(?:ES|\d{1,2}\.\d{1,2}\.\d{4}|[LC]\s?\d+[A-Z]?/\d+|Diario Oficial de la Unión Europea)"
FUERTE = re.compile(
    r"^\s*(?:" + _T + r"\s+)*"
    r"(?:Diario Oficial de la Unión Europea|DO L de \d{1,2}\.\d{1,2}\.\d{4}|"
    r"ELI:\s*https?://\S+|[LC]\s?\d+[A-Z]?/\d+)"
    r"(?:\s+" + _T + r")*\s*$")
DOGV = re.compile(r"^\s*N[uú]m\.\s*\d+\s*/\s*\d{2}\.\d{2}\.\d{4}\s*$")
DEBIL = re.compile(r"^\s*(?:ES|\d{1,2}\.\d{1,2}\.\d{4}|\d{1,4}/\d{1,4})\s*$")
BOE_INLINE = re.compile(
    r"\s*Núm\.\s*\d+\s+\w+\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\s+Sec\.\s*[IVX]+[A-Z]?\.?"
    r"(?:\s*Pág\.\s*\d+)?\s*cve:\s*BOE-[A-Z]-\d{4}-\d+\s*Verificable en https?://www\.boe\.es\s*")


def limpiar(texto: str) -> str:
    t = BOE_INLINE.sub(" ", texto)
    lineas = t.split("\n")
    quitar = [bool(FUERTE.match(x) or DOGV.match(x)) for x in lineas]
    cambiado = True
    while cambiado:  # líneas débiles ("ES", fecha, "32/239") solo si están junto a una cabecera
        cambiado = False
        for i, x in enumerate(lineas):
            if quitar[i] or not DEBIL.match(x):
                continue
            if (i > 0 and quitar[i - 1]) or (i + 1 < len(lineas) and quitar[i + 1]):
                quitar[i] = True
                cambiado = True
    return "\n".join(x for x, q in zip(lineas, quitar) if not q)


def sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--muestras", type=int, default=4)
    args = ap.parse_args()
    db = args.db.resolve()

    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    cambios = []
    for rid, idb, art, texto in con.execute(
            "SELECT id, id_boe, articulo_boe, texto FROM articulos_fuente"):
        if not texto:
            continue
        nuevo = limpiar(texto)
        if nuevo != texto:
            cambios.append((rid, idb, art, texto, nuevo))
    normas = sorted({r[0] for r in con.execute(
        f"SELECT DISTINCT nf.norma_id FROM norma_fuentes nf WHERE UPPER(nf.id_fuente) IN "
        f"({','.join('?' * len({c[1].upper() for c in cambios})) or 'NULL'})",
        sorted({c[1].upper() for c in cambios}))})
    con.close()

    print("=" * 78)
    print("LIMPIEZA DE CABECERAS EN EL CORPUS — " + ("APLICAR" if args.aplicar else "VISTA PREVIA"))
    print("=" * 78)
    print(f"Artículos afectados..... {len(cambios)}")
    print(f"Caracteres eliminados... {sum(len(c[3]) - len(c[4]) for c in cambios)}")
    print(f"Normas afectadas (ids).. {normas}")
    for f, n in Counter(c[1] for c in cambios).most_common():
        print(f"  {f:<55} {n}")
    for rid, idb, art, viejo, nuevo in cambios[:args.muestras]:
        quitado = [x for x in viejo.split("\n") if x not in set(nuevo.split("\n"))]
        print(f"\n  · {idb} art {art}: quitado {quitado[:6]}"[:300])
    if not args.aplicar:
        print("\nVISTA PREVIA: no se ha modificado nada. Añada --aplicar para limpiar.")
        return 0

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"{db.stem}_backup_antes_limpiar_cabeceras_{marca}{db.suffix}")
    shutil.copy2(db, backup)
    con = sqlite3.connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        for rid, _, _, viejo, nuevo in cambios:
            cur = con.execute(
                "UPDATE articulos_fuente SET texto=?, hash_texto=? WHERE id=? AND texto=?",
                (nuevo, sha(nuevo), rid, viejo))
            if cur.rowcount != 1:
                raise RuntimeError(f"El artículo {rid} cambió durante la limpieza.")
        con.commit()
    except Exception as exc:
        con.rollback()
        print(f"ERROR: {exc}. Nada modificado (rollback).")
        return 2
    finally:
        con.close()
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRO.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        for rid, idb, art, viejo, nuevo in cambios:
            w.writerow([datetime.now().isoformat(timespec="seconds"), rid, idb, art,
                        len(viejo) - len(nuevo), backup.name])
    print(f"\nAPLICADO: {len(cambios)} artículos limpiados. Copia: {backup}")
    print("Siguiente: menú 4 → 1 (regenerar resúmenes afectados) y publicar (menú 5).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
