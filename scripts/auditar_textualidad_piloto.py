#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Auditor de textualidad de la opción almacenada como correcta.
SOLO LECTURA. No modifica la base de datos.

Objetivo:
- partir de respuesta_correcta como hipótesis;
- comparar SOLO esa opción con el texto RAG preparado por el piloto;
- localizar el fragmento normativo más parecido;
- NO decidir automáticamente que una respuesta es jurídicamente incorrecta;
- separar coincidencia textual clara de casos que requieren revisión.

Entrada por defecto:
  auditorias/auditoria_respuestas_piloto.json

Salidas:
  auditorias/auditoria_textualidad_piloto.html
  auditorias/auditoria_textualidad_piloto.json
"""
import argparse, json, re, html, unicodedata
from pathlib import Path
from difflib import SequenceMatcher

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokens(s):
    return re.findall(r"[a-z0-9]+", norm(s))

def split_fragments(text):
    # Conserva unidades relativamente pequeñas sin afirmar que sean apartados jurídicos.
    text = re.sub(r"\[[^\]]+\]\s*", "", text or "")
    parts = re.split(r"(?<=[.;:])\s+(?=(?:[A-ZÁÉÍÓÚÑ0-9]|[a-z]\)))", text)
    out=[]
    for p in parts:
        p=re.sub(r"\s+"," ",p).strip()
        if len(p) >= 20:
            out.append(p)
    return out or ([text.strip()] if text.strip() else [])

def similarity(a,b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def best_fragment(option, text):
    frags=split_fragments(text)
    if not frags:
        return "",0.0
    best=max(frags,key=lambda f: similarity(option,f))
    return best, similarity(option,best)

def contiguous_in(option,text):
    o=norm(option); t=norm(text)
    return bool(o and o in t)

def token_coverage(option, fragment):
    ot=tokens(option); ft=set(tokens(fragment))
    if not ot: return 0.0
    return sum(1 for x in ot if x in ft)/len(ot)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--json", default=str(Path("auditorias")/"auditoria_respuestas_piloto.json"))
    ap.add_argument("--salida-dir", default="auditorias")
    args=ap.parse_args()

    src=Path(args.json)
    data=json.loads(src.read_text(encoding="utf-8"))
    rows=[]
    for q in data["preguntas"]:
        letra=q["respuesta_correcta_almacenada"]
        opcion=q["opciones"][letra]
        texto=q.get("texto_articulo_principal") or ""
        frag,sim=best_fragment(opcion,texto)
        exact=contiguous_in(opcion,texto)
        cov=token_coverage(opcion,frag)

        # Estado estrictamente descriptivo. No es un dictamen jurídico.
        if exact:
            estado="TEXTUAL_CLARA"
        else:
            estado="REVISAR_TEXTUALIDAD"

        rows.append({
            "pregunta_id":q["pregunta_id"],
            "norma":q.get("norma"),
            "articulo":q.get("articulo_normalizado"),
            "enunciado":q.get("enunciado"),
            "respuesta_correcta_almacenada":letra,
            "opcion_correcta_almacenada":opcion,
            "estado_textualidad":estado,
            "coincidencia_contigua_normalizada":exact,
            "similitud_mejor_fragmento":round(sim,4),
            "cobertura_tokens_mejor_fragmento":round(cov,4),
            "mejor_fragmento_rag":frag,
            "incidencias_evidencia":q.get("incidencias_evidencia",[]),
        })

    outdir=Path(args.salida_dir); outdir.mkdir(parents=True,exist_ok=True)
    jout=outdir/"auditoria_textualidad_piloto.json"
    hout=outdir/"auditoria_textualidad_piloto.html"
    jout.write_text(json.dumps({"meta":{
        "origen":str(src),
        "preguntas":len(rows),
        "criterio":"La respuesta almacenada es hipótesis inicial. Esta fase solo mide textualidad; no cambia respuestas ni opciones.",
        "escrituras_bd":0
    },"preguntas":rows},ensure_ascii=False,indent=2),encoding="utf-8")

    counts={}
    for r in rows: counts[r["estado_textualidad"]]=counts.get(r["estado_textualidad"],0)+1

    body=[]
    for r in rows:
        body.append(f"""
        <section>
        <h2>ID {r['pregunta_id']} — {html.escape(str(r['norma']))} — art. {html.escape(str(r['articulo']))}</h2>
        <p><b>Estado:</b> {r['estado_textualidad']} · <b>Respuesta almacenada:</b> {r['respuesta_correcta_almacenada']}</p>
        <p><b>Enunciado:</b> {html.escape(r['enunciado'] or '')}</p>
        <div class="stored"><b>Opción almacenada:</b><br>{html.escape(r['opcion_correcta_almacenada'])}</div>
        <div class="rag"><b>Fragmento RAG más próximo:</b><br>{html.escape(r['mejor_fragmento_rag'])}</div>
        <p class="metric">Coincidencia contigua: {r['coincidencia_contigua_normalizada']} · similitud: {r['similitud_mejor_fragmento']:.4f} · cobertura tokens: {r['cobertura_tokens_mejor_fragmento']:.4f}</p>
        </section>""")

    doc=f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <title>Auditoría textualidad piloto</title>
    <style>
    body{{font-family:Arial,sans-serif;max-width:1100px;margin:32px auto;padding:0 22px;line-height:1.45;color:#222}}
    section{{border-top:2px solid #bbb;padding:18px 0}} h1{{font-size:26px}} h2{{font-size:19px}}
    .stored,.rag{{padding:12px;margin:8px 0;background:#f5f5f5;border-left:4px solid #777}}
    .metric{{color:#555;font-size:13px}}
    </style></head><body>
    <h1>Auditoría de textualidad — piloto</h1>
    <p><b>SOLO LECTURA.</b> Parte de la respuesta almacenada como hipótesis. No decide por sí sola si una respuesta es jurídicamente correcta y no modifica la base.</p>
    <p>TEXTUAL_CLARA: {counts.get('TEXTUAL_CLARA',0)} · REVISAR_TEXTUALIDAD: {counts.get('REVISAR_TEXTUALIDAD',0)} · Total: {len(rows)}</p>
    {''.join(body)}
    </body></html>"""
    hout.write_text(doc,encoding="utf-8")

    print("="*78)
    print("AUDITORÍA DE TEXTUALIDAD - PILOTO - SOLO LECTURA")
    print("="*78)
    print(f"Preguntas................................. {len(rows)}")
    print(f"Textualidad clara......................... {counts.get('TEXTUAL_CLARA',0)}")
    print(f"Revisar textualidad....................... {counts.get('REVISAR_TEXTUALIDAD',0)}")
    print(f"JSON...................................... {jout}")
    print(f"HTML...................................... {hout}")
    print("Cambios en BD............................. 0")
    print("="*78)

if __name__=="__main__":
    main()
