from __future__ import annotations

import argparse
import csv
import html
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
INFORMES = ROOT / "informes"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from localizador_normativa import obtener_indice, resolver_alcance  # noqa: E402
from openai_api import seleccionar_fragmento_json  # noqa: E402

COLUMNAS = ("parte", "tema", "titulo", "LEY", "articulo", "tipo")
MODELO = "gpt-5.4"


@dataclass
class Hallazgo:
    parte: str
    tema: str
    norma_pdf: str
    norma_csv: str
    accion: str
    articulos: list[str]
    motivo: str
    alcance: str = ""
    fuente: str = ""


def norm(s: Any) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.casefold()).strip(" .,:;-")


def parte(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).upper()


def tema(s: Any) -> str:
    m = re.search(r"\d+", str(s or ""))
    return str(int(m.group())) if m else str(s or "").strip()


def art(s: Any) -> str:
    s = norm(s)
    s = re.sub(r"^(articulo|art\.?)\s+", "", s).replace(",", ".")
    return s.strip(" .ºª")


def clave_articulo(s: Any) -> tuple:
    t = art(s)
    nums = tuple(int(x) for x in re.findall(r"\d+", t))
    suf = {"bis": 1, "ter": 2, "quater": 3, "quinquies": 4, "sexies": 5, "septies": 6}
    return (*nums, next((v for k, v in suf.items() if re.search(rf"\b{k}\b", t)), 0), t)


def compactar(vals: list[str]) -> str:
    vals = sorted({art(x) for x in vals if art(x)}, key=clave_articulo)
    return ", ".join(vals) if vals else "—"


def identidad_norma(s: str) -> tuple[str, str, str] | None:
    t = norm(s)
    pats = (
        ("real decreto legislativo", r"real decreto legislativo"),
        ("real decreto ley", r"real decreto[\s-]+ley"),
        ("decreto legislativo", r"decreto legislativo"),
        ("decreto ley", r"decreto[\s-]+ley"),
        ("ley organica", r"ley organica"),
        ("real decreto", r"real decreto"),
        ("ley", r"ley"),
        ("decreto", r"decreto"),
        ("orden", r"orden"),
    )
    for nombre, pat in pats:
        m = re.search(rf"\b{pat}\s+(\d+)\s*/\s*(\d{{4}})\b", t)
        if m:
            return nombre, str(int(m.group(1))), m.group(2)
    return None


def similitud(a: str, b: str) -> float:
    ia, ib = identidad_norma(a), identidad_norma(b)
    if ia and ib:
        return 1.0 if ia == ib else 0.0
    aa = {x for x in re.findall(r"[a-z0-9]+", norm(a)) if len(x) > 2}
    bb = {x for x in re.findall(r"[a-z0-9]+", norm(b)) if len(x) > 2}
    return len(aa & bb) / len(aa | bb) if aa and bb else 0.0


def leer_pdf(path: Path) -> str:
    with fitz.open(path) as doc:
        texto = "\n".join(f"--- PÁGINA {i + 1} ---\n{p.get_text('text') or ''}" for i, p in enumerate(doc))
    if len(texto.strip()) < 200:
        raise RuntimeError("El PDF no contiene texto extraíble suficiente.")
    return texto


def leer_csv(path: Path):
    raw = path.read_bytes()
    texto = encoding = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            texto, encoding = raw.decode(enc), enc
            break
        except UnicodeDecodeError:
            pass
    if texto is None:
        raise RuntimeError("No se pudo detectar la codificación del CSV.")
    try:
        d = csv.Sniffer().sniff(texto[:8192], delimiters=",;|\t")
        delim, quote = d.delimiter, d.quotechar or '"'
    except csv.Error:
        delim, quote = ";", '"'
    reader = csv.DictReader(texto.splitlines(), delimiter=delim, quotechar=quote)
    campos = list(reader.fieldnames or [])
    mapa = {norm(x): x for x in campos}
    faltan = [c for c in COLUMNAS if norm(c) not in mapa]
    if faltan:
        raise RuntimeError("Faltan columnas requeridas: " + ", ".join(faltan))
    real = {c: mapa[norm(c)] for c in COLUMNAS}
    filas = []
    for r in reader:
        x = dict(r)
        for c in COLUMNAS:
            x[c] = str(r.get(real[c], "") or "")
        filas.append(x)
    return filas, campos, real, encoding, delim, quote, "\r\n" if "\r\n" in texto else "\n"


def agrupar(filas):
    grupos = {}
    for f in filas:
        k = (parte(f["parte"]), tema(f["tema"]))
        g = grupos.setdefault(k, {"titulo": f["titulo"], "normas": defaultdict(list), "filas": []})
        g["filas"].append(f)
        if f["LEY"].strip():
            g["normas"][f["LEY"].strip()].append(f)
    return grupos


