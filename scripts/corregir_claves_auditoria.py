"""Corrige en lote la respuesta correcta de preguntas auditadas.

Uso:
    python scripts/corregir_claves_auditoria.py --casos auditorias/correcciones_claves_20261015.json
    python scripts/corregir_claves_auditoria.py --casos ... --aplicar

Sin ``--aplicar`` abre la base en solo lectura y muestra la vista previa.
Con ``--aplicar``:
  1. exige decision_usuario=true en el fichero de casos;
  2. aborta si alguna pregunta no existe, ya está excluida o su clave actual
     no coincide con ``clave_anterior`` (alguien la cambió después de auditar);
  3. crea una copia de seguridad verificada de la base;
  4. actualiza todas las claves en una sola transacción
     (UPDATE ... WHERE id=? AND respuesta_correcta=?);
  5. deja cada cambio en registros/correcciones_claves_auditoria.csv.

No modifica bancos ni enunciados/opciones. Para que la web refleje los
cambios hay que publicar después (menú 5).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
REGISTRO = RAIZ / "registros" / "correcciones_claves_auditoria.csv"
CLAVES = {"A", "B", "C", "D"}


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def cargar(ruta: Path) -> tuple[dict, list[dict]]:
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    casos = doc.get("casos")
    if not isinstance(casos, list) or not casos:
        raise SystemExit("ERROR: el fichero no contiene casos.")
    vistos: set[int] = set()
    salida = []
    for c in casos:
        pid = int(c["pregunta_id"])
        ant = str(c["clave_anterior"]).strip().upper()
        nue = str(c["clave_nueva"]).strip().upper()
        mot = str(c.get("motivo") or "").strip()
        if pid in vistos:
            raise SystemExit(f"ERROR: ID duplicado {pid}.")
        if ant not in CLAVES or nue not in CLAVES or ant == nue:
            raise SystemExit(f"ERROR: claves no válidas en {pid}: {ant} -> {nue}.")
        if not mot:
            raise SystemExit(f"ERROR: falta motivo en {pid}.")
        vistos.add(pid)
        salida.append({"pregunta_id": pid, "clave_anterior": ant,
                       "clave_nueva": nue, "motivo": mot,
                       "origen": str(c.get("origen") or doc.get("origen_auditoria") or "")})
    return doc, salida


def inspeccionar(db: Path, casos: list[dict]) -> dict:
    ids = [c["pregunta_id"] for c in casos]
    ph = ",".join("?" * len(ids))
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        actual = {int(i): str(r).strip().upper() for i, r in con.execute(
            f"SELECT id, respuesta_correcta FROM lote_preguntas WHERE id IN ({ph})", ids)}
        excluidas: set[int] = set()
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='preguntas_exclusiones'").fetchone():
            excluidas = {int(r[0]) for r in con.execute(
                f"SELECT pregunta_id FROM preguntas_exclusiones WHERE pregunta_id IN ({ph}) "
                "AND estado IN ('CUARENTENA','RETIRADA')", ids)}
        vinculos = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT convocatoria_id) FROM banco_preguntas "
            f"WHERE pregunta_id IN ({ph}) AND estado='INCLUIDA'", ids).fetchone()
    finally:
        con.close()
    return {
        "faltan": [i for i in ids if i not in actual],
        "distintas": [c["pregunta_id"] for c in casos
                      if c["pregunta_id"] in actual
                      and actual[c["pregunta_id"]] != c["clave_anterior"]],
        "excluidas": sorted(excluidas),
        "vinculos": vinculos[0], "convocatorias": vinculos[1],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    ap.add_argument("--casos", type=Path, required=True)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()
    db = args.db.resolve()
    doc, casos = cargar(args.casos.resolve())
    imp = inspeccionar(db, casos)

    print("=" * 78)
    print("CORRECCIÓN DE CLAVES AUDITADAS — " + ("APLICAR" if args.aplicar else "VISTA PREVIA"))
    print("=" * 78)
    print(f"Base.................. {db}")
    print(f"Casos................. {len(casos)}")
    print(f"Vínculos de banco..... {imp['vinculos']} en {imp['convocatorias']} convocatorias")
    print(f"No existen............ {len(imp['faltan'])} {imp['faltan'][:10]}")
    print(f"Clave ya cambiada..... {len(imp['distintas'])} {imp['distintas'][:10]}")
    print(f"Excluidas (cuarent.).. {len(imp['excluidas'])} {imp['excluidas'][:10]}")
    print()
    for c in casos[:15]:
        print(f"  {c['pregunta_id']:>6}  {c['clave_anterior']} -> {c['clave_nueva']}  {c['motivo'][:60]}")
    if len(casos) > 15:
        print(f"  ... y {len(casos) - 15} más")

    bloqueo = imp["faltan"] or imp["distintas"] or imp["excluidas"]
    if not args.aplicar:
        print("\nVISTA PREVIA: no se ha modificado nada."
              + (" HAY INCIDENCIAS: revíselas antes de aplicar." if bloqueo else
                 " Añada --aplicar para corregir."))
        return 1 if bloqueo else 0

    if doc.get("decision_usuario") is not True:
        print("ABORTADO: el fichero no tiene decision_usuario=true.")
        return 2
    if bloqueo:
        print("ABORTADO: hay preguntas ausentes, excluidas o con la clave ya cambiada.")
        return 2

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"{db.stem}_backup_antes_corregir_claves_{marca}{db.suffix}")
    shutil.copy2(db, backup)
    if sha256(backup) != sha256(db):
        print("ABORTADO: la copia de seguridad no coincide con la base.")
        return 2

    con = sqlite3.connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        for c in casos:
            cur = con.execute(
                "UPDATE lote_preguntas SET respuesta_correcta=? "
                "WHERE id=? AND respuesta_correcta=?",
                (c["clave_nueva"], c["pregunta_id"], c["clave_anterior"]))
            if cur.rowcount != 1:
                raise RuntimeError(f"No se pudo actualizar {c['pregunta_id']}.")
        con.commit()
    except Exception as exc:
        con.rollback()
        print(f"ERROR: {exc}. No se ha modificado nada (rollback).")
        return 2
    finally:
        con.close()

    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not REGISTRO.exists()
    ahora = datetime.now().isoformat(timespec="seconds")
    with REGISTRO.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        if nuevo:
            w.writerow(["fecha_hora", "pregunta_id", "clave_anterior", "clave_nueva",
                        "origen", "motivo", "fichero_casos", "backup"])
        for c in casos:
            w.writerow([ahora, c["pregunta_id"], c["clave_anterior"], c["clave_nueva"],
                        c["origen"], c["motivo"], str(args.casos), backup.name])

    print(f"\nAPLICADO: {len(casos)} claves corregidas.")
    print(f"Copia de seguridad: {backup}")
    print(f"Registro: {REGISTRO}")
    print("Recuerde publicar (menú 5) para que la web refleje las claves corregidas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
