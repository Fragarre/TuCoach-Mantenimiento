#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AUDITOR DIRECTO DE RESPUESTA ALMACENADA — PILOTO — SOLO LECTURA

Idea:
1) La respuesta almacenada se toma como hipótesis inicial.
2) Se intenta demostrar primero con el texto RAG.
3) Solo si no puede demostrarse, se marca para analizar las otras opciones.
4) No modifica BD y no cambia respuesta_correcta ni textos.

NO es un dictamen jurídico automático. Produce evidencias y prioridades de revisión.

Entrada:
  auditorias/auditoria_respuestas_piloto.json

Salidas:
  auditorias/auditoria_directa_piloto.json
  auditorias/auditoria_directa_piloto.html
"""

import argparse
import html
import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

# Palabras cuyo cambio suele alterar el sentido jurídico.
CRITICAL = {
    "no","si","solo","solamente","unicamente","siempre","nunca",
    "podra","podran","debera","deberan","puede","pueden","debe","deben",
    "obligatorio","obligatoria","facultativo","facultativa",
    "estimada","desestimada","formal","informal",
    "antes","despues","superior","inferior","maximo","maxima","minimo","minima",
    "todos","todas","ninguno","ninguna","excepto","salvo","sin","con",
    "y","o"
}

NEGATIVE_CUES = [
    "incorrecta","incorrecto","no es","no esta","no figura","no se encuentra",
    "no se incluye","no corresponde","no reconocido","no reconocida","excepto"
]

COMPOSITE_RE = re.compile(
    r"^\s*(?:"
    r"[a-d]\s+y\s+[a-d]\s+son\s+correctas?"
    r"|[a-d]\s+y\s+[a-d]\s+son\s+incorrectas?"
    r"|todas\s+las\s+(?:anteriores|respuestas)"
    r"|ninguna(?:\s+de\s+las\s+anteriores)?"
    r")\s*[\.\,]?\s*$", re.I
)

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("º","o").replace("ª","a")
    s = re.sub(r"[^a-z0-9%]+", " ", s)
    # equivalencias puramente lingüísticas que no cambian el contenido
    s = re.sub(r"\\bunicament(?:e)?\\b", "solo", s)
    return re.sub(r"\\s+", " ", s).strip()

def words(s):
    return re.findall(r"[a-z0-9%]+", norm(s))

def content_words(s):
    stop = {
        "el","la","los","las","un","una","unos","unas","de","del","al","a","en",
        "que","se","su","sus","por","para","como","con","y","o","e","u","es","son",
        "ser","esta","este","estos","estas","lo"
    }
    return [w for w in words(s) if w not in stop and len(w) > 2]

def literal_safe(option, evidence):
    """Coincidencia de la secuencia completa de palabras, evitando el falso positivo
    'Toda...' dentro de 'No toda...'.
    """
    ot = words(option)
    et = words(evidence)
    if not ot or len(ot) > len(et):
        return False
    n=len(ot)
    for i in range(len(et)-n+1):
        if et[i:i+n] == ot:
            # Si justo antes hay un modificador crítico que no pertenece a la opción,
            # no consideramos la secuencia como afirmación literal autónoma.
            if i>0 and et[i-1] in {"no","sin","salvo","excepto"}:
                continue
            return True
    return False

def is_negative_question(s):
    t=norm(s)
    return any(c in t for c in NEGATIVE_CUES)

def chunks(text):
    text = re.sub(r"^\[[^\]]+\]\s*", "", text or "")
    # párrafos, apartados y frases. Se conservan también ventanas de dos frases.
    parts = re.split(r"\n+|(?<=[.;:])\s+(?=(?:[A-ZÁÉÍÓÚÑ0-9]|[a-z]\)))", text)
    parts = [re.sub(r"\s+"," ",p).strip() for p in parts if len(p.strip()) >= 8]
    out=list(parts)
    for i in range(len(parts)-1):
        z=(parts[i]+" "+parts[i+1]).strip()
        if len(z) <= 1600:
            out.append(z)
    return out

def coverage(option, fragment):
    ow=content_words(option)
    fw=set(content_words(fragment))
    if not ow: return 0.0
    return sum(w in fw for w in ow)/len(ow)

def seqsim(a,b):
    return SequenceMatcher(None,norm(a),norm(b)).ratio()

def best_fragment(option, evidence):
    cc=chunks(evidence)
    if not cc:
        return "",0.0,0.0
    scored=[(coverage(option,c),seqsim(option,c),c) for c in cc]
    cov,sim,c=max(scored,key=lambda x:(x[0],x[1]))
    return c,cov,sim

def critical_diff(option, fragment):
    o=set(words(option)); f=set(words(fragment))
    only_o=sorted((o-f) & CRITICAL)
    only_f=sorted((f-o) & CRITICAL)
    return only_o,only_f

def stem_support(stem, fragment):
    sw=set(content_words(stem))
    fw=set(content_words(fragment))
    if not sw: return 0.0
    return len(sw & fw)/min(max(len(sw),1),10)

def classify(q):
    stem=q.get("enunciado") or ""
    letter=q.get("respuesta_correcta_almacenada")
    option=(q.get("opciones") or {}).get(letter,"")
    evidence=q.get("texto_articulo_principal") or ""

    base={
        "pregunta_id":q.get("pregunta_id"),
        "norma":q.get("norma"),
        "articulo":q.get("articulo_normalizado"),
        "enunciado":stem,
        "respuesta_correcta_almacenada":letter,
        "opcion_correcta_almacenada":option,
        "tipo_fuente":q.get("tipo_fuente"),
        "origen_oposicion":q.get("origen_oposicion"),
    }

    if q.get("incidencias_evidencia") or not q.get("articulo_principal_localizado") or not evidence.strip():
        return base | {
            "estado":"EVIDENCIA_INSUFICIENTE",
            "motivo":"La evidencia principal no está disponible/localizada de forma utilizable.",
            "fragmento_rag":"",
            "cobertura":0.0,"similitud":0.0,
            "diferencias_criticas_opcion":[],"diferencias_criticas_rag":[]
        }

    if COMPOSITE_RE.match(option):
        return base | {
            "estado":"RESPUESTA_COMPUESTA_REVISAR",
            "motivo":"La respuesta almacenada depende de otras opciones (p. ej. A y B/todas/ninguna); no puede validarse por literalidad de su propio texto.",
            "fragmento_rag":"",
            "cobertura":0.0,"similitud":0.0,
            "diferencias_criticas_opcion":[],"diferencias_criticas_rag":[]
        }

    frag,cov,sim=best_fragment(option,evidence)
    exact = literal_safe(option, evidence)
    neg=is_negative_question(stem)
    ow=content_words(option)
    od,fd=critical_diff(option,frag)

    # 1. Coincidencia literal larga: respaldo fuerte para positiva.
    if exact and len(words(option)) >= 4 and not neg:
        state="RESPALDO_TEXTUAL"
        reason="La opción almacenada aparece íntegramente en el artículo RAG."
    # 2. Coincidencia literal en pregunta negativa: no basta; puede ser un distractor verdadero.
    elif exact and neg:
        state="NEGATIVA_ANALIZAR_POLARIDAD"
        reason="La opción contiene texto literal del RAG, pero la pregunta pide la incorrecta/no válida; debe analizarse qué parte altera o añade."
    # 3. Opción corta: exigir además contexto del enunciado.
    elif len(words(option)) <= 3 and cov >= 0.99 and stem_support(stem,frag) >= 0.12:
        state="RESPALDO_CONTEXTUAL_CORTO"
        reason="La opción es corta, aparece en el RAG y el fragmento comparte contexto relevante con el enunciado."
    # 4. Positiva casi literal, sin diferencias críticas detectadas.
    elif not neg and cov >= 0.90 and sim >= 0.55 and not od:
        state="CANDIDATA_REESCRITURA_TEXTUAL"
        reason="La opción parece jurídicamente apoyada por un fragmento muy próximo, pero no es literal; candidata a sustituirse por redacción textual tras revisión."
    # 5. Negativa con altísimo solapamiento y cambio crítico.
    elif neg and ((cov >= 0.88 and sim >= 0.72) or (cov >= 0.72 and sim >= 0.35)) and (od or fd):
        state="CONTRADICCION_TEXTUAL_CANDIDATA"
        reason="La opción y el RAG son muy próximos pero presentan diferencias en términos jurídicamente sensibles; compatible con que la almacenada sea la opción incorrecta."
    else:
        state="ANALIZAR_OTRAS_OPCIONES"
        reason="No se ha podido demostrar con seguridad la respuesta almacenada mediante comparación directa; procede analizar las otras tres opciones."

    return base | {
        "estado":state,
        "motivo":reason,
        "fragmento_rag":frag,
        "cobertura":round(cov,4),
        "similitud":round(sim,4),
        "diferencias_criticas_opcion":od,
        "diferencias_criticas_rag":fd,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--json",default=str(Path("auditorias")/"auditoria_respuestas_piloto.json"))
    ap.add_argument("--salida-dir",default="auditorias")
    args=ap.parse_args()

    src=Path(args.json)
    data=json.loads(src.read_text(encoding="utf-8"))
    rows=[classify(q) for q in data["preguntas"]]
    counts=Counter(r["estado"] for r in rows)

    outdir=Path(args.salida_dir); outdir.mkdir(parents=True,exist_ok=True)
    jout=outdir/"auditoria_directa_piloto.json"
    hout=outdir/"auditoria_directa_piloto.html"

    jout.write_text(json.dumps({
        "meta":{
            "origen":str(src),
            "preguntas":len(rows),
            "escrituras_bd":0,
            "criterio":"Primero se intenta demostrar la respuesta almacenada contra RAG. Solo si falla, se remite al análisis de las demás opciones.",
            "estados":dict(counts)
        },
        "preguntas":rows
    },ensure_ascii=False,indent=2),encoding="utf-8")

    secs=[]
    for r in rows:
        dif=""
        if r["diferencias_criticas_opcion"] or r["diferencias_criticas_rag"]:
            dif=(f"<p><b>Diferencias críticas detectadas:</b> "
                 f"solo opción={html.escape(str(r['diferencias_criticas_opcion']))} · "
                 f"solo RAG={html.escape(str(r['diferencias_criticas_rag']))}</p>")
        secs.append(f"""