def resumen(filas) -> str:
    out = []
    for (p, t), g in sorted(agrupar(filas).items(), key=lambda x: (x[0][0], int(x[0][1]) if x[0][1].isdigit() else 9999)):
        out.append(f"{p} {t} | {g['titulo']}")
        for ley, rs in g["normas"].items():
            out.append(f"  - {ley}: {compactar([r['articulo'] for r in rs])}")
        if not g["normas"]:
            out.append("  - SIN REFERENCIAS JURÍDICAS")
    return "\n".join(out)


def extraer_ia(pdf: str, filas, modelo: str):
    prompt = f"""Actúas como auditor de fidelidad de un temario oficial de oposición.
Extrae del PDF, tema por tema, SOLO lo que el PDF permite afirmar.

REGLAS:
- No uses memoria jurídica externa.
- EXPLICITA: el PDF identifica inequívocamente la norma y un alcance verificable
  (norma completa, artículos, título, capítulo, sección, disposición...).
- IMPLICITA: la materia exige interpretar qué norma o artículos corresponden.
- Divide alcances complejos en piezas simples.
- Si se cita la norma completa, usa alcances=[].
- Conserva los temas no jurídicos con juridico=false.
- Ante duda, IMPLICITA.

CSV ACTUAL (solo contexto para reconocer denominaciones):
{resumen(filas)}

PDF:
{pdf}

Devuelve SOLO JSON:
{{"temas":[{{"parte":"GENERAL","tema":"1","texto_literal":"...",
"juridico":true,"referencias":[{{"norma":"...","base":"EXPLICITA|IMPLICITA",
"alcances":["Título I"],"motivo":"..."}}]}}]}}"""
    r = seleccionar_fragmento_json(
        prompt=prompt,
        modelo=modelo,
        operacion="auditar_fidelidad_temario_extraer",
        max_output_tokens=16000,
    )
    temas = r.get("temas")
    if not isinstance(temas, list) or not temas:
        raise RuntimeError("La IA no devolvió temas utilizables.")
    return temas


def encontrar_norma(nombre: str, opciones: list[str]) -> str | None:
    if not opciones:
        return None
    scores = sorted(((similitud(nombre, x), x) for x in opciones), reverse=True)
    if scores[0][0] >= 0.72 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.12):
        return scores[0][1]
    return None


def resolver_oficial(norma: str, alcances: list[str]):
    try:
        localizada, indice = obtener_indice(norma)
        if not alcances:
            arts = resolver_alcance(indice, "")
        else:
            arts = []
            for a in alcances:
                arts.extend(resolver_alcance(indice, a))
        arts = sorted({art(x) for x in arts}, key=clave_articulo)
        return arts, localizada.url_indice, ""
    except Exception as exc:
        return None, "", f"No pudo resolverse con seguridad contra el índice oficial: {exc}"


def auditar(temas_ia, filas):
    grupos = agrupar(filas)
    confirmados, dudas, ok = [], [], []
    vistos = set()
    for ti in temas_ia:
        p, t = parte(ti.get("parte")), tema(ti.get("tema"))
        k = (p, t)
        vistos.add(k)
        g = grupos.get(k)
        if g is None:
            dudas.append(Hallazgo(p, t, "", "", "DUDA", [], "El tema del PDF no existe en el CSV."))
            continue
        if not bool(ti.get("juridico", True)):
            continue
        opciones = list(g["normas"])
        referenciadas = set()
        hubo = False
        for ref in ti.get("referencias") or []:
            npdf = str(ref.get("norma") or "").strip()
            base = str(ref.get("base") or "IMPLICITA").upper()
            alc = [str(x).strip() for x in ref.get("alcances") or [] if str(x).strip()]
            motivo = str(ref.get("motivo") or "").strip()
            if not npdf:
                dudas.append(Hallazgo(p, t, "", "", "DUDA", [], motivo or "Referencia no identificable.", "; ".join(alc)))
                continue
            ncsv = encontrar_norma(npdf, opciones)
            if ncsv:
                referenciadas.add(ncsv)
            if base != "EXPLICITA":
                dudas.append(Hallazgo(p, t, npdf, ncsv or "", "DUDA", [], motivo or "Requiere interpretación.", "; ".join(alc)))
                continue
            esperados, fuente, err = resolver_oficial(npdf, alc)
            if esperados is None:
                dudas.append(Hallazgo(p, t, npdf, ncsv or "", "DUDA", [], err, "; ".join(alc), fuente))
                continue
            if ncsv is None:
                confirmados.append(Hallazgo(
                    p, t, npdf, "", "AÑADIR", esperados,
                    "Norma y alcance explícitos en PDF; no existe norma equivalente en CSV.",
                    "; ".join(alc) or "Norma completa", fuente,
                ))
                hubo = True
                continue
            actuales = {art(r["articulo"]) for r in g["normas"][ncsv] if art(r["articulo"])}
            faltan = sorted(set(esperados) - actuales, key=clave_articulo)
            sobran = sorted(actuales - set(esperados), key=clave_articulo)
            if faltan:
                confirmados.append(Hallazgo(
                    p, t, npdf, ncsv, "AÑADIR", faltan,
                    "Artículos exigidos por alcance explícito del PDF y ausentes del CSV.",
                    "; ".join(alc) or "Norma completa", fuente,
                ))
                hubo = True
            if sobran:
                confirmados.append(Hallazgo(
                    p, t, npdf, ncsv, "ELIMINAR", sobran,
                    "Artículos del CSV fuera del alcance explícito resuelto oficialmente.",
                    "; ".join(alc) or "Norma completa", fuente,
                ))
                hubo = True
        for ncsv in opciones:
            if ncsv not in referenciadas:
                dudas.append(Hallazgo(
                    p, t, "", ncsv, "DUDA", [],
                    "La norma del CSV no quedó vinculada a una referencia extraída del PDF; se mantiene.",
                ))
        if not hubo and not any(x.parte == p and x.tema == t for x in dudas):
            ok.append((p, t, g["titulo"]))
    for (p, t), g in grupos.items():
        if (p, t) not in vistos:
            dudas.append(Hallazgo(
                p, t, "", "", "DUDA", [],
                "El tema del CSV no fue reconocido en la extracción del PDF; se mantiene.",
            ))
    return confirmados, dudas, ok


