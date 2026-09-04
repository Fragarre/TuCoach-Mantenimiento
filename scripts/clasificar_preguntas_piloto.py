#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CLASIFICADOR PREVIO DE PREGUNTAS JURÍDICAS — SOLO LECTURA

NO audita la corrección jurídica.
NO modifica la BD.
NO cambia respuestas ni opciones.

Su única función es separar conservadoramente las preguntas del piloto en:
  - TEXTUAL_NEGATIVA
  - TEXTUAL_POSITIVA
  - APLICACION_RAZONAMIENTO
  - EVIDENCIA_DEFECTUOSA
  - INDETERMINADA

Además añade una señal auxiliar sobre la opción almacenada:
  - OPCION_LITERAL_LARGA
  - OPCION_CORTA_REQUIERE_CONTEXTO
  - NO_LITERAL_REQUIERE_REVISION
  - NO_APLICA

Entrada:
  auditorias/auditoria_respuestas_piloto.json

Salidas:
  auditorias/clasificacion_previa_piloto.json
  auditorias/clasificacion_previa_piloto.html
"""
import argparse
import html
import json
import re
import unicodedata
from pathlib import Path
from collections import Counter

NEGATIVE_PATTERNS = [
    r"\bincorrect[ao]s?\b",
    r"\bno es\b",
    r"\bno esta\b",
    r"\bno figura\b",
    r"\bno se encuentra\b",
    r"\bno se incluye\b",
    r"\bno corresponde\b",
    r"\bno reconocid[ao]s?\b",
    r"\bexcepto\b",
    r"\bexcepcion\b",
]

LEGAL_CUES = [
    "según ", "conforme ", "de conformidad con", "de acuerdo con",
    "a tenor de", "artículo ", "art. ", "ley ", "decreto ",
    "constitución ", "reglamento ", "tratado ", "orden "
]

# Solo señales fuertes. Si no se cumplen, se deja en textual/indeterminada.
APPLICATION_PATTERNS = [
    r"\bD\.\s*[A-ZÁÉÍÓÚÑ]",
    r"\bDª\.\s*[A-ZÁÉÍÓÚÑ]",
    r"\bdoña\s+[A-ZÁÉÍÓÚÑ]",
    r"\bdon\s+[A-ZÁÉÍÓÚÑ]",
    r"\bse\s+plantea\s+el\s+siguiente\s+supuesto\b",
    r"\bsupuesto\s+pr[aá]ctico\b",
]

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("º", "o").replace("ª", "a")
    s = re.sub(r"[^\w%]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()

def word_tokens(s):
    return re.findall(r"[a-záéíóúñü0-9%]+", (s or "").lower())

def is_negative(enunciado):
    t = norm(enunciado)
    return any(re.search(p, t, flags=re.I) for p in NEGATIVE_PATTERNS)

def is_application(enunciado):
    if len(enunciado or "") < 250:
        return False
    return any(re.search(p, enunciado or "", flags=re.I) for p in APPLICATION_PATTERNS)

def has_legal_cue(enunciado):
    t = norm(enunciado)
    return any(norm(cue) in t for cue in LEGAL_CUES)

def option_literal_signal(option, evidence):
    o = norm(option)
    e = norm(evidence)
    toks = word_tokens(option)
    if not o or not e:
        return "NO_APLICA", "Falta opción o evidencia."
    if len(toks) <= 3:
        # Evita falsos positivos como "Anual", "el artículo 47", etc.
        return "OPCION_CORTA_REQUIERE_CONTEXTO", "La opción tiene 3 palabras o menos; una coincidencia aislada no prueba correspondencia normativa."
    if o in e:
        return "OPCION_LITERAL_LARGA", "La opción completa aparece, normalizada, dentro del texto RAG."
    return "NO_LITERAL_REQUIERE_REVISION", "La opción completa no aparece literalmente en el texto RAG; no se concluye que sea incorrecta."


GENERIC_TITLE_WORDS = {
    "ley","organica","decreto","real","legislativo","reglamento","orden",
    "del","de","la","el","y","para","por","en","sobre","generalitat",
    "comunitat","valenciana","comunidad","valenciana","consell","estado",
}

def salient_title_tokens(enunciado):
    """
    Extrae de forma conservadora palabras informativas del título cuando el
    enunciado usa una forma típica: 'Ley X/YYYY, de <fecha>, de <título>'.
    Si no puede hacerlo con seguridad devuelve [].
    """
    t = norm(enunciado)
    m = re.search(
        r"\bley\s+(?:organica\s+)?\d+\s+\d{4}\s+de\s+\d{1,2}\s+de\s+[a-z]+\s+de\s+(.+?)(?:\ben relacion\b|\bsegun\b|\bconforme\b|\barticulo\b|\bart\b|$)",
        t
    )
    if not m:
        return []
    cand = m.group(1)
    toks = [x for x in cand.split() if len(x) >= 5 and x not in GENERIC_TITLE_WORDS and not x.isdigit()]
    return toks[:12]

def evidence_semantic_warning(q):
    """
    Solo genera una ALERTA, nunca declara que la fuente sea errónea.
    Detecta dos anomalías fuertes:
      1) cabecera de artículo anormalmente larga (posible falso encabezado);
      2) título legal explícito con >=3 términos informativos de los que ninguno
         aparece en el texto RAG.
    """
    evidence = q.get("texto_articulo_principal") or ""
    warnings = []

    first = evidence.splitlines()[0] if evidence else ""
    if first.startswith("[") and "|" in first:
        right = first.split("|",1)[1].rstrip("] ").strip()
        if len(right) > 120:
            warnings.append("Cabecera de artículo anormalmente larga; posible localización sobre una referencia interna y no sobre el encabezado real.")

    title_toks = salient_title_tokens(q.get("enunciado") or "")
    if len(title_toks) >= 3:
        ev = set(word_tokens(norm(evidence)))
        present = [t for t in title_toks if t in ev]
        if not present:
            warnings.append("Ningún término informativo del título legal explícito aparece en el texto RAG; posible colisión de norma/fuente.")

    return warnings

def evidence_defect(q):
    reasons = []
    if not q.get("articulo_principal_localizado"):
        reasons.append("No se localizó el artículo principal.")
    for x in q.get("incidencias_evidencia") or []:
        reasons.append(str(x))
    if not (q.get("texto_articulo_principal") or "").strip():
        reasons.append("El texto del artículo principal está vacío.")
    return reasons

def classify(q):
    enu = q.get("enunciado") or ""
    defects = evidence_defect(q)
    if defects:
        return "EVIDENCIA_DEFECTUOSA", defects

    warnings = evidence_semantic_warning(q)
    if warnings:
        return "EVIDENCIA_SOSPECHOSA", warnings

    if is_application(enu):
        return "APLICACION_RAZONAMIENTO", ["El enunciado contiene señales fuertes de supuesto práctico; se excluye del control textual automático."]

    if is_negative(enu):
        return "TEXTUAL_NEGATIVA", ["El enunciado pide identificar una opción incorrecta/no incluida/exceptuada; la opción almacenada debe contrastarse como contradicción normativa."]

    if has_legal_cue(enu):
        return "TEXTUAL_POSITIVA", ["El enunciado remite expresamente a una norma/precepto y no contiene una polaridad negativa detectada."]

    return "INDETERMINADA", ["Las reglas conservadoras no permiten asignar con seguridad un tipo."]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(Path("auditorias") / "auditoria_respuestas_piloto.json"))
    ap.add_argument("--salida-dir", default="auditorias")
    args = ap.parse_args()

    src = Path(args.json)
    data = json.loads(src.read_text(encoding="utf-8"))

    out = []
    for q in data["preguntas"]:
        category, reasons = classify(q)
        letter = q.get("respuesta_correcta_almacenada")
        option = (q.get("opciones") or {}).get(letter, "")
        sig, sig_reason = option_literal_signal(option, q.get("texto_articulo_principal") or "")

        # En aplicación/razonamiento y evidencia defectuosa, la señal literal no se usa.
        if category in {"APLICACION_RAZONAMIENTO", "EVIDENCIA_DEFECTUOSA", "EVIDENCIA_SOSPECHOSA"}:
            sig = "NO_APLICA"
            sig_reason = "No se aplica control de literalidad en esta fase."

        out.append({
            "pregunta_id": q.get("pregunta_id"),
            "categoria_previa": category,
            "motivos_categoria": reasons,
            "norma": q.get("norma"),
            "articulo": q.get("articulo_normalizado"),
            "enunciado": q.get("enunciado") or "",
            "respuesta_correcta_almacenada": letter,
            "opcion_correcta_almacenada": option,
            "senal_literalidad": sig,
            "motivo_literalidad": sig_reason,
            "texto_articulo_principal": q.get("texto_articulo_principal") or "",
            "tipo_fuente": q.get("tipo_fuente"),
            "origen_oposicion": q.get("origen_oposicion"),
        })

    counts = Counter(r["categoria_previa"] for r in out)
    sig_counts = Counter(r["senal_literalidad"] for r in out)

    outdir = Path(args.salida_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    jout = outdir / "clasificacion_previa_piloto.json"
    hout = outdir / "clasificacion_previa_piloto.html"

    payload = {
        "meta": {
            "origen": str(src),
            "preguntas": len(out),
            "escrituras_bd": 0,
            "advertencia": "Clasificación previa conservadora. No constituye auditoría jurídica ni autoriza cambios en respuesta_correcta u opciones.",
            "categorias": dict(counts),
            "senales_literalidad": dict(sig_counts),
        },
        "preguntas": out,
    }
    jout.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    sections = []
    for r in out:
        reasons = " ".join(r["motivos_categoria"])
        evidence = r["texto_articulo_principal"]
        if len(evidence) > 1800:
            evidence = evidence[:1800] + " […]"
        sections.append(f"""
