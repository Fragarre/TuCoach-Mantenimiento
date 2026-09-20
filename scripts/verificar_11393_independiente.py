#!/usr/bin/env python3
"""Segunda verificación aislada de la pregunta 11393.

No usa la respuesta almacenada ni modifica la base de datos.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from openai_api import seleccionar_fragmento_json

BASE = ROOT / "outputs" / "auditoria_control_tests_v1"
DB = ROOT / "db" / "oposiciones.sqlite3"

with sqlite3.connect(DB) as con:
    row = con.execute(
        "SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d "
        "FROM lote_preguntas WHERE id=11393"
    ).fetchone()
pregunta = dict(zip(("id", "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d"), row))
evidencia = json.loads((BASE / "evidencia_rescatada_11393.json").read_text(encoding="utf-8"))

prompt = (
    "Devuelve solo JSON con pregunta_id, opcion_correcta (A|B|C|D|null), "
    "certeza (UNICA|NO_DETERMINABLE) y motivo de 25 palabras máximo. "
    "Responde qué opción satisface el enunciado conforme a la evidencia oficial. "
    "No hay respuesta almacenada y no debes inferirla.\n\n"
    + json.dumps({"pregunta": pregunta, "evidencia": evidencia}, ensure_ascii=False)
)
resultado = seleccionar_fragmento_json(
    prompt, modelo="gpt-5.4-nano", operacion="segunda_verificacion_11393", max_output_tokens=500
)
if (
    resultado.get("pregunta_id") != 11393
    or resultado.get("opcion_correcta") not in {"A", "B", "C", "D", None}
    or resultado.get("certeza") not in {"UNICA", "NO_DETERMINABLE"}
):
    raise SystemExit("Respuesta inválida: no se guarda")

salida = BASE / "segunda_verificacion_11393.json"
salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
print("OK", salida)
