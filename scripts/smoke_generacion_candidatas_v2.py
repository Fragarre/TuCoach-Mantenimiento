"""Smoke aislado del generador de candidatas PSEUDO V2. No llama a IA ni toca BD."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generar_candidatas_preguntas_v2.py"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v2_gen_candidate_") as td:
        base = Path(td)
        contextos = base / "contextos.jsonl"
        fixture = base / "respuesta.json"
        salida = base / "candidatas.jsonl"

        contexto = {
            "tipo_registro": "CONTEXTO_GENERACION_PSEUDO_V2",
            "tipo_contexto_propuesto": "PSEUDO",
            "tema_id": 53,
            "parte": "ESPECIAL",
            "numero_tema": 53,
            "texto_oficial_tema": "Gobierno abierto: transparencia, participación y rendición de cuentas.",
            "pseudonorma_id": 1,
            "pseudonorma": "PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO",
            "pseudoarticulo_id": 10,
            "pseudoarticulo": "P01",
            "orden": 1,
            "texto_temario": "Concepto de gobierno abierto.",
            "tipo_contenido": "JURIDICO_DOCTRINAL_TRANSVERSAL",
            "sintesis": "El gobierno abierto integra transparencia, participación ciudadana y rendición de cuentas.",
            "proposiciones": [
                {"id": 100, "codigo": "A1E53-P01", "texto": "El gobierno abierto integra transparencia, participación y rendición de cuentas.", "esencial": 1, "tipo": "DEFINICION", "estado": "ACEPTADA"}
            ],
            "respaldos_verificados": [
                {
                    "respaldo_id": 501,
                    "proposicion_id": 100,
                    "proposicion_codigo": "A1E53-P01",
                    "proposicion_texto": "El gobierno abierto integra transparencia, participación y rendición de cuentas.",
                    "fuente": {
                        "clase": "FUENTE_CONOCIMIENTO",
                        "id": 20,
                        "tipo": "FUENTE_PRIMARIA_OFICIAL",
                        "organismo": "Jefatura del Estado",
                        "nombre": "Ley de transparencia",
                        "identificador_oficial": "BOE-A-2022-8187",
                        "localizador_oficial": "https://www.boe.es/"
                    },
                    "evidencia_texto": "Evidencia verificada de transparencia y gobierno abierto.",
                    "evidencia_localizador": "preámbulo"
                }
            ],
            "regla_mixta": "NO_INFERIR; requiere temario_referencia_id explícito y del mismo tema"
        }
        contextos.write_text(json.dumps(contexto, ensure_ascii=False) + "\n", encoding="utf-8")

        respuesta = {
            "apta": True,
            "tipo_contexto": "PSEUDO",
            "enunciado": "¿Qué combinación caracteriza mejor el enfoque de gobierno abierto descrito en el contexto?",
            "opcion_a": "Transparencia, participación ciudadana y rendición de cuentas.",
            "opcion_b": "Jerarquía administrativa, tutela judicial y potestad sancionadora.",
            "opcion_c": "Reserva de ley, inderogabilidad singular y retroactividad.",
            "opcion_d": "Coordinación presupuestaria, contratación y expropiación.",
            "respuesta_correcta": "A",
            "explicacion": "La síntesis aceptada identifica conjuntamente transparencia, participación y rendición de cuentas.",
            "respaldo_ids": [501],
            "motivo_no_apta": None
        }
        fixture.write_text(json.dumps(respuesta, ensure_ascii=False, indent=2), encoding="utf-8")

        proc = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--contextos", str(contextos),
                "--salida", str(salida),
                "--respuesta-fixture", str(fixture),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        print(proc.stdout, end="")
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            raise RuntimeError(f"El generador terminó con código {proc.returncode}.")

        filas = [json.loads(x) for x in salida.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert len(filas) == 1
        r = filas[0]
        assert r["estado"] == "CANDIDATA_VALIDADA"
        assert r["tipo_contexto"] == "PSEUDO"
        assert r["pseudoarticulo_id"] == 10
        assert r["respaldo_ids"] == [501]
        assert r["respuesta_correcta"] == "A"
        assert "PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO" not in r["enunciado"]

        # Barrera crítica: un respaldo ajeno al contexto debe rechazarse.
        mala = dict(respuesta)
        mala["respaldo_ids"] = [999]
        fixture.write_text(json.dumps(mala, ensure_ascii=False), encoding="utf-8")
        proc_mala = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--contextos", str(contextos),
                "--salida", str(base / "mala.jsonl"),
                "--respuesta-fixture", str(fixture),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert proc_mala.returncode != 0
        assert "respaldos ajenos al contexto" in (proc_mala.stderr + proc_mala.stdout)

    print()
    print("=" * 78)
    print("SMOKE GENERACION CANDIDATAS V2: OK")
    print("=" * 78)
    print("Candidata PSEUDO validada......... OK")
    print("Trazabilidad respaldo............. OK")
    print("Respaldo ajeno rechazado.......... OK")
    print("MIXTA no inferida................. OK")
    print("IA................................ NO EJECUTADA")
    print("BD................................ SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
