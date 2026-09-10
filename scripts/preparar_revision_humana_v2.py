"""
Constructor Temario V2 - preparación de cola de revisión humana.

SOLO LECTURA respecto de BD, corpus, temario.csv y bancos.

Objetivo:
- leer el manifiesto común V2 ya generado;
- seleccionar únicamente temas PENDIENTE_REVISION_HUMANA;
- para JURIDICO_EXPLICITO conservar las normas ya resueltas literalmente y
  señalar qué normas carecen todavía de artículos;
- para JURIDICO_DELIMITABLE adjuntar candidatas de norma como ayuda de revisión,
  sin aceptarlas ni convertir sus puntuaciones en decisión jurídica;
- registrar las combinaciones norma-artículo ya asignadas por el manifiesto para
  impedir que se asignen a un segundo tema durante la revisión humana;
- producir un artefacto JSON estable que pueda alimentar posteriormente una UI.

No modifica datos consolidados y no consulta temario_referencias.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import proponer_normas_delimitables_v2 as propuestas


def cargar_json(ruta: Path) -> dict:
    if not ruta.is_file():
        raise SystemExit(f"ERROR: no existe {ruta}")
    return json.loads(ruta.read_text(encoding="utf-8"))


def normalizar_articulo(valor) -> str:
    return " ".join(str(valor or "").strip().lower().split())


def articulos_por_norma(item: dict) -> dict[int, list[dict]]:
    salida: dict[int, list[dict]] = {}
    for art in item.get("articulos", []):
        try:
            nid = int(art["norma_id"])
        except (KeyError, TypeError, ValueError):
            continue
        salida.setdefault(nid, []).append(art)
    return salida


def construir_ocupacion_inicial(manifiesto: dict) -> list[dict]:
    """Registra cada pareja norma-artículo ya asignada por el manifiesto.

    Si el propio manifiesto contuviera la misma pareja en dos temas distintos,
    no la resuelve: conserva todos los propietarios para que el revisor pueda
    bloquear la situación en lugar de ocultarla.
    """
    por_clave: dict[tuple[int, str], dict] = {}

    for item in manifiesto.get("temas", []):
        propietario = {
            "parte": item.get("parte"),
            "numero_tema": item.get("numero_tema"),
        }
        for art in item.get("articulos", []):
            try:
                nid = int(art["norma_id"])
            except (KeyError, TypeError, ValueError):
                continue
            etiqueta = normalizar_articulo(art.get("articulo"))
            if not etiqueta:
                continue
            clave = (nid, etiqueta)
            registro = por_clave.setdefault(
                clave,
                {
                    "norma_id": nid,
                    "articulo": etiqueta,
                    "propietarios": [],
                },
            )
            if propietario not in registro["propietarios"]:
                registro["propietarios"].append(propietario)

    return sorted(
        por_clave.values(),
        key=lambda x: (x["norma_id"], x["articulo"]),
    )


def construir(manifiesto: dict, top_candidatas: int) -> dict:
    pdf = Path(manifiesto["pdf"])
    db = Path(manifiesto["db"])

    delimitables = propuestas.construir_propuestas(pdf, db, top_candidatas)
    candidatas_por_tema = {
        (x["parte"], int(x["numero_tema"])): x.get("candidatos_norma", [])
        for x in delimitables.get("temas", [])
    }

    revisiones = []
    for item in manifiesto.get("temas", []):
        if item.get("estado_v2") != "PENDIENTE_REVISION_HUMANA":
            continue

        clave = (item["parte"], int(item["numero_tema"]))
        clase = item.get("clasificacion_v2")
        arts = articulos_por_norma(item)

        revision = {
            "parte": item["parte"],
            "numero_tema": item["numero_tema"],
            "texto_oficial": item["texto_oficial"],
            "clasificacion_v2": clase,
            "motivo_revision": [],
            "normas_resueltas_literalmente": [],
            "normas_candidatas": [],
            "decision_humana": {
                "normas_aceptadas": [],
                "bloques_aceptados": [],
                "articulos_incluidos": [],
                "articulos_excluidos": [],
                "estado": "PENDIENTE",
            },
        }

        for inc in item.get("incidencias", []):
            tipo = inc.get("tipo") if isinstance(inc, dict) else str(inc)
            if tipo and tipo not in revision["motivo_revision"]:
                revision["motivo_revision"].append(tipo)

        if clase == "JURIDICO_EXPLICITO":
            for n in item.get("normas", []):
                nid = int(n["norma_id"])
                revision["normas_resueltas_literalmente"].append({
                    "norma_id": nid,
                    "nombre_canonico": n.get("nombre_canonico"),
                    "articulos_ya_resueltos": len(arts.get(nid, [])),
                    "requiere_delimitar_alcance": not bool(arts.get(nid)),
                })

        elif clase == "JURIDICO_DELIMITABLE":
            for c in candidatas_por_tema.get(clave, []):
                revision["normas_candidatas"].append({
                    "posicion": c.get("posicion"),
                    "norma_id": c.get("norma_id"),
                    "nombre_canonico": c.get("nombre_canonico"),
                    "score_contenido": c.get("score_contenido"),
                    "terminos_comunes": c.get("terminos_comunes", []),
                    "estado": "SOLO_PROPUESTA",
                })

        else:
            revision["motivo_revision"].append("CAMINO_ESPECIFICO_NO_CUBIERTO_POR_ESTA_COLA")

        revisiones.append(revision)

    ocupacion = construir_ocupacion_inicial(manifiesto)
    conflictos_iniciales = [x for x in ocupacion if len(x["propietarios"]) > 1]

    return {
        "version": 2,
        "naturaleza": "COLA_REVISION_HUMANA_V2",
        "modo": "SOLO_LECTURA",
        "origen_manifiesto": manifiesto.get("naturaleza"),
        "pdf": manifiesto.get("pdf"),
        "db": manifiesto.get("db"),
        "reglas": {
            "consulta_temario_referencias": False,
            "aceptacion_automatica": False,
            "unicidad_norma_articulo_por_convocatoria": True,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
            "modifica_bancos": False,
        },
        "ocupacion_inicial": ocupacion,
        "conflictos_ocupacion_inicial": conflictos_iniciales,
        "resumen": {
            "temas_revision": len(revisiones),
            "explicitos": sum(1 for x in revisiones if x["clasificacion_v2"] == "JURIDICO_EXPLICITO"),
            "delimitables": sum(1 for x in revisiones if x["clasificacion_v2"] == "JURIDICO_DELIMITABLE"),
            "pares_norma_articulo_ocupados": len(ocupacion),
            "conflictos_ocupacion_inicial": len(conflictos_iniciales),
        },
        "temas": revisiones,
    }


def imprimir(payload: dict) -> None:
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - COLA DE REVISION HUMANA - SOLO LECTURA")
    print("=" * 78)
    r = payload["resumen"]
    print(f"Temas pendientes................ {r['temas_revision']}")
    print(f"  JURIDICO_EXPLICITO............ {r['explicitos']}")
    print(f"  JURIDICO_DELIMITABLE.......... {r['delimitables']}")
    print(f"Pares norma-artículo ocupados... {r['pares_norma_articulo_ocupados']}")
    print(f"Conflictos iniciales............. {r['conflictos_ocupacion_inicial']}")
    print()

    for x in payload["temas"]:
        print(f"{x['parte']:<8} | {int(x['numero_tema']):>2} | {x['clasificacion_v2']}")
        if x["normas_resueltas_literalmente"]:
            for n in x["normas_resueltas_literalmente"]:
                estado = "DELIMITAR" if n["requiere_delimitar_alcance"] else f"{n['articulos_ya_resueltos']} ART. YA RESUELTOS"
                print(f"  [{n['norma_id']}] {n['nombre_canonico']} -> {estado}")
        if x["normas_candidatas"]:
            print("  candidatas orientativas:")
            for c in x["normas_candidatas"]:
                print(f"    {c['posicion']}. [{c['norma_id']}] {c['nombre_canonico']} | score={c['score_contenido']}")
        if x["motivo_revision"]:
            print("  motivo: " + ", ".join(x["motivo_revision"]))
        print()

    if payload["conflictos_ocupacion_inicial"]:
        print("ATENCIÓN: el manifiesto ya contiene combinaciones norma-artículo asignadas a más de un tema.")
        print("La revisión debe considerarse bloqueada hasta resolver esos conflictos.")
    else:
        print("Regla de unicidad norma-artículo: sin conflictos en la ocupación inicial.")
    print("No se ha consultado temario_referencias.")
    print("No se ha aceptado automáticamente ninguna norma ni artículo.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("temario.csv NO ha sido modificado.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepara la cola de revisión humana V2 sin persistencia.")
    ap.add_argument("--manifiesto", required=True)
    ap.add_argument("--salida-json", required=True)
    ap.add_argument("--top-candidatas", type=int, default=5)
    args = ap.parse_args()

    manifiesto_ruta = Path(args.manifiesto).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    manifiesto = cargar_json(manifiesto_ruta)

    payload = construir(manifiesto, max(1, args.top_candidatas))
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    imprimir(payload)
    print(f"Cola JSON........................ {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
