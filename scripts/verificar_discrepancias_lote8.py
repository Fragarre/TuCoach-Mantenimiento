import json, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
from openai_api import seleccionar_fragmento_json
DB=ROOT/'db'/'oposiciones.sqlite3'; OUT=ROOT/'outputs'/'auditoria_control_tests_v1'/'segunda_verificacion_discrepancias_lote8.json'
ids=(12424,12719,13248)
with sqlite3.connect(DB) as c:
 q=[]
 for i in ids:
  r=c.execute('select id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d from lote_preguntas where id=?',(i,)).fetchone(); q.append(dict(zip(('id','enunciado','opcion_a','opcion_b','opcion_c','opcion_d'),r)))
 ev=[]
 for boe,art in [('BOE-A-2015-10565','24'),('BOE-A-2021-8880','74'),('BOE-A-2017-12902','107')]:
  r=c.execute('select id_boe,articulo_boe,texto from articulos_fuente where id_boe=? and articulo_boe=?',(boe,art)).fetchone(); ev.append(dict(zip(('id_fuente','articulo','texto'),r)))
prompt='Devuelve solo JSON {"resultados":[{"pregunta_id":int,"opcion_correcta":"A|B|C|D|null","certeza":"UNICA|NO_DETERMINABLE"}]}. Determina solo por textos oficiales; no hay respuesta almacenada. '+json.dumps({'preguntas':q,'evidencia':ev},ensure_ascii=False)
r=seleccionar_fragmento_json(prompt,modelo='gpt-5.4-nano',operacion='segunda_verificacion_lote8',max_output_tokens=700)
if not isinstance(r.get('resultados'),list) or {x.get('pregunta_id') for x in r['resultados']}!=set(ids): raise SystemExit('Respuesta invalida: no se guarda')
OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8'); print('OK',OUT)
