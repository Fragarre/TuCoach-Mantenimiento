"""
TuCoach-Mantenimiento
Auditoría IA de preguntas jurídicas de los bancos (clave correcta y artículo).

Detecta dos tipos de error:
  1. CLAVE: la respuesta marcada no es la que se deduce del artículo.
  2. ARTÍCULO: la pregunta no trata del artículo con el que está etiquetada.

Diseño para evitar falsos positivos (motivo de que se archivara el auditor de
septiembre):
  - Pasada 1 (modelo barato, ciego): resuelve sin conocer la clave, en lotes de
    preguntas que comparten el mismo texto oficial.
  - Pasada 2 (modelo más fuerte, una a una): solo para discrepancias. Debe
    aportar una CITA LITERAL del artículo. El script comprueba que la cita
    existe en el texto oficial; si no existe, el caso no se da por demostrado.
  - Solo es ERROR_CLAVE (propuesta de cuarentena) si las dos pasadas coinciden
    en la misma opción distinta de la almacenada y la cita se verifica.
  - Todo lo demás dudoso va a revisión humana (informe HTML). Nunca se escribe
    en la base: el resultado es un expediente para gestionar_cuarentena_preguntas.py.

Selección:
  --fuente tests                  todas las de esa fuente (en bancos, no excluidas)
  --muestra ia_generada=300,apoyo=150,AYTO-examen=30,modelo=20  --semilla N

Por defecto solo muestra el PLAN (recuento y coste estimado). --ejecutar llama a la IA.
Es reanudable: repetir con la misma --etiqueta continúa donde se quedó.

Ejemplos:
  python scripts\\auditar_preguntas_bancos_ia.py --fuente tests
  python scripts\\auditar_preguntas_bancos_ia.py --fuente tests --ejecutar
  python scripts\\auditar_preguntas_bancos_ia.py --muestra ia_generada=300,apoyo=150,AYTO-examen=30,modelo=20 --ejecutar
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import random
import re
import sqlite3
import sys
import threading
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
REGISTROS = RAIZ / "registros"
SALIDA = RAIZ / "auditorias"

MODELO_P1 = "gpt-5.4-nano"
MODELO_P2 = "gpt-5-mini"
RAZONAMIENTO_P2 = "low"
MAX_LOTE = 15
MAX_CHARS_TEXTO = 20000
MIN_CITA = 25
# Estimaciones de coste (USD) a partir de logs/costes_ia.csv (sep/oct 2026).
COSTE_LOTE_P1 = 0.0004
COSTE_P2 = 0.0020  # gpt-5-mini con razonamiento low (medido: 0.006 sin limitar)
FRACCION_P2 = 0.30

_bloqueo = threading.Lock()


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def limpiar(v: object | None) -> str:
    return " ".join(str(v or "").split()).strip()


def normalizar_cita(v: str) -> str:
    v = unicodedata.normalize("NFKC", str(v or "")).lower()
    v = v.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
    v = v.replace("’", "'").replace("‘", "'")
    v = re.sub(r"[\"'.,;:()\[\]–—-]", " ", v)
    return " ".join(v.split())


def cita_verificada(cita: str, texto: str) -> bool:
    c = normalizar_cita(cita)
    if len(c) < MIN_CITA:
        return False
    t = normalizar_cita(texto)
    if c in t:
        return True
    # Tolera elisiones "..." de la IA: todos los tramos deben existir en orden.
    tramos = [normalizar_cita(x) for x in re.split(r"\.\.\.|…", cita) if len(normalizar_cita(x)) >= 12]
    if len(tramos) < 2:
        return False
    pos = 0
    for tramo in tramos:
        i = t.find(tramo, pos)
        if i < 0:
            return False
        pos = i + len(tramo)
    return True


def articulo_base(valor: object | None) -> str | None:
    a = limpiar(valor).lower().replace(",", ".")
    a = re.sub(r"^(art[íi]culo|art\.?)\s*", "", a)
    m = re.match(r"^(\d+\.\d+-\d+|\d+)(?:\s*(bis|ter|qu[aá]ter|quinquies|sexies|septies))?", a)
    if not m:
        return None
    sufijo = (m.group(2) or "").replace("á", "a")
    return m.group(1) + (f" {sufijo}" if sufijo else "")


def comprobar_seguridad() -> None:
    if (os.getenv("TUCOACH_MANTENIMIENTO_SESION_ID", "").strip()
            or os.getenv("OPOCOACH_MANTENIMIENTO_SESION_ID", "").strip()):
        raise SystemExit(
            "ABORTADO: hay una sesión de mantenimiento activa; openai_api.py "
            "escribiría costes en la BD. Ejecute la auditoría fuera de esa sesión."
        )


# ----------------------------------------------------------------------------
# Datos
# ----------------------------------------------------------------------------
def abrir(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def universo(con: sqlite3.Connection) -> list[sqlite3.Row]:
    excl = ""
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='preguntas_exclusiones'").fetchone():
        excl = "AND lp.id NOT IN (SELECT pregunta_id FROM preguntas_exclusiones)"
    return con.execute(
        f"""
        SELECT lp.id, lp.enunciado, lp.opcion_a, lp.opcion_b, lp.opcion_c, lp.opcion_d,
               lp.respuesta_correcta, lp.tipo_fuente, lp.nombre_norma, lp.articulo,
               lp.norma_id_normalizada, lp.articulo_normalizado,
               (SELECT nombre_canonico FROM normas n WHERE n.id = lp.norma_id_normalizada) AS norma_canonica,
               (SELECT GROUP_CONCAT(DISTINCT c.codigo) FROM banco_preguntas b
                  JOIN convocatorias c ON c.id = b.convocatoria_id
                 WHERE b.pregunta_id = lp.id AND b.estado = 'INCLUIDA') AS convocatorias
        FROM lote_preguntas lp
        WHERE lp.tipo_clasificacion = 'JURIDICA'
          AND lp.norma_id_normalizada IS NOT NULL
          AND EXISTS (SELECT 1 FROM banco_preguntas b
                       WHERE b.pregunta_id = lp.id AND b.estado = 'INCLUIDA')
          {excl}
        ORDER BY lp.id
        """
    ).fetchall()


def indice_textos(con: sqlite3.Connection) -> dict[tuple[int, str], str]:
    """(norma_id, artículo base) -> texto oficial enlazado por el temario."""
    textos: dict[tuple[int, str], list[str]] = defaultdict(list)
    for norma_id, articulo, texto in con.execute(
        """
        SELECT tr.norma_id, tr.articulo_solicitado, af.texto
        FROM temario_referencias tr
        JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
        WHERE tr.estado = 'COMPLETADO' AND tr.norma_id IS NOT NULL
        """
    ):
        base = articulo_base(articulo)
        if base and texto and texto.strip():
            textos[(int(norma_id), base)].append(texto.strip())
    # Si una norma tiene dos fuentes (p. ej. RGPD local y DOUE) se usa la más larga.
    return {k: max(v, key=len) for k, v in textos.items()}


def seleccionar(filas, fuente: str | None, muestra: str | None, semilla: int):
    if fuente:
        return [f for f in filas if f["tipo_fuente"] == fuente]
    cupos = {}
    for parte in (muestra or "").split(","):
        if "=" in parte:
            k, v = parte.split("=", 1)
            cupos[k.strip()] = int(v)
    rnd = random.Random(semilla)
    por_fuente: dict[str, list] = defaultdict(list)
    for f in filas:
        por_fuente[f["tipo_fuente"]].append(f)
    elegidas = []
    for fuente_k, n in cupos.items():
        grupo = por_fuente.get(fuente_k, [])
        elegidas.extend(rnd.sample(grupo, min(n, len(grupo))))
    return sorted(elegidas, key=lambda f: f["id"])


# ----------------------------------------------------------------------------
# Prompts
# ----------------------------------------------------------------------------
def prompt_p1(texto: str, norma: str, articulo: str, lote) -> str:
    preguntas = "\n\n".join(
        f"ID {r['id']}\nENUNCIADO: {r['enunciado']}\nA: {r['opcion_a']}\nB: {r['opcion_b']}\n"
        f"C: {r['opcion_c']}\nD: {r['opcion_d']}"
        for r in lote
    )
    return f"""Actúa como auditor jurídico de preguntas tipo test de oposiciones.

