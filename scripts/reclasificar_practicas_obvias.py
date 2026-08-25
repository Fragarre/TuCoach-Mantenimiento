from __future__ import annotations
import argparse, sqlite3, shutil
from datetime import datetime
from pathlib import Path

IDS = (10387, 10391, 9079, 17452, 8419, 13082, 3610, 3222, 11843, 13081, 13221, 17451, 15873, 5247, 5458, 16520, 12669, 1667, 10372, 8631, 10365, 5750, 5590, 14524, 16753, 5536, 10761, 11972, 1272, 6356, 17518, 13152, 1791, 2607, 8733, 7369, 10759, 9994, 4537, 15744, 9120, 7041, 4465, 13360, 11758, 5221, 14491, 5178)

def main():
    p=argparse.ArgumentParser(description="Reclasifica a TEORICA solo 48 preguntas inequívocamente teóricas revisadas.")
    p.add_argument("--db", default=str(Path(__file__).resolve().parents[1]/"db"/"oposiciones.sqlite3"))
    p.add_argument("--aplicar", action="store_true")
    a=p.parse_args()
    db=Path(a.db).resolve()
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row
    marks=",".join("?" for _ in IDS)
    rows=con.execute(f"""
        SELECT id, teorica_practica, tipo_fuente, origen_oposicion, enunciado
        FROM lote_preguntas WHERE id IN ({marks}) ORDER BY id
    """, IDS).fetchall()
    presentes={int(r["id"]) for r in rows}
    faltan=sorted(set(IDS)-presentes)
    candidatos=[r for r in rows if (r["teorica_practica"] or "").strip().upper()=="PRACTICA"]
    print("="*78)
    print("RECLASIFICACIÓN OBVIA PRACTICA -> TEORICA")
    print("="*78)
    print(f"IDs revisados: {len(IDS)}")
    print(f"Presentes en BD: {len(rows)}")
    print(f"Aún marcados PRACTICA: {len(candidatos)}")
    print(f"IDs ausentes: {len(faltan)}")
    print("Modo:", "APLICAR" if a.aplicar else "SOLO REVISIÓN")
    if faltan: print("AVISO IDs ausentes:", ",".join(map(str,faltan)))
    if not a.aplicar:
        print("\nNo se ha modificado la base de datos.")
        return
    if faltan:
        raise SystemExit("ERROR: faltan IDs esperados; no se aplica ningún cambio.")
    backup=db.with_name(f"{db.stem}_backup_antes_reclasificar_obvias_{datetime.now():%Y%m%d_%H%M%S}{db.suffix}")
    shutil.copy2(db,backup)
    con.execute("BEGIN IMMEDIATE")
    con.executemany(
        "UPDATE lote_preguntas SET teorica_practica='TEORICA' WHERE id=? AND UPPER(TRIM(COALESCE(teorica_practica,'')))='PRACTICA'",
        [(int(r["id"]),) for r in candidatos],
    )
    con.commit()
    restantes=con.execute(f"SELECT COUNT(*) FROM lote_preguntas WHERE id IN ({marks}) AND UPPER(TRIM(COALESCE(teorica_practica,'')))='PRACTICA'", IDS).fetchone()[0]
    print(f"\nActualizadas: {len(candidatos)}")
    print(f"Restantes PRACTICA entre estos IDs: {restantes}")
    print("Backup:", backup)
    if restantes:
        raise SystemExit("ERROR: verificación final incorrecta.")
    print("ESTADO: CORRECTO")

if __name__=="__main__":
    main()
