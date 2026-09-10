"""
Constructor Temario V2 - propuesta de normas para temas JURIDICO_DELIMITABLE.

SOLO LECTURA.

Objetivo:
- partir de la clasificación de alcance jurídico V2;
- evaluar exclusivamente temas JURIDICO_DELIMITABLE;
- proponer normas canónicas usando únicamente el catálogo y el corpus oficial
  (normas -> norma_fuentes -> articulos_fuente);
- no consultar temario_referencias ni usar una convocatoria patrón;
- no aceptar normas, no resolver artículos y no modificar la base de datos;
- producir opcionalmente un artefacto JSON estable para revisión y fases
  posteriores del Constructor V2.

El ranking es evidencia para revisión, no una decisión automática.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import evaluar_normas_por_contenido_v2 as contenido
import extraer_estructura_temario_v2 as estructura

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def construir_propuestas(pdf: Path, db: Path, top: int) -> dict:
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    delimitables = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        if clase == "JURIDICO_DELIMITABLE":
            delimitables.append((tema, evidencia))

    with comun.conectar_sqlite_solo_lectura(db) as con:
        docs = contenido.cargar_corpus_normas(con)
        idf = contenido.construir_idf(docs)

        resultados = []
        for tema, evidencia in delimitables:
            ranking = []
            for nid, d in docs.items():
                score, comunes = contenido.puntuacion_tema_norma(
                    tema.texto, d["tokens"], idf
                )
                if score > 0:
                    ranking.append(
                        (score, nid, d["nombre"], d["articulos"], comunes)
                    )
            ranking.sort(key=lambda x: (-x[0], x[1]))

            candidatos = []
            for posicion, (score, nid, nombre, articulos, comunes) in enumerate(
                ranking[:top], 1
            ):
                candidatos.append({
                    "posicion": posicion,
                    "norma_id": nid,
                    "nombre_canonico": nombre,
                    "score_contenido": round(float(score), 6),
                    "articulos_corpus": articulos,
                    "terminos_comunes": comunes,
                    "estado": "PROPUESTO",
                    "decision_humana": None,
                })

            resultados.append({
                "parte": tema.parte,
                "numero_tema": tema.numero_tema,
                "bloque_numero": tema.bloque_numero,
                "bloque_titulo": tema.bloque_titulo,
                "texto_oficial": tema.texto,
                "alcance_v2": "JURIDICO_DELIMITABLE",
                "evidencia_alcance": evidencia,
                "estado": "REVISION_NORMAS",
                "candidatos_norma": candidatos,
            })

    sin_candidatas = sum(1 for r in resultados if not r["candidatos_norma"])
    return {
        "version": 1,
        "naturaleza": "PROPUESTAS_NORMAS_DELIMITABLES_V2",
        "modo": "SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "estado": "REVISION",
        "reglas": {
            "origen_candidatas": "CATALOGO_Y_CORPUS_OFICIAL",
            "consulta_temario_referencias": False,
            "aceptacion_automatica": False,
            "resuelve_articulos": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
        },
        "resumen": {
            "temas_totales": len(temas),
            "temas_delimitables": len(resultados),
            "normas_con_corpus_oficial": len(docs),
            "candidatas_maximas_por_tema": top,
            "temas_sin_candidatas": sin_candidatas,
        },
        "temas": resultados,
    }


def imprimir(payload: dict) -> None:
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - NORMAS CANDIDATAS PARA DELIMITABLES - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {payload['pdf']}")
    print(f"BD............................... {payload['db']}")
    print(f"Temas totales.................... {payload['resumen']['temas_totales']}")
    print(f"Temas JURIDICO_DELIMITABLE....... {payload['resumen']['temas_delimitables']}")
    print(f"Normas con corpus oficial........ {payload['resumen']['normas_con_corpus_oficial']}")
    print(f"Candidatas mostradas por tema.... {payload['resumen']['candidatas_maximas_por_tema']}")
    print()

    for tema in payload["temas"]:
        print(f"{tema['parte']:<8} | {tema['numero_tema']:>2} | JURIDICO_DELIMITABLE")
        print(f"  evidencia alcance: {tema['evidencia_alcance']}")
        print(f"  texto: {tema['texto_oficial']}")
        print("  NORMAS CANDIDATAS POR CONTENIDO OFICIAL:")
        if not tema["candidatos_norma"]:
            print("    SIN CANDIDATAS")
        else:
            for c in tema["candidatos_norma"]:
                muestra = ", ".join(c["terminos_comunes"][:10]) if c["terminos_comunes"] else "-"
                print(
                    f"    {c['posicion']}. [{c['norma_id']}] {c['nombre_canonico']} | "
                    f"score={c['score_contenido']:.3f} | articulos={c['articulos_corpus']} | "
                    f"terminos={muestra}"
                )
        print()

    print("Resumen:")
    print(f"  Temas delimitables............. {payload['resumen']['temas_delimitables']}")
    print(f"  Sin candidatas................. {payload['resumen']['temas_sin_candidatas']}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("No se ha consultado temario_referencias.")
    print("No se han aceptado normas ni resuelto artículos.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Propone normas para temas delimitables sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument(
        "--salida-json",
        help="Ruta opcional del artefacto JSON de propuestas; sólo escribe este fichero.",
    )
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    top = max(1, args.top)

    payload = construir_propuestas(pdf, db, top)
    imprimir(payload)

    if args.salida_json:
        salida = Path(args.salida_json).expanduser().resolve()
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Propuestas JSON.................. {salida}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
