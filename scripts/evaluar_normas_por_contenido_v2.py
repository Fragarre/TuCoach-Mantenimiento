"""
Constructor Temario V2 - evaluación de normas implícitas por contenido oficial.

SOLO LECTURA.

Para los temas que V2 deja en REVISION o JURIDICO sin cita literal:
1. extrae el texto oficial del tema desde el PDF;
2. compara sus términos con el contenido oficial disponible de las normas
   canónicas (norma_fuentes -> articulos_fuente);
3. genera un ranking sin consultar el temario consolidado de la convocatoria
   patrón;
4. sólo al final consulta temario_referencias de la convocatoria patrón para
   medir el resultado.

No acepta normas, no resuelve artículos y no modifica la base de datos.
"""
from __future__ import annotations

import argparse
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import resolver_normas_temario_v2 as resolver

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"

STOP = {
    "de","del","la","las","el","los","y","e","o","u","a","al","en","por","para","con","sin",
    "un","una","unos","unas","su","sus","se","que","como","sobre","entre","i","ii","iii","iv",
    "ser","son","sus","esta","este","estos","estas","una","unas","unos","todo","toda","todos","todas",
    "administracion","administraciones","publica","publicas","publico","publicos",
}


def normalizar(texto: str) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", x).strip()


def tokens(texto: str) -> set[str]:
    return {
        t for t in normalizar(texto).split()
        if len(t) >= 4 and t not in STOP and not t.isdigit()
    }


def cargar_corpus_normas(con):
    """Devuelve corpus textual oficial por norma canónica.

    Sólo usa artículos enlazados mediante norma_fuentes.id_fuente =
    articulos_fuente.id_boe. Una norma sin texto oficial enlazable queda fuera
    del ranking; no se intenta completar por otros medios.
    """
    filas = con.execute(
        """
        SELECT n.id norma_id,
               n.nombre_canonico,
               af.id articulo_fuente_id,
               af.articulo_boe,
               af.titulo_bloque,
               af.texto
        FROM normas n
        JOIN norma_fuentes nf ON nf.norma_id=n.id
        JOIN articulos_fuente af ON af.id_boe=nf.id_fuente
        WHERE COALESCE(af.texto,'')<>''
        ORDER BY n.id, af.id
        """
    ).fetchall()

    docs = defaultdict(lambda: {"nombre": "", "tokens": set(), "articulos": 0})
    articulos_vistos = defaultdict(set)
    for r in filas:
        nid = int(r["norma_id"])
        aid = int(r["articulo_fuente_id"])
        if aid in articulos_vistos[nid]:
            continue
        articulos_vistos[nid].add(aid)
        d = docs[nid]
        d["nombre"] = str(r["nombre_canonico"] or "")
        texto = " ".join(
            x for x in (
                str(r["articulo_boe"] or ""),
                str(r["titulo_bloque"] or ""),
                str(r["texto"] or ""),
            ) if x
        )
        d["tokens"].update(tokens(texto))
        d["articulos"] += 1
    return dict(docs)


def construir_idf(docs):
    n = len(docs)
    df = Counter()
    for d in docs.values():
        for tok in d["tokens"]:
            df[tok] += 1
    return {
        tok: math.log((n + 1) / (freq + 1)) + 1.0
        for tok, freq in df.items()
    }


def puntuacion_tema_norma(texto_tema: str, tokens_norma: set[str], idf: dict[str, float]):
    tt = tokens(texto_tema)
    if not tt:
        return 0.0, []
    pesos = {t: idf.get(t, math.log(2.0)) for t in tt}
    total = sum(pesos.values())
    if total <= 0:
        return 0.0, []
    comunes = sorted(
        (t for t in tt if t in tokens_norma),
        key=lambda t: (-pesos[t], t),
    )
    score = sum(pesos[t] for t in comunes) / total
    return score, comunes


def cargar_verdad(con, convocatoria_id: int, parte: str, numero_tema: int):
    filas = con.execute(
        """
        SELECT DISTINCT n.id norma_id, n.nombre_canonico
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        JOIN normas n ON n.id=tr.norma_id
        WHERE t.convocatoria_id=?
          AND UPPER(tt.parte)=UPPER(?)
          AND tt.numero_tema=?
          AND tr.estado='COMPLETADO'
        ORDER BY n.id
        """,
        (convocatoria_id, parte, numero_tema),
    ).fetchall()
    return [(int(r["norma_id"]), str(r["nombre_canonico"])) for r in filas]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Evalúa normas implícitas por contenido oficial sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--convocatoria-patron", required=True, type=int)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

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
        docs = cargar_corpus_normas(con)
        idf = construir_idf(docs)

        resultados = []
        for tema in pendientes:
            ranking = []
            for nid, d in docs.items():
                score, comunes = puntuacion_tema_norma(tema.texto, d["tokens"], idf)
                if score > 0:
                    ranking.append((score, nid, d["nombre"], d["articulos"], comunes))
            ranking.sort(key=lambda x: (-x[0], x[1]))
            verdad = cargar_verdad(
                con, args.convocatoria_patron, tema.parte, tema.numero_tema
            )
            resultados.append((tema, ranking[:max(1, args.top)], verdad))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - EVALUACION POR CONTENIDO OFICIAL - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patrón.............. {args.convocatoria_patron}")
    print(f"Normas con corpus oficial........ {len(docs)}")
    print(f"Temas evaluados................... {len(resultados)}")
    print("La convocatoria patrón sólo se consulta al final para medir el resultado.")
    print()

    top1_ok = 0
    top5_ok = 0
    top10_ok = 0

    for tema, ranking, verdad in resultados:
        verdad_ids = {nid for nid, _ in verdad}
        ids_rank = [nid for _, nid, _, _, _ in ranking]
        ok1 = bool(ids_rank and ids_rank[0] in verdad_ids)
        ok5 = bool(verdad_ids.intersection(ids_rank[:5]))
        ok10 = bool(verdad_ids.intersection(ids_rank[:10]))
        top1_ok += int(ok1)
        top5_ok += int(ok5)
        top10_ok += int(ok10)

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | {tema.clasificacion}")
        print("  VERDAD CONSOLIDADA:")
        if verdad:
            for nid, nombre in verdad:
                print(f"    [{nid}] {nombre}")
        else:
            print("    (sin normas consolidadas)")
        print("  PROPUESTAS POR CONTENIDO OFICIAL:")
        if not ranking:
            print("    SIN CANDIDATAS")
        else:
            for i, (score, nid, nombre, articulos, comunes) in enumerate(ranking, 1):
                marca = "OK" if nid in verdad_ids else "NO"
                muestra = ", ".join(comunes[:8]) if comunes else "-"
                print(
                    f"    {i}. [{nid}] {nombre} | score={score:.3f} | {marca} | "
                    f"articulos={articulos} | terminos={muestra}"
                )
        print(f"  TOP1_CORRECTO.............. {'SI' if ok1 else 'NO'}")
        print(f"  TOP5_CONTIENE_ALGUNA....... {'SI' if ok5 else 'NO'}")
        print(f"  TOP10_CONTIENE_ALGUNA...... {'SI' if ok10 else 'NO'}")
        print()

    total = len(resultados)
    print("Resumen:")
    print(f"  Temas evaluados.............. {total}")
    print(f"  Top1 correcto................ {top1_ok}/{total}")
    print(f"  Top5 contiene alguna correcta {top5_ok}/{total}")
    print(f"  Top10 contiene alguna correcta {top10_ok}/{total}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("La verdad consolidada NO ha participado en la generación de candidatas.")
    print("No se han aceptado normas ni resuelto artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
