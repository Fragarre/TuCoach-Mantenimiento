"""
Constructor Temario V2 - materialización GENERAL de evidencia aceptada.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO DECIDE PERTINENCIA.

Cruza una auditoría de pertinencia con un artefacto explícito de decisiones humanas.
Solo materializa relaciones ya decididas por una persona. Para relaciones PERTINENTE
conserva los mismos fragmentos priorizados que el dossier de revisión humana, de forma
reproducible, junto con posiciones, anclas y fundamento humano.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def norm(s: str) -> str:
    return " ".join((s or "").split())


def cobertura_anclas(fragmento: str, anclas: list[str]) -> int:
    bajo = fragmento.casefold()
    return sum(1 for a in anclas if a.casefold() in bajo)


def solapa(a: dict, b: dict, margen: int = 180) -> bool:
    return not (
        int(a.get("fin", 0)) + margen < int(b.get("inicio", 0))
        or int(b.get("fin", 0)) + margen < int(a.get("inicio", 0))
    )


def seleccionar_fragmentos(ficha: dict, maximo: int) -> list[dict]:
    anclas = ficha.get("anclas") or []
    candidatos = []
    for f in ficha.get("fragmentos_candidatos") or []:
        frag = norm(f.get("fragmento", ""))
        if not frag:
            continue
        x = dict(f)
        x["fragmento"] = frag
        x["cobertura_anclas"] = cobertura_anclas(frag, anclas)
        candidatos.append(x)
    candidatos.sort(key=lambda x: (-int(x.get("cobertura_anclas", 0)), int(x.get("inicio", 0))))
    elegidos: list[dict] = []
    for cand in candidatos:
        if any(solapa(cand, e) for e in elegidos):
            continue
        elegidos.append(cand)
        if len(elegidos) >= maximo:
            break
    return elegidos


def clave(x: dict) -> tuple[str, str]:
    return (str(x.get("codigo_proposicion") or ""), str(x.get("identificador_oficial") or ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Materializa evidencia de decisiones humanas V2 sin BD/corpus.")
    ap.add_argument("--pertinencia-json", required=True)
    ap.add_argument("--decisiones-json", required=True)
    ap.add_argument("--salida-json", required=True)
    ap.add_argument("--max-fragmentos", type=int, default=3)
    args = ap.parse_args()

    p_path = Path(args.pertinencia_json).expanduser().resolve()
    d_path = Path(args.decisiones_json).expanduser().resolve()
    s_path = Path(args.salida_json).expanduser().resolve()
    pertinencia = json.loads(p_path.read_text(encoding="utf-8"))
    decisiones = json.loads(d_path.read_text(encoding="utf-8"))

    if pertinencia.get("naturaleza") != "AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2":
        raise SystemExit("ERROR: pertinencia-json no es una auditoría V2 válida.")
    if pertinencia.get("estado") != "LISTO_PARA_REVISION_HUMANA":
        raise SystemExit("ERROR: la auditoría de pertinencia no está LISTO_PARA_REVISION_HUMANA.")
    if decisiones.get("naturaleza") != "DECISIONES_HUMANAS_PERTINENCIA_V2":
        raise SystemExit("ERROR: decisiones-json no es un artefacto de decisiones humanas V2.")
    if decisiones.get("estado") != "REVISION_HUMANA_COMPLETADA":
        raise SystemExit("ERROR: las decisiones humanas no están completadas.")
    if pertinencia.get("caso") != decisiones.get("caso"):
        raise SystemExit("ERROR: caso distinto entre pertinencia y decisiones.")
    if pertinencia.get("pseudonorma") != decisiones.get("pseudonorma"):
        raise SystemExit("ERROR: pseudonorma distinta entre pertinencia y decisiones.")

    fichas = pertinencia.get("fichas") or []
    decs = decisiones.get("decisiones") or []
    por_ficha = {clave(x): x for x in fichas}
    por_decision = {clave(x): x for x in decs}
    if len(por_ficha) != len(fichas):
        raise SystemExit("ERROR: relaciones duplicadas en auditoría de pertinencia.")
    if len(por_decision) != len(decs):
        raise SystemExit("ERROR: relaciones duplicadas en decisiones humanas.")

    faltan_decision = sorted(set(por_ficha) - set(por_decision))
    decisiones_sin_ficha = sorted(set(por_decision) - set(por_ficha))
    if faltan_decision or decisiones_sin_ficha:
        raise SystemExit(
            "ERROR: decisiones y fichas no coinciden 1:1. "
            f"Sin decisión={faltan_decision}; sin ficha={decisiones_sin_ficha}"
        )

    relaciones = []
    incidencias = []
    total_fragmentos = 0
    for k, ficha in por_ficha.items():
        dec = por_decision[k]
        decision = str(dec.get("decision") or "").upper()
        if decision not in {"PERTINENTE", "DESCARTADO", "PENDIENTE"}:
            incidencias.append(f"Decisión no válida {k}: {decision}")
            continue
        evidencia = seleccionar_fragmentos(ficha, max(1, args.max_fragmentos)) if decision == "PERTINENTE" else []
        if decision == "PERTINENTE" and not evidencia:
            incidencias.append(f"Relación PERTINENTE sin evidencia materializable: {k}")
        total_fragmentos += len(evidencia)
        relaciones.append({
            "codigo_proposicion": k[0],
            "proposicion": ficha.get("proposicion"),
            "identificador_oficial": k[1],
            "tipo_fuente": ficha.get("tipo_fuente"),
            "decision_humana": decision,
            "fundamento_humano": dec.get("fundamento"),
            "evidencia_aceptada": evidencia,
        })

    estado = "EVIDENCIA_HUMANA_MATERIALIZADA" if not incidencias else "BLOQUEADO"
    payload = {
        "version": 1,
        "naturaleza": "EVIDENCIA_HUMANA_PERTINENCIA_V2",
        "caso": decisiones.get("caso"),
        "pseudonorma": decisiones.get("pseudonorma"),
        "unidad": decisiones.get("unidad") or pertinencia.get("unidad"),
        "estado": estado,
        "entradas": {
            "pertinencia": str(p_path),
            "decisiones_humanas": str(d_path),
        },
        "reglas": {
            "decision_humana_preexistente": True,
            "seleccion_reproduce_dossier_revision": True,
            "aceptacion_automatica": False,
            "modifica_bd": False,
            "modifica_corpus": False,
        },
        "relaciones": relaciones,
        "incidencias": incidencias,
        "resultado": {
            "relaciones": len(relaciones),
            "pertinentes": sum(1 for x in relaciones if x["decision_humana"] == "PERTINENTE"),
            "descartadas": sum(1 for x in relaciones if x["decision_humana"] == "DESCARTADO"),
            "pendientes": sum(1 for x in relaciones if x["decision_humana"] == "PENDIENTE"),
            "fragmentos_evidencia": total_fragmentos,
        },
    }
    s_path.parent.mkdir(parents=True, exist_ok=True)
    s_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - MATERIALIZACION EVIDENCIA HUMANA - SIN BD")
    print("=" * 78)
    print(f"Caso.............................. {payload['caso']}")
    print(f"Relaciones......................... {len(relaciones)}")
    print(f"PERTINENTE......................... {payload['resultado']['pertinentes']}")
    print(f"Fragmentos de evidencia............ {total_fragmentos}")
    print(f"Incidencias......................... {len(incidencias)}")
    print(f"Estado.............................. {estado}")
    print(f"JSON................................ {s_path}")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0 if not incidencias else 2


if __name__ == "__main__":
    raise SystemExit(main())
