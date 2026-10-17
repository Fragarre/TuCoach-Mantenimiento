"""Marca internamente las preguntas de lote_preguntas que coinciden con exámenes oficiales GVA.

Crea (si no existe) la tabla preguntas_examen_oficial y la llena con las coincidencias de
data_examenes_gva/fase4_coincidencias_oficiales.json (exámenes C1 7/22 y 8/22, A2 141/21 y
138/21, 179/21; similitud >= 0,85 y misma respuesta correcta que la plantilla oficial).

Es una marca SOLO INTERNA:
  - no cambia lote_preguntas ni los bancos;
  - no se publica en la web (no está entre las tablas que se suben a Supabase);
  - sirve para no reescribir estas preguntas en la fase de reescritura (son oficiales).

Uso (desde TuCoach-Mantenimiento):
    python scripts/marcar_preguntas_examen_oficial.py            -> muestra lo que haría
    python scripts/marcar_preguntas_examen_oficial.py --aplicar  -> copia de seguridad + marca
Consulta:  SELECT pregunta_id FROM preguntas_examen_oficial
"""
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
ENTRADA = RAIZ / "data_examenes_gva" / "fase4_coincidencias_oficiales.json"

DDL = """
CREATE TABLE IF NOT EXISTS preguntas_examen_oficial (
    id INTEGER PRIMARY KEY,
    pregunta_id INTEGER NOT NULL REFERENCES lote_preguntas(id) ON DELETE CASCADE,
    examen TEXT NOT NULL,
    numero INTEGER,
    similitud REAL,
    fecha TEXT NOT NULL,
    UNIQUE (pregunta_id, examen, numero)
)
"""


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    datos = json.loads(ENTRADA.read_text(encoding="utf-8"))
    con = sqlite3.connect(DB)
    existentes = {r[0] for r in con.execute("SELECT id FROM lote_preguntas")}
    faltan = [d for d in datos if d["pregunta_id"] not in existentes]
    if faltan:
        print(f"{len(faltan)} preguntas ya no existen en lote_preguntas: {[d['pregunta_id'] for d in faltan][:10]}")
        datos = [d for d in datos if d["pregunta_id"] in existentes]
    ids = {d["pregunta_id"] for d in datos}
    print(f"Coincidencias: {len(datos)} · preguntas distintas: {len(ids)}")
    for (fuente, n) in sorted(Counter(d["fuente"] for d in datos).items()):
        print(f"  fuente {fuente:12s} {n}")
    for (examen, n) in sorted(Counter(d["examen"] for d in datos).items()):
        print(f"  examen {examen:12s} {n}")
    if not aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0
    con.close()
    copia = DB.with_name(f"oposiciones_backup_antes_marcar_oficiales_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    o = sqlite3.connect(DB); d = sqlite3.connect(copia); o.backup(d); d.close(); o.close()
    print(f"Copia de seguridad: {copia}")
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    ahora = datetime.now().astimezone().isoformat(timespec="seconds")
    with con:
        con.execute(DDL)
        antes = con.execute("SELECT COUNT(*) FROM preguntas_examen_oficial").fetchone()[0]
        con.executemany(
            """INSERT OR IGNORE INTO preguntas_examen_oficial (pregunta_id, examen, numero, similitud, fecha)
               VALUES (?, ?, ?, ?, ?)""",
            [(d["pregunta_id"], d["examen"], d["numero"], d["similitud"], ahora) for d in datos])
        despues = con.execute("SELECT COUNT(*) FROM preguntas_examen_oficial").fetchone()[0]
    distintas = con.execute("SELECT COUNT(DISTINCT pregunta_id) FROM preguntas_examen_oficial").fetchone()[0]
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    print(f"Marcas nuevas: {despues - antes} · total marcas: {despues} · preguntas marcadas: {distintas}")
    print(f"Integridad: {integridad} · errores FK: {len(fk)}")
    print("RESULTADO:", "OK" if integridad == "ok" and not fk else "REVISAR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