Para CADA pregunta, usando EXCLUSIVAMENTE el TEXTO OFICIAL de {norma}, artículo {articulo}:
- "r": la opción correcta (A, B, C o D), o "?" si el texto no permite decidirlo con seguridad.
- "t": "S" si la pregunta trata realmente de lo que regula este artículo; "N" si trata de
  otra materia, otro artículo u otra norma; "?" si no está claro.

Reglas:
1. No conoces la respuesta almacenada.
2. Respeta la polaridad del enunciado (correcta/incorrecta, verdadera/falsa, NO, excepto...).
3. Examina A, B, C y D completas. Una opción solo es correcta si TODA ella es compatible
   con el texto. Fíjate en plazos, cantidades, órganos, "y"/"o", excepciones.
4. No elijas por parecido textual. Si dudas, "?".
5. Devuelve un resultado por cada ID.

Devuelve SOLO JSON: {{"resultados":[{{"id":123,"r":"A","t":"S"}}]}}

TEXTO OFICIAL ({norma}, art. {articulo}):
{texto}

PREGUNTAS:
{preguntas}"""


def prompt_p2(texto: str, norma: str, articulo: str, r) -> str:
    return f"""Eres un jurista que revisa una pregunta de examen de oposiciones.
Usa EXCLUSIVAMENTE el TEXTO OFICIAL de {norma}, artículo {articulo}. No uses conocimiento externo.

