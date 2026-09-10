"""
Constructor Temario V2 - auditoría de representabilidad estructural con fallback BOE.

SOLO LECTURA.

Extiende auditar_representabilidad_estructural_v2.py sin modificarlo:
- usa índice cacheado si existe;
- si falta y la fuente es BOE-A-*, descarga temporalmente el HTML oficial del BOE;
- normaliza artículos históricos rotulados en letras (primero, nueve, dieciocho...)
  para compararlos con las etiquetas numéricas del corpus;
- no persiste la descarga;
- no modifica BD, corpus, temario.csv, bancos ni caché.

La verdad consolidada se usa exclusivamente como referencia de evaluación.
"""
from __future__ import annotations

import argparse
import copy
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import comun
import auditar_representabilidad_estructural_v2 as base
import extraer_jerarquia_indice_v2 as jerarquia_v2

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
CACHE = RAIZ / "cache_localizador_normativa"

NUMEROS_ARTICULO = {
    "primero": "1", "primer": "1", "uno": "1",
    "segundo": "2", "dos": "2",
    "tercero": "3", "tercer": "3", "tres": "3",
    "cuarto": "4", "cuatro": "4",
    "quinto": "5", "cinco": "5",
    "sexto": "6", "seis": "6",
    "septimo": "7", "siete": "7",
    "octavo": "8", "ocho": "8",
    "noveno": "9", "nueve": "9",
    "decimo": "10", "diez": "10",
    "undecimo": "11", "once": "11",
    "duodecimo": "12", "doce": "12",
    "decimotercero": "13", "trece": "13",
    "decimocuarto": "14", "catorce": "14",
    "decimoquinto": "15", "quince": "15",
    "decimosexto": "16", "dieciseis": "16",
    "decimoseptimo": "17", "diecisiete": "17",
    "decimoctavo": "18", "dieciocho": "18",
    "decimonoveno": "19", "diecinueve": "19",
    "vigesimo": "20", "veinte": "20",
    "veintiuno": "21", "veintidos": "22", "veintitres": "23",
    "veinticuatro": "24", "veinticinco": "25", "veintiseis": "26",
    "veintisiete": "27", "veintiocho": "28", "veintinueve": "29",
    "treinta": "30", "treinta y uno": "31", "treinta y dos": "32",
    "treinta y tres": "33", "treinta y cuatro": "34", "treinta y cinco": "35",
    "treinta y seis": "36", "treinta y siete": "37", "treinta y ocho": "38",
    "treinta y nueve": "39", "cuarenta": "40", "cuarenta y uno": "41",
    "cuarenta y dos": "42", "cuarenta y tres": "43", "cuarenta y cuatro": "44",
    "cuarenta y cinco": "45", "cuarenta y seis": "46", "cuarenta y siete": "47",
    "cuarenta y ocho": "48", "cuarenta y nueve": "49",
}


def normalizar_etiqueta_historica(valor: str) -> str:
    clave = base.norm_articulo(valor)
    return NUMEROS_ARTICULO.get(clave, valor)


def normalizar_payload_historico(payload: dict) -> dict:
    """Normaliza solo la etiqueta del artículo para la comparación de esta auditoría."""
    salida = copy.deepcopy(payload)
    for articulo in salida.get("articulos", []):
        articulo["articulo"] = normalizar_etiqueta_historica(str(articulo.get("articulo") or ""))
    return salida


