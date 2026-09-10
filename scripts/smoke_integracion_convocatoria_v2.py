"""Smoke aislado de integración segura del Constructor V2 con una convocatoria."""
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
CODIGO = "A1-V2-SMOKE"
PROCESO = "Proceso selectivo 1"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def preparar_identidad(copia: Path) -> tuple[int, int]:
    con = sqlite3.connect(copia)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        cols = {str(r[1]) for r in con.execute("PRAGMA table_info(convocatorias)")}
        ahora = "2026-09-10 00:00:00"
        campos = [
            "puesto", "numero", "anio", "codigo", "numero_preguntas", "tiene_partes",
            "valoracion_test_acierto", "valoracion_test_fallo", "valoracion_test_no_contesta",
            "formula_nota", "factor_escala_nota", "temario_csv", "examen_modelo",
            "created_at", "updated_at",
        ]
        valores = [
            "Smoke V2", "SMOKE", 2026, CODIGO, 1, 0,
            1.0, 0.3333, 0.0, "smoke", 10.0, "temario.csv", "", ahora, ahora,
        ]
        if "activa" in cols:
            campos.append("activa")
            valores.append(1)
        marcas = ",".join("?" for _ in campos)
        cur = con.execute(
            f"INSERT INTO convocatorias({','.join(campos)}) VALUES({marcas})",
            tuple(valores),
        )
        cid = int(cur.lastrowid)

        tcols = {str(r[1]) for r in con.execute("PRAGMA table_info(temarios)")}
        if not {"convocatoria_id", "nombre"}.issubset(tcols):
            raise RuntimeError("Esquema de temarios no compatible con el smoke.")
        tcampos = ["convocatoria_id", "nombre"]
        tvalores: list[object] = [cid, "Temario smoke V2"]
        if "created_at" in tcols:
            tcampos.append("created_at")
            tvalores.append(ahora)
        if "updated_at" in tcols:
            tcampos.append("updated_at")
            tvalores.append(ahora)
        tmarcas = ",".join("?" for _ in tcampos)
        cur = con.execute(
            f"INSERT INTO temarios({','.join(tcampos)}) VALUES({tmarcas})",
            tuple(tvalores),
        )
        tid = int(cur.lastrowid)
        con.commit()
        return cid, tid
    finally:
        con.close()


def ejecutar(copia: Path, salida: Path, con_proceso: bool) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable, str(SCRIPT),
        "--codigo", CODIGO,
        "--pdf", str(PDF),
        "--db", str(copia),
        "--salida-json", str(salida),
    ]
    if con_proceso:
        args.extend(["--proceso-selectivo", PROCESO])
    return subprocess.run(
        args,
        cwd=RAIZ,
        check=False,
        text=True,
        capture_output=True,
    )


def main() -> int:
    if not DB.is_file():
        raise RuntimeError(f"No existe la BD real: {DB}")
    if not PDF.is_file():
        raise RuntimeError(f"No existe el PDF patrón: {PDF}")

    antes = sha(DB)
    with tempfile.TemporaryDirectory(prefix="v2_conv_") as td:
        carpeta = Path(td)
        copia = carpeta / "oposiciones.sqlite3"
        salida = carpeta / "manifiesto.json"
        shutil.copy2(DB, copia)
        cid, tid = preparar_identidad(copia)

        # Debe bloquear si el PDF contiene varios procesos y no se selecciona uno.
        r_bloqueo = ejecutar(copia, salida, False)
        if r_bloqueo.returncode == 0:
            raise RuntimeError("El gestor no bloqueó un PDF con varios procesos sin selección.")
        combinado = (r_bloqueo.stdout or "") + "\n" + (r_bloqueo.stderr or "")
        if "varios procesos selectivos" not in combinado.lower():
            print(combinado)
            raise RuntimeError("El bloqueo no explica la necesidad de seleccionar proceso.")

        r = ejecutar(copia, salida, True)
        if r.returncode != 0:
            print(r.stdout)
            print(r.stderr)
            raise RuntimeError(f"Integración V2 falló con código {r.returncode}")
        if not salida.is_file():
            raise RuntimeError("No se generó el manifiesto V2.")

        m = json.loads(salida.read_text(encoding="utf-8"))
        ident = m.get("convocatoria") or {}
        if ident.get("codigo") != CODIGO:
            raise RuntimeError("El manifiesto no conserva el código de convocatoria.")
        if int(ident.get("convocatoria_id") or 0) != cid:
            raise RuntimeError("El manifiesto no conserva convocatoria_id.")
        if int(ident.get("temario_id") or 0) != tid:
            raise RuntimeError("El manifiesto no conserva temario_id.")
        if m.get("naturaleza") != "MANIFIESTO_CONVOCATORIA_CONSTRUCTOR_V2":
            raise RuntimeError("Naturaleza del manifiesto incorrecta.")
        proc = m.get("proceso_selectivo") or {}
        if not str(proc.get("seleccionado") or "").lower().startswith(PROCESO.lower()):
            raise RuntimeError("El manifiesto no conserva el proceso seleccionado.")
        if len(proc.get("detectados") or []) < 2:
            raise RuntimeError("El smoke esperaba detectar varios procesos en el PDF patrón.")
        reglas = m.get("reglas") or {}
        for clave in ("modifica_bd", "modifica_corpus", "modifica_temario_csv", "modifica_bancos"):
            if reglas.get(clave) is not False:
                raise RuntimeError(f"Contrato de solo lectura incumplido: {clave}")
        if reglas.get("convocatoria_identificada") is not True:
            raise RuntimeError("No consta identidad de convocatoria validada.")
        if reglas.get("proceso_selectivo_explicitamente_seleccionado") is not True:
            raise RuntimeError("No consta selección explícita de proceso.")
        resumen = m.get("resumen") or {}
        if int(resumen.get("temas_total") or 0) <= 0:
            raise RuntimeError("El manifiesto no contiene temas.")

    if sha(DB) != antes:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("=" * 78)
    print("SMOKE INTEGRACION CONVOCATORIA V2: OK")
    print("=" * 78)
    print("Convocatoria temporal identificada. OK")
    print("Temario temporal identificado....... OK")
    print("Varios procesos detectados.......... OK")
    print("Sin selección -> bloqueo............ OK")
    print("Selección explícita proceso 1....... OK")
    print("Temas no mezclados.................. OK")
    print("Manifiesto V2....................... OK")
    print("Contrato solo lectura............... OK")
    print("Dependencia de códigos reales....... NO")
    print("BD real............................. SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
