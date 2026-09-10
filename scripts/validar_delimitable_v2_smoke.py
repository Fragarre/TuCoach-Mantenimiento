"""
Smoke test de JURIDICO_DELIMITABLE para Constructor Temario V2.

Caso patrón: A1 GENERAL 2.
SOLO LECTURA respecto de BD, corpus, bancos y temario.csv.

Valida:
- clasificación delimitable estable;
- procesamiento IA V4/V5 de pseudoartículos de trabajo;
- conservación de articulos_reales preexistentes;
- ninguna promoción automática de candidatos a articulos_reales;
- DUDOSO separado de prioritarios;
- límite de 12.000 caracteres por candidato IA;
- reanudación/idempotencia;
- hash de BD y temario.csv C1/C2 sin cambios.
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
SALIDA = RAIZ / "auditorias" / "constructor_v2" / "smoke_delimitable"
INICIAL = SALIDA / "A1_manifiesto_inicial.json"
COMPLETADO = SALIDA / "A1_GENERAL_2_completado.json"
REANUDADO = SALIDA / "A1_GENERAL_2_reanudado.json"
MAX_TEXTO_IA = 12_000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def ejecutar(cmd: list[str]) -> None:
    print("\n" + "=" * 78)
    print(" ".join(f'\"{x}\"' if " " in x else x for x in cmd))
    print("=" * 78)
    r = subprocess.run(cmd, cwd=RAIZ, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"Comando fallido ({r.returncode}): {cmd[1]}")


def cargar(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def tema(data: dict) -> dict:
    encontrados = [
        x for x in data.get("temas", [])
        if str(x.get("parte") or "").upper() == "GENERAL"
        and int(x.get("numero_tema")) == 2
    ]
    if len(encontrados) != 1:
        raise RuntimeError(f"GENERAL 2: localizados={len(encontrados)}")
    return encontrados[0]


def validar_seguridad(data: dict) -> None:
    reglas = data.get("reglas") or {}
    for clave in (
        "modifica_bd",
        "modifica_corpus",
        "modifica_temario_csv",
        "modifica_bancos",
        "aceptacion_juridica_automatica",
    ):
        if reglas.get(clave) is not False:
            raise RuntimeError(f"Regla de seguridad incumplida: {clave}")


def validar_completado(data: dict, articulos_reales_antes: list) -> dict:
    t = tema(data)
    if t.get("clasificacion_v2") != "JURIDICO_DELIMITABLE":
        raise RuntimeError(f"GENERAL 2 cambió de clase: {t.get('clasificacion_v2')}")
    if t.get("articulos_reales") != articulos_reales_antes:
        raise RuntimeError("GENERAL 2 alteró articulos_reales preexistentes.")

    cobertura = t.get("cobertura_hibrida") or {}
    if cobertura.get("promocion_automatica_a_articulo_real") is not False:
        raise RuntimeError("Falta garantía de no promoción automática.")

    pseudo = t.get("pseudoarticulos") or []
    if not pseudo:
        raise RuntimeError("GENERAL 2 no contiene pseudoartículos tras completar.")

    for p in pseudo:
        if p.get("estado") == "PENDIENTE_PROPUESTA_IA":
            raise RuntimeError(f"{p.get('codigo')}: sigue pendiente de IA.")
        prioritarios = {
            int(x["articulo_fuente_id"])
            for x in p.get("candidatos_locales_prioritarios") or []
        }
        dudosos = {
            int(x["articulo_fuente_id"])
            for x in p.get("dudosos") or []
        }
        if prioritarios & dudosos:
            raise RuntimeError(f"{p.get('codigo')}: DUDOSO promovido a prioritario.")

    controles = [
        x.get("control_recursos") or {}
        for x in t.get("trazabilidad") or []
        if x.get("tipo") == "PROPUESTA_PSEUDONORMATIVA_IA_V4_NORMALIZADA_V5"
    ]
    if not controles:
        raise RuntimeError("No consta control_recursos en trazabilidad.")
    max_enviado = max(int(c.get("max_enviado_caracteres") or 0) for c in controles)
    if max_enviado > MAX_TEXTO_IA:
        raise RuntimeError(f"Límite IA superado: {max_enviado}>{MAX_TEXTO_IA}")
    return t


def main() -> int:
    for p in (DB, PDF, C1, C2):
        if not p.is_file():
            raise RuntimeError(f"Falta archivo requerido: {p}")
    if os.getenv("OPOCOACH_MANTENIMIENTO_SESION_ID", "").strip():
        raise RuntimeError("Existe OPOCOACH_MANTENIMIENTO_SESION_ID; use una consola segura.")

    hashes_antes = {p: sha256(p) for p in (DB, C1, C2)}
    SALIDA.mkdir(parents=True, exist_ok=True)

    ejecutar([
        sys.executable,
        str(SCRIPTS / "analizar_convocatoria_v2.py"),
        "--pdf", str(PDF),
        "--db", str(DB),
        "--salida-json", str(INICIAL),
    ])
    inicial = cargar(INICIAL)
    validar_seguridad(inicial)
    t0 = tema(inicial)
    if t0.get("clasificacion_v2") != "JURIDICO_DELIMITABLE":
        raise RuntimeError(f"GENERAL 2 no es delimitable: {t0.get('clasificacion_v2')}")
    articulos_reales_antes = t0.get("articulos_reales") or []

    ejecutar([
        sys.executable,
        str(SCRIPTS / "completar_manifiesto_delimitables_v2.py"),
        "--manifiesto", str(INICIAL),
        "--salida-json", str(COMPLETADO),
        "--parte", "GENERAL",
        "--tema", "2",
        "--top", "4",
    ])
    completado = cargar(COMPLETADO)
    validar_seguridad(completado)
    t1 = validar_completado(completado, articulos_reales_antes)

    ejecutar([
        sys.executable,
        str(SCRIPTS / "completar_manifiesto_delimitables_v2.py"),
        "--manifiesto", str(COMPLETADO),
        "--salida-json", str(REANUDADO),
        "--parte", "GENERAL",
        "--tema", "2",
        "--top", "4",
    ])
    reanudado = cargar(REANUDADO)
    validar_seguridad(reanudado)
    t2 = validar_completado(reanudado, articulos_reales_antes)
    if t2 != t1:
        raise RuntimeError("La reanudación alteró GENERAL 2.")

    hashes_despues = {p: sha256(p) for p in (DB, C1, C2)}
    alterados = [str(p) for p in hashes_antes if hashes_antes[p] != hashes_despues[p]]
    if alterados:
        raise RuntimeError("Archivos protegidos modificados: " + ", ".join(alterados))

    cobertura = t1["cobertura_hibrida"]
    print("\n" + "=" * 78)
    print("SMOKE TEST DELIMITABLE V2: OK")
    print("=" * 78)
    print("Caso patrón....................... A1 GENERAL 2")
    print(f"Artículos reales conservados...... {cobertura['articulos_reales_conservados']}")
    print(f"Candidatos pendientes verificar... {cobertura['candidatos_reales_pendientes_verificacion']}")
    print(f"Pseudoartículos................... {cobertura['pseudoarticulos']}")
    print("Promoción automática.............. NO")
    print("Límite recursos................... OK")
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
        print(f"\nERROR SMOKE DELIMITABLE: {exc}")
        raise SystemExit(1)