Responde en JSON con estas claves:
- "respuesta": opción correcta según el texto (A, B, C o D), o "?" si no puede determinarse con
  seguridad solo con este texto.
- "trata_del_articulo": "S" si la pregunta versa sobre lo que regula este artículo, "N" si no.
- "cita": copia LITERAL (sin parafrasear, mínimo una frase completa) del fragmento del texto
  oficial que demuestra la respuesta. Cadena vacía si "respuesta" es "?".
- "motivo": una o dos frases explicando por qué esa opción es la correcta y las demás no.

Respeta la polaridad del enunciado (correcta/incorrecta, NO, excepto...).
Una opción solo es correcta si TODA ella es compatible con el texto.

PREGUNTA (ID {r['id']}):
{r['enunciado']}
A: {r['opcion_a']}
B: {r['opcion_b']}
C: {r['opcion_c']}
D: {r['opcion_d']}

TEXTO OFICIAL ({norma}, art. {articulo}):
{texto}"""


# ----------------------------------------------------------------------------
# Checkpoint
# ----------------------------------------------------------------------------
def cargar_checkpoint(ruta: Path) -> dict[tuple[str, int], dict]:
    datos: dict[tuple[str, int], dict] = {}
    if ruta.is_file():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(linea)
                datos[(d["fase"], int(d["id"]))] = d
            except Exception:
                continue
    return datos


def guardar(ruta: Path, dato: dict) -> None:
    with _bloqueo:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dato, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


# ----------------------------------------------------------------------------
# Ejecución
# ----------------------------------------------------------------------------
def pasada_1(grupos, ckpt, ruta_ckpt, hilos):
    from openai_api import seleccionar_fragmento_json

    trabajos = []
    for (norma, art, texto), filas in grupos.items():
        pend = [r for r in filas if ("P1", r["id"]) not in ckpt]
        for i in range(0, len(pend), MAX_LOTE):
            trabajos.append((norma, art, texto, pend[i:i + MAX_LOTE]))
    print(f"\nPASADA 1: {len(trabajos)} lotes pendientes")

    def uno(t):
        norma, art, texto, lote = t
        esperados = {r["id"] for r in lote}
        try:
            res = seleccionar_fragmento_json(
                prompt=prompt_p1(texto, norma, art, lote), modelo=MODELO_P1,
                operacion="auditoria_bancos_p1", max_output_tokens=4000,
            )
        except Exception as exc:  # se reintentará en otra ejecución
            print(f"  ERROR lote: {exc}")
            return 0
        n = 0
        for item in res.get("resultados", []):
            try:
                qid = int(item["id"])
            except Exception:
                continue
            if qid not in esperados:
                continue
            r = str(item.get("r", "?")).strip().upper()[:1]
            tr = str(item.get("t", "?")).strip().upper()[:1]
            dato = {"fase": "P1", "id": qid, "r": r if r in "ABCD?" else "?",
                    "t": tr if tr in "SN?" else "?"}
            guardar(ruta_ckpt, dato)
            ckpt[("P1", qid)] = dato
            n += 1
        return n

    hechos = 0
    with ThreadPoolExecutor(max_workers=hilos) as ex:
        for i, fut in enumerate(as_completed([ex.submit(uno, t) for t in trabajos]), 1):
            hechos += fut.result()
            if i % 25 == 0:
                print(f"  P1 {i}/{len(trabajos)} lotes · {hechos} preguntas")


def necesita_p2(r, ckpt) -> bool:
    p1 = ckpt.get(("P1", r["id"]))
    if not p1:
        return False
    return p1["r"] != r["respuesta_correcta"].strip().upper() or p1["t"] != "S"


def pasada_2(seleccion, textos, ckpt, ruta_ckpt, hilos):
    from openai_api import seleccionar_fragmento_json

    pend = [r for r in seleccion if necesita_p2(r, ckpt) and ("P2", r["id"]) not in ckpt]
    print(f"\nPASADA 2: {len(pend)} preguntas")

    def uno(r):
        norma, art, texto = textos[r["id"]]
        # gpt-5-mini razona antes de responder y ese razonamiento consume
        # max_output_tokens: margen amplio y un reintento si aun así se trunca.
        res = None
        for limite in (8000, 16000):
            try:
                extra = {"razonamiento": RAZONAMIENTO_P2} if RAZONAMIENTO_P2 else {}
                res = seleccionar_fragmento_json(
                    prompt=prompt_p2(texto, norma, art, r), modelo=MODELO_P2,
                    operacion="auditoria_bancos_p2", max_output_tokens=limite,
                    **extra,
                )
                break
            except RuntimeError as exc:
                if "TRUNCADA" in str(exc) and limite == 8000:
                    continue
                print(f"  ERROR {r['id']}: {exc}")
                return
            except Exception as exc:
                print(f"  ERROR {r['id']}: {exc}")
                return
        if res is None:
            return
        resp = str(res.get("respuesta", "?")).strip().upper()[:1]
        trata = str(res.get("trata_del_articulo", "?")).strip().upper()[:1]
        cita = str(res.get("cita", "") or "")
        dato = {"fase": "P2", "id": r["id"], "r": resp if resp in "ABCD?" else "?",
                "t": trata if trata in "SN?" else "?", "cita": cita,
                "cita_ok": cita_verificada(cita, texto), "motivo": str(res.get("motivo", ""))}
        guardar(ruta_ckpt, dato)
        ckpt[("P2", r["id"])] = dato

    with ThreadPoolExecutor(max_workers=hilos) as ex:
        for i, _ in enumerate(as_completed([ex.submit(uno, r) for r in pend]), 1):
            if i % 50 == 0:
                print(f"  P2 {i}/{len(pend)}")


def veredicto(r, ckpt) -> tuple[str, str]:
    clave = r["respuesta_correcta"].strip().upper()
    p1 = ckpt.get(("P1", r["id"]))
    p2 = ckpt.get(("P2", r["id"]))
    if not p1:
        return "SIN_AUDITAR", ""
    if not necesita_p2(r, ckpt):
        return "OK", ""
    if not p2:
        return "SIN_AUDITAR", "pendiente de pasada 2"
    if p2["t"] == "N":
        return "REVISAR_ARTICULO", "la pregunta no parece tratar de este artículo"
    if p2["r"] == clave:
        return "OK_P2", ""
    if p2["r"] in "ABCD" and p2["cita_ok"] and p1["r"] == p2["r"]:
        return "ERROR_CLAVE", f"ambas pasadas: {p2['r']} (almacenada {clave}); cita verificada"
    if p2["r"] in "ABCD" and p2["cita_ok"]:
        return "REVISAR_CLAVE", f"pasada 2: {p2['r']} con cita verificada; pasada 1: {p1['r']}"
    if p2["r"] in "ABCD":
        return "REVISAR_CLAVE", f"pasada 2: {p2['r']} pero la cita NO aparece en el texto"
    return "INDETERMINADA", "el texto del artículo no permite decidir"


# ----------------------------------------------------------------------------
# Informes
# ----------------------------------------------------------------------------
def informes(etiqueta, seleccion, textos, ckpt, sin_texto):
    SALIDA.mkdir(parents=True, exist_ok=True)
    base = SALIDA / f"auditoria_preguntas_{etiqueta}"
    filas = []
    for r in seleccion:
        if r["id"] in sin_texto:
            v, m = "SIN_TEXTO", "no hay texto oficial enlazado para su norma/artículo"
        else:
            v, m = veredicto(r, ckpt)
        p1 = ckpt.get(("P1", r["id"]), {})
        p2 = ckpt.get(("P2", r["id"]), {})
        filas.append({
            "id": r["id"], "veredicto": v, "detalle": m, "fuente": r["tipo_fuente"],
            "convocatorias": r["convocatorias"], "norma": r["norma_canonica"] or r["nombre_norma"],
            "articulo": r["articulo_normalizado"] or r["articulo"],
            "almacenada": r["respuesta_correcta"], "p1": p1.get("r", ""), "p1_trata": p1.get("t", ""),
            "p2": p2.get("r", ""), "p2_trata": p2.get("t", ""), "cita_verificada": p2.get("cita_ok", ""),
            "cita": p2.get("cita", ""), "motivo": p2.get("motivo", ""), "enunciado": r["enunciado"],
            "A": r["opcion_a"], "B": r["opcion_b"], "C": r["opcion_c"], "D": r["opcion_d"],
        })
    with (base.with_suffix(".csv")).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(filas)

    cont = Counter(f["veredicto"] for f in filas)
    revisar = [f for f in filas if f["veredicto"] not in {"OK", "OK_P2"}]
    orden = {"ERROR_CLAVE": 0, "REVISAR_CLAVE": 1, "REVISAR_ARTICULO": 2, "INDETERMINADA": 3,
             "SIN_TEXTO": 4, "SIN_AUDITAR": 5}
    revisar.sort(key=lambda f: (orden.get(f["veredicto"], 9), f["id"]))
    e = html.escape
    tarjetas = []
    for f in revisar:
        ops = "".join(
            f'<li class="{"alm" if k == f["almacenada"] else ""} {"ia" if k == f["p2"] else ""}">'
            f"<b>{k}</b> {e(str(f[k]))}</li>" for k in "ABCD"
        )
        tarjetas.append(
            f'<div class="c {f["veredicto"]}"><div class="h"><span class="v">{f["veredicto"]}</span> '
            f'#{f["id"]} · {e(str(f["norma"]))} · art. {e(str(f["articulo"]))} · {e(f["fuente"])} · '
            f'{e(str(f["convocatorias"] or ""))}</div><p class="q">{e(f["enunciado"])}</p><ul>{ops}</ul>'
            f'<p class="m">Almacenada <b>{f["almacenada"]}</b> · P1 {f["p1"]}/{f["p1_trata"]} · '
            f'P2 {f["p2"]}/{f["p2_trata"]} · {e(f["detalle"])}</p>'
            + (f'<blockquote>{e(f["cita"])}</blockquote>' if f["cita"] else "")
            + (f'<p class="m">{e(f["motivo"])}</p>' if f["motivo"] else "")
            + "</div>"
        )
    resumen = " · ".join(f"{k}: {v}" for k, v in sorted(cont.items()))
    (base.with_suffix(".html")).write_text(f"""<!doctype html><meta charset="utf-8">
