#!/usr/bin/env python3
"""
Depura TODOS los bancos de convocatoria aplicando exclusivamente dos reglas:

1. Una pregunta JURIDICA cuyo estado_vigencia empiece por OBSOLETA no puede
   permanecer en ningún banco.
2. Una pregunta JURIDICA sin normalización completa tampoco puede permanecer:
   tipo_norma_normalizado, nombre_norma_normalizado, norma_id_normalizada y
   articulo_normalizado son obligatorios.

NO elimina ni modifica registros de lote_preguntas.
NO excluye VIGENTE, NO_VERIFICABLE, REVISAR ni NULL por razón de vigencia.
Modo predeterminado: SOLO REVISIÓN.
Con --aplicar crea copia de seguridad y elimina solo las relaciones de banco.
"""
from __future__ import annotations
import argparse
import csv
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"db"/"oposiciones.sqlite3"
BACK=ROOT/"db"/"copias_seguridad"
REG=ROOT/"registros"

def vacio(v):
    return v is None or str(v).strip()==""

def es_obsoleta(v):
    return str(v or "").strip().upper().startswith("OBSOLETA")

def motivo(r):
    if es_obsoleta(r["estado_vigencia"]):
        return "OBSOLETA"
    faltan=[]
    if vacio(r["tipo_norma_normalizado"]): faltan.append("tipo_norma_normalizado")
    if vacio(r["nombre_norma_normalizado"]): faltan.append("nombre_norma_normalizado")
    if r["norma_id_normalizada"] is None: faltan.append("norma_id_normalizada")
    if vacio(r["articulo_normalizado"]): faltan.append("articulo_normalizado")
    if faltan:
        return "NORMALIZACION_INCOMPLETA:" + ",".join(faltan)
    return ""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",type=Path,default=DB)
    ap.add_argument("--aplicar",action="store_true")
    args=ap.parse_args()
    db=args.db.expanduser().resolve()
    con=sqlite3.connect(db)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        filas=con.execute("""
            SELECT bp.id AS banco_pregunta_id,bp.convocatoria_id,bp.pregunta_id,
                   c.codigo AS convocatoria_codigo,
                   lp.nombre_norma,lp.articulo,lp.estado_vigencia,
                   lp.tipo_norma_normalizado,lp.nombre_norma_normalizado,
                   lp.norma_id_normalizada,lp.articulo_normalizado
            FROM banco_preguntas bp
            JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
            LEFT JOIN convocatorias c ON c.id=bp.convocatoria_id
            WHERE UPPER(TRIM(COALESCE(lp.tipo_clasificacion,'')))='JURIDICA'
            ORDER BY bp.convocatoria_id,bp.id
        """).fetchall()

        bajas=[]
        for r in filas:
            m=motivo(r)
            if m:
                d=dict(r); d["motivo_baja"]=m; bajas.append(d)

        obs=sum(1 for r in bajas if r["motivo_baja"]=="OBSOLETA")
        inc=len(bajas)-obs
        REG.mkdir(parents=True,exist_ok=True)
        csvp=REG/"bajas_bancos_vigencia_normalizacion.csv"
        with csvp.open("w",encoding="utf-8-sig",newline="") as f:
            campos=list(bajas[0].keys()) if bajas else [
                "banco_pregunta_id","convocatoria_id","pregunta_id","motivo_baja"
            ]
            w=csv.DictWriter(f,fieldnames=campos,delimiter=";",quoting=csv.QUOTE_ALL)
            w.writeheader(); w.writerows(bajas)

        print("="*78)
        print("DEPURACIÓN DE BANCOS: VIGENCIA Y NORMALIZACIÓN")
        print("="*78)
        print(f"Modo................................. {'APLICAR' if args.aplicar else 'SOLO REVISIÓN'}")
        print(f"Jurídicas actualmente en bancos...... {len(filas)}")
        print(f"Bajas por estado OBSOLETA............ {obs}")
        print(f"Bajas por normalización incompleta... {inc}")
        print(f"Total bajas.......................... {len(bajas)}")
        print(f"Detalle CSV.......................... {csvp}")

        if not args.aplicar:
            print("La base no se ha modificado.")
            return 0

        BACK.mkdir(parents=True,exist_ok=True)
        marca=datetime.now().strftime("%Y%m%d_%H%M%S")
        copia=BACK/f"{db.stem}_antes_depurar_bancos_{marca}{db.suffix}"
        shutil.copy2(db,copia)
        print(f"Copia de seguridad................... {copia}")

        con.execute("BEGIN IMMEDIATE")
        borradas=0
        for r in bajas:
            bid=int(r["banco_pregunta_id"])
            con.execute("DELETE FROM banco_preguntas_temas WHERE banco_pregunta_id=?",(bid,))
            cur=con.execute("DELETE FROM banco_preguntas WHERE id=?",(bid,))
            borradas+=cur.rowcount

        integrity=con.execute("PRAGMA integrity_check").fetchone()[0]
        fk=con.execute("PRAGMA foreign_key_check").fetchall()
        if integrity!="ok" or fk:
            raise RuntimeError(f"Validación fallida: integrity={integrity}, fk={len(fk)}")
        con.commit()

        restantes=int(con.execute("""
            SELECT COUNT(*)
            FROM banco_preguntas bp
            JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
            WHERE UPPER(TRIM(COALESCE(lp.tipo_clasificacion,'')))='JURIDICA'
              AND (
                UPPER(TRIM(COALESCE(lp.estado_vigencia,''))) LIKE 'OBSOLETA%'
                OR lp.tipo_norma_normalizado IS NULL OR TRIM(lp.tipo_norma_normalizado)=''
                OR lp.nombre_norma_normalizado IS NULL OR TRIM(lp.nombre_norma_normalizado)=''
                OR lp.norma_id_normalizada IS NULL
                OR lp.articulo_normalizado IS NULL OR TRIM(lp.articulo_normalizado)=''
              )
        """).fetchone()[0])
        print(f"Bajas aplicadas....................... {borradas}")
        print(f"Casos prohibidos restantes en bancos. {restantes}")
        print(f"Integridad SQLite.................... {integrity}")
        print(f"Foreign key errors................... {len(fk)}")
        return 0
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

if __name__=="__main__":
    raise SystemExit(main())
