#!/usr/bin/env python3
"""Propone evidencia normativa por coincidencia literal para una muestra de control.

No modifica la base de datos ni los expedientes existentes. Busca frases largas del
enunciado y de las opciones dentro de las fuentes ya asociadas a la norma de cada
pregunta, y escribe propuestas auditables en JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "db" / "oposiciones.sqlite3"
DEFAULT_SAMPLE = ROOT / "outputs" / "auditoria_control_tests_v1" / "muestra_535.json"


def normalizar(texto: str | None) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def frases(texto: str | None, minimo: int = 9, maximo: int = 15) -> list[str]:
    palabras = normalizar(texto).split()
    if len(palabras) < minimo:
        return []
    resultado: list[str] = []
    for longitud in range(min(maximo, len(palabras)), minimo - 1, -1):
        for inicio in range(len(palabras) - longitud + 1):
            resultado.append(" ".join(palabras[inicio:inicio + longitud]))
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--muestra", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--ids", nargs="+", type=int, required=True)
    parser.add_argument("--salida", type=Path, required=True)
    args = parser.parse_args()
    muestra = json.loads(args.muestra.read_text(encoding="utf-8"))
    por_id = {int(x["id"]): x for x in muestra}
    faltan = sorted(set(args.ids) - set(por_id))
    if faltan:
        raise SystemExit(f"IDs ausentes de la muestra: {faltan}")
    con = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    propuestas = []
    for pregunta_id in args.ids:
        pregunta = por_id[pregunta_id]
        fuentes = [r[0] for r in con.execute(
            "SELECT id_fuente FROM norma_fuentes WHERE norma_id=? ORDER BY id_fuente",
            (pregunta["norma_id_normalizada"],),
        )]
        if not fuentes:
            propuestas.append({"pregunta_id": pregunta_id, "estado": "SIN_FUENTE_DE_NORMA", "coincidencias": []})
            continue
        marks = ",".join("?" for _ in fuentes)
        articulos = con.execute(
            f"SELECT id, id_boe, id_bloque, articulo_boe, titulo_bloque, texto, hash_texto "
            f"FROM articulos_fuente WHERE id_boe IN ({marks}) AND TRIM(texto)<>''",
            fuentes,
        ).fetchall()
        consultas = [("enunciado", pregunta["enunciado"])] + [
            (f"opcion_{letra}", pregunta.get(f"opcion_{letra}")) for letra in "abcd"
        ]
        hallazgos: dict[int, dict] = {}
        for origen, texto in consultas:
            for fragmento in frases(texto):
                for articulo in articulos:
                    if fragmento in normalizar(articulo["texto"]):
                        actual = hallazgos.setdefault(int(articulo["id"]), {
                            "origenes": set(), "fragmentos": set(), "articulo": articulo,
                        })
                        actual["origenes"].add(origen)
                        actual["fragmentos"].add(fragmento)
        coincidencias = []
        for dato in hallazgos.values():
            a = dato["articulo"]
            coincidencias.append({
                "id_fuente": a["id_boe"], "id": a["id"], "id_bloque": a["id_bloque"],
                "articulo_boe": a["articulo_boe"], "titulo_bloque": a["titulo_bloque"],
                "hash_texto": a["hash_texto"], "origenes": sorted(dato["origenes"]),
                "fragmentos": sorted(dato["fragmentos"], key=lambda x: (-len(x.split()), x))[:5],
            })
        coincidencias.sort(key=lambda x: (-len(x["fragmentos"][0].split()), x["id"]))
        propuestas.append({
            "pregunta_id": pregunta_id,
            "estado": "COINCIDENCIA_LITERAL_UNICA" if len(coincidencias) == 1 else "COINCIDENCIAS_MULTIPLES" if coincidencias else "SIN_COINCIDENCIA_LITERAL",
            "coincidencias": coincidencias,
        })
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(propuestas, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK {args.salida}")

if __name__ == "__main__":
    main()
