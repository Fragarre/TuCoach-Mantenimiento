"""Smoke aislado del alta optativa con Constructor Temario V2."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_REAL = RAIZ / "db" / "oposiciones.sqlite3"
SCRIPT = RAIZ / "scripts" / "alta_convocatoria_v2_orquestador.py"
PDF = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26" / "temario_A1.pdf"
CODIGO = "A1-V2-ALTA-SMOKE"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def ejecutar(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=RAIZ,
        check=False,
        text=True,
        capture_output=True,
    )


def config_base(csv_path: Path) -> dict:
    return {
        "convocatoria": {
            "puesto": "Smoke alta V2",
            "numero": "SMOKE/26",
            "anio": 2026,
            "codigo": CODIGO,
            "numero_preguntas": 1,
            "tiene_partes": 1,
            "valoracion_test_acierto": 1.0,
            "valoracion_test_fallo": 0.3333,
            "valoracion_test_no_contesta": 0.0,
            "formula_nota": "smoke",
            "factor_escala_nota": 10.0,
            "temario_csv": str(csv_path),
        },
        "partes": [
            {"nombre": "GENERAL", "numero_preguntas": 1, "orden": 1}
        ],
        "temario": {
            "csv": str(csv_path),
            "nombre": "Temario smoke V2",
            "encoding": "utf-8",
            "sincronizar_eliminaciones": False,
        },
    }


def main() -> int:
    if not DB_REAL.is_file():
        raise RuntimeError(f"No existe la BD real: {DB_REAL}")
    if not PDF.is_file():
        raise RuntimeError(f"No existe el PDF patrón: {PDF}")

    real_antes = sha(DB_REAL)

    with tempfile.TemporaryDirectory(prefix="v2_alta_", ignore_cleanup_errors=True) as td:
        carpeta = Path(td)
        db = carpeta / "oposiciones.sqlite3"
        csv_path = carpeta / "temario.csv"
        config_legacy = carpeta / "legacy.json"
        config_v2 = carpeta / "v2.json"
        manifiesto = carpeta / "manifiesto_v2.json"
        shutil.copy2(DB_REAL, db)

        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["parte", "tema", "titulo", "LEY", "articulo", "tipo"],
            )
            w.writeheader()
            w.writerow({
                "parte": "GENERAL",
                "tema": "1",
                "titulo": "Tema smoke",
                "LEY": "",
                "articulo": "",
                "tipo": "INFORMATICA",
            })

        legacy = config_base(csv_path)
        config_legacy.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

        antes_legacy = sha(db)
        r = ejecutar(["--config", str(config_legacy), "--db", str(db), "--solo-validar"])
        if r.returncode != 0:
            print(r.stdout); print(r.stderr)
            raise RuntimeError("Falló la validación del camino legado.")
        if sha(db) != antes_legacy:
            raise RuntimeError("El camino legado --solo-validar modificó la copia.")
        if "flujo legado intacto" not in r.stdout:
            raise RuntimeError("No consta delegación íntegra al flujo legado.")

        v2 = config_base(csv_path)
        v2["constructor_v2"] = {
            "pdf": str(PDF),
            "proceso_selectivo": "Proceso selectivo 1",
            "salida_json": str(manifiesto),
        }
        config_v2.write_text(json.dumps(v2, ensure_ascii=False, indent=2), encoding="utf-8")

        antes_validar = sha(db)
        r = ejecutar(["--config", str(config_v2), "--db", str(db), "--solo-validar"])
        if r.returncode != 0:
            print(r.stdout); print(r.stderr)
            raise RuntimeError("Falló --solo-validar con V2.")
        if sha(db) != antes_validar:
            raise RuntimeError("--solo-validar V2 modificó la copia.")
        if manifiesto.exists():
            raise RuntimeError("--solo-validar no debe materializar manifiesto final.")

        r = ejecutar(["--config", str(config_v2), "--db", str(db)])
        if r.returncode != 0:
            print(r.stdout); print(r.stderr)
            raise RuntimeError("Falló el alta completa con V2.")
        if not manifiesto.is_file():
            raise RuntimeError("No se creó el manifiesto V2 tras el alta.")

        m = json.loads(manifiesto.read_text(encoding="utf-8"))
        if (m.get("convocatoria") or {}).get("codigo") != CODIGO:
            raise RuntimeError("El manifiesto no quedó vinculado a la nueva convocatoria.")
        if ((m.get("proceso_selectivo") or {}).get("seleccionado") or "").lower().find("proceso selectivo 1") < 0:
            raise RuntimeError("No consta el proceso selectivo elegido.")

        con = sqlite3.connect(db)
        try:
            conv = con.execute("SELECT id FROM convocatorias WHERE codigo=?", (CODIGO,)).fetchone()
            if conv is None:
                raise RuntimeError("La convocatoria no fue creada en la copia.")
            cid = int(conv[0])
            tem = con.execute("SELECT COUNT(*) FROM temarios WHERE convocatoria_id=?", (cid,)).fetchone()[0]
            temas = con.execute(
                "SELECT COUNT(*) FROM temario_temas tt JOIN temarios t ON t.id=tt.temario_id WHERE t.convocatoria_id=?",
                (cid,),
            ).fetchone()[0]
            if int(tem) != 1 or int(temas) != 1:
                raise RuntimeError("El alta consolidada no dejó el temario smoke esperado.")
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(f"foreign_key_check con incidencias: {fk[:5]}")
        finally:
            con.close()

    if sha(DB_REAL) != real_antes:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("=" * 78)
    print("SMOKE ALTA CONVOCATORIA + CONSTRUCTOR V2: OK")
    print("=" * 78)
    print("Camino legado sin V2.............. OK")
    print("V2 prevalidado antes de escribir.. OK")
    print("Solo validar V2.................... SIN CAMBIOS")
    print("Alta consolidada................... OK")
    print("Temario consolidado................ OK")
    print("Proceso selectivo explícito........ OK")
    print("Manifiesto V2 post-alta............ OK")
    print("foreign_key_check.................. OK")
    print("BD real............................ SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
