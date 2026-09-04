#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Añade la norma normalizada al enunciado de preguntas que:
- citan explícitamente un artículo;
- no contienen identificación reconocible de ninguna norma;
- tienen nombre_norma_normalizado y articulo_normalizado en metadata.

No cambia opciones, respuesta ni metadata.
Por defecto SOLO LECTURA. --aplicar crea backup.
"""

import argparse
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

PAT_ART = re.compile(r"\b(?:art[ií]culo|art\.)\s*(\d+(?:\.\d+)*)", re.I)
PAT_TIPO = re.compile(
    r"\b(?:constituci[oó]n|estatuto|ley(?:\s+org[aá]nica)?|decreto(?:-ley)?|"
    r"real\s+decreto|orden|reglamento|directiva|tratado|resoluci[oó]n)\b", re.I
)
PAT_NUM = re.compile(r"\b\d{1,4}\s*/\s*\d{2,4}\b")
PAT_ALIAS = re.compile(
    r"\b(?:CE|LPAC(?:AP)?|LCSP(?:-17)?|TFUE|LRJSP|LFPV|EACV|LJCA|TREBEB|"
    r"EBEP|EBP|TUE|LOPJ|LOTC|LOFCA|LGS|LGT|LRBRL)\b", re.I
)

def candidatos(con):
    filas = con.execute("""
        SELECT id, enunciado, nombre_norma_normalizado, articulo_normalizado
        FROM lote_preguntas
        ORDER BY id
    """).fetchall()
    out = []
    for qid, enun, norma, art_meta in filas:
        enun = enun or ""
        if not PAT_ART.search(enun):
            continue
        if PAT_TIPO.search(enun) or PAT_NUM.search(enun) or PAT_ALIAS.search(enun):
            continue
        if not (norma or "").strip() or not str(art_meta or "").strip():
            continue
        out.append((qid, enun, str(norma).strip(), str(art_meta).strip()))
    return out

def nuevo_enunciado(enun, norma):
    # Inserta la norma tras la PRIMERA referencia explícita al artículo,
    # conservando intacto el resto del enunciado.
    m = PAT_ART.search(enun)
    if not m:
        raise RuntimeError("No se encontró artículo al construir el nuevo enunciado.")
    return enun[:m.end()] + f" de {norma}" + enun[m.end():]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bd", required=True)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    db = Path(args.bd).resolve()
    if not db.exists():
        raise SystemExit(f"ERROR: no existe {db}")

    con = sqlite3.connect(str(db))
    try:
        cand = candidatos(con)

        # Bloqueo de seguridad ligado a la auditoría acordada.
        if len(cand) != 49:
            raise SystemExit(
                f"SEGURIDAD: esperaba exactamente 49 candidatas y encontré {len(cand)}. "
                "No se modifica nada."
            )

        cambios = [(qid, enun, nuevo_enunciado(enun, norma), norma, art)
                   for qid, enun, norma, art in cand]

        if any(antes == despues for _, antes, despues, _, _ in cambios):
            raise SystemExit("SEGURIDAD: hay un candidato sin cambio efectivo.")

        print("=" * 78)
        print("AÑADIR NORMA A ENUNCIADOS QUE CITAN ARTÍCULO SIN NORMA")
        print("=" * 78)
        print(f"Candidatas.................................. {len(cambios)}")
        print(f"Modo........................................ {'ESCRITURA' if args.aplicar else 'SOLO LECTURA'}")
        print("Campos que se modificarán................... SOLO enunciado")
        print("Opciones/respuesta/metadata.................. SIN CAMBIOS")

        if not args.aplicar:
            print("\nNo se ha modificado la base de datos.")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db.with_name(db.stem + f"_backup_norma_enunciado_{stamp}" + db.suffix)
        shutil.copy2(db, backup)
        print(f"\nBackup: {backup}")

        # Snapshot de todos los demás campos para verificar después.
        cols = [r[1] for r in con.execute("PRAGMA table_info(lote_preguntas)")]
        otras = [c for c in cols if c != "enunciado"]
        colsql = ",".join(f'"{c}"' for c in otras)
        antes_otros = {
            qid: tuple(con.execute(
                f'SELECT {colsql} FROM lote_preguntas WHERE id=?', (qid,)
            ).fetchone())
            for qid, *_ in cambios
        }

        con.execute("BEGIN IMMEDIATE")
        try:
            for qid, antes, despues, norma, art in cambios:
                cur = con.execute(
                    "UPDATE lote_preguntas SET enunciado=? WHERE id=? AND enunciado=?",
                    (despues, qid, antes)
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"ID {qid}: actualización no unívoca.")

            # Verificación antes del COMMIT.
            for qid, antes, despues, norma, art in cambios:
                row = con.execute(
                    f'SELECT enunciado,{colsql} FROM lote_preguntas WHERE id=?', (qid,)
                ).fetchone()
                if row is None or row[0] != despues:
                    raise RuntimeError(f"ID {qid}: enunciado no coincide.")
                if tuple(row[1:]) != antes_otros[qid]:
                    raise RuntimeError(f"ID {qid}: cambió algún campo distinto de enunciado.")

            con.commit()
        except Exception:
            con.rollback()
            raise

        print(f"\nEnunciados modificados....................... {len(cambios)}")
        print("Verificación otros campos.................... OK")
        print("COMMIT........................................ OK")
        print("banco_preguntas.............................. SIN MODIFICAR")

    finally:
        con.close()

if __name__ == "__main__":
    main()
