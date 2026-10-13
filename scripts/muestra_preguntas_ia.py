"""Muestra aleatoria de preguntas IA nuevas (origen IA-2026-10) en HTML. Solo lectura."""
import sqlite3, html, sys, os, webbrowser
from pathlib import Path

N = int(sys.argv[1]) if len(sys.argv) > 1 else 15
raiz = Path(__file__).resolve().parent.parent
con = sqlite3.connect(f"file:{raiz / 'db' / 'oposiciones.sqlite3'}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
filas = con.execute("""
    SELECT lp.id, lp.enunciado, lp.opcion_a, lp.opcion_b, lp.opcion_c, lp.opcion_d,
           lp.respuesta_correcta, lp.nombre_norma, lp.articulo, lp.origen_oposicion
    FROM lote_preguntas lp
    JOIN importaciones_ficheros i ON i.id = lp.importacion_fichero_id
    WHERE i.nombre_fichero LIKE 'IA-2026-10%'
    ORDER BY RANDOM() LIMIT ?""", (N,)).fetchall()
total = con.execute("""SELECT COUNT(*) FROM lote_preguntas lp JOIN importaciones_ficheros i
    ON i.id = lp.importacion_fichero_id WHERE i.nombre_fichero LIKE 'IA-2026-10%'""").fetchone()[0]
e = html.escape
partes = [f"""<!doctype html><meta charset="utf-8"><title>Muestra IA-2026-10</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:820px;margin:24px auto;padding:0 16px;color:#222}}
.p{{border:1px solid #ccc;border-radius:8px;padding:14px 18px;margin:18px 0}}
.r{{font-size:.85em;color:#555;margin-bottom:8px}} .ok{{background:#e6f4ea;font-weight:600}}
li{{margin:4px 0;list-style:none;padding:3px 6px;border-radius:4px}}ul{{padding-left:0}}</style>
<h1>Muestra de {len(filas)} preguntas IA (de {total} con origen IA-2026-10)</h1>"""]
for k, f in enumerate(filas, 1):
    ops = "".join(
        f'<li class="{"ok" if f["respuesta_correcta"].strip().lower()==l else ""}">{l.upper()}) {e(f["opcion_"+l] or "")}</li>'
        for l in "abcd")
    partes.append(f'<div class="p"><div class="r">#{k} · id {f["id"]} · {e(f["nombre_norma"] or "")} · art. {e(str(f["articulo"] or ""))} · origen {e(f["origen_oposicion"] or "")}</div>'
                  f'<div><b>{e(f["enunciado"])}</b></div><ul>{ops}</ul></div>')
salida = raiz / "informes" / "muestra_preguntas_ia_2026_10.html"
salida.parent.mkdir(exist_ok=True)
salida.write_text("".join(partes), encoding="utf-8")
print("Generado:", salida)
try: os.startfile(salida)
except Exception: webbrowser.open(salida.as_uri())