<title>Auditoría de preguntas · {e(etiqueta)}</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1f2328}}
h1{{font-size:20px}} .c{{border:1px solid #d9dee5;border-left:5px solid #999;border-radius:6px;padding:10px 14px;margin:12px 0}}
.ERROR_CLAVE{{border-left-color:#c62828}} .REVISAR_CLAVE{{border-left-color:#ef6c00}} .REVISAR_ARTICULO{{border-left-color:#6a1b9a}}
.INDETERMINADA,.SIN_TEXTO,.SIN_AUDITAR{{border-left-color:#78909c}} .h{{font-size:12px;color:#555}} .v{{font-weight:700;color:#14325c}}
.q{{font-weight:600}} ul{{list-style:none;padding-left:0}} li{{padding:2px 6px;border-radius:4px}}
li.alm{{background:#fdecea}} li.ia{{background:#e8f5e9}} li.alm.ia{{background:#e3f2fd}}
blockquote{{margin:6px 0;padding:6px 10px;background:#f6f8fa;border-left:3px solid #c8963e;font-style:italic}}
.m{{font-size:12.5px;color:#444}}</style>
<h1>Auditoría de preguntas · {e(etiqueta)}</h1>
<p>{len(filas)} preguntas auditadas. {e(resumen)}</p>
<p>Rojo claro = respuesta almacenada · verde = respuesta del auditor (pasada 2) · azul = coinciden.</p>
{''.join(tarjetas)}""", encoding="utf-8")

    errores = [f for f in filas if f["veredicto"] == "ERROR_CLAVE"]
    decisiones = {
        "decision_usuario": False,
        "nota": "Revise el informe HTML. Ponga decision_usuario a true y elimine los casos que no comparta.",
        "origen_auditoria": f"auditar_preguntas_bancos_ia.py {etiqueta}",
        "casos": [{
            "pregunta_id": f["id"], "clasificacion": "ERROR_DEMOSTRADO",
            "respuesta_almacenada": f["almacenada"], "respuesta_demostrada": f["p2"],
            "motivo": f"Clave incorrecta según {f['norma']} art. {f['articulo']}: {f['motivo']}"[:900],
        } for f in errores],
    }
    (base.parent / f"{base.name}_decisiones.json").write_text(
        json.dumps(decisiones, ensure_ascii=False, indent=2), encoding="utf-8")
    (base.parent / f"{base.name}_expediente.json").write_text(
        json.dumps({"etiqueta": etiqueta, "casos": [
            {k: f[k] for k in ("id", "norma", "articulo", "almacenada", "p1", "p2", "cita", "motivo", "enunciado")}
            for f in errores]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return cont, base


# ----------------------------------------------------------------------------
def main() -> int:
    global MODELO_P2, RAZONAMIENTO_P2
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fuente", help="tipo_fuente a auditar completo (p. ej. tests)")
    g.add_argument("--muestra", help="cupos por fuente: ia_generada=300,apoyo=150,...")
    ap.add_argument("--semilla", type=int, default=20261014)
    ap.add_argument("--etiqueta", help="nombre del expediente (por defecto, según la selección)")
    ap.add_argument("--hilos", type=int, default=4)
    ap.add_argument("--modelo-p2", default=MODELO_P2,
                    help="modelo de la pasada 2 (gpt-5-mini, gpt-5.4-nano, gpt-5.4)")
    ap.add_argument("--razonamiento-p2", default=RAZONAMIENTO_P2,
                    help="minimal/low/medium/high, o 'no' para no enviarlo (modelos sin razonamiento)")
    ap.add_argument("--ejecutar", action="store_true", help="llama a la IA (sin esto, solo plan)")
    ap.add_argument("--solo-informe", action="store_true", help="regenera informes desde el checkpoint")
    args = ap.parse_args()

    MODELO_P2 = args.modelo_p2
    RAZONAMIENTO_P2 = None if args.razonamiento_p2.lower() in {"no", "none", ""} else args.razonamiento_p2
    etiqueta = args.etiqueta or (f"fuente_{args.fuente}" if args.fuente else f"muestra_{args.semilla}")
    ruta_ckpt = REGISTROS / f"auditoria_preguntas_{etiqueta}.jsonl"

    with abrir(Path(args.db)) as con:
        filas = universo(con)
        idx = indice_textos(con)
    seleccion = seleccionar(filas, args.fuente, args.muestra, args.semilla)

    textos: dict[int, tuple[str, str, str]] = {}
    sin_texto: set[int] = set()
    grupos: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in seleccion:
        base = articulo_base(r["articulo_normalizado"] or r["articulo"])
        texto = idx.get((int(r["norma_id_normalizada"]), base)) if base else None
        if not texto or len(texto) > MAX_CHARS_TEXTO:
            sin_texto.add(r["id"])
            continue
        norma = r["norma_canonica"] or r["nombre_norma"] or ""
        textos[r["id"]] = (norma, base, texto)
        grupos[(norma, base, texto)].append(r)

    ckpt = cargar_checkpoint(ruta_ckpt)
    lotes = sum((len(v) + MAX_LOTE - 1) // MAX_LOTE for v in grupos.values())
    auditables = len(seleccion) - len(sin_texto)
    print("=" * 78)
    print("AUDITORÍA IA DE PREGUNTAS DE BANCOS — " + ("EJECUCIÓN" if args.ejecutar else "PLAN"))
    print("=" * 78)
    print(f"Selección................ {args.fuente and 'fuente ' + args.fuente or 'muestra ' + args.muestra}")
    print(f"Por fuente............... {dict(Counter(r['tipo_fuente'] for r in seleccion))}")
    print(f"Preguntas................ {len(seleccion)}")
    print(f"  con texto oficial...... {auditables}")
    print(f"  sin texto (no audit.).. {len(sin_texto)}")
    print(f"Ya auditadas (P1)........ {sum(1 for r in seleccion if ('P1', r['id']) in ckpt)}")
    print(f"Lotes pasada 1........... {lotes}  ({MODELO_P1}, {MAX_LOTE} preguntas/lote)")
    estimado = lotes * COSTE_LOTE_P1 + auditables * FRACCION_P2 * COSTE_P2
    print(f"Coste estimado........... {estimado:.2f} USD (P2 sobre ~{int(FRACCION_P2*100)} % con {MODELO_P2}, razonamiento {RAZONAMIENTO_P2 or 'no'})")
    print(f"Checkpoint............... {ruta_ckpt}")

    if args.ejecutar:
        comprobar_seguridad()
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        pasada_1(grupos, ckpt, ruta_ckpt, args.hilos)
        pasada_2([r for r in seleccion if r["id"] in textos], textos, ckpt, ruta_ckpt, args.hilos)
    elif not args.solo_informe:
        print("\nPLAN: no se ha llamado a la IA. Añada --ejecutar para auditar.")
        return 0

    cont, base = informes(etiqueta, seleccion, textos, ckpt, sin_texto)
    print("\nRESULTADO")
    for k in ("OK", "OK_P2", "ERROR_CLAVE", "REVISAR_CLAVE", "REVISAR_ARTICULO",
              "INDETERMINADA", "SIN_TEXTO", "SIN_AUDITAR"):
        if cont.get(k):
            print(f"  {k:<18} {cont[k]:>6}  ({cont[k] / max(1, len(seleccion)):.1%})")
    print(f"\nInforme para revisar: {base.with_suffix('.html')}")
    print(f"Tabla completa:       {base.with_suffix('.csv')}")
    print(f"Propuesta cuarentena: {base.parent / (base.name + '_decisiones.json')} (decision_usuario=false)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
