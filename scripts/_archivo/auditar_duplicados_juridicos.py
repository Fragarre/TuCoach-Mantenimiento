from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime


RAIZ = Path(__file__).resolve().parents[1]
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SALIDA_DEFECTO = RAIZ / "auditorias" / "duplicados_juridicos"


def normalizar(texto: str | None) -> str:
    s = "" if texto is None else str(texto)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[“”«»]", '"', s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def firma_exacta(fila: sqlite3.Row) -> tuple[str, ...]:
    return tuple(
        normalizar(fila[c])
        for c in ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d")
    )


def similitud_opciones(a: sqlite3.Row, b: sqlite3.Row) -> float:
    sims = []
    for c in ("opcion_a", "opcion_b", "opcion_c", "opcion_d"):
        x = normalizar(a[c])
        y = normalizar(b[c])
        if x == y:
            sims.append(1.0)
        else:
            sims.append(SequenceMatcher(None, x, y, autojunk=False).ratio())
    return sum(sims) / 4.0


def abrir_solo_lectura(db: Path) -> sqlite3.Connection:
    uri = db.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def fila_salida(tipo: str, grupo: str, a: sqlite3.Row, b: sqlite3.Row | None, sim: float | None):
    base = {
        "tipo": tipo,
        "grupo": grupo,
        "id_1": a["id"],
        "id_2": "" if b is None else b["id"],
        "similitud": "" if sim is None else f"{sim:.6f}",
        "respuesta_1": a["respuesta_correcta"] or "",
        "respuesta_2": "" if b is None else (b["respuesta_correcta"] or ""),
        "origen_1": a["origen_oposicion"] or "",
        "origen_2": "" if b is None else (b["origen_oposicion"] or ""),
        "fuente_1": a["tipo_fuente"] or "",
        "fuente_2": "" if b is None else (b["tipo_fuente"] or ""),
        "norma_1": a["nombre_norma_normalizado"] or a["nombre_norma"] or "",
        "norma_2": "" if b is None else (b["nombre_norma_normalizado"] or b["nombre_norma"] or ""),
        "articulo_1": a["articulo_normalizado"] or a["articulo"] or "",
        "articulo_2": "" if b is None else (b["articulo_normalizado"] or b["articulo"] or ""),
        "enunciado_1": a["enunciado"] or "",
        "enunciado_2": "" if b is None else (b["enunciado"] or ""),
        "opcion_a_1": a["opcion_a"] or "",
        "opcion_a_2": "" if b is None else (b["opcion_a"] or ""),
        "opcion_b_1": a["opcion_b"] or "",
        "opcion_b_2": "" if b is None else (b["opcion_b"] or ""),
        "opcion_c_1": a["opcion_c"] or "",
        "opcion_c_2": "" if b is None else (b["opcion_c"] or ""),
        "opcion_d_1": a["opcion_d"] or "",
        "opcion_d_2": "" if b is None else (b["opcion_d"] or ""),
    }
    return base


