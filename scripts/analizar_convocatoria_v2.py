"""
Constructor Temario V2 - manifiesto GENERAL de análisis de una convocatoria.

SOLO LECTURA respecto de la base de datos y del corpus.

Objetivo:
- extraer literalmente los temas del PDF oficial;
- clasificar cada tema con el clasificador de alcance V2 ya existente;
- producir un manifiesto estable que indique el tratamiento siguiente;
- no resolver ni aceptar automáticamente normas, artículos o pseudonormas.

Este script es el punto de entrada general de la fase de análisis. No sustituye
los scripts especializados: los coordina conceptualmente mediante un manifiesto
que puede consumir una futura interfaz/orquestador.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance


ACCION_POR_CLASE = {
    "NO_JURIDICO": "TRATAR_COMO_TEMA_NO_JURIDICO",
    "JURIDICO_EXPLICITO": "RESOLVER_REFERENCIAS_EXPLICITAS",
    "JURIDICO_DELIMITABLE": "PROPONER_Y_REVISAR_NORMAS_ARTICULOS",
    "JURIDICO_DOCTRINAL_TRANSVERSAL": "CONSTRUIR_PSEUDONORMA_V2",
    "REVISION": "REVISION_HUMANA_DE_CLASIFICACION",
}

ESTADO_POR_CLASE = {
    "NO_JURIDICO": "LISTO_PARA_TRATAMIENTO_ESPECIFICO",
    "JURIDICO_EXPLICITO": "LISTO_PARA_RESOLUCION_JURIDICA",
    "JURIDICO_DELIMITABLE": "LISTO_PARA_PROPUESTA_JURIDICA",
    "JURIDICO_DOCTRINAL_TRANSVERSAL": "LISTO_PARA_PSEUDONORMA",
    "REVISION": "PENDIENTE_REVISION",
}


def construir_manifiesto(pdf: Path) -> dict:
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    items = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        items.append({
            "parte": tema.parte,
            "bloque_numero": tema.bloque_numero,
            "bloque_titulo": tema.bloque_titulo,
            "numero_tema": tema.numero_tema,
            "texto_oficial": tema.texto,
            "clasificacion_estructural": tema.clasificacion,
            "evidencia_estructural": tema.evidencia_clasificacion,
            "alcance_v2": clase,
            "evidencia_alcance": evidencia,
            "estado": ESTADO_POR_CLASE.get(clase, "BLOQUEADO"),
            "accion_siguiente": ACCION_POR_CLASE.get(clase, "REVISION_MANUAL"),
            "decision_humana_requerida_ahora": clase == "REVISION",
        })

    conteo = Counter(x["alcance_v2"] for x in items)
    pendientes = sum(1 for x in items if x["decision_humana_requerida_ahora"])

    return {
        "version": 1,
        "naturaleza": "MANIFIESTO_ANALISIS_CONVOCATORIA_V2",
        "modo": "SOLO_LECTURA",
        "pdf": str(pdf),
        "estado": "REVISION" if pendientes else "ANALISIS_COMPLETADO",
        "reglas": {
            "texto_oficial_conservado": True,
            "aceptacion_juridica_automatica": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
            "modifica_bancos": False,
        },
        "resumen": {
            "temas_total": len(items),
            "por_alcance": dict(sorted(conteo.items())),
            "pendientes_revision_clasificacion": pendientes,
        },
        "temas": items,
    }


def imprimir(manifiesto: dict) -> None:
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - ANALISIS GENERAL DE CONVOCATORIA - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {manifiesto['pdf']}")
    print(f"Temas............................ {manifiesto['resumen']['temas_total']}")
    print(f"Estado........................... {manifiesto['estado']}")
    print()

    orden = (
        "JURIDICO_EXPLICITO",
        "JURIDICO_DELIMITABLE",
        "JURIDICO_DOCTRINAL_TRANSVERSAL",
        "NO_JURIDICO",
        "REVISION",
    )
    conteo = manifiesto["resumen"]["por_alcance"]
    for clase in orden:
        print(f"{clase:<34} {conteo.get(clase, 0)}")

    print()
    for x in manifiesto["temas"]:
        print(
            f"{x['parte']:<8} | {x['numero_tema']:>2} | "
            f"{x['alcance_v2']:<32} | {x['accion_siguiente']}"
        )
        if x["alcance_v2"] == "REVISION":
            print(f"  evidencia: {x['evidencia_alcance']}")
            print(f"  texto: {x['texto_oficial']}")

    print()
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("temario.csv NO ha sido modificado.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Analiza una convocatoria y crea el manifiesto general V2 sin modificar datos."
    )
    ap.add_argument("--pdf", required=True, help="Ruta al PDF oficial del temario")
    ap.add_argument("--salida-json", required=True, help="Ruta del manifiesto JSON")
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF {pdf}")

    manifiesto = construir_manifiesto(pdf)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")

    imprimir(manifiesto)
    print(f"Manifiesto JSON.................. {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
