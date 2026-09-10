"""
Auditoría SOLO LECTURA de intervalos consecutivos para JURIDICO_DELIMITABLE.
La verdad consolidada se usa únicamente después de generar candidatos para medir.
No modifica BD, corpus, temario.csv ni bancos.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import extraer_estructura_temario_v2 as estructura
import proponer_intervalos_consecutivos_delimitables_v2 as intervalos_v2

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def cargar_verdad(con, convocatoria_id: int):
    filas = con.execute(
        """
        SELECT UPPER(TRIM(tt.parte)) AS parte,
               tt.numero_tema,
               tr.norma_id,
               tr.articulo_fuente_id
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        WHERE t.convocatoria_id=?
          AND tr.norma_id IS NOT NULL
          AND tr.articulo_fuente_id IS NOT NULL
        """,
        (convocatoria_id,),
    ).fetchall()
    verdad = defaultdict(set)
    for r in filas:
        verdad[(str(r["parte"]), int(r["numero_tema"]), int(r["norma_id"]))].add(int(r["articulo_fuente_id"]))
    return dict(verdad)


def temas_delimitables(pdf: Path):
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    out = []
    for tema in temas:
        estructura.clasificar_tema(tema)
        clase, evidencia = alcance.clasificar(tema)
        if clase == "JURIDICO_DELIMITABLE":
            out.append((tema, evidencia))
    return out


def metricas(refs: set[int], ids: set[int]):
    tp = len(refs & ids)
    rec = tp / len(refs) if refs else 0.0
    pre = tp / len(ids) if ids else 0.0
    f1 = 2 * pre * rec / (pre + rec) if pre + rec else 0.0
    return tp, pre, rec, f1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--convocatoria-patron", required=True, type=int)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    top = max(1, args.top)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        verdad = cargar_verdad(con, args.convocatoria_patron)
        norma_ids = sorted({k[2] for k in verdad})
        todos = intervalos_v2.articulos_v2.cargar_articulos_normas(con, norma_ids) if norma_ids else []

    por_norma = defaultdict(list)
    for a in todos:
        por_norma[a["norma_id"]].append(a)

    pares = []
    for tema, _ in temas_delimitables(pdf):
        parte = tema.parte.upper()
        for clave in sorted(k for k in verdad if k[0] == parte and k[1] == tema.numero_tema):
            nid = clave[2]
            refs = verdad[clave]
            grupos = intervalos_v2.agrupar_por_fuente(por_norma.get(nid, []))
            candidatos = []
            for (gnid, fuente), arts in grupos.items():
                for c in intervalos_v2.generar_intervalos(tema.texto, arts):
                    ids = set(c["articulo_fuente_ids"])
                    tp, pre, rec, f1 = metricas(refs, ids)
                    candidatos.append((c, fuente, tp, pre, rec, f1))
            candidatos.sort(key=lambda x: (-x[0]["score_intervalo"], x[0]["numero_articulos"]))
            pares.append((tema, nid, refs, candidatos))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA INTERVALOS CONSECUTIVOS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patrón.............. {args.convocatoria_patron}")
    print(f"Pares tema-norma evaluados....... {len(pares)}")
    print()
    print("La verdad consolidada sólo se usa después de generar/rankear intervalos.")
    print()

    mejor_f1_total = 0.0
    mejor_rec_total = 0.0
    top1_rec_total = 0.0
    exactos = 0

    for tema, nid, refs, candidatos in pares:
        evaluados = candidatos[:top]
        mejor = max(candidatos, key=lambda x: x[5]) if candidatos else None
        top1 = candidatos[0] if candidatos else None
        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | norma_id={nid} | refs={len(refs)}")
        if top1:
            c, fuente, tp, pre, rec, f1 = top1
            print(f"  TOP1  {c['articulo_inicial']} -> {c['articulo_final']} | n={c['numero_articulos']} | P={pre:.1%} R={rec:.1%} F1={f1:.1%} | {c['origen']} | fuente={fuente}")
            top1_rec_total += rec
        if mejor:
            c, fuente, tp, pre, rec, f1 = mejor
            print(f"  MEJOR {c['articulo_inicial']} -> {c['articulo_final']} | n={c['numero_articulos']} | P={pre:.1%} R={rec:.1%} F1={f1:.1%} | {c['origen']} | fuente={fuente}")
            mejor_f1_total += f1
            mejor_rec_total += rec
            if pre == 1.0 and rec == 1.0:
                exactos += 1
        print("  Top candidatos:")
        for c, fuente, tp, pre, rec, f1 in evaluados[:5]:
            print(f"    {c['posicion']:>2}. {c['articulo_inicial']} -> {c['articulo_final']} | n={c['numero_articulos']} | score={c['score_intervalo']:.3f} | P={pre:.1%} R={rec:.1%} F1={f1:.1%} | {c['origen']}")
        print()

    n = len(pares)
    print("Resumen:")
    print(f"  Pares........................... {n}")
    print(f"  Recall medio Top1............... {(top1_rec_total/n if n else 0):.1%}")
    print(f"  Mejor recall alcanzable......... {(mejor_rec_total/n if n else 0):.1%}")
    print(f"  Mejor F1 medio alcanzable....... {(mejor_f1_total/n if n else 0):.1%}")
    print(f"  Intervalo exacto existente...... {exactos}/{n}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("No se han aceptado normas, intervalos ni artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