def main() -> int:
    p = argparse.ArgumentParser(
        description="Auditoría SOLO LECTURA de duplicados e incoherencias en preguntas jurídicas."
    )
    p.add_argument("--db", type=Path, default=DB_DEFECTO)
    p.add_argument("--salida", type=Path, default=SALIDA_DEFECTO)
    p.add_argument(
        "--umbral",
        type=float,
        default=0.985,
        help="Similitud mínima de opciones para considerar casi duplicado cuando el enunciado coincide.",
    )
    args = p.parse_args()

    db = args.db.resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la base: {db}")

    salida = args.salida.resolve()
    salida.mkdir(parents=True, exist_ok=True)

    with abrir_solo_lectura(db) as con:
        filas = con.execute(
            """
            SELECT
                id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d,
                respuesta_correcta, tipo_clasificacion,
                nombre_norma, nombre_norma_normalizado,
                articulo, articulo_normalizado,
                origen_oposicion, tipo_fuente, importacion_fichero_id
            FROM lote_preguntas
            WHERE tipo_clasificacion='JURIDICA'
            ORDER BY id
            """
        ).fetchall()

    exactos = defaultdict(list)
    por_enunciado = defaultdict(list)

    for f in filas:
        exactos[firma_exacta(f)].append(f)
        por_enunciado[normalizar(f["enunciado"])].append(f)

    grupos_exactos = [g for g in exactos.values() if len(g) > 1]
    exactos_conflictivos = [
        g for g in grupos_exactos
        if len({normalizar(x["respuesta_correcta"]) for x in g}) > 1
    ]

    # Casi duplicados: mismo enunciado normalizado + opciones en las mismas posiciones
    # casi idénticas. Se limita cada grupo por norma/artículo cuando están informados
    # para evitar combinatoria y falsos positivos en enunciados muy genéricos.
    casi = []
    vistos = set()
    for grupo_enunciado in por_enunciado.values():
        if len(grupo_enunciado) < 2:
            continue

        subgrupos = defaultdict(list)
        for f in grupo_enunciado:
            norma = normalizar(f["nombre_norma_normalizado"] or f["nombre_norma"])
            art = normalizar(f["articulo_normalizado"] or f["articulo"])
            subgrupos[(norma, art)].append(f)

        for sg in subgrupos.values():
            n = len(sg)
            if n < 2:
                continue
            for i in range(n - 1):
                for j in range(i + 1, n):
                    a, b = sg[i], sg[j]
                    par = tuple(sorted((int(a["id"]), int(b["id"]))))
                    if par in vistos:
                        continue
                    vistos.add(par)

                    if firma_exacta(a) == firma_exacta(b):
                        continue

                    sim = similitud_opciones(a, b)
                    if sim >= args.umbral:
                        casi.append((a, b, sim))

    casi_conflictivos = [
        (a, b, sim)
        for a, b, sim in casi
        if normalizar(a["respuesta_correcta"]) != normalizar(b["respuesta_correcta"])
    ]

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_conf = salida / f"duplicados_conflictivos_{marca}.csv"
    campos = list(
        fila_salida("", "", filas[0], None, None).keys()
    ) if filas else []

    registros = []

    num = 0
    for grupo in exactos_conflictivos:
        num += 1
        for i in range(len(grupo) - 1):
            for j in range(i + 1, len(grupo)):
                a, b = grupo[i], grupo[j]
                if normalizar(a["respuesta_correcta"]) != normalizar(b["respuesta_correcta"]):
                    registros.append(fila_salida("EXACTO_RESPUESTA_DISTINTA", f"E{num}", a, b, 1.0))

    for idx, (a, b, sim) in enumerate(casi_conflictivos, 1):
        registros.append(fila_salida("CASI_DUPLICADO_RESPUESTA_DISTINTA", f"C{idx}", a, b, sim))

    if registros:
        with csv_conf.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
            w.writeheader()
            w.writerows(registros)

    print("=" * 78)
    print("AUDITORÍA DE DUPLICADOS JURÍDICOS - SOLO LECTURA")
    print("=" * 78)
    print(f"Base: {db}")
    print(f"Preguntas jurídicas analizadas........... {len(filas)}")
    print()
    print(f"Grupos duplicados exactos................ {len(grupos_exactos)}")
    print(f"Preguntas dentro de duplicados exactos... {sum(len(g) for g in grupos_exactos)}")
    print(f"Grupos exactos con respuesta distinta.... {len(exactos_conflictivos)}")
    print()
    print(f"Pares casi duplicados (umbral {args.umbral:.3f})........ {len(casi)}")
    print(f"Casi duplicados con respuesta distinta... {len(casi_conflictivos)}")
    print()
    if registros:
        print(f"Informe prioritario....................... {csv_conf}")
    else:
        print("Informe prioritario....................... no necesario")
    print()
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
