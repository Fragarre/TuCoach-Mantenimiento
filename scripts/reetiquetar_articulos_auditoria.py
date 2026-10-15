"""Reetiqueta en lote el artículo de preguntas cuyo artículo asignado no es el que trata.

Uso:
    python scripts/reetiquetar_articulos_auditoria.py --casos auditorias/reetiquetado_articulos_20261015.json
    python scripts/reetiquetar_articulos_auditoria.py --casos ... --aplicar

Cada caso: {pregunta_id, norma_id, articulo_anterior, articulo_nuevo, motivo}.
Solo cambia el artículo dentro de la MISMA norma (no cambia la norma).

Sin ``--aplicar``: solo lectura. Muestra los casos y el impacto previsible en los
bancos (cuántas preguntas quedarían dentro/fuera del temario de sus convocatorias).
Con ``--aplicar``: exige decision_usuario=true, aborta si alguna pregunta no existe,
cambió de norma o de artículo desde la auditoría, o el artículo nuevo no existe en el
corpus de la norma; hace copia de seguridad verificada y actualiza ``articulo`` y
``articulo_normalizado`` en una sola transacción. Registro en
registros/reetiquetado_articulos_auditoria.csv.

Después hay que sincronizar los bancos (menú 3 → 1) y publicar (menú 5).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
REGISTRO = RAIZ / "registros" / "reetiquetado_articulos_auditoria.csv"


def base(valor) -> str:
    t = unicodedata.normalize("NFKD", str(valor or "")).lower()
    t = "".join(c for c in t if not unicodedata.combining(c)).strip()
    t = re.sub(r"^(articulo|art)\.?\s*", "", t)
    m = re.match(r"^(\d+)(?:[\s.\-]*(bis|ter|quater|quinquies))?", t)
    if not m:
        return t
    return m.group(1) + (f" {m.group(2)}" if m.group(2) else "")


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def cargar(ruta: Path) -> tuple[dict, list[dict]]:
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    casos, vistos = [], set()
    for c in doc.get("casos") or []:
        pid = int(c["pregunta_id"])
        if pid in vistos:
            raise SystemExit(f"ERROR: ID duplicado {pid}.")
        vistos.add(pid)
        nuevo = str(c["articulo_nuevo"]).strip()
        if not nuevo or base(nuevo) == base(c.get("articulo_anterior")):
            raise SystemExit(f"ERROR: artículo nuevo no válido en {pid}.")
        casos.append({"pregunta_id": pid, "norma_id": int(c["norma_id"]),
                      "articulo_anterior": c.get("articulo_anterior"),
                      "articulo_nuevo": nuevo, "motivo": str(c.get("motivo") or "")})
    if not casos:
        raise SystemExit("ERROR: el fichero no contiene casos.")
    return doc, casos


def inspeccionar(db: Path, casos: list[dict]) -> dict:
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        incid, dentro_antes, dentro_despues = [], 0, 0
        vinculos = 0
        for c in casos:
            fila = con.execute(
                "SELECT norma_id_normalizada, articulo_normalizado FROM lote_preguntas WHERE id=?",
                (c["pregunta_id"],)).fetchone()
            if fila is None:
                incid.append((c["pregunta_id"], "no existe"))
                continue
            if int(fila[0] or 0) != c["norma_id"]:
                incid.append((c["pregunta_id"], "cambió de norma"))
                continue
            if (fila[1] or None) != (c["articulo_anterior"] or None):
                incid.append((c["pregunta_id"], f"artículo actual {fila[1]!r} distinto"))
                continue
            existe = any(
                base(a) == base(c["articulo_nuevo"])
                for (a,) in con.execute(
                    "SELECT af.articulo_boe FROM articulos_fuente af JOIN norma_fuentes nf "
                    "ON UPPER(af.id_boe)=UPPER(nf.id_fuente) WHERE nf.norma_id=?",
                    (c["norma_id"],)))
            if not existe:
                incid.append((c["pregunta_id"], f"artículo {c['articulo_nuevo']} no está en el corpus"))
                continue
            convs = [r[0] for r in con.execute(
                "SELECT DISTINCT convocatoria_id FROM banco_preguntas "
                "WHERE pregunta_id=? AND estado='INCLUIDA'", (c["pregunta_id"],))]
            vinculos += len(convs)
            arts_temario = {base(r[0]) for r in con.execute(
                "SELECT tr.articulo_solicitado FROM temario_referencias tr "
                "JOIN temario_temas t ON t.id=tr.tema_id JOIN temarios tm ON tm.id=t.temario_id "
                f"WHERE tr.norma_id=? AND tm.convocatoria_id IN ({','.join('?' * len(convs)) or 'NULL'})",
                (c["norma_id"], *convs))} if convs else set()
            if base(c["articulo_anterior"]) in arts_temario:
                dentro_antes += 1
            if base(c["articulo_nuevo"]) in arts_temario:
                dentro_despues += 1
        return {"incidencias": incid, "vinculos": vinculos,
                "en_temario_antes": dentro_antes, "en_temario_despues": dentro_despues}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    ap.add_argument("--casos", type=Path, required=True)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()
    db = args.db.resolve()
    doc, casos = cargar(args.casos.resolve())
    try:
        imp = inspeccionar(db, casos)
    except sqlite3.OperationalError as exc:
        print(f"AVISO: no se pudo calcular el impacto en temarios ({exc}).")
        imp = {"incidencias": [], "vinculos": "?", "en_temario_antes": "?", "en_temario_despues": "?"}

    print("=" * 78)
    print("REETIQUETADO DE ARTÍCULOS — " + ("APLICAR" if args.aplicar else "VISTA PREVIA"))
    print("=" * 78)
    print(f"Base.......................... {db}")
    print(f"Casos......................... {len(casos)}")
    print(f"Vínculos de banco (INCLUIDA).. {imp['vinculos']}")
    print(f"Con artículo en temario antes. {imp['en_temario_antes']}")
    print(f"Con artículo en temario después {imp['en_temario_despues']}")
    print(f"Incidencias................... {len(imp['incidencias'])}")
    for pid, msg in imp["incidencias"][:20]:
        print(f"   {pid}: {msg}")
    print()
    for c in casos[:12]:
        print(f"  {c['pregunta_id']:>6}  art {c['articulo_anterior']} -> {c['articulo_nuevo']}")
    if len(casos) > 12:
        print(f"  ... y {len(casos) - 12} más")

    if not args.aplicar:
        print("\nVISTA PREVIA: no se ha modificado nada."
              + (" HAY INCIDENCIAS." if imp["incidencias"] else " Añada --aplicar para aplicar."))
        return 1 if imp["incidencias"] else 0
    if doc.get("decision_usuario") is not True:
        print("ABORTADO: falta decision_usuario=true.")
        return 2
    if imp["incidencias"]:
        print("ABORTADO: hay incidencias.")
        return 2

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"{db.stem}_backup_antes_reetiquetar_{marca}{db.suffix}")
    shutil.copy2(db, backup)
    if sha256(backup) != sha256(db):
        print("ABORTADO: la copia de seguridad no coincide.")
        return 2
    con = sqlite3.connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        for c in casos:
            cur = con.execute(
                "UPDATE lote_preguntas SET articulo=?, articulo_normalizado=? "
                "WHERE id=? AND norma_id_normalizada=? AND articulo_normalizado IS ?",
                (c["articulo_nuevo"], c["articulo_nuevo"], c["pregunta_id"],
                 c["norma_id"], c["articulo_anterior"]))
            if cur.rowcount != 1:
                raise RuntimeError(f"No se pudo actualizar {c['pregunta_id']}.")
        con.commit()
    except Exception as exc:
        con.rollback()
        print(f"ERROR: {exc}. Nada modificado (rollback).")
        return 2
    finally:
        con.close()
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not REGISTRO.exists()
    ahora = datetime.now().isoformat(timespec="seconds")
    with REGISTRO.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        if nuevo:
            w.writerow(["fecha_hora", "pregunta_id", "norma_id", "articulo_anterior",
                        "articulo_nuevo", "motivo", "backup"])
        for c in casos:
            w.writerow([ahora, c["pregunta_id"], c["norma_id"], c["articulo_anterior"],
                        c["articulo_nuevo"], c["motivo"], backup.name])
    print(f"\nAPLICADO: {len(casos)} preguntas reetiquetadas.")
    print(f"Copia de seguridad: {backup}")
    print("Siguiente: sincronizar bancos (menú 3 → 1) y publicar (menú 5).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
