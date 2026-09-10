"""Smoke de integración segura del Constructor V2 con una convocatoria existente."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
SCRIPT = RAIZ / "scripts" / "gestionar_constructor_v2_convocatoria.py"
PDF = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26" / "temario_A1.pdf"
CODIGO = "A1-01_01_26"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    if not DB.is_file():
        raise RuntimeError(f"No existe la BD real: {DB}")
    if not PDF.is_file():
        raise RuntimeError(f"No existe el PDF patrón: {PDF}")

    antes = sha(DB)
    with tempfile.TemporaryDirectory(prefix="v2_conv_") as td:
        salida = Path(td) / "manifiesto.json"
        r = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--codigo", CODIGO,
                "--pdf", str(PDF),
                "--db", str(DB),
                "--salida-json", str(salida),
            ],
            cwd=RAIZ,
            check=False,
            text=True,
            capture_output=True,
        )
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
        if not isinstance(ident.get("convocatoria_id"), int):
            raise RuntimeError("El manifiesto no identifica convocatoria_id.")
        if not isinstance(ident.get("temario_id"), int):
            raise RuntimeError("El manifiesto no identifica temario_id.")
        if m.get("naturaleza") != "MANIFIESTO_CONVOCATORIA_CONSTRUCTOR_V2":
            raise RuntimeError("Naturaleza del manifiesto incorrecta.")
        reglas = m.get("reglas") or {}
        for clave in ("modifica_bd", "modifica_corpus", "modifica_temario_csv", "modifica_bancos"):
            if reglas.get(clave) is not False:
                raise RuntimeError(f"Contrato de solo lectura incumplido: {clave}")
        if reglas.get("convocatoria_identificada") is not True:
            raise RuntimeError("No consta identidad de convocatoria validada.")
        resumen = m.get("resumen") or {}
        if int(resumen.get("temas_total") or 0) <= 0:
            raise RuntimeError("El manifiesto no contiene temas.")

    if sha(DB) != antes:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("=" * 78)
    print("SMOKE INTEGRACION CONVOCATORIA V2: OK")
    print("=" * 78)
    print("Convocatoria identificada......... OK")
    print("Temario identificado............... OK")
    print("PDF oficial analizado.............. OK")
    print("Manifiesto V2...................... OK")
    print("Contrato solo lectura.............. OK")
    print("BD real............................ SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
