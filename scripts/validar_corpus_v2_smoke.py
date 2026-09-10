"""Smoke test de corpus V2. No modifica la BD real."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
DB = RAIZ / "db" / "oposiciones.sqlite3"
OUT = RAIZ / "auditorias" / "constructor_v2" / "smoke_corpus_v2"
COPIA = OUT / "oposiciones_corpus_v2_smoke.sqlite3"
REAL_OUT = OUT / "C1_real_pseudo.jsonl"
TEMP_OUT = OUT / "C1_temporal_pseudo.jsonl"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def ejecutar(db: Path, salida: Path) -> None:
    cmd = [
        sys.executable,
        str(SCRIPTS / "construir_corpus_v2_convocatoria.py"),
        "--codigo", "C1-01_58_26",
        "--db", str(db),
        "--salida", str(salida),
    ]
    print("\n" + "=" * 78)
    print(" ".join(cmd))
    print("=" * 78)
    r = subprocess.run(cmd, cwd=RAIZ, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"Falló construir_corpus_v2_convocatoria.py: {r.returncode}")


def leer_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def preparar_copia() -> None:
    shutil.copy2(DB, COPIA)
    with sqlite3.connect(COPIA) as con:
        con.execute("PRAGMA foreign_keys=ON")
        tema_id = con.execute(
            """
            SELECT tt.id
            FROM temario_temas tt
            JOIN temarios t ON t.id=tt.temario_id
            JOIN convocatorias c ON c.id=t.convocatoria_id
            WHERE c.codigo='C1-01_58_26'
            ORDER BY tt.id LIMIT 1
            """
        ).fetchone()[0]
        articulo_fuente_id = con.execute("SELECT id FROM articulos_fuente ORDER BY id LIMIT 1").fetchone()[0]
        con.execute(
            "INSERT INTO temario_pseudonormas(tema_id,codigo,estado) VALUES(?,?,?)",
            (tema_id, "PSEUDO:SMOKE", "RESUELTO"),
        )
        pn = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute(
            """
            INSERT INTO temario_pseudoarticulos
            (pseudonorma_id,codigo,orden,texto_temario,tipo_contenido,sintesis,estado)
            VALUES(?,?,?,?,?,?,?)
            """,
            (pn, "P01", 1, "Texto temario smoke", "MIXTO", "Síntesis verificada smoke", "RESUELTO"),
        )
        pa = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute(
            """
            INSERT INTO temario_pseudoarticulo_respaldos
            (pseudoarticulo_id,proposicion_id,articulo_fuente_id,fuente_conocimiento_id,
             pertinencia,estado,motivo,evidencia_texto,evidencia_localizador)
            VALUES(?,NULL,?,NULL,'PERTINENTE','VERIFICADO','smoke','evidencia','smoke')
            """,
            (pa, articulo_fuente_id),
        )
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check copia: {len(fk)}")
        con.commit()


def main() -> int:
    if not DB.is_file():
        raise RuntimeError(f"Falta BD: {DB}")
    OUT.mkdir(parents=True, exist_ok=True)
    hash_real = sha256(DB)

    ejecutar(DB, REAL_OUT)
    reales = leer_jsonl(REAL_OUT)
    if reales:
        raise RuntimeError(f"C1 real no debe tener corpus pseudo V2; filas={len(reales)}")

    preparar_copia()
    ejecutar(COPIA, TEMP_OUT)
    temporales = leer_jsonl(TEMP_OUT)
    if len(temporales) != 1:
        raise RuntimeError(f"La copia debe incluir exactamente 1 pseudoartículo; filas={len(temporales)}")
    fila = temporales[0]
    if fila.get("tipo_registro") != "PSEUDOARTICULO_V2":
        raise RuntimeError("Tipo de registro V2 incorrecto.")
    if fila.get("sintesis") != "Síntesis verificada smoke":
        raise RuntimeError("Síntesis incorrecta en corpus V2.")
    if int(fila.get("respaldos_verificados") or 0) != 1:
        raise RuntimeError("Respaldo verificado no reflejado.")

    if sha256(DB) != hash_real:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("\n" + "=" * 78)
    print("SMOKE TEST CORPUS V2: OK")
    print("=" * 78)
    print("C1 real pseudoartículos........... 0")
    print("Copia temporal incluidos.......... 1")
    print("Filtro estado final............... OK")
    print("Filtro PERTINENTE/VERIFICADO...... OK")
    print("BD real........................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR SMOKE CORPUS V2: {exc}")
        raise SystemExit(1)
