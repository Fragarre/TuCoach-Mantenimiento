"""
Constructor Temario V2 - propuesta de rangos estructurales para temas JURIDICO_DELIMITABLE.

SOLO LECTURA.

Una vez seleccionada explícitamente una norma, no intenta adivinar artículos uno a uno.
Agrupa los artículos oficiales en bloques estructurales consecutivos según titulo_bloque,
compara cada bloque con el texto oficial del tema y propone los bloques más compatibles.
El resultado es evidencia para revisión humana, nunca una aceptación automática.

No consulta temario_referencias y no modifica BD, corpus, temario.csv ni bancos.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import evaluar_normas_por_contenido_v2 as contenido
import extraer_estructura_temario_v2 as estructura
import proponer_articulos_delimitables_v2 as articulos_v2

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def _normalizar_titulo(valor: str) -> str:
    return re.sub(r"\s+", " ", (valor or "").strip())


def agrupar_bloques(articulos: list[dict]) -> list[dict]:
    """Agrupa artículos consecutivos por titulo_bloque, sin mezclar normas."""
    resultado: list[dict] = []
    actual: dict | None = None

    for a in articulos:
        titulo = _normalizar_titulo(a.get("titulo_bloque", ""))
        clave = (a["norma_id"], titulo)
        if actual is None or actual["clave"] != clave:
            actual = {
                "clave": clave,
                "norma_id": a["norma_id"],
                "nombre_canonico": a["nombre_canonico"],
                "titulo_bloque": titulo,
                "articulos": [],
            }
            resultado.append(actual)
        actual["articulos"].append(a)

    for b in resultado:
        texto = " ".join(
            " ".join(x for x in (a.get("articulo", ""), a.get("titulo_bloque", ""), a.get("texto", "")) if x)
            for a in b["articulos"]
        )
        b["tokens"] = contenido.tokens(texto)
        b["articulo_inicial"] = b["articulos"][0]["articulo"]
        b["articulo_final"] = b["articulos"][-1]["articulo"]
        b["articulo_fuente_ids"] = [a["articulo_fuente_id"] for a in b["articulos"]]
        b["numero_articulos"] = len(b["articulos"])
    return resultado


def construir_idf_bloques(bloques: list[dict]) -> dict[str, float]:
    n = len(bloques)
    df = Counter()
    for b in bloques:
        for tok in b["tokens"]:
            df[tok] += 1
    return {tok: math.log((n + 1) / (freq + 1)) + 1.0 for tok, freq in df.items()}


def _cobertura_tema(texto_tema: str, tokens_bloque: set[str]) -> tuple[int, int, float]:
    tokens_tema = set(contenido.tokens(texto_tema))
    if not tokens_tema:
        return 0, 0, 0.0
    comunes = tokens_tema & tokens_bloque
    return len(comunes), len(tokens_tema), len(comunes) / len(tokens_tema)


def construir_propuesta(pdf: Path, db: Path, parte: str, numero_tema: int, norma_ids: list[int], top: int) -> dict:
    tema, evidencia_alcance = articulos_v2.localizar_tema(pdf, parte, numero_tema)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        arts = articulos_v2.cargar_articulos_normas(con, norma_ids)

    encontrados = {a["norma_id"] for a in arts}
    faltantes = sorted(set(norma_ids) - encontrados)
    if faltantes:
        raise SystemExit("ERROR: normas sin artículos oficiales enlazados en corpus: " + ", ".join(map(str, faltantes)))

    bloques = agrupar_bloques(arts)
    nombres = {a["norma_id"]: a["nombre_canonico"] for a in arts}
    salida_bloques = []

    for nid in norma_ids:
        propios = [b for b in bloques if b["norma_id"] == nid]
        idf = construir_idf_bloques(propios)
        candidatos = []
        for b in propios:
            score, comunes = contenido.puntuacion_tema_norma(tema.texto, b["tokens"], idf)
            if score <= 0:
                continue
            n_comunes, n_tema, cobertura = _cobertura_tema(tema.texto, set(b["tokens"]))
            candidatos.append((score, cobertura, b, comunes, n_comunes, n_tema))
        candidatos.sort(key=lambda x: (-x[0], -x[1], x[2]["articulo_fuente_ids"][0]))

        salida_bloques.append({
            "norma_id": nid,
            "nombre_canonico": nombres.get(nid, ""),
            "bloques_candidatos": [
                {
                    "posicion": i,
                    "titulo_bloque": b["titulo_bloque"],
                    "articulo_inicial": b["articulo_inicial"],
                    "articulo_final": b["articulo_final"],
                    "numero_articulos": b["numero_articulos"],
                    "articulo_fuente_ids": b["articulo_fuente_ids"],
                    "score_contenido": round(float(score), 6),
                    "cobertura_lexica_tema": round(float(cobertura), 6),
                    "terminos_tema_cubiertos": n_comunes,
                    "terminos_tema_total": n_tema,
                    "terminos_comunes": comunes,
                    "estado": "PROPUESTO",
                    "decision_humana": None,
                }
                for i, (score, cobertura, b, comunes, n_comunes, n_tema) in enumerate(candidatos[:top], 1)
            ],
        })

    return {
        "version": 1,
        "naturaleza": "PROPUESTAS_RANGOS_ESTRUCTURALES_DELIMITABLES_V2",
        "modo": "SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "tema": {
            "parte": tema.parte,
            "numero_tema": tema.numero_tema,
            "bloque_numero": tema.bloque_numero,
            "bloque_titulo": tema.bloque_titulo,
            "texto_oficial": tema.texto,
            "alcance_v2": "JURIDICO_DELIMITABLE",
            "evidencia_alcance": evidencia_alcance,
        },
        "normas_seleccionadas": [{"norma_id": nid, "nombre_canonico": nombres.get(nid, "")} for nid in norma_ids],
        "estado": "REVISION_RANGOS_ESTRUCTURALES",
        "reglas": {
            "normas_seleccionadas_explicitamente": True,
            "unidad_propuesta": "BLOQUE_ESTRUCTURAL_CONSECUTIVO",
            "titulo_bloque_como_estructura": True,
            "ranking_articulo_individual_es_decisorio": False,
            "consulta_temario_referencias": False,
            "aceptacion_automatica": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
            "modifica_bancos": False,
        },
        "candidatos_por_norma": salida_bloques,
    }


def imprimir(payload: dict) -> None:
    tema = payload["tema"]
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - RANGOS ESTRUCTURALES DELIMITABLES - SOLO LECTURA")
    print("=" * 78)
    print(f"Tema............................. {tema['parte']} {tema['numero_tema']}")
    print(f"Texto............................ {tema['texto_oficial']}")
    print()
    for grupo in payload["candidatos_por_norma"]:
        print(f"NORMA [{grupo['norma_id']}] {grupo['nombre_canonico']}")
        for c in grupo["bloques_candidatos"]:
            titulo = c["titulo_bloque"] or "(sin título estructural)"
            print(f"  {c['posicion']}. {c['articulo_inicial']} -> {c['articulo_final']} | n={c['numero_articulos']} | score={c['score_contenido']:.3f} | cobertura={c['cobertura_lexica_tema']:.3f} | {titulo}")
        print()
    print("Todos los rangos permanecen PROPUESTO.")
    print("La base de datos NO ha sido modificada.")
    print("No se ha consultado temario_referencias.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Propone bloques/rangos estructurales dentro de normas seleccionadas.")
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
    norma_ids = list(dict.fromkeys(args.norma_id))
    payload = construir_propuesta(pdf, db, args.parte, args.tema, norma_ids, max(1, args.top))
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    imprimir(payload)
    print(f"Propuestas JSON.................. {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
