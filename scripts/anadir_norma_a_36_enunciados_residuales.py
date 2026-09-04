#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Añade nombre_norma_normalizado al enunciado de los 36 casos residuales
revisados el 02/09/2026. Solo modifica enunciado.
Por defecto SOLO LECTURA. --aplicar crea backup.
"""
import argparse, sqlite3, shutil, re
from pathlib import Path
from datetime import datetime

IDS = [2484, 2556, 3521, 3971, 5206, 7008, 9120, 13933, 15103, 15159, 15498, 15744, 17177, 19220, 19229, 21606, 21607, 21610, 21611, 21612, 21613, 21614, 21616, 21617, 21618, 21620, 21621, 21622, 21623, 21625, 21626, 21627, 21628, 21757, 21904, 21935]
PAT_ART = re.compile(r"\b(?:art[ií]culos?|arts?\.?)\s*\d+(?:\.\d+)*", re.I)

def nuevo(enun, norma):
    m=PAT_ART.search(enun or "")
    if not m:
        raise RuntimeError("No se localiza referencia a artículo.")
    return enun[:m.end()] + " de " + norma.strip() + enun[m.end():]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--bd",required=True)
    ap.add_argument("--aplicar",action="store_true")
    a=ap.parse_args()
    db=Path(a.bd).resolve()
    con=sqlite3.connect(str(db))
    try:
        filas=con.execute(
            "SELECT id,enunciado,nombre_norma_normalizado FROM lote_preguntas "
            "WHERE id IN (" + ",".join("?"*len(IDS)) + ") ORDER BY id", IDS
        ).fetchall()
        if len(filas)!=36:
            raise SystemExit(f"SEGURIDAD: esperaba 36 IDs y existen {len(filas)}. No se hace nada.")
        cambios=[]
        for qid,enun,norma in filas:
            if not (norma or "").strip():
                raise SystemExit(f"SEGURIDAD: ID {qid} sin norma normalizada.")
            cambios.append((qid,enun,nuevo(enun,norma)))
        print("="*78)
        print("AÑADIR NORMA A 36 ENUNCIADOS RESIDUALES")
        print("="*78)
        print(f"Candidatas.................................. {len(cambios)}")
        print(f"Modo........................................ {'ESCRITURA' if a.aplicar else 'SOLO LECTURA'}")
        print("Campo modificado............................ SOLO enunciado")
        if not a.aplicar:
            print("\nNo se ha modificado la base de datos.")
            return
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        backup=db.with_name(db.stem+f"_backup_36_norma_enunciado_{stamp}"+db.suffix)
        shutil.copy2(db,backup)
        print(f"\nBackup: {backup}")
        cols=[r[1] for r in con.execute("PRAGMA table_info(lote_preguntas)")]
        otras=[c for c in cols if c!="enunciado"]
        colsql=",".join(f'"{c}"' for c in otras)
        antes_otros={qid:tuple(con.execute(f"SELECT {colsql} FROM lote_preguntas WHERE id=?",(qid,)).fetchone())
                     for qid,_,_ in cambios}
        con.execute("BEGIN IMMEDIATE")
        try:
            for qid,antes,despues in cambios:
                cur=con.execute("UPDATE lote_preguntas SET enunciado=? WHERE id=? AND enunciado=?",
                                (despues,qid,antes))
                if cur.rowcount!=1: raise RuntimeError(f"ID {qid}: actualización no unívoca.")
            for qid,antes,despues in cambios:
                row=con.execute(f"SELECT enunciado,{colsql} FROM lote_preguntas WHERE id=?",(qid,)).fetchone()
                if row[0]!=despues or tuple(row[1:])!=antes_otros[qid]:
                    raise RuntimeError(f"ID {qid}: verificación fallida.")
            con.commit()
        except Exception:
            con.rollback(); raise
        print(f"\nEnunciados modificados....................... {len(cambios)}")
        print("Verificación otros campos.................... OK")
        print("COMMIT........................................ OK")
        print("banco_preguntas.............................. SIN MODIFICAR")
    finally:
        con.close()
if __name__=="__main__": main()
