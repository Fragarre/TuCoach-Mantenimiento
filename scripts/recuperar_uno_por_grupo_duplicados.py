#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RECUPERACIÓN CONSERVADORA DE GRUPOS DE DUPLICADOS

Objetivo:
- La BD actual procede de una depuración que eliminó TODOS los miembros de cada
  grupo de duplicados/casi duplicados.
- Este script recupera UN solo registro por grupo cuando actualmente no queda
  ninguno.
- Si un grupo ya tiene un miembro en la BD actual, no hace nada.
- Si todos los miembros eliminados de un grupo tienen además una incidencia de
  autosuficiencia no reparada, no recupera ninguno.
- La procedencia/origen NO interviene en la elección.
- Entre candidatos equivalentes se conserva el ID menor, de forma determinista.

NO modifica bancos ni otras tablas. Solo puede insertar filas previamente
existentes en lote_preguntas, copiándolas de la BD anterior.

Por defecto funciona en SOLO LECTURA.
Para escribir hay que usar --aplicar. Antes de escribir crea backup.
"""

import argparse
import csv
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def leer_ids_autosuficiencia(path):
    ids = set()
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter=";")
        if "id" not in (rd.fieldnames or []):
            raise RuntimeError("El CSV de autosuficiencia no contiene columna 'id'.")
        for r in rd:
            ids.add(int(r["id"]))
    return ids


def leer_grafo_duplicados(path, umbral=0.94):
    adj = defaultdict(set)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter=";")
        requeridas = {"id_a", "id_b", "similitud"}
        if not requeridas.issubset(set(rd.fieldnames or [])):
            raise RuntimeError(
                f"El CSV de duplicados debe contener {sorted(requeridas)}."
            )
        for r in rd:
            sim = float(r["similitud"])
            if sim < umbral:
                continue
            a = int(r["id_a"])
            b = int(r["id_b"])
            if a == b:
                continue
            adj[a].add(b)
            adj[b].add(a)
    return adj


def componentes(adj):
    vistos = set()
    salida = []
    for inicio in sorted(adj):
        if inicio in vistos:
            continue
        pila = [inicio]
        vistos.add(inicio)
        comp = []
        while pila:
            x = pila.pop()
            comp.append(x)
            for y in adj[x]:
                if y not in vistos:
                    vistos.add(y)
                    pila.append(y)
        salida.append(sorted(comp))
    return salida


def columnas_tabla(con, tabla):
    return [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bd-actual", required=True)
    ap.add_argument("--bd-anterior", required=True)
    ap.add_argument("--duplicados", required=True)
    ap.add_argument("--autosuficiencia", required=True)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    actual = Path(args.bd_actual).resolve()
    anterior = Path(args.bd_anterior).resolve()
    csv_dup = Path(args.duplicados).resolve()
    csv_auto = Path(args.autosuficiencia).resolve()

    for p in (actual, anterior, csv_dup, csv_auto):
        if not p.exists():
            raise SystemExit(f"ERROR: no existe: {p}")

    autosuf = leer_ids_autosuficiencia(csv_auto)
    adj = leer_grafo_duplicados(csv_dup, 0.94)
    comps = componentes(adj)

    con_act = sqlite3.connect(str(actual))
    con_ant = sqlite3.connect(str(anterior))
    con_act.row_factory = sqlite3.Row
    con_ant.row_factory = sqlite3.Row

    try:
        cols_act = columnas_tabla(con_act, "lote_preguntas")
        cols_ant = columnas_tabla(con_ant, "lote_preguntas")
        if cols_act != cols_ant:
            raise RuntimeError(
                "La estructura de lote_preguntas no coincide entre ambas BD."
            )

        ids_actuales = {
            int(r[0]) for r in con_act.execute("SELECT id FROM lote_preguntas")
        }
        ids_anteriores = {
            int(r[0]) for r in con_ant.execute("SELECT id FROM lote_preguntas")
        }

        recuperar = []
        grupos_con_superviviente = 0
        grupos_todos_autosuf = 0

        for comp in comps:
            supervivientes = [x for x in comp if x in ids_actuales]
            if supervivientes:
                grupos_con_superviviente += 1
                continue

            # Solo pueden recuperarse IDs que existían en la BD anterior.
            ausentes = [x for x in comp if x in ids_anteriores]
            limpios = [x for x in ausentes if x not in autosuf]

            if not limpios:
                grupos_todos_autosuf += 1
                continue

            # Regla determinista. No se usa origen/procedencia.
            recuperar.append(min(limpios))

        print("=" * 78)
        print("RECUPERACIÓN CONSERVADORA DE DUPLICADOS")
        print("=" * 78)
        print(f"Grupos de duplicados/casi duplicados..... {len(comps)}")
        print(f"Grupos que ya conservan un miembro....... {grupos_con_superviviente}")
        print(f"Grupos sin miembro, recuperables.......... {len(recuperar)}")
        print(f"Grupos no recuperados por autosuficiencia. {grupos_todos_autosuf}")
        print(f"Filas a recuperar en lote_preguntas....... {len(recuperar)}")
        print()
        print("Procedencia/origen usada para elegir....... NO")
        print("Tablas distintas de lote_preguntas......... NO")
        print("Modo........................................ "
              + ("ESCRITURA" if args.aplicar else "SOLO LECTURA"))

        if not args.aplicar:
            print()
            print("No se ha modificado la base de datos.")
            print("Para aplicar, repetir exactamente el comando añadiendo --aplicar.")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = actual.with_name(actual.stem + f"_backup_duplicados_{stamp}" + actual.suffix)
        shutil.copy2(actual, backup)
        print(f"\nBackup: {backup}")

        placeholders = ",".join("?" for _ in cols_act)
        colsql = ",".join(f'"{c}"' for c in cols_act)
        insert_sql = f'INSERT INTO lote_preguntas ({colsql}) VALUES ({placeholders})'

        con_act.execute("BEGIN IMMEDIATE")
        insertadas = 0
        try:
            for qid in recuperar:
                row = con_ant.execute(
                    f'SELECT {colsql} FROM lote_preguntas WHERE id=?', (qid,)
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"ID {qid}: no existe en la BD anterior.")
                con_act.execute(insert_sql, tuple(row[c] for c in cols_act))
                insertadas += 1

            # Verificación antes del COMMIT.
            faltan = []
            for qid in recuperar:
                existe = con_act.execute(
                    "SELECT 1 FROM lote_preguntas WHERE id=?", (qid,)
                ).fetchone()
                if not existe:
                    faltan.append(qid)
            if faltan:
                raise RuntimeError(f"Fallo de verificación. IDs no insertados: {faltan[:20]}")

            con_act.commit()
        except Exception:
            con_act.rollback()
            raise

        total = con_act.execute("SELECT COUNT(*) FROM lote_preguntas").fetchone()[0]
        print()
        print(f"Insertadas................................. {insertadas}")
        print(f"Total lote_preguntas después............... {total}")
        print("COMMIT...................................... OK")
        print()
        print("IMPORTANTE: banco_preguntas no se ha modificado.")
        print("La sincronización de bancos debe hacerse después con el procedimiento")
        print("consolidado del proyecto.")

    finally:
        con_act.close()
        con_ant.close()


if __name__ == "__main__":
    main()
