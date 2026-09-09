"""
Constructor Temario V2 - propuesta de respaldos para pseudonormas.

SOLO LECTURA.

Parte de los borradores generados para temas JURIDICO_DOCTRINAL_TRANSVERSAL y
busca artículos oficiales candidatos en el corpus ya enlazado de la BD
(normas -> norma_fuentes -> articulos_fuente).

No redacta doctrina, no acepta respaldos, no crea pseudonormas definitivas y no
modifica la base de datos. Toda coincidencia es únicamente PROPUESTA.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance
import generar_borradores_pseudonormas_v2 as borradores_mod

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"

STOP = {
    "de", "del", "la", "las", "el", "los", "y", "e", "o", "u", "a", "al",
    "en", "por", "para", "con", "sin", "un", "una", "unos", "unas", "su", "sus",
    "se", "que", "como", "sobre", "entre", "i", "ii", "iii", "iv", "v", "vi",
    "ser", "son", "esta", "este", "estos", "estas", "todo", "toda", "todos", "todas",
    "referencia", "especial", "general", "derecho", "norma", "normas", "juridica",
    "juridicas", "administracion", "administraciones", "publica", "publicas", "publico",
    "publicos",
}


@dataclass
class ArticuloCorpus:
    norma_id: int
    norma: str
    id_fuente: str
    articulo_fuente_id: int
    articulo: str
    titulo_bloque: str
    texto: str
    tokens: set[str]


@dataclass
class RespaldoPropuesto:
    estado: str
    score: float
    cobertura: float
    norma_id: int
    norma: str
    id_fuente: str
    articulo_fuente_id: int
    articulo: str
    titulo_bloque: str
    terminos: list[str]


def normalizar(texto: str) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", x).strip()


def tokens(texto: str) -> set[str]:
    return {
        t for t in normalizar(texto).split()
        if len(t) >= 4 and t not in STOP and not t.isdigit()
    }


def cargar_articulos(con) -> list[ArticuloCorpus]:
    filas = con.execute(
        """
        SELECT DISTINCT
               n.id AS norma_id,
               n.nombre_canonico,
               nf.id_fuente,
               af.id AS articulo_fuente_id,
               af.articulo_boe,
               af.titulo_bloque,
               af.texto
        FROM normas n
        JOIN norma_fuentes nf ON nf.norma_id = n.id
        JOIN articulos_fuente af ON af.id_boe = nf.id_fuente
        WHERE COALESCE(af.texto, '') <> ''
        ORDER BY n.id, af.id
        """
    ).fetchall()

    salida: list[ArticuloCorpus] = []
    vistos: set[tuple[int, int]] = set()
    for r in filas:
        clave = (int(r["norma_id"]), int(r["articulo_fuente_id"]))
        if clave in vistos:
            continue
        vistos.add(clave)
        texto_indice = " ".join(
            x for x in (
                str(r["articulo_boe"] or ""),
                str(r["titulo_bloque"] or ""),
                str(r["texto"] or ""),
            ) if x
        )
        salida.append(
            ArticuloCorpus(
                norma_id=int(r["norma_id"]),
                norma=str(r["nombre_canonico"] or ""),
                id_fuente=str(r["id_fuente"] or ""),
                articulo_fuente_id=int(r["articulo_fuente_id"]),
                articulo=str(r["articulo_boe"] or ""),
                titulo_bloque=str(r["titulo_bloque"] or ""),
                texto=str(r["texto"] or ""),
                tokens=tokens(texto_indice),
            )
        )
    return salida


def construir_idf(articulos: list[ArticuloCorpus]) -> dict[str, float]:
    n = len(articulos)
    df = Counter()
    for art in articulos:
        for tok in art.tokens:
            df[tok] += 1
    return {
        tok: math.log((n + 1) / (freq + 1)) + 1.0
        for tok, freq in df.items()
    }


def puntuar(texto_unidad: str, art: ArticuloCorpus, idf: dict[str, float]) -> tuple[float, float, list[str]]:
    consulta = tokens(texto_unidad)
    if not consulta:
        return 0.0, 0.0, []

    comunes = consulta.intersection(art.tokens)
    if not comunes:
        return 0.0, 0.0, []

    pesos = {t: idf.get(t, 1.0) for t in consulta}
    total = sum(pesos.values())
    cubierto = sum(pesos[t] for t in comunes)
    cobertura = cubierto / total if total else 0.0

    # Penaliza coincidencias apoyadas en un único término salvo que cubran una
    # consulta extremadamente corta. El objetivo es reducir falsos positivos,
    # no maximizar recall.
    diversidad = min(1.0, len(comunes) / max(2, min(4, len(consulta))))
    score = cobertura * diversidad

    comunes_ordenados = sorted(comunes, key=lambda t: (-pesos[t], t))
    return score, cobertura, comunes_ordenados


def proponer(
    texto_unidad: str,
    articulos: list[ArticuloCorpus],
    idf: dict[str, float],
    top: int,
) -> list[RespaldoPropuesto]:
    ranking = []
    for art in articulos:
        score, cobertura, comunes = puntuar(texto_unidad, art, idf)
        if score <= 0:
            continue
        ranking.append((score, cobertura, art, comunes))
    ranking.sort(key=lambda x: (-x[0], -x[1], x[2].norma_id, x[2].articulo_fuente_id))

    return [
        RespaldoPropuesto(
            estado="PROPUESTO",
            score=round(score, 6),
            cobertura=round(cobertura, 6),
            norma_id=art.norma_id,
            norma=art.norma,
            id_fuente=art.id_fuente,
            articulo_fuente_id=art.articulo_fuente_id,
            articulo=art.articulo,
            titulo_bloque=art.titulo_bloque,
            terminos=comunes[:10],
        )
        for score, cobertura, art, comunes in ranking[:max(1, top)]
    ]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Propone artículos oficiales de respaldo para borradores pseudonormativos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--parte", default=None)
    ap.add_argument("--tema", type=int, default=None)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--salida-json")
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    seleccion = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        if clase != "JURIDICO_DOCTRINAL_TRANSVERSAL":
            continue
        if args.parte and tema.parte.upper() != args.parte.upper():
            continue
        if args.tema is not None and tema.numero_tema != args.tema:
            continue
        seleccion.append(borradores_mod.construir_borrador(tema, evidencia))

    with comun.conectar_sqlite_solo_lectura(db) as con:
        articulos = cargar_articulos(con)
    idf = construir_idf(articulos)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - RESPALDOS PSEUDONORMATIVOS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Artículos oficiales indexados.... {len(articulos)}")
    print(f"Pseudonormas evaluadas............ {len(seleccion)}")
    print()

    payload = []
    total_unidades = 0
    total_propuestas = 0

    for borrador in seleccion:
        print(f"{borrador.parte:<8} | {borrador.numero_tema:>2} | {borrador.identidad}")
        unidades_json = []
        for unidad in borrador.unidades:
            total_unidades += 1
            propuestas = proponer(unidad.texto_temario, articulos, idf, args.top)
            total_propuestas += len(propuestas)
            print(f"  {unidad.codigo} | {unidad.texto_temario}")
            if not propuestas:
                print("       SIN RESPALDOS CANDIDATOS POR COINCIDENCIA TEXTUAL")
            else:
                for i, p in enumerate(propuestas, 1):
                    terminos = ", ".join(p.terminos) or "-"
                    print(
                        f"       {i}. [{p.norma_id}] {p.norma} | {p.articulo or '(sin rótulo)'} "
                        f"| score={p.score:.3f} | cobertura={p.cobertura:.3f} | términos={terminos}"
                    )
            unidades_json.append({
                "codigo": unidad.codigo,
                "texto_temario": unidad.texto_temario,
                "estado": "REVISION",
                "respaldos": [asdict(p) for p in propuestas],
            })
        payload.append({
            "identidad": borrador.identidad,
            "parte": borrador.parte,
            "numero_tema": borrador.numero_tema,
            "estado": "REVISION",
            "unidades": unidades_json,
        })
        print()

    print("Resumen:")
    print(f"  Pseudonormas evaluadas.......... {len(seleccion)}")
    print(f"  Unidades evaluadas.............. {total_unidades}")
    print(f"  Respaldos propuestos............ {total_propuestas}")
    print()
    print("Todos los respaldos están en estado PROPUESTO.")
    print("No se ha redactado contenido doctrinal.")
    print("No se ha aceptado ningún respaldo.")
    print("La base de datos NO ha sido modificada.")

    if args.salida_json:
        salida = Path(args.salida_json).expanduser().resolve()
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(
            json.dumps({
                "version": 1,
                "naturaleza": "RESPALDOS_PSEUDONORMATIVOS_PROPUESTOS",
                "estado": "REVISION",
                "pdf": str(pdf),
                "db": str(db),
                "resultados": payload,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON escrito...................... {salida}")
        print("La escritura anterior afecta sólo al fichero de salida solicitado.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
