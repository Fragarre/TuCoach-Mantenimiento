"""
Evalúa, en SOLO LECTURA, las propuestas de normas implícitas frente al temario
consolidado de la convocatoria usada como patrón.

IMPORTANTE:
- La convocatoria patrón NO participa en la generación de candidatas.
- Sus mappings consolidados se consultan únicamente DESPUÉS, para medir aciertos.
- No acepta candidatas, no resuelve artículos y no modifica la base de datos.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import resolver_normas_temario_v2 as resolver
import proponer_normas_implicitas_v2 as propuestas

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def cargar_verdad(con, convocatoria_id: int):
    filas = con.execute(
        """
        SELECT tt.parte, tt.numero_tema, n.id norma_id, n.nombre_canonico
        FROM temario_temas tt
        JOIN temarios t ON t.id=tt.temario_id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        JOIN normas n ON n.id=tr.norma_id
        WHERE t.convocatoria_id=? AND tr.estado='COMPLETADO'
        GROUP BY tt.id, n.id, n.nombre_canonico
        ORDER BY tt.parte, tt.numero_tema, n.id
        """,
        (convocatoria_id,),
    ).fetchall()
    verdad = defaultdict(dict)
    for r in filas:
        verdad[(str(r["parte"]), int(r["numero_tema"]))][int(r["norma_id"])] = str(r["nombre_canonico"])
    return verdad


def main() -> int:
    ap = argparse.ArgumentParser(description="Evalúa propuestas implícitas sin modificar datos.")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--convocatoria-patron", required=True, type=int)
    ap.add_argument("--evidencia-convocatorias", required=True, type=int, nargs="+")
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    if args.convocatoria_patron in set(args.evidencia_convocatorias):
        raise SystemExit("ERROR: la convocatoria patrón no puede usarse como evidencia.")

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for t in temas:
        estructura.clasificar_tema(t)

    pendientes = [
        t for t in temas
        if t.clasificacion == "REVISION"
        or (t.clasificacion == "JURIDICO" and not resolver.detectar_citas(t.texto))
    ]

    with comun.conectar_sqlite_solo_lectura(db) as con:
        evidencias = propuestas.cargar_evidencias(
            con,
            args.convocatoria_patron,
            args.evidencia_convocatorias,
        )
        verdad = cargar_verdad(con, args.convocatoria_patron)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - EVALUACION DE NORMAS IMPLICITAS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patrón.............. {args.convocatoria_patron}")
    print("Convocatorias evidencia.......... " + ", ".join(map(str, sorted(set(args.evidencia_convocatorias)))))
    print(f"Temas evaluados................... {len(pendientes)}")
    print("La convocatoria patrón sólo se consulta al final para medir el resultado.")
    print()

    top1_correcto = 0
    topn_contiene_alguna = 0
    total = 0

    for tema in pendientes:
        por_norma = defaultdict(list)
        for r in evidencias:
            s = propuestas.similitud(tema.texto, str(r["titulo"] or ""))
            if s > 0:
                por_norma[(int(r["norma_id"]), str(r["nombre_canonico"]))].append((s, r))

        ranking = []
        for (nid, nombre), items in por_norma.items():
            mejor = max(items, key=lambda x: x[0])
            ranking.append((mejor[0], nid, nombre, mejor[1]))
        ranking.sort(key=lambda x: (-x[0], x[1]))

        verdad_tema = verdad.get((tema.parte, tema.numero_tema), {})
        ids_verdad = set(verdad_tema)
        ids_top = [nid for _, nid, _, _ in ranking[:max(1, args.top)]]
        total += 1

        acierto_top1 = bool(ids_top and ids_top[0] in ids_verdad)
        contiene = bool(ids_verdad & set(ids_top))
        if acierto_top1:
            top1_correcto += 1
        if contiene:
            topn_contiene_alguna += 1

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | {tema.clasificacion}")
        print("  VERDAD CONSOLIDADA:")
        if verdad_tema:
            for nid, nombre in sorted(verdad_tema.items()):
                print(f"    [{nid}] {nombre}")
        else:
            print("    SIN NORMAS CONSOLIDADAS")

        print("  PROPUESTAS:")
        if not ranking:
            print("    SIN EVIDENCIA REUTILIZABLE")
        else:
            for pos, (s, nid, nombre, r) in enumerate(ranking[:max(1, args.top)], start=1):
                marca = "OK" if nid in ids_verdad else "NO"
                print(
                    f"    {pos}. [{nid}] {nombre} | similitud={s:.3f} | {marca} | "
                    f"evidencia={r['convocatoria_codigo']} {r['parte']} {r['numero_tema']}"
                )
        print(f"  TOP1_CORRECTO.............. {'SI' if acierto_top1 else 'NO'}")
        print(f"  TOP{max(1,args.top)}_CONTIENE_ALGUNA... {'SI' if contiene else 'NO'}")
        print()

    print("Resumen:")
    print(f"  Temas evaluados.............. {total}")
    print(f"  Top1 correcto................ {top1_correcto}/{total}")
    print(f"  Top{max(1,args.top)} contiene alguna correcta {topn_contiene_alguna}/{total}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("La verdad consolidada NO ha participado en la generación de candidatas.")
    print("No se han aceptado normas ni resuelto artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
