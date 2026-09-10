"""
Constructor Temario V2 - auditoría del ranking de artículos delimitables.

SOLO LECTURA.

Esta herramienta es exclusivamente de evaluación. Comprueba si, una vez conocida
la norma correcta de un tema JURIDICO_DELIMITABLE, el ranking de artículos basado
sólo en el texto oficial del tema y el corpus oficial sitúa las referencias ya
consolidadas en posiciones útiles.

Regla de separación:
- `temario_referencias` NO participa en la puntuación ni en el orden de candidatos;
- se consulta únicamente para obtener la verdad de comparación y, en esta prueba,
  la norma que simula una selección humana correcta;
- producción seguirá exigiendo selección/revisión humana de la norma.

No modifica BD, corpus, temario.csv ni bancos.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import evaluar_normas_por_contenido_v2 as contenido
import extraer_estructura_temario_v2 as estructura
import proponer_articulos_delimitables_v2 as propuesta

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def cargar_verdad(con, convocatoria_id: int, parte: str, numero_tema: int) -> dict[int, set[int]]:
    """Verdad consolidada por norma, sólo para medir el ranking generado aparte."""
    filas = con.execute(
        """
        SELECT DISTINCT
               tr.norma_id,
               tr.articulo_fuente_id
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        WHERE t.convocatoria_id=?
          AND UPPER(tt.parte)=UPPER(?)
          AND tt.numero_tema=?
          AND tr.estado='COMPLETADO'
          AND tr.norma_id IS NOT NULL
          AND tr.articulo_fuente_id IS NOT NULL
        ORDER BY tr.norma_id, tr.articulo_fuente_id
        """,
        (convocatoria_id, parte, numero_tema),
    ).fetchall()
    resultado: dict[int, set[int]] = defaultdict(set)
    for r in filas:
        resultado[int(r["norma_id"])].add(int(r["articulo_fuente_id"]))
    return dict(resultado)


def ranking_completo(texto_tema: str, articulos: list[dict]) -> list[tuple[float, dict, list[str]]]:
    """Reproduce la lógica productiva de propuesta, sin consultar la verdad."""
    idf = propuesta.construir_idf_articulos(articulos)
    ranking = []
    for a in articulos:
        score, comunes = contenido.puntuacion_tema_norma(texto_tema, a["tokens"], idf)
        if score <= 0:
            continue
        ranking.append((score, a, comunes))
    ranking.sort(key=lambda x: (-x[0], x[1]["articulo_fuente_id"]))
    return ranking


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mide el ranking de artículos V2 contra una convocatoria consolidada, sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--convocatoria-patron", required=True, type=int)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--mostrar-top", type=int, default=10)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    mostrar_top = max(1, args.mostrar_top)

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    delimitables = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        if clase == "JURIDICO_DELIMITABLE":
            delimitables.append((tema, evidencia))

    filas_resultado = []
    with comun.conectar_sqlite_solo_lectura(db) as con:
        for tema, evidencia in delimitables:
            verdad = cargar_verdad(
                con, args.convocatoria_patron, tema.parte, tema.numero_tema
            )
            for norma_id, verdad_ids in sorted(verdad.items()):
                articulos = propuesta.cargar_articulos_normas(con, [norma_id])
                ranking = ranking_completo(tema.texto, articulos)
                posiciones = {
                    int(a["articulo_fuente_id"]): pos
                    for pos, (_, a, _) in enumerate(ranking, 1)
                }
                posiciones_verdad = [
                    posiciones.get(aid)
                    for aid in sorted(verdad_ids)
                ]
                filas_resultado.append(
                    {
                        "tema": tema,
                        "evidencia": evidencia,
                        "norma_id": norma_id,
                        "verdad_ids": verdad_ids,
                        "ranking": ranking,
                        "posiciones_verdad": posiciones_verdad,
                    }
                )

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA RANKING ARTICULOS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patrón.............. {args.convocatoria_patron}")
    print(f"Temas JURIDICO_DELIMITABLE....... {len(delimitables)}")
    print(f"Pares tema-norma evaluados....... {len(filas_resultado)}")
    print()
    print("La verdad consolidada sólo se usa después del ranking para medirlo.")
    print("La norma consolidada simula en esta auditoría una selección humana correcta.")
    print()

    refs_total = 0
    refs_con_score = 0
    top1 = top5 = top10 = top20 = 0
    pares_todas_top10 = 0
    pares_todas_top20 = 0

    for r in filas_resultado:
        tema = r["tema"]
        ranking = r["ranking"]
        posiciones = r["posiciones_verdad"]
        refs_total += len(posiciones)
        refs_con_score += sum(p is not None for p in posiciones)
        top1 += sum(p is not None and p <= 1 for p in posiciones)
        top5 += sum(p is not None and p <= 5 for p in posiciones)
        top10 += sum(p is not None and p <= 10 for p in posiciones)
        top20 += sum(p is not None and p <= 20 for p in posiciones)
        if posiciones and all(p is not None and p <= 10 for p in posiciones):
            pares_todas_top10 += 1
        if posiciones and all(p is not None and p <= 20 for p in posiciones):
            pares_todas_top20 += 1

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | norma_id={r['norma_id']}")
        print(f"  referencias consolidadas....... {len(posiciones)}")
        print(
            "  posiciones..................... "
            + ", ".join("SIN_SCORE" if p is None else str(p) for p in posiciones)
        )
        print("  TOP CANDIDATOS:")
        for pos, (score, a, comunes) in enumerate(ranking[:mostrar_top], 1):
            marca = " *" if int(a["articulo_fuente_id"]) in r["verdad_ids"] else ""
            terminos = ", ".join(comunes[:8]) or "-"
            print(
                f"    {pos:>2}. {a['articulo']} | score={score:.3f} | "
                f"id={a['articulo_fuente_id']} | terminos={terminos}{marca}"
            )
        print()

    print("Resumen por referencia consolidada:")
    print(f"  Referencias..................... {refs_total}")
    print(f"  Con algún score................. {refs_con_score}/{refs_total}")
    print(f"  Top 1........................... {top1}/{refs_total}")
    print(f"  Top 5........................... {top5}/{refs_total}")
    print(f"  Top 10.......................... {top10}/{refs_total}")
    print(f"  Top 20.......................... {top20}/{refs_total}")
    print()
    print("Resumen por par tema-norma:")
    total_pares = len(filas_resultado)
    print(f"  Todas las referencias en Top10. {pares_todas_top10}/{total_pares}")
    print(f"  Todas las referencias en Top20. {pares_todas_top20}/{total_pares}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("No se han aceptado normas ni artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