def cargar_payload(fid: str):
    ruta = CACHE / fid / "indice.html"
    if ruta.exists():
        payload = jerarquia_v2.reconstruir(fid, ruta)
        return normalizar_payload_historico(payload), "CACHE"

    if not fid.startswith("BOE-A-"):
        return None, "NO_DISPONIBLE"

    url = f"https://www.boe.es/buscar/act.php?id={fid}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 ConstructorTemarioV2/solo-lectura"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            bruto = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None, "ERROR_BOE"

    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1", errors="replace")

    with tempfile.TemporaryDirectory(prefix="temario_v2_") as td:
        tmp = Path(td) / "indice.html"
        tmp.write_text(texto, encoding="utf-8")
        payload = jerarquia_v2.reconstruir(fid, tmp)

    if not payload.get("articulos"):
        return None, "SIN_INDICE_ESTRUCTURAL"
    return normalizar_payload_historico(payload), "BOE_TEMPORAL"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--convocatoria-patron", required=True, type=int)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()

    with comun.conectar_sqlite_solo_lectura(db) as con:
        verdad = base.cargar_verdad(con, args.convocatoria_patron)
        fuentes_norma = base.cargar_fuentes_normas(con, {k[2] for k in verdad})

    delimitables = {(t.parte.upper(), t.numero_tema): t for t in base.temas_delimitables(pdf)}
    pares = [k for k in sorted(verdad) if (k[0], k[1]) in delimitables]

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - REPRESENTABILIDAD ESTRUCTURAL + BOE TEMPORAL")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria patrón.............. {args.convocatoria_patron}")
    print(f"Pares tema-norma................. {len(pares)}")
    print()

    n = 0
    f1_sum = rec_sum = pre_sum = 0.0
    exactos = 0
    sin_fuente = 0
    cache_n = boe_temp_n = 0
    payload_cache = {}

    for clave in pares:
        parte, tema_num, nid = clave
        refs = verdad[clave]
        mejor_global = None
        estados = []

        for fid in fuentes_norma.get(nid, []):
            if fid not in payload_cache:
                payload_cache[fid] = cargar_payload(fid)
            payload, origen = payload_cache[fid]
            estados.append(f"{fid}:{origen}")
            if payload is None:
                continue

            bs = base.bloques(payload)
            elegidos, union, pre, rec, f1 = base.mejor_union_bloques(refs, bs)
            cand = (f1, rec, pre, fid, origen, elegidos, union)
            if mejor_global is None or cand[:3] > mejor_global[:3]:
                mejor_global = cand

        print(f"{parte:<8} | {tema_num:>2} | norma_id={nid} | refs={len(refs)}")
        if mejor_global is None:
            sin_fuente += 1
            print("  SIN ESTRUCTURA EVALUABLE")
            if estados:
                print("  fuentes: " + " | ".join(estados))
            print()
            continue

        f1, rec, pre, fid, origen, elegidos, union = mejor_global
        n += 1
        f1_sum += f1
        rec_sum += rec
        pre_sum += pre
        if pre == 1.0 and rec == 1.0:
            exactos += 1
        if origen == "CACHE":
            cache_n += 1
        elif origen == "BOE_TEMPORAL":
            boe_temp_n += 1

        print(f"  fuente={fid} | origen={origen} | union={len(union)} | P={pre:.1%} R={rec:.1%} F1={f1:.1%}")
        for b in elegidos:
            print(f"    {b['nivel']}: {' > '.join(b['ruta'])} | n={len(b['articulos'])}")
        faltan = sorted(refs - union)
        sobran = sorted(union - refs)
        if faltan:
            print("    faltan: " + ", ".join(faltan[:20]) + (" ..." if len(faltan) > 20 else ""))
        if sobran:
            print("    sobran: " + ", ".join(sobran[:20]) + (" ..." if len(sobran) > 20 else ""))
        print()

    print("Resumen:")
    print(f"  Pares evaluables................. {n}")
    print(f"  Desde caché...................... {cache_n}")
    print(f"  Desde BOE temporal............... {boe_temp_n}")
    print(f"  Sin estructura evaluable......... {sin_fuente}")
    print(f"  Precisión media oracle estruct... {(pre_sum/n if n else 0):.1%}")
    print(f"  Recall medio oracle estruct...... {(rec_sum/n if n else 0):.1%}")
    print(f"  F1 medio oracle estruct.......... {(f1_sum/n if n else 0):.1%}")
    print(f"  Representación exacta............ {exactos}/{n}")
    print()
    print("IMPORTANTE: sigue siendo una prueba de representabilidad, no un algoritmo de propuesta.")
    print("Las descargas BOE son temporales y no se guardan.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("La caché NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
