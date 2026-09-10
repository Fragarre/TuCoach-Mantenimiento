"""
Constructor Temario V2 - intervalos consecutivos para JURIDICO_DELIMITABLE.

SOLO LECTURA. Parte de normas seleccionadas por el operador y trabaja por cada
fuente oficial enlazada, sin mezclar artículos procedentes de fuentes distintas.
Genera intervalos consecutivos alrededor de anclas léxicas y por envolvente de
las mejores anclas. Son propuestas para revisión humana, nunca decisiones.
No consulta temario_referencias ni modifica BD/corpus/temario.csv/bancos.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import comun
import evaluar_normas_por_contenido_v2 as contenido
import proponer_articulos_delimitables_v2 as articulos_v2

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
TAMANOS = (3, 5, 10, 20, 40)
ANCLAS_ENVOLVENTE = (2, 3, 5, 8, 12)


def agrupar_por_fuente(articulos: list[dict]) -> dict[tuple[int, str], list[dict]]:
    grupos: dict[tuple[int, str], list[dict]] = {}
    for a in articulos:
        grupos.setdefault((a["norma_id"], a["id_fuente"]), []).append(a)
    # cargar_articulos_normas devuelve orden estable de la fuente (af.id).
    return grupos


def _idf(arts: list[dict]) -> dict[str, float]:
    n = len(arts)
    df = Counter()
    for a in arts:
        for t in a["tokens"]:
            df[t] += 1
    return {t: math.log((n + 1) / (f + 1)) + 1.0 for t, f in df.items()}


def _tokens_intervalo(arts: list[dict], ini: int, fin: int) -> set[str]:
    out: set[str] = set()
    for a in arts[ini:fin + 1]:
        out.update(a["tokens"])
    return out


def _score_intervalo(texto_tema: str, arts: list[dict], ini: int, fin: int, idf: dict[str, float]) -> tuple[float, float, list[str]]:
    tokens = _tokens_intervalo(arts, ini, fin)
    score, comunes = contenido.puntuacion_tema_norma(texto_tema, tokens, idf)
    tema_tokens = set(contenido.tokens(texto_tema))
    cobertura = len(tema_tokens & tokens) / len(tema_tokens) if tema_tokens else 0.0
    # Penalización suave por amplitud: evita que "toda la norma" gane sólo por tamaño.
    longitud = fin - ini + 1
    calidad = (0.65 * float(score) + 0.35 * cobertura) / (1.0 + 0.08 * math.log1p(longitud))
    return calidad, cobertura, comunes


def generar_intervalos(texto_tema: str, arts: list[dict]) -> list[dict]:
    if not arts:
        return []
    idf = _idf(arts)
    ranking = []
    for i, a in enumerate(arts):
        score, comunes = contenido.puntuacion_tema_norma(texto_tema, a["tokens"], idf)
        if score > 0:
            ranking.append((float(score), i, comunes))
    ranking.sort(key=lambda x: (-x[0], x[1]))
    if not ranking:
        return []

    candidatos: set[tuple[int, int, str]] = set()
    n = len(arts)

    # Ventanas consecutivas alrededor de las mejores anclas.
    for _, idx, _ in ranking[:12]:
        for tam in TAMANOS:
            if tam > n:
                continue
            ini = max(0, idx - tam // 2)
            fin = min(n - 1, ini + tam - 1)
            ini = max(0, fin - tam + 1)
            candidatos.add((ini, fin, f"VENTANA_{tam}"))

    # Intervalo mínimo que envuelve las N mejores anclas.
    for k in ANCLAS_ENVOLVENTE:
        top = ranking[:min(k, len(ranking))]
        indices = [x[1] for x in top]
        candidatos.add((min(indices), max(indices), f"ENVOLVENTE_TOP_{len(top)}"))

    salida = []
    for ini, fin, origen in candidatos:
        calidad, cobertura, comunes = _score_intervalo(texto_tema, arts, ini, fin, idf)
        tramo = arts[ini:fin + 1]
        salida.append({
            "origen": origen,
            "indice_inicial": ini,
            "indice_final": fin,
            "articulo_inicial": tramo[0]["articulo"],
            "articulo_final": tramo[-1]["articulo"],
            "numero_articulos": len(tramo),
            "articulo_fuente_ids": [a["articulo_fuente_id"] for a in tramo],
            "score_intervalo": round(calidad, 6),
            "cobertura_lexica_tema": round(cobertura, 6),
            "terminos_comunes": comunes,
            "estado": "PROPUESTO",
            "decision_humana": None,
        })
    salida.sort(key=lambda x: (-x["score_intervalo"], x["numero_articulos"], x["indice_inicial"]))
    for pos, c in enumerate(salida, 1):
        c["posicion"] = pos
    return salida


def construir_propuesta(pdf: Path, db: Path, parte: str, numero_tema: int, norma_ids: list[int], top: int) -> dict:
    tema, evidencia = articulos_v2.localizar_tema(pdf, parte, numero_tema)
    with comun.conectar_sqlite_solo_lectura(db) as con:
        arts = articulos_v2.cargar_articulos_normas(con, norma_ids)
    faltantes = sorted(set(norma_ids) - {a["norma_id"] for a in arts})
    if faltantes:
        raise SystemExit("ERROR: normas sin artículos oficiales enlazados: " + ", ".join(map(str, faltantes)))

    grupos = agrupar_por_fuente(arts)
    propuestas = []
    for (nid, fuente), lista in grupos.items():
        candidatos = generar_intervalos(tema.texto, lista)[:top]
        propuestas.append({
            "norma_id": nid,
            "nombre_canonico": lista[0]["nombre_canonico"],
            "id_fuente": fuente,
            "articulos_fuente": len(lista),
            "intervalos_candidatos": candidatos,
        })

    return {
        "version": 1,
        "naturaleza": "PROPUESTAS_INTERVALOS_CONSECUTIVOS_DELIMITABLES_V2",
        "modo": "SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "tema": {
            "parte": tema.parte,
            "numero_tema": tema.numero_tema,
            "texto_oficial": tema.texto,
            "alcance_v2": "JURIDICO_DELIMITABLE",
            "evidencia_alcance": evidencia,
        },
        "estado": "REVISION_INTERVALOS",
        "reglas": {
            "normas_seleccionadas_explicitamente": True,
            "separa_fuentes_oficiales": True,
            "ranking_lexico_es_auxiliar": True,
            "consulta_temario_referencias": False,
            "aceptacion_automatica": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
            "modifica_bancos": False,
        },
        "propuestas_por_fuente": propuestas,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--parte", required=True)
    ap.add_argument("--tema", required=True, type=int)
    ap.add_argument("--norma-id", required=True, type=int, action="append")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()
    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    payload = construir_propuesta(pdf, db, args.parte, args.tema, list(dict.fromkeys(args.norma_id)), max(1, args.top))
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - INTERVALOS CONSECUTIVOS - SOLO LECTURA")
    print("=" * 78)
    print(f"Tema: {payload['tema']['parte']} {payload['tema']['numero_tema']}")
    for g in payload["propuestas_por_fuente"]:
        print(f"NORMA [{g['norma_id']}] {g['nombre_canonico']} | fuente={g['id_fuente']} | n={g['articulos_fuente']}")
        for c in g["intervalos_candidatos"]:
            print(f"  {c['posicion']}. {c['articulo_inicial']} -> {c['articulo_final']} | n={c['numero_articulos']} | score={c['score_intervalo']:.3f} | cobertura={c['cobertura_lexica_tema']:.3f} | {c['origen']}")
    print("No se ha consultado temario_referencias. BD/corpus sin cambios.")
    print(f"JSON: {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