def generar_html(pdf, csvp, modelo, confirmados, dudas, ok, aplicado=False, backup=None):
    def esc(x):
        return html.escape(str(x or ""))

    def tabla(items):
        if not items:
            return "<p>Sin elementos.</p>"
        rows = []
        for h in items:
            rows.append("<tr>" + "".join([
                f"<td>{esc(h.parte)}</td>",
                f"<td>{esc(h.tema)}</td>",
                f"<td>{esc(h.norma_pdf or h.norma_csv)}</td>",
                f"<td>{esc(h.accion)}</td>",
                f"<td>{esc(compactar(h.articulos))}</td>",
                f"<td>{esc(h.alcance)}</td>",
                f"<td>{esc(h.motivo)}</td>",
            ]) + "</tr>")
        return "<table><thead><tr><th>Parte</th><th>Tema</th><th>Norma</th><th>Estado</th><th>Artículos</th><th>Alcance</th><th>Motivo</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"

    aplicado_txt = f"<p><b>Aplicado:</b> sí. Backup: {esc(backup)}</p>" if aplicado else "<p><b>Aplicado:</b> no.</p>"
    ok_html = "<ul>" + "".join(f"<li>{esc(p)} {esc(t)} — {esc(tit)}</li>" for p, t, tit in ok) + "</ul>" if ok else "<p>Sin temas clasificados como OK.</p>"
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Auditoría temario</title>
<style>body{{font-family:Arial,sans-serif;max-width:1500px;margin:28px auto;padding:0 18px;color:#222}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #ccc;padding:7px;vertical-align:top}}th{{background:#eee}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #bbb;border-radius:8px;padding:12px 18px}}</style></head>
<body><h1>Auditoría de fidelidad PDF ↔ temario.csv</h1>
<p><b>PDF:</b> {esc(pdf)}<br><b>CSV:</b> {esc(csvp)}<br><b>Modelo:</b> {esc(modelo)}</p>{aplicado_txt}
<div class="cards"><div class="card"><b>Confirmados</b><br>{len(confirmados)}</div><div class="card"><b>Dudas</b><br>{len(dudas)}</div><div class="card"><b>OK</b><br>{len(ok)}</div></div>
<h2>Modificaciones confirmadas</h2><p>Solo estas pueden aplicarse automáticamente.</p>{tabla(confirmados)}
<h2>Dudas / revisión manual</h2><p>No producen cambios automáticos.</p>{tabla(dudas)}
<h2>Temas sin incidencias</h2>{ok_html}
<h2>Criterio</h2><p>Solo se confirma una diferencia cuando el PDF aporta una referencia explícita y su alcance puede resolverse contra el índice oficial. Las inferencias quedan como DUDA.</p>
</body></html>"""


def aplicar(csvp, filas, campos, real, encoding, delim, quote, eol, confirmados):
    eliminar = set()
    altas = []
    for h in confirmados:
        if h.accion == "ELIMINAR":
            eliminar |= {(parte(h.parte), tema(h.tema), norm(h.norma_csv), art(a)) for a in h.articulos}
        elif h.accion == "AÑADIR":
            altas.append(h)

    salida = []
    for r in filas:
        k = (parte(r["parte"]), tema(r["tema"]), norm(r["LEY"]), art(r["articulo"]))
        if k not in eliminar:
            x = {c: str(r.get(c, "") or "") for c in campos}
            for c in COLUMNAS:
                x[real[c]] = r[c]
            salida.append(x)

    plantema = {}
    planley = {}
    existentes = set()
    for r in salida:
        p, t = parte(r[real["parte"]]), tema(r[real["tema"]])
        l = str(r[real["LEY"]] or "").strip()
        plantema.setdefault((p, t), r)
        if l:
            planley.setdefault((p, t, norm(l)), r)
            existentes.add((p, t, norm(l), art(r[real["articulo"]])))

    nuevas = defaultdict(list)
    for h in altas:
        ley = h.norma_csv or h.norma_pdf
        k = (parte(h.parte), tema(h.tema), norm(ley))
        plantilla = planley.get(k) or plantema.get(k[:2])
        if plantilla is None:
            raise RuntimeError(f"No existe fila plantilla para {h.parte} tema {h.tema}.")
        for a in h.articulos:
            ka = (*k, art(a))
            if ka in existentes:
                continue
            x = dict(plantilla)
            x[real["parte"]] = h.parte
            x[real["tema"]] = h.tema
            x[real["LEY"]] = ley
            x[real["articulo"]] = a
            x[real["tipo"]] = "JURIDICO"
            nuevas[k[:2]].append(x)
            existentes.add(ka)

    final = []
    for i, r in enumerate(salida):
        kt = (parte(r[real["parte"]]), tema(r[real["tema"]]))
        final.append(r)
        sig = None
        if i + 1 < len(salida):
            rr = salida[i + 1]
            sig = (parte(rr[real["parte"]]), tema(rr[real["tema"]]))
        if sig != kt and kt in nuevas:
            final.extend(sorted(nuevas.pop(kt), key=lambda x: (norm(x[real["LEY"]]), clave_articulo(x[real["articulo"]]))))
    for vals in nuevas.values():
        final.extend(vals)

    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = csvp.with_name(f"{csvp.stem}_base_{sello}{csvp.suffix}")
    shutil.copy2(csvp, backup)

    fd, tmp = tempfile.mkstemp(prefix=csvp.stem + "_", suffix=".tmp", dir=csvp.parent)
    os.close(fd)
    tp = Path(tmp)
    try:
        with tp.open("w", encoding=encoding, newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=campos,
                delimiter=delim,
                quotechar=quote,
                lineterminator=eol,
                extrasaction="ignore",
            )
            w.writeheader()
            w.writerows(final)
        leer_csv(tp)
        os.replace(tp, csvp)
    except Exception:
        tp.unlink(missing_ok=True)
        raise
    return backup


def ruta_arg(p: Path | None, mensaje: str) -> Path:
    while p is None:
        v = input(mensaje).strip().strip('"')
        p = Path(v) if v else None
    p = p.expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not p.is_file():
        raise RuntimeError(f"No existe el fichero: {p}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--csv", dest="csvp", type=Path)
    ap.add_argument("--modelo", default=MODELO)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--no-preguntar-aplicar", action="store_true")
    a = ap.parse_args()

    pdf = ruta_arg(a.pdf, "Ruta del temario PDF: ")
    csvp = ruta_arg(a.csvp, "Ruta del temario CSV: ")
    print("\nAUDITORÍA DE FIDELIDAD PDF ↔ TEMARIO.CSV")
    print(f"PDF: {pdf}\nCSV: {csvp}\nLa base de datos NO se modifica.")

    filas, campos, real, enc, delim, quote, eol = leer_csv(csvp)
    temas = extraer_ia(leer_pdf(pdf), filas, a.modelo)
    conf, dudas, ok = auditar(temas, filas)

    INFORMES.mkdir(parents=True, exist_ok=True)
    informe = INFORMES / f"auditoria_fidelidad_temario_{csvp.stem}_{datetime.now():%Y%m%d_%H%M%S}.html"
    informe.write_text(generar_html(pdf, csvp, a.modelo, conf, dudas, ok), encoding="utf-8")
    print(f"\nConfirmados: {len(conf)}\nDudas: {len(dudas)}\nOK: {len(ok)}\nInforme: {informe}")

    aplicar_ok = a.aplicar
    if not a.no_preguntar_aplicar and not a.aplicar:
        aplicar_ok = input("\n¿Incorporar modificaciones confirmadas? [s/N]: ").strip().lower() in {"s", "si", "sí"}
    if not aplicar_ok:
        print("No se ha modificado el CSV.")
        return 0
    if not conf:
        print("No existen modificaciones confirmadas.")
        return 0

    backup = aplicar(csvp, filas, campos, real, enc, delim, quote, eol, conf)
    informe.write_text(generar_html(pdf, csvp, a.modelo, conf, dudas, ok, True, backup), encoding="utf-8")
    print(f"Backup: {backup}\nTemario actualizado: {csvp}\nInforme: {informe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
