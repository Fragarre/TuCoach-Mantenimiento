"""Smoke aislado de mantenimiento/reanálisis V2 de una convocatoria existente."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
SCRIPT = RAIZ / "scripts" / "gestionar_constructor_v2_convocatoria.py"
PDF = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26" / "temario_A1.pdf"
CODIGO = "A1-V2-MANT-SMOKE"
PROCESO = "Proceso selectivo 1"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def preparar_identidad(copia: Path) -> None:
    con = sqlite3.connect(copia)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        ahora = "2026-09-10 00:00:00"
        cols = {str(r[1]) for r in con.execute("PRAGMA table_info(convocatorias)")}
        campos = [
            "puesto", "numero", "anio", "codigo", "numero_preguntas", "tiene_partes",
            "valoracion_test_acierto", "valoracion_test_fallo", "valoracion_test_no_contesta",
            "formula_nota", "factor_escala_nota", "temario_csv", "examen_modelo",
            "created_at", "updated_at",
        ]
        valores: list[object] = [
            "Smoke mantenimiento V2", "SMOKE", 2026, CODIGO, 1, 0,
            1.0, 0.3333, 0.0, "smoke", 10.0, "temario.csv", "", ahora, ahora,
        ]
        if "activa" in cols:
            campos.append("activa")
            valores.append(1)
        cur = con.execute(
            f"INSERT INTO convocatorias({','.join(campos)}) VALUES({','.join('?' for _ in campos)})",
            tuple(valores),
        )
        cid = int(cur.lastrowid)

        tcols = {str(r[1]) for r in con.execute("PRAGMA table_info(temarios)")}
        tcampos = ["convocatoria_id", "nombre"]
        tvalores: list[object] = [cid, "Temario mantenimiento V2"]
        if "created_at" in tcols:
            tcampos.append("created_at")
            tvalores.append(ahora)
        if "updated_at" in tcols:
            tcampos.append("updated_at")
            tvalores.append(ahora)
        con.execute(
            f"INSERT INTO temarios({','.join(tcampos)}) VALUES({','.join('?' for _ in tcampos)})",
            tuple(tvalores),
        )
        con.commit()
    finally:
        con.close()


def ejecutar(copia: Path, salida: Path) -> dict:
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--codigo", CODIGO,
            "--pdf", str(PDF),
            "--db", str(copia),
            "--salida-json", str(salida),
            "--proceso-selectivo", PROCESO,
        ],
        cwd=RAIZ,
        check=False,
        text=True,
        capture_output=True,
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise RuntimeError(f"Reanálisis V2 falló con código {r.returncode}")
    return json.loads(salida.read_text(encoding="utf-8"))


def comparable(m: dict) -> dict:
    x = dict(m)
    return x


def main() -> int:
    if not DB.is_file():
        raise RuntimeError(f"No existe la BD real: {DB}")
    if not PDF.is_file():
        raise RuntimeError(f"No existe el PDF patrón: {PDF}")

    antes_real = sha(DB)
    with tempfile.TemporaryDirectory(prefix="v2_mant_", ignore_cleanup_errors=True) as td:
        carpeta = Path(td)
        copia = carpeta / "oposiciones.sqlite3"
        salida1 = carpeta / "m1.json"
        salida2 = carpeta / "m2.json"
        shutil.copy2(DB, copia)
        preparar_identidad(copia)
        antes_copia = sha(copia)

        m1 = ejecutar(copia, salida1)
        entre = sha(copia)
        m2 = ejecutar(copia, salida2)
        despues_copia = sha(copia)

        if antes_copia != entre or entre != despues_copia:
            raise RuntimeError("El mantenimiento V2 modificó SQLite.")
        if comparable(m1) != comparable(m2):
            raise RuntimeError("Dos reanálisis consecutivos no producen el mismo manifiesto.")
        if (m1.get("convocatoria") or {}).get("codigo") != CODIGO:
            raise RuntimeError("Identidad de convocatoria incorrecta.")
        proc = m1.get("proceso_selectivo") or {}
        if not str(proc.get("seleccionado") or "").startswith(PROCESO):
            raise RuntimeError("No se conserva el proceso selectivo elegido.")
        if int((m1.get("resumen") or {}).get("temas_total") or 0) <= 0:
            raise RuntimeError("El reanálisis no produjo temas.")

    if sha(DB) != antes_real:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("=" * 78)
    print("SMOKE MANTENIMIENTO CONVOCATORIA V2: OK")
    print("=" * 78)
    print("Convocatoria existente.............. OK")
    print("Proceso selectivo explícito......... OK")
    print("Reanálisis V2....................... OK")
    print("Repetibilidad 2 ejecuciones......... OK")
    print("SQLite temporal..................... SIN CAMBIOS")
    print("BD real............................. SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
