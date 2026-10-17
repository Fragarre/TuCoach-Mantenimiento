"""Elimina el origen de examen que aparece entre paréntesis al final del enunciado.

Ejemplos que se eliminan:  "(A1 OPE 2023)", "(Cuerpo Gestión del Estado Promoción Interna 2016)",
                            "(auxiliar administrativo Torrent)", "(dificultad alta)".
Se respetan instrucciones y aclaraciones: "(señale la incorrecta)", "(TFUE)", "(Ley 38/2003)",
"(art. 3 de la Ley 1/2015)", rangos de Excel como "(A1:A3)", etc.

Uso (desde TuCoach-Mantenimiento):
    python scripts/limpiar_origenes_enunciados.py            -> SIMULACIÓN: no cambia nada
    python scripts/limpiar_origenes_enunciados.py --aplicar  -> copia de seguridad + cambios

Solo modifica la columna lote_preguntas.enunciado. Guarda un CSV con cada cambio
(id, enunciado anterior, enunciado nuevo) para poder revisarlo o deshacerlo.
"""
import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import re
FIN=re.compile(r'\s*\(([^()]{2,160})\)\s*([:.?]?)\s*$')
INSTR=re.compile(r'^\s*(señal|indi|marqu|elij|escoj|seleccion|respuesta|opción|afirmaci)',re.I)
ORIG=re.compile(r"""(\b(19|20)\d{2}\b|\bOPE\b|\boficial\b|\bcuerpo\b|\bAGE\b|\bGVA\b|auxiliar|\baux\b|administrativ|adtivo|advo|estabilizaci|turno|promoci|\bTAG\b|
 \b[ABC][12]\b|t[eé]cnico|subalterno|gesti[oó]n\b|\bgen(eralitat)?\b|ayuntamiento|diputaci|\bconsell\b|\bOEP\b|\bGACE\b|orientador|libre|covid|examen|convocatoria|oposici|bolsa|admón|admon)""",re.I|re.X)
NO_ORIG=re.compile(r'^\s*(ley\b|lo\b|l\.o\.|rd\b|real decreto|decreto|art[íi]?c?u?l?o?\.?\s*\d|art\.|artículo|en adelante|considere|se asume|se entiende|nota|o\s|y\s|y,|disposiciones|consell\s*$)|[:;"=]|^[A-Z]\d+-[A-Z]\d+$',re.I)
META=re.compile(r'^\s*dificultad\b',re.I)
def es_origen(t):
    if META.search(t): return True
    return bool(ORIG.search(t)) and not INSTR.search(t) and not NO_ORIG.search(t)
def limpiar(enunciado):
    m=FIN.search(enunciado or '')
    if not m or not es_origen(m.group(1)): return enunciado, None
    base=enunciado[:m.start()].rstrip()
    punt=m.group(2)
    if punt and not base.endswith(punt): base+=punt
    return base, m.group(1)


RAIZ = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(RAIZ / "db" / "oposiciones.sqlite3"))
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    db = Path(a.db).resolve()
    if not db.exists():
        print(f"No existe la base: {db}")
        return 1
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    informes = RAIZ / "informes"
    informes.mkdir(exist_ok=True)

    con = sqlite3.connect(db)
    filas = con.execute("SELECT id, enunciado, tipo_fuente FROM lote_preguntas").fetchall()
    cambios = []
    for pid, enunciado, fuente in filas:
        nuevo, origen = limpiar(enunciado)
        if origen is not None and nuevo.strip():
            cambios.append((pid, fuente, origen, enunciado, nuevo))

    print(f"Base de datos: {db}")
    print(f"Preguntas revisadas: {len(filas)}")
    print(f"Enunciados con origen al final: {len(cambios)}")
    log = informes / f"limpiar_origenes_{'APLICADO' if a.aplicar else 'SIMULACION'}_{ts}.csv"
    with open(log, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "tipo_fuente", "origen_eliminado", "enunciado_anterior", "enunciado_nuevo"])
        w.writerows(cambios)
    print(f"Detalle de los cambios: {log}")
    for pid, _, origen, antes, despues in cambios[:3]:
        print(f"\n  id {pid}  -> se elimina: ({origen})\n  ANTES:   ...{antes[-90:]}\n  DESPUÉS: ...{despues[-90:]}")

    if not a.aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0

    copia = db.with_name(f"oposiciones_backup_antes_limpiar_origenes_{ts}.sqlite3")
    destino = sqlite3.connect(copia)
    con.backup(destino)
    destino.close()
    print(f"\nCopia de seguridad: {copia}")

    with con:
        con.executemany(
            "UPDATE lote_preguntas SET enunciado = ? WHERE id = ? AND enunciado = ?",
            [(despues, pid, antes) for pid, _, _, antes, despues in cambios],
        )
    pendientes = sum(1 for pid, e, _ in con.execute("SELECT id, enunciado, tipo_fuente FROM lote_preguntas")
                     if limpiar(e)[1] is not None)
    total = con.execute("SELECT COUNT(*) FROM lote_preguntas").fetchone()[0]
    print(f"Enunciados modificados: {len(cambios)}")
    print(f"Orígenes que quedan: {pendientes}   (debe ser 0)")
    print(f"Total de preguntas: {total}        (no debe cambiar: {len(filas)})")
    print("RESULTADO:", "OK" if pendientes == 0 and total == len(filas) else "REVISAR")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