<section>
<h2>ID {r['pregunta_id']} — {html.escape(r['estado'])}</h2>
<p><b>{html.escape(str(r['norma']))}</b> · art. {html.escape(str(r['articulo']))} · respuesta almacenada {html.escape(str(r['respuesta_correcta_almacenada']))}</p>
<p><b>Motivo:</b> {html.escape(r['motivo'])}</p>
<p><b>Cobertura:</b> {r['cobertura']:.4f} · <b>similitud:</b> {r['similitud']:.4f}</p>
{dif}
<div class="q"><b>Enunciado</b><br>{html.escape(r['enunciado'])}</div>
<div class="opt"><b>Opción almacenada</b><br>{html.escape(r['opcion_correcta_almacenada'])}</div>
<div class="rag"><b>Fragmento RAG seleccionado</b><br>{html.escape(r['fragmento_rag'])}</div>
</section>""")

    summary=" · ".join(f"{k}: {v}" for k,v in sorted(counts.items()))
    hout.write_text(f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Auditoría directa — piloto</title>
<style>
body{{font-family:Arial,sans-serif;max-width:1150px;margin:30px auto;padding:0 22px;line-height:1.45;color:#222}}
section{{border-top:2px solid #bbb;padding:18px 0}}
.q,.opt,.rag{{padding:11px;margin:8px 0;background:#f5f5f5;border-left:4px solid #777}}
.rag{{font-size:13px}}
</style></head><body>
<h1>Auditoría directa de la respuesta almacenada — piloto</h1>
<p><b>SOLO LECTURA.</b> No cambia respuestas ni opciones.</p>
<p>{html.escape(summary)}</p>
{''.join(secs)}
</body></html>""",encoding="utf-8")

    print("="*78)
    print("AUDITORÍA DIRECTA DE RESPUESTA ALMACENADA - PILOTO - SOLO LECTURA")
    print("="*78)
    print(f"Preguntas................................. {len(rows)}")
    for k,v in sorted(counts.items()):
        print(f"{k:42} {v}")
    print(f"JSON...................................... {jout}")
    print(f"HTML...................................... {hout}")
    print("Cambios en BD............................. 0")
    print("="*78)

if __name__=="__main__":
    main()
