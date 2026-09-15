"""Compara un temario.csv aprobado con el temario actual de una convocatoria.

Solo lectura: no modifica la BD ni el CSV. Informa de altas y bajas previstas
con las mismas claves normalizadas que usa importar_temario.py.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from importar_temario import claves_presentes, leer_csv


def main() -> int:
    p = argparse.ArgumentParser(description="Revisa el delta CSV/BD de un temario sin modificar datos.")
    p.add_argument("--codigo", required=True)
    p.add_argument("--csv", required=True)
    p.add_argument("--db", required=True)
    args = p.parse_args()

    db = Path(args.db).expanduser().resolve()
    csv = Path(args.csv).expanduser().resolve()
    filas = leer_csv(csv)
    temas_csv, refs_csv, equiv_csv = claves_presentes(filas)

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        conv = con.execute("SELECT id FROM convocatorias WHERE codigo=?", (args.codigo,)).fetchone()
        if conv is None:
            raise RuntimeError(f"No existe la convocatoria {args.codigo}.")
        temarios = con.execute("SELECT id FROM temarios WHERE convocatoria_id=?", (conv["id"],)).fetchall()
        if len(temarios) != 1:
            raise RuntimeError(f"La convocatoria debe tener exactamente un temario; encontrados: {len(temarios)}.")
        temario_id = int(temarios[0]["id"])

        temas_db = {
            (str(r["parte"]), int(r["numero_tema"]))
            for r in con.execute("SELECT parte, numero_tema FROM temario_temas WHERE temario_id=?", (temario_id,))
        }
        refs_db = {
            (str(r["parte"]), int(r["numero_tema"]), str(r["nombre_norma_normalizada"]), str(r["articulo_solicitado"]))
            for r in con.execute(
                """SELECT t.parte, t.numero_tema, r.nombre_norma_normalizada, r.articulo_solicitado
                   FROM temario_referencias r JOIN temario_temas t ON t.id=r.tema_id
                   WHERE t.temario_id=?""", (temario_id,)
            )
        }
        equiv_db = {
            (str(r["parte"]), int(r["numero_tema"]), str(r["tema_no_juridico"]))
            for r in con.execute(
                """SELECT t.parte, t.numero_tema, e.tema_no_juridico
                   FROM equivalencias_temas_no_juridicos e JOIN temario_temas t ON t.id=e.tema_id
                   WHERE t.temario_id=?""", (temario_id,)
            )
        }

    altas_refs = sorted(refs_csv - refs_db)
    bajas_refs = sorted(refs_db - refs_csv)
    altas_equiv = sorted(equiv_csv - equiv_db)
    bajas_equiv = sorted(equiv_db - equiv_csv)
    altas_temas = sorted(temas_csv - temas_db)
    bajas_temas = sorted(temas_db - temas_csv)

    print("\n" + "=" * 78)
    print("REVISIÓN DE CAMBIOS DEL TEMARIO - SOLO LECTURA")
    print("=" * 78)
    print(f"Convocatoria: {args.codigo}")
    print(f"Referencias jurídicas: CSV {len(refs_csv)} | BD {len(refs_db)}")
    print(f"  Altas previstas: {len(altas_refs)}")
    print(f"  Bajas previstas: {len(bajas_refs)}")
    print(f"Equivalencias no jurídicas: altas {len(altas_equiv)} | bajas {len(bajas_equiv)}")
    print(f"Temas: altas {len(altas_temas)} | bajas {len(bajas_temas)}")

    if altas_refs:
        print("\nALTAS JURÍDICAS PREVISTAS")
        for parte, tema, norma, articulo in altas_refs:
            print(f"  + {parte} tema {tema} | {norma} | art. {articulo}")
    if bajas_refs:
        print("\nBAJAS JURÍDICAS PREVISTAS")
        for parte, tema, norma, articulo in bajas_refs:
            print(f"  - {parte} tema {tema} | {norma} | art. {articulo}")

    total = sum(map(len, (altas_refs, bajas_refs, altas_equiv, bajas_equiv, altas_temas, bajas_temas)))
    print(f"\nCambios estructurales previstos: {total}")
    print("BD modificada: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
