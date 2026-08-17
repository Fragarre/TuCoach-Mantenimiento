#!/usr/bin/env python3
"""
Valida que todas las preguntas JURIDICA aptas para bancos tengan completa la
normalización exigida. SOLO LECTURA.

Requisitos:
- tipo_norma_normalizado
- nombre_norma_normalizado
- norma_id_normalizada
- articulo_normalizado

Las preguntas incompletas permanecen en lote_preguntas, pero son rechazadas
para cualquier banco de convocatoria.
"""
from __future__ import annotations
import argparse
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "db" / "oposiciones.sqlite3"
REG = ROOT / "registros"

def vacio(v):
    return v is None or str(v).strip() == ""

def motivo(r):
    faltan=[]
    if vacio(r["tipo_norma_normalizado"]): faltan.append("tipo_norma_normalizado")
    if vacio(r["nombre_norma_normalizado"]): faltan.append("nombre_norma_normalizado")
    if r["norma_id_normalizada"] is None: faltan.append("norma_id_normalizada")
    if vacio(r["articulo_normalizado"]): faltan.append("articulo_normalizado")
    return ",".join(faltan)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB)
    args=ap.parse_args()
    db=args.db.expanduser().resolve()
    con=sqlite3.connect(db)
    con.row_factory=sqlite3.Row
    try:
        filas=con.execute("""
            SELECT id,nombre_norma,articulo,tipo_norma_normalizado,
                   nombre_norma_normalizado,norma_id_normalizada,
                   articulo_normalizado,estado_vigencia
            FROM lote_preguntas
            WHERE UPPER(TRIM(COALESCE(tipo_clasificacion,'')))='JURIDICA'
            ORDER BY id
        """).fetchall()
        malas=[]
        obsoletas=0
        for r in filas:
            if str(r["estado_vigencia"] or "").strip().upper().startswith("OBSOLETA"):
                obsoletas+=1
            m=motivo(r)
            if m:
                d=dict(r); d["campos_faltantes"]=m; malas.append(d)

        en_bancos=0
        ids={int(x["id"]) for x in malas}
        if ids:
            marcas=",".join("?" for _ in ids)
            en_bancos=int(con.execute(
                f"SELECT COUNT(DISTINCT pregunta_id) FROM banco_preguntas WHERE pregunta_id IN ({marcas})",
                tuple(sorted(ids))
            ).fetchone()[0])

        REG.mkdir(parents=True, exist_ok=True)
        ruta=REG/"juridicas_normalizacion_incompleta.csv"
        with ruta.open("w",encoding="utf-8-sig",newline="") as f:
            campos=list(malas[0].keys()) if malas else [
                "id","nombre_norma","articulo","campos_faltantes"
            ]
            w=csv.DictWriter(f,fieldnames=campos,delimiter=";",quoting=csv.QUOTE_ALL,
                             extrasaction="ignore")
            w.writeheader(); w.writerows(malas)

        print("="*78)
        print("VALIDACIÓN DE NORMALIZACIÓN JURÍDICA")
        print("="*78)
        print("Modo................................. SOLO LECTURA")
        print(f"Jurídicas totales.................... {len(filas)}")
        print(f"Normalización completa............... {len(filas)-len(malas)}")
        print(f"Normalización incompleta/rechazadas.. {len(malas)}")
        print(f"Incompletas actualmente en bancos.... {en_bancos}")
        print(f"Estados obsoletos en lote............ {obsoletas}")
        print(f"CSV pendientes....................... {ruta}")
        print("La base no se ha modificado.")
        return 0
    finally:
        con.close()

if __name__=="__main__":
    raise SystemExit(main())
