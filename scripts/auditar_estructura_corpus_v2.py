"""
Constructor Temario V2 - auditoria de estructura real del corpus normativo.

SOLO LECTURA. No propone ni acepta normas o articulos.

Objetivo: determinar que informacion estructural existe realmente en articulos_fuente
y si el orden almacenado permite reconstruir titulos/capitulos/secciones o, al menos,
la secuencia juridica de articulos. Esta auditoria precede a cualquier nuevo algoritmo.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

import comun

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"

PATRON_ESTRUCTURA = re.compile(
    r"\b(t[ií]tulo|cap[ií]tulo|secci[oó]n|subsecci[oó]n|libro|parte)\b",
    re.I,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audita estructura disponible en articulos_fuente.")
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--norma-id", type=int, action="append", help="Limita a normas concretas; repetible.")
    ap.add_argument("--muestras", type=int, default=12)
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    muestras = max(1, args.muestras)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        columnas = con.execute("PRAGMA table_info(articulos_fuente)").fetchall()
        print("=" * 78)
        print("CONSTRUCTOR TEMARIO V2 - AUDITORIA ESTRUCTURA CORPUS - SOLO LECTURA")
        print("=" * 78)
        print(f"BD............................... {db}")
        print()
        print("COLUMNAS articulos_fuente:")
        for c in columnas:
            print(f"  {c['name']:<28} {c['type'] or '-'}")
        print()

        where = ""
        params = []
        if args.norma_id:
            marcas = ",".join("?" for _ in args.norma_id)
            where = f"WHERE n.id IN ({marcas})"
            params = list(dict.fromkeys(args.norma_id))

        filas = con.execute(
            f"""
            SELECT n.id norma_id, n.nombre_canonico, af.id articulo_fuente_id,
                   af.id_boe id_fuente, af.articulo_boe articulo,
                   af.titulo_bloque, af.texto
            FROM normas n
            JOIN norma_fuentes nf ON nf.norma_id=n.id
            JOIN articulos_fuente af ON af.id_boe=nf.id_fuente
            {where}
            ORDER BY n.id, af.id_boe, af.id
            """,
            tuple(params),
        ).fetchall()

    por_fuente = defaultdict(list)
    for r in filas:
        por_fuente[(int(r["norma_id"]), str(r["nombre_canonico"] or ""), str(r["id_fuente"] or ""))].append(r)

    print(f"Fuentes analizadas............... {len(por_fuente)}")
    print(f"Filas analizadas................. {len(filas)}")
    print()

    for (nid, nombre, fuente), arts in por_fuente.items():
        titulos = [str(a["titulo_bloque"] or "").strip() for a in arts]
        no_vacios = [t for t in titulos if t]
        estructurales = [t for t in no_vacios if PATRON_ESTRUCTURA.search(t)]
        distintos = list(dict.fromkeys(no_vacios))
        print("-" * 78)
        print(f"NORMA [{nid}] {nombre}")
        print(f"FUENTE {fuente}")
        print(f"Articulos........................ {len(arts)}")
        print(f"titulo_bloque no vacio........... {len(no_vacios)}/{len(arts)}")
        print(f"titulo_bloque distintos.......... {len(distintos)}")
        print(f"Con marcador estructural......... {len(estructurales)}/{len(no_vacios) if no_vacios else 0}")
        print("Primeras filas en ORDEN DE ALMACENAMIENTO:")
        for a in arts[:muestras]:
            art = str(a["articulo"] or "").strip()
            tb = str(a["titulo_bloque"] or "").strip()
            print(f"  id={a['articulo_fuente_id']:<6} art={art:<18} bloque={tb[:80]}")
        if distintos:
            print("Muestra titulo_bloque distintos:")
            for t in distintos[:muestras]:
                print(f"  {t[:120]}")
        print()

    print("Conclusiones automáticas limitadas:")
    print("  Esta salida describe los datos existentes; NO presupone que titulo_bloque sea jerarquia legal.")
    print("  El orden mostrado es el orden de almacenamiento por fuente; debe verificarse antes de usarlo como orden juridico.")
    print("  No se ha consultado temario_referencias.")
    print("  La base de datos NO ha sido modificada.")
    print("  El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
