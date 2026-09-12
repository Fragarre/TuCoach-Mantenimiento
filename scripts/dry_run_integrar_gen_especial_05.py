"""
Dry-run acumulativo de integración GEN A1 para ESPECIAL 1, 2, 4 y 5.

Garantías:
- NO importa sqlite3;
- NO abre ni modifica ninguna base de datos;
- NO modifica CSV ni JSON;
- parte siempre de temario_A1_v1.csv;
- sustituye virtualmente ESPECIAL 1, 2, 4 y 5;
- aplica exclusividad secuencial GENERAL 1 -> ESPECIAL 60;
- valida estructura y hash de los cuatro GEN revisados.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from extraer_temario_convocatoria import articulo_raiz, canonical_ley

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM"
CSV_DEFECTO = CARPETA / "temario_A1_v1.csv"
GEN_DIR = CARPETA / "gen_revision"

MARCADORES = {"no determinado", "no determinada", "no determinados", "no determinadas"}

CONFIG = {
    1: {
        "id": "GEN-A1-ESPECIAL-01",
        "nombre": "GEN - Las fuentes del derecho administrativo (I)",
        "n": 9,
        "gen": GEN_DIR / "GEN-A1-ESPECIAL-01.json",
        "directas": (
            ("Real Decreto de 24 de julio de 1889 por el que se publica el Código Civil", ("1","2","3","4","5","6","7")),
            ("Ley 25/2014, de 27 de noviembre, de Tratados y otros Acuerdos Internacionales", ("23","28","29","30")),
            ("Tratado de Funcionamiento de la Unión Europea", ("288",)),
        ),
    },
    2: {
        "id": "GEN-A1-ESPECIAL-02",
        "nombre": "GEN - Las fuentes del derecho administrativo (II)",
        "n": 6,
        "gen": GEN_DIR / "GEN-A1-ESPECIAL-02.json",
        "directas": (
            ("Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas", ("37","47","128","129")),
            ("Ley 29/1998, de 13 de julio, reguladora de la Jurisdicción Contencioso-administrativa", ("25","26","27")),
            ("Ley 7/1985, de 2 de abril, Reguladora de las Bases del Régimen Local", ("4",)),
        ),
    },
    4: {
        "id": "GEN-A1-ESPECIAL-04",
        "nombre": "GEN - Los actos jurídicos de la Administración",
        "n": 6,
        "gen": GEN_DIR / "GEN-A1-ESPECIAL-04.json",
        "directas": (
            ("Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas", ("34","35","36","112","114")),
            ("Ley 40/2015, de 1 de octubre, de Régimen Jurídico del Sector Público", ("5","8")),
            ("Ley 29/1998, de 13 de julio, reguladora de la Jurisdicción Contencioso-administrativa", ("25",)),
        ),
    },
    5: {
        "id": "GEN-A1-ESPECIAL-05",
        "nombre": "GEN - La eficacia temporal de las normas",
        "n": 6,
        "gen": GEN_DIR / "GEN-A1-ESPECIAL-05.json",
        # CC art. 2 y CE art. 9 alimentan el GEN pero ya están ocupados
        # estructuralmente por temas anteriores. ES5 se vincula solo por GEN-artículo.
        "directas": (),
    },
}


def es_nd(f):
    return str(f.get("LEY") or "").strip().casefold() in MARCADORES or str(f.get("articulo") or "").strip().casefold() in MARCADORES


def leer_csv(ruta: Path):
    if not ruta.is_file():
        raise FileNotFoundError(ruta)
    with ruta.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        cols = list(r.fieldnames or [])
        esperadas = ["parte","tema","titulo","LEY","articulo","tipo"]
        if cols != esperadas:
            raise RuntimeError(f"Cabecera inesperada: {cols}")
        return [dict(x) for x in r]


def validar_gen(cfg):
    ruta = Path(cfg["gen"])
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe GEN revisado: {ruta}")
    d = json.loads(ruta.read_text(encoding="utf-8"))
    if d.get("id_gen") != cfg["id"] or d.get("nombre_gen") != cfg["nombre"]:
        raise RuntimeError(f"Identidad GEN incorrecta en {ruta.name}")
    arts = d.get("articulos")
    if not isinstance(arts, list) or len(arts) != cfg["n"]:
        raise RuntimeError(f"{cfg['id']}: número de artículos incorrecto")
    for i, art in enumerate(arts, 1):
        if int(art.get("numero", -1)) != i:
            raise RuntimeError(f"{cfg['id']}: secuencia incorrecta en art. {i}")
        esperado = f"{cfg['id']}-ART-{i:03d}"
        if art.get("id_bloque") != esperado:
            raise RuntimeError(f"{cfg['id']}: id_bloque incorrecto en art. {i}")
        texto = str(art.get("texto_final") or "")
        if not texto.strip():
            raise RuntimeError(f"{cfg['id']}: texto vacío en art. {i}")
        h = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        if art.get("hash_texto_final") != h:
            raise RuntimeError(f"{cfg['id']}: hash incorrecto en art. {i}")
    return d


def filas_nuevas(tema: int, titulo: str, cfg, gen):
    out = []
    for ley, articulos in cfg["directas"]:
        for art in articulos:
            out.append({"parte":"ESPECIAL","tema":str(tema),"titulo":titulo,"LEY":ley,"articulo":art,"tipo":"JURIDICO"})
    for art in gen["articulos"]:
        out.append({"parte":"ESPECIAL","tema":str(tema),"titulo":titulo,"LEY":cfg["nombre"],"articulo":str(art["numero"]),"tipo":"JURIDICO"})
    return out


def sustituir(filas, tema: int, cfg, gen):
    obj = [f for f in filas if f["parte"] == "ESPECIAL" and f["tema"] == str(tema)]
    if len(obj) != 1 or not es_nd(obj[0]):
        raise RuntimeError(f"ESPECIAL {tema}: se esperaba exactamente una fila No determinada")
    nuevas = filas_nuevas(tema, obj[0]["titulo"], cfg, gen)
    out, hecho = [], False
    for f in filas:
        if not hecho and f["parte"] == "ESPECIAL" and f["tema"] == str(tema) and es_nd(f):
            out.extend(nuevas); hecho = True
        else:
            out.append(dict(f))
    return out


def orden(f, pos):
    p = str(f["parte"]).upper()
    return (0 if p == "GENERAL" else 1 if p == "ESPECIAL" else 2, int(f["tema"]), pos)


def exclusividad(filas):
    ordenadas = [f for _, f in sorted(enumerate(filas), key=lambda x: orden(x[1], x[0]))]
    vistos, final, omitidas = {}, [], []
    for f in ordenadas:
        if es_nd(f):
            final.append(f); continue
        k = (canonical_ley(f["LEY"]), articulo_raiz(f["articulo"]))
        tema = (f["parte"], f["tema"])
        ant = vistos.get(k)
        if ant is not None and ant != tema:
            omitidas.append({"fila":f,"prioritario":ant,"raiz":k[1]})
            continue
        vistos.setdefault(k, tema)
        final.append(f)
    return final, omitidas


def main():
    p = argparse.ArgumentParser(description="Dry-run acumulativo A1 ESPECIAL 1, 2, 4 y 5")
    p.add_argument("--csv", default=str(CSV_DEFECTO))
    args = p.parse_args()
    filas = leer_csv(Path(args.csv).resolve())
    nd_antes = sum(es_nd(f) for f in filas)
    gens = {t: validar_gen(c) for t,c in CONFIG.items()}

    virtual = filas
    for tema in (1,2,4,5):
        virtual = sustituir(virtual, tema, CONFIG[tema], gens[tema])

    final, omitidas = exclusividad(virtual)
    nd_despues = sum(es_nd(f) for f in final)

    print("="*78)
    print("DRY-RUN ACUMULATIVO GEN A1 / ESPECIAL 1 + 2 + 4 + 5")
    print("="*78)
    print(f"Filas CSV originales: {len(filas)}")
    print(f"No determinados antes: {nd_antes}")
    for tema in (1,2,4,5):
        cfg = CONFIG[tema]
        rows = [f for f in final if f["parte"] == "ESPECIAL" and f["tema"] == str(tema)]
        gen_rows = [f for f in rows if f["LEY"] == cfg["nombre"]]
        directas = [f for f in rows if f["LEY"] != cfg["nombre"]]
        print(f"ESPECIAL {tema}: directas finales={len(directas)} | GEN={len(gen_rows)} | total={len(rows)}")
    print(f"No determinados después: {nd_despues}")
    print(f"Filas virtuales finales: {len(final)}")
    print(f"Omisiones secuenciales totales: {len(omitidas)}")

    print("\nOMISIONES SECUENCIALES")
    for x in omitidas:
        f = x["fila"]
        print(f"- {f['parte']} {f['tema']}: {f['LEY']} art. {f['articulo']} -> ya asignado en {x['prioritario'][0]} {x['prioritario'][1]} (raíz {x['raiz']})")

    print("\nGEN ESPECIAL 5 PREVISTO")
    g5 = gens[5]
    for art in g5["articulos"]:
        print(f"- art. {art['numero']} | {art['id_bloque']} | sha256={art['hash_texto_final']}")

    print("\nVALIDACION: OK")
    print("BD abierta: NO")
    print("BD modificada: NO")
    print("CSV modificado: NO")
    print("Estado para integración real: BLOQUEADO mientras queden No determinados.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
