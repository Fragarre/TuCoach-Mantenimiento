"""
Constructor Temario V2 - propuesta de normas para temas JURIDICO_DELIMITABLE.

SOLO LECTURA.

Objetivo:
- partir de la clasificación de alcance jurídico V2;
- evaluar exclusivamente temas JURIDICO_DELIMITABLE;
- proponer normas canónicas usando únicamente el catálogo y el corpus oficial
  (normas -> norma_fuentes -> articulos_fuente);
- no consultar temario_referencias ni usar una convocatoria patrón;
- no aceptar normas, no resolver artículos y no modificar la base de datos.

El ranking es evidencia para revisión, no una decisión automática.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import evaluar_normas_por_contenido_v2 as contenido
import extraer_estructura_temario_v2 as estructura

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Propone normas para temas delimitables sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    top = max(1, args.top)

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
            resultados.append((tema, evidencia, ranking[:top]))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - NORMAS CANDIDATAS PARA DELIMITABLES - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Temas totales.................... {len(temas)}")
    print(f"Temas JURIDICO_DELIMITABLE....... {len(delimitables)}")
    print(f"Normas con corpus oficial........ {len(docs)}")
    print(f"Candidatas mostradas por tema.... {top}")
    print()

    sin_candidatas = 0
    for tema, evidencia, ranking in resultados:
        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | JURIDICO_DELIMITABLE")
        print(f"  evidencia alcance: {evidencia}")
        print(f"  texto: {tema.texto}")
        print("  NORMAS CANDIDATAS POR CONTENIDO OFICIAL:")
        if not ranking:
            sin_candidatas += 1
            print("    SIN CANDIDATAS")
        else:
            for i, (score, nid, nombre, articulos, comunes) in enumerate(ranking, 1):
                muestra = ", ".join(comunes[:10]) if comunes else "-"
                print(
                    f"    {i}. [{nid}] {nombre} | score={score:.3f} | "
                    f"articulos={articulos} | terminos={muestra}"
                )
        print()

    print("Resumen:")
    print(f"  Temas delimitables............. {len(resultados)}")
    print(f"  Sin candidatas................. {sin_candidatas}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("No se ha consultado temario_referencias.")
    print("No se han aceptado normas ni resuelto artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
