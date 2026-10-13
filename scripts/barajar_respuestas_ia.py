"""
Reparación: baraja las opciones de las preguntas IA nuevas (origen IA-2026-10)
cuya respuesta correcta quedó siempre en A, y recalcula respuesta_correcta.

  python scripts\\barajar_respuestas_ia.py             -> solo informa (no cambia nada)
  python scripts\\barajar_respuestas_ia.py --aplicar   -> hace copia de seguridad y aplica
  Añadir --alcance anteriores para las IA previas a IA-2026-10 (por defecto: nuevas).

Solo toca lote_preguntas (los bancos leen de ahí por id). Las preguntas cuyas
opciones dependen unas de otras ("todas las anteriores", "A y B"...) no se tocan.
"""
import argparse, random, re, shutil, sqlite3, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
DEP = re.compile(
    r"(anteriores|\bambas\b|\bambos\b|\b[abcd]\s*(y|e|,|o)\s*[abcd]\b|opci[oó]n(es)?\s+[abcd]\b|ninguna de las|todas las)",
    re.IGNORECASE)
SEL = """SELECT lp.id, lp.opcion_a, lp.opcion_b, lp.opcion_c, lp.opcion_d, lp.respuesta_correcta
         FROM lote_preguntas lp JOIN importaciones_ficheros i ON i.id = lp.importacion_fichero_id
         WHERE i.nombre_fichero LIKE 'IA-2026-10%'"""

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--alcance", choices=["nuevas", "anteriores"], default="nuevas")
    a = ap.parse_args()
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    sel = SEL if a.alcance == "nuevas" else """SELECT lp.id, lp.opcion_a, lp.opcion_b, lp.opcion_c, lp.opcion_d, lp.respuesta_correcta
         FROM lote_preguntas lp LEFT JOIN importaciones_ficheros i ON i.id = lp.importacion_fichero_id
         WHERE LOWER(TRIM(lp.tipo_fuente))='ia_generada'
           AND (i.nombre_fichero IS NULL OR i.nombre_fichero NOT LIKE 'IA-2026-10%')"""
    filas = con.execute(sel).fetchall()
    antes = Counter(str(f["respuesta_correcta"]).strip().upper() for f in filas)
    print(f"Alcance {a.alcance}: {len(filas)} preguntas\nDistribución actual de la correcta: {dict(sorted(antes.items()))}")
    cambios, omitidas = [], 0
    rng = random.SystemRandom()
    for f in filas:
        t = [f["opcion_a"], f["opcion_b"], f["opcion_c"], f["opcion_d"]]
        if any(DEP.search(str(x or "")) for x in t): omitidas += 1; continue
        c = "ABCD".index(str(f["respuesta_correcta"]).strip().upper())
        o = [0, 1, 2, 3]; rng.shuffle(o)
        cambios.append((t[o[0]], t[o[1]], t[o[2]], t[o[3]], "ABCD"[o.index(c)], f["id"]))
    despues = Counter(x[4] for x in cambios)
    print(f"A barajar: {len(cambios)} | omitidas por opciones dependientes: {omitidas}")
    print(f"Distribución prevista de las barajadas: {dict(sorted(despues.items()))}")
    if not a.aplicar:
        print("\nMODO INFORME: no se ha modificado nada. Añada --aplicar para ejecutar."); return
    copia = DB.with_name(f"oposiciones_antes_barajar_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    con.close(); shutil.copy2(DB, copia); print("Copia de seguridad:", copia.name)
    con = sqlite3.connect(DB)
    with con:
        con.executemany("UPDATE lote_preguntas SET opcion_a=?,opcion_b=?,opcion_c=?,opcion_d=?,respuesta_correcta=? WHERE id=?", cambios)
    print("Hecho. Preguntas actualizadas:", len(cambios))

if __name__ == "__main__":
    sys.exit(main())
