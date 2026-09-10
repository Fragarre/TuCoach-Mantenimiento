"""
Constructor Temario V2 - cierre formal GENERAL de una pseudonorma.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO DECIDE PERTINENCIA JURIDICA.
NO ACEPTA AUTOMATICAMENTE CONTENIDO.

Encadena, para cualquier caso V2 ya revisado por una persona:
1. materializacion reproducible de la evidencia humana aceptada;
2. auditoria final formal de los siete controles V2.

La decision juridica sigue estando exclusivamente en el artefacto de decisiones
humanas. Este orquestador solo elimina la repeticion manual de comandos y rutas.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
SCRIPTS = RAIZ / "scripts"
MATERIALIZAR = SCRIPTS / "materializar_evidencia_humana_v2.py"
AUDITAR_FINAL = SCRIPTS / "auditar_pseudonorma_final_v2.py"


def cargar_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"ERROR: no existe {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: JSON no valido en {path}: {exc}")


def ejecutar(cmd: list[str], etiqueta: str) -> None:
    print(f"\n--- {etiqueta} ---")
    proc = subprocess.run(cmd, cwd=str(RAIZ))
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def validar_entradas(pertinencia: dict, decisiones: dict) -> None:
    if pertinencia.get("naturaleza") != "AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2":
        raise SystemExit("ERROR: pertinencia-json no es AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2.")
    if pertinencia.get("estado") != "LISTO_PARA_REVISION_HUMANA":
        raise SystemExit("ERROR: la pertinencia no esta LISTO_PARA_REVISION_HUMANA.")
    if decisiones.get("naturaleza") != "DECISIONES_HUMANAS_PERTINENCIA_V2":
        raise SystemExit("ERROR: decisiones-json no es DECISIONES_HUMANAS_PERTINENCIA_V2.")
    if decisiones.get("estado") != "REVISION_HUMANA_COMPLETADA":
        raise SystemExit("ERROR: las decisiones humanas no estan completadas.")
    if pertinencia.get("caso") != decisiones.get("caso"):
        raise SystemExit("ERROR: caso distinto entre pertinencia y decisiones humanas.")
    if pertinencia.get("pseudonorma") != decisiones.get("pseudonorma"):
        raise SystemExit("ERROR: pseudonorma distinta entre pertinencia y decisiones humanas.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Cierra formalmente una pseudonorma V2 ya revisada, sin BD ni corpus."
    )
    ap.add_argument("--sintesis-md", required=True)
    ap.add_argument("--pertinencia-json", required=True)
    ap.add_argument("--decisiones-json", required=True)
    ap.add_argument("--evidencia-json", required=True)
    ap.add_argument("--auditoria-json", required=True)
    ap.add_argument("--max-fragmentos", type=int, default=3)
    args = ap.parse_args()

    sintesis = Path(args.sintesis_md).expanduser().resolve()
    pertinencia_path = Path(args.pertinencia_json).expanduser().resolve()
    decisiones_path = Path(args.decisiones_json).expanduser().resolve()
    evidencia_path = Path(args.evidencia_json).expanduser().resolve()
    auditoria_path = Path(args.auditoria_json).expanduser().resolve()

    if not sintesis.is_file():
        raise SystemExit(f"ERROR: no existe la sintesis {sintesis}")

    pertinencia = cargar_json(pertinencia_path)
    decisiones = cargar_json(decisiones_path)
    validar_entradas(pertinencia, decisiones)

    caso = decisiones.get("caso")
    pseudonorma = decisiones.get("pseudonorma")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - CIERRE FORMAL GENERAL - SIN BD / SIN CORPUS")
    print("=" * 78)
    print(f"Caso.............................. {caso}")
    print(f"Pseudonorma....................... {pseudonorma}")
    print(f"Sintesis.......................... {sintesis}")
    print(f"Pertinencia....................... {pertinencia_path}")
    print(f"Decisiones humanas................ {decisiones_path}")

    ejecutar(
        [
            sys.executable,
            str(MATERIALIZAR),
            "--pertinencia-json", str(pertinencia_path),
            "--decisiones-json", str(decisiones_path),
            "--salida-json", str(evidencia_path),
            "--max-fragmentos", str(max(1, args.max_fragmentos)),
        ],
        "1/2 Materializar evidencia humana",
    )

    ejecutar(
        [
            sys.executable,
            str(AUDITAR_FINAL),
            "--sintesis-md", str(sintesis),
            "--pertinencia-json", str(pertinencia_path),
            "--evidencia-json", str(evidencia_path),
            "--salida-json", str(auditoria_path),
        ],
        "2/2 Auditoria final formal",
    )

    auditoria = cargar_json(auditoria_path)
    estado = auditoria.get("estado")
    controles = auditoria.get("resultado") or {}

    print("\n" + "=" * 78)
    print("CIERRE V2 COMPLETADO")
    print("=" * 78)
    print(f"Estado formal...................... {estado}")
    print(
        "Controles.......................... "
        f"{controles.get('controles_ok', 0)}/{controles.get('controles_total', 7)}"
    )
    print(f"Evidencia.......................... {evidencia_path}")
    print(f"Auditoria.......................... {auditoria_path}")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")

    return 0 if estado == "ACEPTABLE_FORMALMENTE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
