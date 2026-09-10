"""
Auditoría de rangos estructurales para JURIDICO_DELIMITABLE - SOLO LECTURA.

La generación de candidatos NO consulta temario_referencias.
Después del ranking, la verdad consolidada de una convocatoria patrón se usa sólo
para medir cobertura y posición de los bloques estructurales propuestos.

No modifica BD, corpus, temario.csv ni bancos.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import extraer_estructura_temario_v2 as estructura
import proponer_rangos_estructurales_delimitables_v2 as rangos_v2

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def cargar_verdad(con, convocatoria_id: int) -> dict[tuple[str, int, int], set[int]]:
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
    verdad: dict[tuple[str, int, int], set[int]] = defaultdict(set)
    for r in filas:
        verdad[(str(r["parte"]), int(r["numero_tema"]), int(r["norma_id"]))].add(int(r["articulo_fuente_id"]))
    return dict(verdad)


def temas_delimitables(pdf: Path):
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    salida = []
    for tema in temas:
        estructura.clasificar_tema(tema)
        clase, evidencia = alcance.clasificar(tema)
        if clase == "JURIDICO_DELIMITABLE":
            salida.append((tema, evidencia))
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description="Audita bloques estructurales delimitables contra una verdad consolidada.")
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
        articulos_por_norma: dict[int, list[dict]] = {}
        norma_ids = sorted({k[2] for k in verdad})
        if norma_ids:
            todos = rangos_v2.articulos_v2.cargar_articulos_normas(con, norma_ids)
            for a in todos:
                articulos_por_norma.setdefault(a["norma_id"], []).append(a)

    delimitables = temas_delimitables(pdf)
    pares = []
    for tema, _ in delimitables:
        parte = tema.parte.upper()
        claves = sorted(k for k in verdad if k[0] == parte and k[1] == tema.numero_tema)
        for clave in claves:
            nid = clave[2]
            arts = articulos_por_norma.get(nid, [])
            if not arts:
                continue
            bloques = rangos_v2.agrupar_bloques(arts)
            idf = rangos_v2.construir_idf_bloques(bloques)
            candidatos = []
            for b in bloques:
                score, comunes = rangos_v2.contenido.puntuacion_tema_norma(tema.texto, b["tokens"], idf)
                if score <= 0:
                    continue
                n_comunes, n_tema, cobertura = rangos_v2._cobertura_tema(tema.texto, set(b["tokens"]))
                candidatos.append((score, cobertura, b, comunes, n_comunes, n_tema))
            candidatos.sort(key=lambda x: (-x[0], -x[1], x[2]["articulo_fuente_ids"][0]))

            refs = verdad[clave]
            encontrados = []
            acumulados: set[int] = set()
            cobertura_por_top = {}
            primer_top_con_todas = None
            for pos, (_, _, b, _, _, _) in enumerate(candidatos, 1):
                ids = set(b["articulo_fuente_ids"])
                inter = refs & ids
                if inter:
                    encontrados.append((pos, len(inter), len(ids), b["articulo_inicial"], b["articulo_final"], b["titulo_bloque"]))
                acumulados |= ids
                if pos in (1, 3, 5, 10, 20):
                    cobertura_por_top[pos] = len(refs & acumulados) / len(refs)
                if primer_top_con_todas is None and refs <= acumulados:
                    primer_top_con_todas = pos
            for corte in (1, 3, 5, 10, 20):
                if corte not in cobertura_por_top:
                    cobertura_por_top[corte] = len(refs & acumulados) / len(refs) if candidatos else 0.0

            pares.append({
                "tema": tema,
                "norma_id": nid,
                "refs": refs,
                "candidatos": candidatos,
                "encontrados": encontrados,
                "cobertura": cobertura_por_top,
                "primer_top_con_todas": primer_top_con_todas,
            })

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA RANGOS ESTRUCTURALES - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patrón.............. {args.convocatoria_patron}")
    print(f"Temas JURIDICO_DELIMITABLE....... {len(delimitables)}")
    print(f"Pares tema-norma evaluados....... {len(pares)}")
    print()
    print("La verdad consolidada sólo se usa después del ranking para medirlo.")
    print("La norma consolidada simula una selección humana correcta.")
    print()

    total_refs = 0
    cubiertas = {1: 0, 3: 0, 5: 0, 10: 0, 20: 0}
    pares_todas = {1: 0, 3: 0, 5: 0, 10: 0, 20: 0}

    for p in pares:
        tema = p["tema"]
        refs = p["refs"]
        total_refs += len(refs)
        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | norma_id={p['norma_id']} | refs={len(refs)}")
        if p["encontrados"]:
            for pos, nref, nbloque, ini, fin, titulo in p["encontrados"][:8]:
                print(f"  bloque #{pos:>2}: {ini} -> {fin} | refs_correctas={nref}/{len(refs)} | tamaño={nbloque} | {titulo or '(sin título)'}")
        else:
            print("  ninguna referencia correcta cae en bloques con score")
        print("  cobertura acumulada:", " | ".join(f"Top{k}={p['cobertura'][k]:.1%}" for k in (1, 3, 5, 10, 20)))
        print(f"  primer Top con todas............ {p['primer_top_con_todas'] or '-'}")
        print()

        # Cobertura exacta por referencias a partir de los primeros k bloques.
        for k in (1, 3, 5, 10, 20):
            ids = set()
            for _, _, b, _, _, _ in p["candidatos"][:k]:
                ids.update(b["articulo_fuente_ids"])
            n = len(refs & ids)
            cubiertas[k] += n
            if refs <= ids:
                pares_todas[k] += 1

    print("Resumen global por referencia consolidada:")
    print(f"  Referencias..................... {total_refs}")
    for k in (1, 3, 5, 10, 20):
        pct = cubiertas[k] / total_refs if total_refs else 0.0
        print(f"  Cubiertas por Top{k:<2} bloques....... {cubiertas[k]}/{total_refs} ({pct:.1%})")
    print()
    print("Resumen por par tema-norma:")
    for k in (1, 3, 5, 10, 20):
        print(f"  Todas las refs en Top{k:<2} bloques... {pares_todas[k]}/{len(pares)}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("No se han aceptado normas, rangos ni artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
