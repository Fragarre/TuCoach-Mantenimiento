"""
Constructor Temario V2 - auditoría de representabilidad estructural.

SOLO LECTURA.

Objetivo: comprobar si el alcance consolidado de los temas JURIDICO_DELIMITABLE
puede representarse razonablemente mediante bloques reales de la jerarquía oficial
(Título / Capítulo / Sección) reconstruida desde índices HTML cacheados.

La verdad consolidada (temario_referencias) se usa EXCLUSIVAMENTE para evaluar.
No forma parte de ninguna lógica productiva de propuesta.
No modifica BD, corpus, temario.csv ni bancos.

Correcciones de la auditoría:
- compara artículos por etiqueta jurídica normalizada, no por articulo_fuente_id;
- permite evaluar contra cualquier fuente oficial cacheada enlazada a la misma norma;
- distingue bloques homónimos por su contexto jerárquico completo
  (Título -> Capítulo -> Sección), evitando mezclar, por ejemplo, distintos
  "CAPÍTULO I" pertenecientes a títulos diferentes.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import extraer_estructura_temario_v2 as estructura
import extraer_jerarquia_indice_v2 as jerarquia_v2

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
CACHE = RAIZ / "cache_localizador_normativa"


def norm_articulo(valor: str | None) -> str:
    s = str(valor or "").strip().lower().replace("\xa0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^art(?:iculo|\.)?\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .:;,-")
    return s


def cargar_verdad(con, convocatoria_id: int):
    filas = con.execute(
        """
        SELECT UPPER(TRIM(tt.parte)) AS parte,
               tt.numero_tema,
               tr.norma_id,
               af.articulo_boe AS articulo
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE t.convocatoria_id=?
          AND tr.norma_id IS NOT NULL
          AND tr.articulo_fuente_id IS NOT NULL
        """,
        (convocatoria_id,),
    ).fetchall()
    verdad = defaultdict(set)
    for r in filas:
        clave = (str(r["parte"]), int(r["numero_tema"]), int(r["norma_id"]))
        art = norm_articulo(r["articulo"])
        if art:
            verdad[clave].add(art)
    return dict(verdad)


def cargar_fuentes_normas(con, norma_ids: set[int]) -> dict[int, list[str]]:
    if not norma_ids:
        return {}
    marcas = ",".join("?" for _ in norma_ids)
    filas = con.execute(
        f"""
        SELECT norma_id, id_fuente
        FROM norma_fuentes
        WHERE norma_id IN ({marcas})
        ORDER BY norma_id, id
        """,
        tuple(sorted(norma_ids)),
    ).fetchall()
    out = defaultdict(list)
    for r in filas:
        fid = str(r["id_fuente"] or "").strip()
        if fid and fid not in out[int(r["norma_id"])]:
            out[int(r["norma_id"])].append(fid)
    return dict(out)


def temas_delimitables(pdf: Path):
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    out = []
    for tema in temas:
        estructura.clasificar_tema(tema)
        clase, _ = alcance.clasificar(tema)
        if clase == "JURIDICO_DELIMITABLE":
            out.append(tema)
    return out


def bloques(payload: dict) -> list[dict]:
    """Construye bloques estructurales sin mezclar homónimos de ramas distintas."""
    arts = payload["articulos"]
    grupos = {
        "TITULO": defaultdict(set),
        "CAPITULO": defaultdict(set),
        "SECCION": defaultdict(set),
    }

    for a in arts:
        art = norm_articulo(a.get("articulo"))
        if not art:
            continue
        titulo = a.get("titulo")
        capitulo = a.get("capitulo")
        seccion = a.get("seccion")

        if titulo:
            grupos["TITULO"][(titulo,)].add(art)
        if capitulo:
            grupos["CAPITULO"][(titulo, capitulo)].add(art)
        if seccion:
            grupos["SECCION"][(titulo, capitulo, seccion)].add(art)

    out = []
    for nivel in ("SECCION", "CAPITULO", "TITULO"):
        for ruta, aset in grupos[nivel].items():
            nombre = ruta[-1]
            out.append({
                "nivel": nivel,
                "nombre": nombre,
                "ruta": [x for x in ruta if x],
                "articulos": aset,
            })
    return out


def metricas(refs: set[str], candidatos: set[str]):
    tp = len(refs & candidatos)
    pre = tp / len(candidatos) if candidatos else 0.0
    rec = tp / len(refs) if refs else 0.0
    f1 = 2 * pre * rec / (pre + rec) if pre + rec else 0.0
    return tp, pre, rec, f1


def mejor_union_bloques(refs: set[str], bs: list[dict]) -> tuple[list[dict], set[str], float, float, float]:
    """Greedy oracle descriptivo: añade bloques solo si mejora F1 global.

    Usa la verdad para medir representabilidad, no para construir lógica productiva.
    """
    elegidos = []
    union = set()
    _, p, r, f = metricas(refs, union)
    restantes = list(bs)
    while restantes:
        mejor = None
        for b in restantes:
            nueva = union | b["articulos"]
            _, np, nr, nf = metricas(refs, nueva)
            candidato = (nf, nr, np, -len(nueva), b, nueva)
            if mejor is None or candidato[:4] > mejor[:4]:
                mejor = candidato
        if mejor is None or mejor[0] <= f + 1e-12:
            break
        f, r, p, _, b, union = mejor
        elegidos.append(b)
        restantes.remove(b)
    return elegidos, union, p, r, f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--convocatoria-patron", required=True, type=int)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()

    with comun.conectar_sqlite_solo_lectura(db) as con:
        verdad = cargar_verdad(con, args.convocatoria_patron)
        fuentes_norma = cargar_fuentes_normas(con, {k[2] for k in verdad})

    delimitables = {(t.parte.upper(), t.numero_tema): t for t in temas_delimitables(pdf)}
    pares = [k for k in sorted(verdad) if (k[0], k[1]) in delimitables]

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - REPRESENTABILIDAD ESTRUCTURAL - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patrón.............. {args.convocatoria_patron}")
    print(f"Pares tema-norma................. {len(pares)}")
    print()
    print("La verdad consolidada se usa únicamente como referencia de evaluación.")
    print("Los artículos se comparan por etiqueta jurídica normalizada entre fuentes equivalentes.")
    print()

    n = 0
    f1_sum = rec_sum = pre_sum = 0.0
    exactos = 0
    sin_fuente = 0

    for clave in pares:
        parte, tema_num, nid = clave
        refs = verdad[clave]
        mejor_global = None
        fuentes_cacheadas = []

        for fid in fuentes_norma.get(nid, []):
            ruta = CACHE / fid / "indice.html"
            if not ruta.exists():
                continue
            fuentes_cacheadas.append(fid)
            payload = jerarquia_v2.reconstruir(fid, ruta)
            bs = bloques(payload)
            elegidos, union, pre, rec, f1 = mejor_union_bloques(refs, bs)
            cand = (f1, rec, pre, fid, elegidos, union)
            if mejor_global is None or cand[:3] > mejor_global[:3]:
                mejor_global = cand

        print(f"{parte:<8} | {tema_num:>2} | norma_id={nid} | refs={len(refs)}")
        if mejor_global is None:
            sin_fuente += 1
            print("  SIN INDICE OFICIAL CACHEADO ENTRE LAS FUENTES ENLAZADAS A LA NORMA")
            print()
            continue

        f1, rec, pre, fid, elegidos, union = mejor_global
        n += 1
        f1_sum += f1
        rec_sum += rec
        pre_sum += pre
        if pre == 1.0 and rec == 1.0:
            exactos += 1
        print(f"  fuente evaluada={fid} | fuentes cacheadas={len(fuentes_cacheadas)} | union={len(union)} | P={pre:.1%} R={rec:.1%} F1={f1:.1%}")
        if elegidos:
            for b in elegidos:
                ruta_txt = " > ".join(b["ruta"])
                print(f"    {b['nivel']}: {ruta_txt} | n={len(b['articulos'])}")
        else:
            print("    SIN BLOQUE ESTRUCTURAL ÚTIL")
        faltan = sorted(refs - union)
        sobran = sorted(union - refs)
        if faltan:
            print("    faltan: " + ", ".join(faltan[:20]) + (" ..." if len(faltan) > 20 else ""))
        if sobran:
            print("    sobran: " + ", ".join(sobran[:20]) + (" ..." if len(sobran) > 20 else ""))
        print()

    print("Resumen:")
    print(f"  Pares evaluables................. {n}")
    print(f"  Sin índice cacheado.............. {sin_fuente}")
    print(f"  Precisión media oracle estruct... {(pre_sum/n if n else 0):.1%}")
    print(f"  Recall medio oracle estruct...... {(rec_sum/n if n else 0):.1%}")
    print(f"  F1 medio oracle estruct.......... {(f1_sum/n if n else 0):.1%}")
    print(f"  Representación exacta............ {exactos}/{n}")
    print()
    print("IMPORTANTE: es una prueba de representabilidad, no un algoritmo de propuesta.")
    print("No se ha usado temario_referencias para generar candidatos productivos.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
