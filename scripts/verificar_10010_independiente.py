import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; sys.path.insert(0,str(ROOT/'scripts'))
from openai_api import seleccionar_fragmento_json
base=ROOT/'outputs'/'auditoria_control_tests_v1'
e=json.loads((base/'evidencia_corregida_10010.json').read_text(encoding='utf8'))
prompt="""Devuelve solo JSON {"pregunta_id":10010,"opcion_correcta":"A|B|C|D|null","certeza":"UNICA|NO_DETERMINABLE","motivo":"maximo 25 palabras"}. Determina solo la opción que responde al enunciado según el texto oficial; no uses ninguna respuesta almacenada.
"""+json.dumps(e,ensure_ascii=False)
r=seleccionar_fragmento_json(prompt,modelo='gpt-5.4-nano',operacion='segunda_verificacion_10010',max_output_tokens=500)
if r.get('pregunta_id')!=10010 or r.get('opcion_correcta') not in {'A','B','C','D',None} or r.get('certeza') not in {'UNICA','NO_DETERMINABLE'}: raise SystemExit('Respuesta inválida: no se guarda')
out=base/'segunda_verificacion_10010.json';out.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');print('OK',out)
