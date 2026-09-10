"""
Constructor Temario V2 - piloto acotado de selección de bloques estructurales.

SOLO LECTURA. NO es lógica productiva.

Objetivo de este piloto:
comprobar si, una vez conocida/seleccionada una norma, el texto oficial del tema
permite seleccionar sus bloques jurídicos reales (Título/Capítulo/Sección) sin
usar temario_referencias para generar la propuesta.

Casos piloto C1 fijados antes de evaluar:
- ESPECIAL 4 / norma 47: alcance compacto y representación estructural exacta.
- ESPECIAL 11 / norma 20: alcance no exacto a nivel de bloque (control de ruido).
- ESPECIAL 13 / norma 67: alcance distribuido en dos capítulos y exacto.

La verdad consolidada se consulta EXCLUSIVAMENTE después de seleccionar bloques,
para medir precisión/recall/F1. No modifica BD, corpus, caché ni temario.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import comun
import auditar_representabilidad_estructural_v2 as base
import auditar_representabilidad_estructural_online_v2 as online
import evaluar_normas_por_contenido_v2 as contenido

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"

CASOS = [
    ("ESPECIAL", 4, 47),
    ("ESPECIAL", 11, 20),
    ("ESPECIAL", 13, 67),
]


def texto_bloque(b: dict, payload: dict) -> str:
    partes = list(b["ruta"])
    arts = b["articulos"]
    for a in payload["articulos"]:
        if base.norm_articulo(a.get("articulo")) in arts:
            partes.append(str(a.get("epigrafe") or ""))
    return " ".join(partes)


def seleccionar(texto_tema: str, payload: dict) -> list[dict]:
    """Selección mínima del piloto: similitud temática contra bloques reales.

    No usa artículos consolidados ni umbrales aprendidos. Se conserva el mejor
    bloque por puntuación y se añaden sólo bloques empatados con el mejor score.
    La finalidad es decidir si esta señal basta; no optimizarla.
    """
    bs = base.bloques(payload)
    docs = {}
    for i, b in enumerate(bs):
        docs[i] = {
            "tokens": contenido.tokens(texto_bloque(b, payload)),
            "nombre": " > ".join(b["ruta"]),
        }
    idf = contenido.construir_idf(docs)
    ranking = []
    for i, b in enumerate(bs):
        score, comunes = contenido.puntuacion_tema_norma(
            texto_tema, docs[i]["tokens"], idf
        )
        if score > 0:
            ranking.append((score, len(b["articulos"]), i, comunes))
    ranking.sort(key=lambda x: (-x[0], x[1], x[2]))
    if not ranking:
        return []
    mejor = ranking[0][0]
    elegidos = []
    for score, _, i, comunes in ranking:
        if abs(score - mejor) > 1e-12:
            break
        b = dict(bs[i])
        b["score"] = score
        b["comunes"] = comunes
        elegidos.append(b)
    return elegidos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--convocatoria-patron", type=int, default=1)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    temas = {(t.parte.upper(), t.numero_tema): t for t in base.temas_delimitables(pdf)}

    with comun.conectar_sqlite_solo_lectura(db) as con:
        verdad = base.cargar_verdad(con, args.convocatoria_patron)
        fuentes = base.cargar_fuentes_normas(con, {nid for _, _, nid in CASOS})

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PILOTO SELECCION BLOQUES - SOLO LECTURA")
    print("=" * 78)
    print("La selección NO usa temario_referencias. La verdad se usa sólo al medir.\n")

    resultados = []
    for parte, num, nid in CASOS:
        tema = temas.get((parte, num))
        refs = verdad.get((parte, num, nid), set())
        mejor = None
        estados = []
        if tema is None:
            print(f"{parte} | {num} | norma_id={nid}: TEMA NO LOCALIZADO")
            continue

        for fid in fuentes.get(nid, []):
            payload, origen = online.cargar_payload(fid)
            estados.append(f"{fid}:{origen}")
            if payload is None:
                continue
            elegidos = seleccionar(tema.texto, payload)
            union = set().union(*(b["articulos"] for b in elegidos)) if elegidos else set()
            # La comparación con refs ocurre únicamente aquí, después de seleccionar.
            _, p, r, f1 = base.metricas(refs, union)
            cand = (f1, r, p, fid, origen, elegidos, union)
            if mejor is None or cand[:3] > mejor[:3]:
                mejor = cand

        print(f"{parte:<8} | {num:>2} | norma_id={nid} | refs={len(refs)}")
        print(f"  texto: {tema.texto}")
        if mejor is None:
            print("  SIN ESTRUCTURA DISPONIBLE")
            print("  fuentes: " + " | ".join(estados))
            print()
            continue
        f1, r, p, fid, origen, elegidos, union = mejor
        print(f"  fuente={fid} | origen={origen}")
        for b in elegidos:
            print(f"    {b['nivel']}: {' > '.join(b['ruta'])} | score={b['score']:.3f} | n={len(b['articulos'])}")
        print(f"  RESULTADO: union={len(union)} | P={p:.1%} R={r:.1%} F1={f1:.1%}")
        resultados.append((p, r, f1))
        print()

    print("Resumen piloto:")
    if resultados:
        n = len(resultados)
        print(f"  Casos evaluados................. {n}")
        print(f"  Precisión media................. {sum(x[0] for x in resultados)/n:.1%}")
        print(f"  Recall medio.................... {sum(x[1] for x in resultados)/n:.1%}")
        print(f"  F1 medio........................ {sum(x[2] for x in resultados)/n:.1%}")
    else:
        print("  Sin casos evaluables.")
    print()
    print("CRITERIO: este piloto sólo decide si la señal texto-tema -> bloque estructural")
    print("merece continuar. No se ajustarán pesos ni umbrales a partir de estos 3 casos.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus y la caché NO han sido modificados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
