"""
Constructor Temario V2 - normalizador determinista de propuestas IA V4.

SOLO trabaja sobre un JSON de auditoria ya generado por
proponer_contenido_pseudonormas_ia_v4.py.

No llama a IA. No consulta ni modifica BD, corpus, bancos ni temario.csv.

Regla:
- PLAUSIBLE_DIRECTO -> candidatos_locales_prioritarios
- NO_PERTINENTE     -> candidatos_locales_probablemente_no_pertinentes
- DUDOSO             -> permanece solo en evaluaciones_candidatos

El objetivo es impedir que la segunda sintesis IA contradiga la evaluacion
individual del texto literal de cada articulo.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


DECISIONES = {"PLAUSIBLE_DIRECTO", "NO_PERTINENTE", "DUDOSO"}


def cargar(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("El JSON raiz debe ser un objeto.")
    if int(data.get("version", 0)) != 4:
        raise ValueError("Se esperaba un JSON V4.")
    if data.get("modo") != "PILOTO_SOLO_LECTURA":
        raise ValueError("Modo inesperado.")
    if not isinstance(data.get("evaluaciones_candidatos"), dict):
        raise ValueError("Faltan evaluaciones_candidatos.")
    propuesta = data.get("propuesta_ia")
    if not isinstance(propuesta, dict) or not isinstance(propuesta.get("unidades"), list):
        raise ValueError("Falta propuesta_ia.unidades.")
    return data


def normalizar(data: dict) -> dict:
    evaluaciones = data["evaluaciones_candidatos"]
    unidades = data["propuesta_ia"]["unidades"]

    total_plausibles = 0
    total_no_pertinentes = 0
    total_dudosos = 0

    for unidad in unidades:
        codigo = str(unidad.get("codigo") or "")
        evs = evaluaciones.get(codigo)
        if not isinstance(evs, list):
            raise ValueError(f"{codigo}: faltan evaluaciones.")

        vistos = set()
        prioritarios = []
        descartables = []
        dudosos = 0

        for e in evs:
            aid = int(e.get("articulo_fuente_id"))
            if aid in vistos:
                raise ValueError(f"{codigo}: evaluacion duplicada para articulo_fuente_id={aid}.")
            vistos.add(aid)

            decision = e.get("decision")
            if decision not in DECISIONES:
                raise ValueError(f"{codigo}: decision invalida {decision!r}.")
            motivo = str(e.get("motivo") or "").strip()
            if not motivo:
                raise ValueError(f"{codigo}: falta motivo para articulo_fuente_id={aid}.")

            item = {
                "articulo_fuente_id": aid,
                "motivo": motivo,
                "estado": "SOLO_PROPUESTA",
            }
            if decision == "PLAUSIBLE_DIRECTO":
                prioritarios.append(item)
                total_plausibles += 1
            elif decision == "NO_PERTINENTE":
                descartables.append(item)
                total_no_pertinentes += 1
            else:
                dudosos += 1
                total_dudosos += 1

        unidad["candidatos_locales_prioritarios"] = prioritarios
        unidad["candidatos_locales_probablemente_no_pertinentes"] = descartables

        print(
            f"  {codigo} | prioritarios={len(prioritarios)} | "
            f"descartables={len(descartables)} | dudosos={dudosos}"
        )

    data["version"] = 5
    data["normalizacion_local"] = {
        "regla": {
            "PLAUSIBLE_DIRECTO": "candidatos_locales_prioritarios",
            "NO_PERTINENTE": "candidatos_locales_probablemente_no_pertinentes",
            "DUDOSO": "solo_evaluaciones_candidatos",
        },
        "totales": {
            "plausibles_directos": total_plausibles,
            "no_pertinentes": total_no_pertinentes,
            "dudosos": total_dudosos,
        },
        "llamadas_ia_adicionales": 0,
        "modifica_bd": False,
        "modifica_corpus": False,
        "modifica_bancos": False,
    }
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Normaliza un JSON V4 sin llamar a IA ni modificar datos.")
    ap.add_argument("--entrada-json", required=True)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    entrada = Path(args.entrada_json).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    if not entrada.is_file():
        raise SystemExit(f"ERROR: no existe el JSON de entrada: {entrada}")
    if entrada == salida:
        raise SystemExit("ERROR: la salida debe ser distinta de la entrada para conservar la auditoria V4 original.")

    data = cargar(entrada)
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - NORMALIZACION LOCAL V4 - SOLO JSON")
    print("=" * 78)
    print(f"Entrada........................... {entrada}")
    print()

    data = normalizar(data)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"Salida............................ {salida}")
    print("No se ha llamado a IA.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("Los bancos NO han sido modificados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
