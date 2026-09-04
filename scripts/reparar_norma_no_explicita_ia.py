"""
OpoCoach-Mantenimiento - Reparación conservadora de preguntas IA con norma no explícita.

Por defecto SOLO LECTURA.

Solo propone/repara preguntas de tipo_fuente='ia_generada' que cumplen TODAS:
- el auditor actual detecta únicamente NORMA_NO_EXPLICITA;
- no están implicadas en ningún duplicado/casi duplicado >= umbral;
- tienen norma_id_normalizada y artículo;
- existe texto oficial enlazado en temario_referencias/articulos_fuente para esa norma/artículo.

No intenta reparar referencias contextuales, subreferencias ambiguas ni duplicados:
esas preguntas quedan fuera de este proceso.

Con --aplicar crea backup y modifica exclusivamente el enunciado de las preguntas
que superan todos los controles.
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
COPIAS = ROOT / "db" / "copias_seguridad"
REGISTROS = ROOT / "registros"
UMBRAL_DEFECTO = 0.94

sys.path.insert(0, str(SCRIPTS))
from auditar_calidad_lote_preguntas import auditar, incidencias_autosuficiencia


def art_principal(valor) -> str | None:
    m = re.search(r"\d+", str(valor or ""))
    return m.group(0) if m else None


def nombre_norma_visible(nombre: str) -> str:
    s = str(nombre or "").strip()
    s = re.sub(r"(?i)\bLEY_ORGANICA\b", "Ley Orgánica", s)
    s = re.sub(r"(?i)\bLEY ORGANICA\b", "Ley Orgánica", s)
    if s.isupper():
        # Mantener siglas y números, pero evitar una línea entera en mayúsculas.
        palabras = []
        for p in s.split():
            if p in {"UE", "TUE", "TFUE"} or any(ch.isdigit() for ch in p):
                palabras.append(p)
            else:
                palabras.append(p.capitalize())
        s = " ".join(palabras)
    return s


def preparar(db: Path, umbral: float):
    calidad, duplicados = auditar(db, umbral)
    ids_dup = {int(x[k]) for x in duplicados for k in ("id_a", "id_b")}
    incidencias = {int(x["id"]): x for x in calidad}

    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        filas = con.execute("""
            SELECT lp.*, n.nombre_canonico
            FROM lote_preguntas lp
            LEFT JOIN normas n ON n.id = lp.norma_id_normalizada
            WHERE lp.tipo_fuente = 'ia_generada'
            ORDER BY lp.id
        """).fetchall()

        refs = con.execute("""
            SELECT tr.norma_id, tr.articulo_solicitado, af.texto
            FROM temario_referencias tr
            JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
            WHERE tr.norma_id IS NOT NULL
              AND tr.articulo_fuente_id IS NOT NULL
              AND TRIM(COALESCE(af.texto,'')) <> ''
        """).fetchall()
    finally:
        con.close()

    refs_ok = set()
    for r in refs:
        a = art_principal(r["articulo_solicitado"])
        if a:
            refs_ok.add((int(r["norma_id"]), a))

    propuestas = []
    descartadas = []

    for f in filas:
        pid = int(f["id"])
        inc = incidencias.get(pid)
        if not inc:
            continue

        texto_inc = str(inc["incidencias"])
        if not texto_inc.startswith("NORMA_NO_EXPLICITA:") or " | " in texto_inc:
            descartadas.append((pid, "OTRA_INCIDENCIA"))
            continue
        if pid in ids_dup:
            descartadas.append((pid, "DUPLICADO_O_CASI_DUPLICADO"))
            continue

        norma_id = f["norma_id_normalizada"]
        art = str(f["articulo_normalizado"] or f["articulo"] or "").strip()
        art_num = art_principal(art)
        if norma_id is None or not art_num:
            descartadas.append((pid, "METADATOS_INSUFICIENTES"))
            continue
        if (int(norma_id), art_num) not in refs_ok:
            descartadas.append((pid, "SIN_TEXTO_OFICIAL_ENLAZADO"))
            continue

        raw_norma = str(f["nombre_norma"] or "").strip()
        canon_base = raw_norma if re.search(r"\d{1,4}\s*/\s*\d{2,4}", raw_norma) else (f["nombre_canonico"] or f["nombre_norma_normalizado"])
        canon = nombre_norma_visible(canon_base)
        if not canon:
            descartadas.append((pid, "NORMA_NO_IDENTIFICABLE"))
            continue

        original = str(f["enunciado"] or "").strip()

        # Preferencia: ampliar la primera cita al artículo principal ya presente.
        patron = re.compile(rf"\b(art[ií]culo|art\.)\s*({re.escape(art_num)})(?!\d)", re.I)
        m = patron.search(original)
        if m:
            citado = m.group(0)
            nuevo = original[:m.start()] + f"{citado} de {canon}" + original[m.end():]
            metodo = "AMPLIAR_CITA_ARTICULO"
        else:
            # Casos donde el artículo de la pregunta está implícito o se cita otro precepto.
            nuevo = f"En relación con el artículo {art} de {canon}: {original}"
            metodo = "PREFIJO_EXPLICITO"

        prueba = dict(f)
        prueba["enunciado"] = nuevo
        inc_nuevas = incidencias_autosuficiencia(prueba)
        if inc_nuevas:
            descartadas.append((pid, "PROPUESTA_NO_SUPERA_AUDITOR:" + " | ".join(inc_nuevas)))
            continue

        propuestas.append({
            "id": pid,
            "norma_id": int(norma_id),
            "norma": canon,
            "articulo": art,
            "metodo": metodo,
            "enunciado_original": original,
            "enunciado_nuevo": nuevo,
        })

    return propuestas, descartadas


def backup(db: Path) -> Path:
    COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = COPIAS / f"oposiciones_antes_reparar_norma_ia_{marca}.sqlite3"
    shutil.copy2(db, out)
    return out


def exportar(propuestas) -> Path:
    REGISTROS.mkdir(parents=True, exist_ok=True)
    out = REGISTROS / "reparacion_norma_ia_propuestas.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        campos = ["id","norma_id","norma","articulo","metodo","enunciado_original","enunciado_nuevo"]
        w = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
        w.writeheader()
        w.writerows(propuestas)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    ap.add_argument("--umbral", type=float, default=UMBRAL_DEFECTO)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    db = args.db.resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 2

    propuestas, descartadas = preparar(db, args.umbral)
    csv_out = exportar(propuestas)

    razones = {}
    for _, motivo in descartadas:
        clave = motivo.split(":",1)[0]
        razones[clave] = razones.get(clave, 0) + 1

    print("=" * 78)
    print("REPARACIÓN CONSERVADORA IA - NORMA NO EXPLÍCITA")
    print("=" * 78)
    print(f"Base: {db}")
    print(f"Preguntas IA reparables con controles.. {len(propuestas)}")
    print(f"Preguntas IA no reparadas............... {len(descartadas)}")
    for k in sorted(razones):
        print(f"  {k:<36} {razones[k]}")
    print(f"CSV propuestas.......................... {csv_out}")

    if not args.aplicar:
        print("\nSOLO REVISIÓN: la base NO ha sido modificada.")
        return 0

    if not propuestas:
        print("\nNo hay reparaciones que aplicar.")
        return 0

    copia = backup(db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        for p in propuestas:
            cur = con.execute(
                "UPDATE lote_preguntas SET enunciado=? WHERE id=? AND tipo_fuente='ia_generada'",
                (p["enunciado_nuevo"], p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"No se pudo actualizar de forma unívoca el ID {p['id']}")
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check: {len(fk)} incidencia(s)")
        integ = con.execute("PRAGMA integrity_check").fetchone()[0]
        if str(integ).lower() != "ok":
            raise RuntimeError(f"integrity_check: {integ}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    calidad_final, _ = auditar(db, args.umbral)
    ids_mal = {int(x["id"]) for x in calidad_final}
    reparados_mal = [p["id"] for p in propuestas if p["id"] in ids_mal]

    print("\nRESULTADO")
    print("-" * 78)
    print(f"Preguntas IA reparadas................. {len(propuestas)}")
    print(f"Reparadas que siguen con incidencia.... {len(reparados_mal)}")
    print(f"Backup................................. {copia}")
    if reparados_mal:
        print("ERROR: alguna reparación no supera la auditoría. Restaure el backup.")
        return 3
    print("VALIDACIÓN DE LAS REPARACIONES: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
