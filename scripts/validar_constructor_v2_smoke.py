"""
Smoke test reproducible del Constructor Temario V2.

Objetivo:
- ejecutar el manifiesto general sobre el PDF A1 administrativo ya separado;
- completar UN tema doctrinal (ESPECIAL 53) con el flujo V4 + normalizador V5;
- comprobar la reanudación/idempotencia del artefacto;
- demostrar que la BD consolidada y los temario.csv activos de C1/C2 no cambian.

NO escribe en la BD, corpus, bancos ni temarios consolidados. Solo crea JSON de
auditoría bajo auditorias/constructor_v2/smoke/.

Requiere la misma configuración OpenAI que los pilotos V4.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
DB = RAIZ / "db" / "oposiciones.sqlite3"
PDF = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM" / "temario_A1.pdf"
C1 = RAIZ / "data_convocatorias" / "CONV_C1-01_58_26" / "temario.csv"
C2 = RAIZ / "data_convocatorias" / "CONV_C2-01_70_26" / "temario.csv"
SALIDA = RAIZ / "auditorias" / "constructor_v2" / "smoke"
MANIFIESTO_INICIAL = SALIDA / "A1_manifiesto_inicial.json"
MANIFIESTO_53 = SALIDA / "A1_ESPECIAL_53_completado.json"
MANIFIESTO_REANUDADO = SALIDA / "A1_ESPECIAL_53_reanudado.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def exigir_archivos() -> None:
    for path in (DB, PDF, C1, C2):
        if not path.is_file():
            raise RuntimeError(f"Falta archivo requerido: {path}")
    for nombre in (
        "analizar_convocatoria_v2.py",
        "completar_manifiesto_pseudonormas_v2.py",
    ):
        if not (SCRIPTS / nombre).is_file():
            raise RuntimeError(f"Falta script requerido: scripts/{nombre}")


def ejecutar(cmd: list[str]) -> None:
    print("\n" + "=" * 78)
    print(" ".join(f'\"{x}\"' if " " in x else x for x in cmd))
    print("=" * 78)
    resultado = subprocess.run(cmd, cwd=RAIZ, check=False)
    if resultado.returncode != 0:
        raise RuntimeError(
            f"El comando terminó con código {resultado.returncode}: {cmd[1]}"
        )


def cargar(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON raíz inválido: {path}")
    return data


def localizar_tema(data: dict, parte: str, numero: int) -> dict:
    temas = [
        x for x in data.get("temas", [])
        if str(x.get("parte") or "").upper() == parte.upper()
        and int(x.get("numero_tema")) == numero
    ]
    if len(temas) != 1:
        raise RuntimeError(
            f"Se esperaba exactamente {parte} {numero}; localizados={len(temas)}"
        )
    return temas[0]


def validar_seguridad(data: dict) -> None:
    reglas = data.get("reglas") or {}
    for clave in (
        "modifica_bd",
        "modifica_corpus",
        "modifica_temario_csv",
        "modifica_bancos",
        "aceptacion_juridica_automatica",
        "dudosos_promocion_automatica",
    ):
        if reglas.get(clave) is not False:
            raise RuntimeError(f"Regla de seguridad incumplida: {clave}")


def validar_tema_53(data: dict) -> dict:
    tema = localizar_tema(data, "ESPECIAL", 53)
    pseudo = tema.get("pseudoarticulos") or []
    if not pseudo:
        raise RuntimeError("ESPECIAL 53 no contiene pseudoartículos.")

    for unidad in pseudo:
        if unidad.get("estado") == "PENDIENTE_PROPUESTA_IA":
            raise RuntimeError(
                f"{unidad.get('codigo')}: sigue pendiente de propuesta IA."
            )
        prioritarios = {
            int(x["articulo_fuente_id"])
            for x in unidad.get("candidatos_locales_prioritarios") or []
        }
        dudosos = {
            int(x["articulo_fuente_id"])
            for x in unidad.get("dudosos") or []
        }
        solapados = prioritarios & dudosos
        if solapados:
            raise RuntimeError(
                f"{unidad.get('codigo')}: DUDOSO promovido a prioritario: "
                f"{sorted(solapados)}"
            )

    return tema


def main() -> int:
    exigir_archivos()

    # El piloto IA bloquea deliberadamente una sesión de mantenimiento para
    # impedir que openai_api.py registre costes en la BD del proyecto.
    if os.getenv("OPOCOACH_MANTENIMIENTO_SESION_ID", "").strip():
        raise RuntimeError(
            "Existe OPOCOACH_MANTENIMIENTO_SESION_ID. Abra una consola sin esa "
            "variable para ejecutar este smoke test en modo seguro."
        )

    hashes_antes = {p: sha256(p) for p in (DB, C1, C2)}
    SALIDA.mkdir(parents=True, exist_ok=True)

    ejecutar([
        sys.executable,
        str(SCRIPTS / "analizar_convocatoria_v2.py"),
        "--pdf", str(PDF),
        "--db", str(DB),
        "--salida-json", str(MANIFIESTO_INICIAL),
    ])

    inicial = cargar(MANIFIESTO_INICIAL)
    validar_seguridad(inicial)
    inicial_53 = localizar_tema(inicial, "ESPECIAL", 53)
    if inicial_53.get("clasificacion_v2") != "JURIDICO_DOCTRINAL_TRANSVERSAL":
        raise RuntimeError(
            "La clasificación patrón de ESPECIAL 53 ha cambiado: "
            f"{inicial_53.get('clasificacion_v2')}"
        )

    ejecutar([
        sys.executable,
        str(SCRIPTS / "completar_manifiesto_pseudonormas_v2.py"),
        "--manifiesto", str(MANIFIESTO_INICIAL),
        "--salida-json", str(MANIFIESTO_53),
        "--parte", "ESPECIAL",
        "--tema", "53",
    ])

    completado = cargar(MANIFIESTO_53)
    validar_seguridad(completado)
    tema_53 = validar_tema_53(completado)

    # Segunda pasada: no debe volver a ejecutar IA sobre el tema ya completado.
    ejecutar([
        sys.executable,
        str(SCRIPTS / "completar_manifiesto_pseudonormas_v2.py"),
        "--manifiesto", str(MANIFIESTO_53),
        "--salida-json", str(MANIFIESTO_REANUDADO),
        "--parte", "ESPECIAL",
        "--tema", "53",
    ])

    reanudado = cargar(MANIFIESTO_REANUDADO)
    validar_seguridad(reanudado)
    tema_53_reanudado = validar_tema_53(reanudado)
    if tema_53_reanudado != tema_53:
        raise RuntimeError(
            "La segunda pasada alteró ESPECIAL 53; la reanudación no es idempotente."
        )

    hashes_despues = {p: sha256(p) for p in (DB, C1, C2)}
    alterados = [str(p) for p in hashes_antes if hashes_antes[p] != hashes_despues[p]]
    if alterados:
        raise RuntimeError(
            "El smoke test detectó modificación de archivos protegidos:\n  - "
            + "\n  - ".join(alterados)
        )

    print("\n" + "=" * 78)
    print("SMOKE TEST CONSTRUCTOR TEMARIO V2: OK")
    print("=" * 78)
    print("Caso patrón....................... A1 ESPECIAL 53")
    print(f"Pseudoartículos................... {len(tema_53['pseudoarticulos'])}")
    print(f"Dudosos........................... {len(tema_53.get('dudosos') or [])}")
    print("Reanudación/idempotencia.......... OK")
    print("BD consolidada sin cambios........ OK")
    print("temario.csv C1 sin cambios........ OK")
    print("temario.csv C2 sin cambios........ OK")
    print(f"Artefactos........................ {SALIDA}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR SMOKE TEST: {exc}")
        raise SystemExit(1)