<section>
<h2>ID {r['pregunta_id']} — {html.escape(str(r['categoria_previa']))}</h2>
<p><b>Norma:</b> {html.escape(str(r['norma']))} · <b>art.:</b> {html.escape(str(r['articulo']))} ·
<b>respuesta almacenada:</b> {html.escape(str(r['respuesta_correcta_almacenada']))}</p>
<p><b>Motivo clasificación:</b> {html.escape(reasons)}</p>
<p><b>Señal de literalidad:</b> {html.escape(r['senal_literalidad'])}<br>
{html.escape(r['motivo_literalidad'])}</p>
<div class="q"><b>Enunciado</b><br>{html.escape(r['enunciado'])}</div>
<div class="opt"><b>Opción almacenada como correcta</b><br>{html.escape(r['opcion_correcta_almacenada'])}</div>
<div class="rag"><b>Texto RAG principal</b><br>{html.escape(evidence)}</div>
</section>""")

    summary = " · ".join(f"{k}: {counts.get(k,0)}" for k in [
        "TEXTUAL_POSITIVA", "TEXTUAL_NEGATIVA", "APLICACION_RAZONAMIENTO",
        "EVIDENCIA_DEFECTUOSA", "EVIDENCIA_SOSPECHOSA", "INDETERMINADA"
    ])
    sigsummary = " · ".join(f"{k}: {sig_counts.get(k,0)}" for k in [
        "OPCION_LITERAL_LARGA", "OPCION_CORTA_REQUIERE_CONTEXTO",
        "NO_LITERAL_REQUIERE_REVISION", "NO_APLICA"
    ])

    hout.write_text(f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Clasificación previa — piloto</title>
<style>
body{{font-family:Arial,sans-serif;max-width:1150px;margin:30px auto;padding:0 22px;line-height:1.45;color:#222}}
section{{border-top:2px solid #bbb;padding:18px 0}}
.q,.opt,.rag{{padding:11px;margin:8px 0;background:#f5f5f5;border-left:4px solid #777}}
.rag{{font-size:13px}}
</style></head><body>
<h1>Clasificación previa de preguntas jurídicas — piloto</h1>
<p><b>SOLO LECTURA.</b> No decide corrección jurídica y no modifica la base de datos.</p>
<p><b>Categorías:</b> {html.escape(summary)}</p>
<p><b>Señales auxiliares:</b> {html.escape(sigsummary)}</p>
{''.join(sections)}
</body></html>""", encoding="utf-8")

    print("=" * 78)
    print("CLASIFICACIÓN PREVIA - PILOTO - SOLO LECTURA")
    print("=" * 78)
    print(f"Preguntas................................. {len(out)}")
    for k in ["TEXTUAL_POSITIVA","TEXTUAL_NEGATIVA","APLICACION_RAZONAMIENTO","EVIDENCIA_DEFECTUOSA","INDETERMINADA"]:
        print(f"{k:40} {counts.get(k,0)}")
    print("-" * 78)
    for k in ["OPCION_LITERAL_LARGA","OPCION_CORTA_REQUIERE_CONTEXTO","NO_LITERAL_REQUIERE_REVISION","NO_APLICA"]:
        print(f"{k:40} {sig_counts.get(k,0)}")
    print(f"JSON...................................... {jout}")
    print(f"HTML...................................... {hout}")
    print("Cambios en BD............................. 0")
    print("=" * 78)

if __name__ == "__main__":
    main()
