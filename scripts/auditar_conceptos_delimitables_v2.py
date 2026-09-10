"""Auditoria conceptual JURIDICO_DELIMITABLE. SOLO LECTURA.
La verdad consolidada se usa solo al final para medir candidatos.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import evaluar_normas_por_contenido_v2 as contenido
import extraer_estructura_temario_v2 as estructura
import proponer_articulos_delimitables_v2 as articulos_v2

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def conceptos(texto: str) -> list[str]:
    texto = re.sub(r"\s+", " ", texto.strip())
    partes = [p.strip(" .;:") for p in re.split(r";|(?<=[.])\s+(?=[A-ZÁÉÍÓÚÑ])", texto)]
    return [p for p in partes if len(contenido.tokens(p)) >= 2]


def temas_delimitables(pdf: Path):
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    salida = []
    for tema in temas:
        estructura.clasificar_tema(tema)
        clase, _ = alcance.clasificar(tema)
        if clase == "JURIDICO_DELIMITABLE":
            salida.append(tema)
    return salida


def ranking(texto: str, articulos: list[dict], top: int):
    idf = articulos_v2.construir_idf_articulos(articulos)
    filas = []
    for a in articulos:
        score, comunes = contenido.puntuacion_tema_norma(texto, a["tokens"], idf)
        if score > 0:
            filas.append((score, a, comunes))
    filas.sort(key=lambda x: (-x[0], x[1]["articulo_fuente_id"]))
    return filas[:top]


def main() -> int:
    ap = argparse.ArgumentParser(description="Audita localizacion de articulos por unidades conceptuales.")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--convocatoria-patron", required=True, type=int)
    ap.add_argument("--top-concepto", type=int, default=5)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    top = max(1, args.top_concepto)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        filas = con.execute("""
            SELECT UPPER(TRIM(tt.parte)) parte, tt.numero_tema,
                   tr.norma_id, tr.articulo_fuente_id
            FROM temarios t
            JOIN temario_temas tt ON tt.temario_id=t.id
            JOIN temario_referencias tr ON tr.tema_id=tt.id
            WHERE t.convocatoria_id=?
              AND tr.estado='COMPLETADO'
              AND tr.norma_id IS NOT NULL
              AND tr.articulo_fuente_id IS NOT NULL
        """, (args.convocatoria_patron,)).fetchall()
        verdad = {}
        for r in filas:
            clave = (str(r["parte"]), int(r["numero_tema"]), int(r["norma_id"]))
            verdad.setdefault(clave, set()).add(int(r["articulo_fuente_id"]))
        norma_ids = sorted({k[2] for k in verdad})
        todos = articulos_v2.cargar_articulos_normas(con, norma_ids)

    por_norma = {}
    for a in todos:
        por_norma.setdefault(a["norma_id"], []).append(a)

    resultados = []
    for tema in temas_delimitables(pdf):
        claves = sorted(k for k in verdad if k[0] == tema.parte.upper() and k[1] == tema.numero_tema)
        unidades = conceptos(tema.texto)
        for clave in claves:
            nid = clave[2]
            arts = por_norma.get(nid, [])
            if not arts:
                continue
            union = set()
            detalle = []
            for unidad in unidades:
                rnk = ranking(unidad, arts, top)
                union.update(a["articulo_fuente_id"] for _, a, _ in rnk)
                detalle.append((unidad, rnk))
            refs = verdad[clave]
            aciertos = refs & union
            precision = len(aciertos) / len(union) if union else 0.0
            recall = len(aciertos) / len(refs) if refs else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            resultados.append((tema, nid, refs, unidades, detalle, union, precision, recall, f1))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA CONCEPTUAL DELIMITABLES - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patron.............. {args.convocatoria_patron}")
    print(f"Top por concepto................. {top}")
    print(f"Pares tema-norma................. {len(resultados)}")
    print()

    sp = sr = sf = 0.0
    for tema, nid, refs, unidades, detalle, union, p, r, f1 in resultados:
        sp += p; sr += r; sf += f1
        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | norma_id={nid} | refs={len(refs)} | conceptos={len(unidades)}")
        print(f"  union candidatos={len(union)} | P={p:.1%} R={r:.1%} F1={f1:.1%}")
        for unidad, rnk in detalle:
            print(f"  - {unidad}")
            for pos, (score, a, comunes) in enumerate(rnk[:3], 1):
                marca = "*" if a["articulo_fuente_id"] in refs else " "
                print(f"      {marca}{pos}. {a['articulo']} | score={score:.3f} | terminos={', '.join(comunes[:8])}")
        print()

    n = len(resultados)
    print("Resumen:")
    print(f"  Precision media................. {sp/n if n else 0:.1%}")
    print(f"  Recall medio.................... {sr/n if n else 0:.1%}")
    print(f"  F1 medio........................ {sf/n if n else 0:.1%}")
    print()
    print("La verdad consolidada solo se ha usado para medir candidatos.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("No se han aceptado normas ni articulos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
