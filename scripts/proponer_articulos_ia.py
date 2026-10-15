"""Propone, con ayuda de IA, el artículo correcto de preguntas mal etiquetadas.

Toma las preguntas con veredicto REVISAR_ARTICULO de los informes
auditorias/auditoria_preguntas_*.csv que todavía no se han reetiquetado ni
están en cuarentena. Para cada una:

1. Busca por coincidencia textual los 4 artículos candidatos de la MISMA norma
   (más el artículo actual).
2. Pide a la IA (gpt-5-mini, razonamiento low) que elija el artículo que
   trata la pregunta, con una cita literal de ese artículo.
3. Solo propone el cambio si la cita aparece literalmente en el artículo
   elegido y es distinto del actual.

Uso:
    python scripts/proponer_articulos_ia.py              # plan, sin IA
    python scripts/proponer_articulos_ia.py --ejecutar   # llama a la IA

Salida: auditorias/reetiquetado_articulos_ia_<fecha>.json (formato de
reetiquetar_articulos_auditoria.py, decision_usuario=false) y .csv para revisar.
No modifica la base de datos. Checkpoint en registros/proponer_articulos_ia.jsonl.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
AUDITORIAS = RAIZ / "auditorias"
REGISTROS = RAIZ / "registros"
CKPT = REGISTROS / "proponer_articulos_ia.jsonl"
MODELO = "gpt-5-mini"
RAZONAMIENTO = "low"
MAX_CHARS_ART = 3500
N_CAND = 4
STOP = set(("de la el los las en que por para con del una uno sus su se al o y a lo como "
            "sera seran podra podran esta este ser sin entre sobre cual cuales siguientes "
            "respuesta correcta incorrecta senale indique ley articulo segun dispuesto "
            "establecido anteriores todas ninguna son").split())
_lock = threading.Lock()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auditar_preguntas_bancos_ia import cita_verificada  # noqa: E402


def norm_tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "").lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return [t for t in re.findall(r"[a-z0-9]+", s) if len(t) >= 4 and t not in STOP]


def base(valor) -> str:
    t = unicodedata.normalize("NFKD", str(valor or "")).lower()
    t = "".join(c for c in t if not unicodedata.combining(c)).strip()
    t = re.sub(r"^(articulo|art)\.?\s*", "", t)
    m = re.match(r"^(\d+)(?:[\s.\-]*(bis|ter|quater|quinquies))?", t)
    return (m.group(1) + (f" {m.group(2)}" if m.group(2) else "")) if m else t


def ids_pendientes(con: sqlite3.Connection) -> list[int]:
    ids: set[int] = set()
    for f in AUDITORIAS.glob("auditoria_preguntas_*.csv"):
        with f.open(encoding="utf-8-sig") as fh:
            for x in csv.DictReader(fh, delimiter=";"):
                if x.get("veredicto") == "REVISAR_ARTICULO":
                    ids.add(int(x["id"]))
    hechos: set[int] = set()
    reg = REGISTROS / "reetiquetado_articulos_auditoria.csv"
    if reg.is_file():
        with reg.open(encoding="utf-8-sig") as fh:
            hechos = {int(x["pregunta_id"]) for x in csv.DictReader(fh, delimiter=";")}
    excl = {int(r[0]) for r in con.execute(
        "SELECT pregunta_id FROM preguntas_exclusiones WHERE estado IN ('CUARENTENA','RETIRADA')")}
    return sorted(ids - hechos - excl)


_corpus: dict[int, dict[str, str]] = {}


def articulos(con, nid: int) -> dict[str, str]:
    if nid not in _corpus:
        d: dict[str, str] = {}
        for (f,) in con.execute("SELECT id_fuente FROM norma_fuentes WHERE norma_id=?", (nid,)):
            for a, t in con.execute(
                    "SELECT articulo_boe, texto FROM articulos_fuente WHERE UPPER(id_boe)=UPPER(?)", (f,)):
                if a and t and a not in d:
                    d[a] = t
        _corpus[nid] = d
    return _corpus[nid]


def candidatos(con, p) -> list[str]:
    arts = articulos(con, p["norma_id_normalizada"])
    if not arts:
        return []
    sets = {a: set(norm_tokens(t)) for a, t in arts.items()}
    df = Counter()
    for s in sets.values():
        df.update(s)
    n = len(sets)
    idf = lambda t: math.log((n + 1) / (df.get(t, 0) + 1))  # noqa: E731
    qt = set(norm_tokens(" ".join([p["enunciado"], p["opcion_a"], p["opcion_b"],
                                   p["opcion_c"], p["opcion_d"]])))
    w = sum(idf(t) for t in qt) or 1
    orden = sorted(arts, key=lambda a: -sum(idf(t) for t in qt if t in sets[a]) / w)
    cand = orden[:N_CAND]
    actual = next((a for a in arts if base(a) == base(p["articulo_normalizado"])), None)
    if actual and actual not in cand:
        cand.append(actual)
    return cand


def prompt(p, norma: str, cand: list[str], textos: dict[str, str]) -> str:
    bloques = "\n\n".join(
        f"=== ARTÍCULO {a} ===\n{textos[a][:MAX_CHARS_ART]}" for a in cand)
    return f"""Eres un jurista que clasifica preguntas de examen de oposiciones por artículo.
Norma: {norma}. Abajo tienes varios artículos candidatos de esa norma (texto oficial).
Elige el ÚNICO artículo cuyo contenido trata la pregunta (el que sirve para resolverla).
Usa exclusivamente estos textos. Si ninguno la trata, responde "NINGUNO".

