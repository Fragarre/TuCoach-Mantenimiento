"""
Constructor Temario V2 - propuesta de artículos para temas JURIDICO_DELIMITABLE.

SOLO LECTURA.

Parte de una o varias normas seleccionadas explícitamente por el operador tras
revisar las propuestas de norma. Dentro de esas normas:
- recupera únicamente artículos del corpus oficial enlazado;
- compara el texto oficial del tema con cada artículo;
- genera un ranking de artículos candidatos;
- no consulta temario_referencias;
- no acepta artículos automáticamente;
- no modifica BD, corpus, temario.csv ni bancos.

El ranking es evidencia para revisión humana, no una decisión jurídica.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import comun
import clasificar_alcance_juridico_v2 as alcance
import evaluar_normas_por_contenido_v2 as contenido
import extraer_estructura_temario_v2 as estructura

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def cargar_articulos_normas(con, norma_ids: list[int]) -> list[dict]:
    if not norma_ids:
        return []
    marcas = ",".join("?" for _ in norma_ids)
    filas = con.execute(
        f"""
        SELECT DISTINCT
               n.id AS norma_id,
               n.nombre_canonico,
               af.id AS articulo_fuente_id,
               af.id_boe,
               af.articulo_boe,
               af.titulo_bloque,
               af.texto
        FROM normas n
        JOIN norma_fuentes nf ON nf.norma_id=n.id
        JOIN articulos_fuente af ON af.id_boe=nf.id_fuente
        WHERE n.id IN ({marcas})
          AND COALESCE(af.texto,'')<>''
        ORDER BY n.id, af.id
        """,
        tuple(norma_ids),
    ).fetchall()

    vistos = set()
    resultado = []
    for r in filas:
        clave = (int(r["norma_id"]), int(r["articulo_fuente_id"]))
        if clave in vistos:
            continue
        vistos.add(clave)
        texto = " ".join(
            x for x in (
                str(r["articulo_boe"] or ""),
                str(r["titulo_bloque"] or ""),
                str(r["texto"] or ""),
            ) if x
        )
        resultado.append({
            "norma_id": int(r["norma_id"]),
            "nombre_canonico": str(r["nombre_canonico"] or ""),
            "articulo_fuente_id": int(r["articulo_fuente_id"]),
            "id_fuente": str(r["id_boe"] or ""),
            "articulo": str(r["articulo_boe"] or ""),
            "titulo_bloque": str(r["titulo_bloque"] or ""),
            "texto": str(r["texto"] or ""),
            "tokens": contenido.tokens(texto),
        })
    return resultado


def construir_idf_articulos(articulos: list[dict]) -> dict[str, float]:
    n = len(articulos)
    df = Counter()
    for a in articulos:
        for tok in a["tokens"]:
            df[tok] += 1
    return {
        tok: math.log((n + 1) / (freq + 1)) + 1.0
        for tok, freq in df.items()
    }


def localizar_tema(pdf: Path, parte: str, numero_tema: int):
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)
        if tema.parte.upper() == parte.upper() and tema.numero_tema == numero_tema:
            clase, evidencia = alcance.clasificar(tema)
            if clase != "JURIDICO_DELIMITABLE":
                raise SystemExit(
                    f"ERROR: {parte} {numero_tema} no es JURIDICO_DELIMITABLE; "
                    f"clasificacion={clase}."
                )
            return tema, evidencia
    raise SystemExit(f"ERROR: no existe el tema {parte} {numero_tema} en el PDF.")


def construir_propuesta(
    pdf: Path,
    db: Path,
    parte: str,
    numero_tema: int,
    norma_ids: list[int],
    top: int,
) -> dict:
    tema, evidencia_alcance = localizar_tema(pdf, parte, numero_tema)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        articulos = cargar_articulos_normas(con, norma_ids)

    encontrados = {a["norma_id"] for a in articulos}
    faltantes = sorted(set(norma_ids) - encontrados)
    if faltantes:
        raise SystemExit(
            "ERROR: normas sin artículos oficiales enlazados en corpus: "
            + ", ".join(str(x) for x in faltantes)
        )

    idf = construir_idf_articulos(articulos)
    por_norma: dict[int, list[dict]] = {}
    for nid in norma_ids:
        candidatos = []
        for a in articulos:
            if a["norma_id"] != nid:
                continue
            score, comunes = contenido.puntuacion_tema_norma(
                tema.texto, a["tokens"], idf
            )
            if score <= 0:
                continue
            candidatos.append((score, a, comunes))
        candidatos.sort(
            key=lambda x: (-x[0], x[1]["articulo_fuente_id"])
        )
        por_norma[nid] = [
            {
                "posicion": i,
                "norma_id": a["norma_id"],
                "nombre_canonico": a["nombre_canonico"],
                "articulo_fuente_id": a["articulo_fuente_id"],
                "id_fuente": a["id_fuente"],
                "articulo": a["articulo"],
                "titulo_bloque": a["titulo_bloque"],
                "score_contenido": round(float(score), 6),
                "terminos_comunes": comunes,
                "estado": "PROPUESTO",
                "decision_humana": None,
            }
            for i, (score, a, comunes) in enumerate(candidatos[:top], 1)
        ]

    nombres = {}
    for a in articulos:
        nombres[a["norma_id"]] = a["nombre_canonico"]

    return {
        "version": 1,
        "naturaleza": "PROPUESTAS_ARTICULOS_DELIMITABLES_V2",
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
        "normas_seleccionadas": [
            {"norma_id": nid, "nombre_canonico": nombres.get(nid, "")}
            for nid in norma_ids
        ],
        "estado": "REVISION_ARTICULOS",
        "reglas": {
            "normas_seleccionadas_explicitamente": True,
            "origen_articulos": "CORPUS_OFICIAL_ENLAZADO",
            "consulta_temario_referencias": False,
            "aceptacion_automatica": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
            "modifica_bancos": False,
        },
        "candidatos_por_norma": [
            {
                "norma_id": nid,
                "nombre_canonico": nombres.get(nid, ""),
                "candidatos_articulo": por_norma[nid],
            }
            for nid in norma_ids
        ],
    }


def imprimir(payload: dict) -> None:
    tema = payload["tema"]
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - ARTICULOS CANDIDATOS DELIMITABLES - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {payload['pdf']}")
    print(f"BD............................... {payload['db']}")
    print(f"Tema............................. {tema['parte']} {tema['numero_tema']}")
    print(f"Alcance.......................... {tema['alcance_v2']}")
    print(f"Texto............................ {tema['texto_oficial']}")
    print()

    for grupo in payload["candidatos_por_norma"]:
        print(f"NORMA [{grupo['norma_id']}] {grupo['nombre_canonico']}")
        candidatos = grupo["candidatos_articulo"]
        if not candidatos:
            print("  SIN ARTICULOS CANDIDATOS")
        for c in candidatos:
            terminos = ", ".join(c["terminos_comunes"][:10]) or "-"
            titulo = f" | {c['titulo_bloque']}" if c["titulo_bloque"] else ""
            print(
                f"  {c['posicion']}. {c['articulo']} | score={c['score_contenido']:.3f}"
                f"{titulo} | terminos={terminos}"
            )
        print()

    print("Todos los artículos permanecen PROPUESTO.")
    print("La base de datos NO ha sido modificada.")
    print("No se ha consultado temario_referencias.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Propone artículos dentro de normas seleccionadas para un tema delimitable."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--parte", required=True)
    ap.add_argument("--tema", required=True, type=int)
    ap.add_argument(
        "--norma-id", required=True, type=int, action="append",
        help="Norma canónica previamente seleccionada; puede repetirse.",
    )
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    norma_ids = list(dict.fromkeys(args.norma_id))

    payload = construir_propuesta(
        pdf, db, args.parte, args.tema, norma_ids, max(1, args.top)
    )
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    imprimir(payload)
    print(f"Propuestas JSON.................. {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