PREGUNTA: {p['enunciado']}
A) {p['opcion_a']}
B) {p['opcion_b']}
C) {p['opcion_c']}
D) {p['opcion_d']}

{bloques}

Responde SOLO con JSON:
{{"articulo": "<número tal como aparece tras '=== ARTÍCULO' o NINGUNO>",
  "cita": "<fragmento LITERAL de al menos 40 caracteres copiado del artículo elegido que demuestre que trata la pregunta>",
  "motivo": "<una frase>"}}"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ejecutar", action="store_true")
    ap.add_argument("--hilos", type=int, default=6)
    args = ap.parse_args()

    con = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    ids = ids_pendientes(con)
    preguntas = {r["id"]: dict(r) for r in con.execute(
        f"SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d, "
        f"norma_id_normalizada, articulo_normalizado, nombre_norma_normalizado "
        f"FROM lote_preguntas WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
    hecho: dict[int, dict] = {}
    if CKPT.is_file():
        for linea in CKPT.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(linea)
                hecho[int(d["id"])] = d
            except Exception:
                continue
    pend = [i for i in ids if i in preguntas and i not in hecho]
    print("=" * 78)
    print("PROPUESTA DE ARTÍCULOS CON IA — " + ("EJECUCIÓN" if args.ejecutar else "PLAN"))
    print("=" * 78)
    print(f"Preguntas REVISAR_ARTICULO pendientes.. {len(ids)}")
    print(f"Ya procesadas (checkpoint)............. {len(hecho)}")
    print(f"Por procesar........................... {len(pend)}")
    print(f"Coste estimado......................... {len(pend) * 0.004:.2f} USD ({MODELO}, {RAZONAMIENTO})")
    if not args.ejecutar:
        print("\nPLAN: no se ha llamado a la IA. Añada --ejecutar.")
        return 0

    from openai_api import seleccionar_fragmento_json

    # Lectura de la BD solo en el hilo principal (sqlite no es seguro entre hilos).
    previos = {i: (candidatos(con, preguntas[i]),
                   articulos(con, preguntas[i]["norma_id_normalizada"])) for i in pend}

    def uno(i: int):
        p = preguntas[i]
        cand, textos = previos[i]
        if not cand:
            return {"id": i, "estado": "SIN_CORPUS"}
        res = None
        for limite in (8000, 16000):
            try:
                res = seleccionar_fragmento_json(
                    prompt=prompt(p, p["nombre_norma_normalizado"] or "", cand, textos),
                    modelo=MODELO, operacion="proponer_articulo_ia",
                    max_output_tokens=limite, razonamiento=RAZONAMIENTO)
                break
            except RuntimeError as exc:
                if "TRUNCADA" in str(exc) and limite == 8000:
                    continue
                return {"id": i, "estado": "ERROR", "error": str(exc)[:200]}
            except Exception as exc:  # noqa: BLE001
                return {"id": i, "estado": "ERROR", "error": str(exc)[:200]}
        elegido = str(res.get("articulo", "")).strip()
        cita = str(res.get("cita", "") or "")
        art = next((a for a in cand if base(a) == base(elegido)), None)
        ok = bool(art) and cita_verificada(cita, textos.get(art, ""))
        return {"id": i, "estado": "OK", "candidatos": cand, "elegido": art or elegido,
                "cita": cita, "cita_ok": ok, "motivo": str(res.get("motivo", ""))[:300]}

    REGISTROS.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.hilos) as ex:
        futs = [ex.submit(uno, i) for i in pend]
        for n, fut in enumerate(as_completed(futs), 1):
            d = fut.result()
            with _lock:
                with CKPT.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                hecho[d["id"]] = d
            if n % 20 == 0:
                print(f"  {n}/{len(pend)}")

    casos, filas, cuenta = [], [], Counter()
    for i in ids:
        d = hecho.get(i)
        p = preguntas.get(i)
        if not d or not p:
            continue
        if d.get("estado") != "OK":
            cuenta[d.get("estado", "?")] += 1
            continue
        actual = p["articulo_normalizado"]
        if not d["cita_ok"]:
            cat = "SIN_CITA_VALIDA"
        elif base(d["elegido"]) == base(actual):
            cat = "ACTUAL_CORRECTO"
        else:
            cat = "PROPUESTA"
            casos.append({"pregunta_id": i, "norma_id": p["norma_id_normalizada"],
                          "articulo_anterior": actual, "articulo_nuevo": d["elegido"],
                          "motivo": "IA con cita literal: " + d["motivo"][:200]})
        cuenta[cat] += 1
        filas.append([i, p["nombre_norma_normalizado"], actual, d["elegido"], cat,
                      d["cita"][:200], d["motivo"][:200], p["enunciado"][:200]])
    marca = datetime.now().strftime("%Y%m%d")
    salida = AUDITORIAS / f"reetiquetado_articulos_ia_{marca}.json"
    salida.write_text(json.dumps({"origen_auditoria": "proponer_articulos_ia",
                                  "decision_usuario": False, "casos": casos},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    with salida.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["id", "norma", "articulo_actual", "articulo_ia", "resultado",
                    "cita", "motivo", "enunciado"])
        w.writerows(filas)
    print("\nRESULTADO")
    for k, v in cuenta.most_common():
        print(f"  {k:<20} {v}")
    print(f"\nPropuestas: {salida}  (decision_usuario=false)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
