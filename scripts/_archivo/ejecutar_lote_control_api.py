import argparse, json, sys
import time
import re
import unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; sys.path.insert(0,str(ROOT/'scripts'))
from openai_api import seleccionar_fragmento_json
p=argparse.ArgumentParser()
p.add_argument('inicio',type=int)
p.add_argument('--tamano',type=int,default=5,help='Preguntas por llamada; máximo recomendado: 5')
p.add_argument('--serie',type=int,default=0,help='Total de preguntas consecutivas; se divide en llamadas de --tamano')
p.add_argument('--lote',type=int,default=1,help='Número del fichero lote_XX_polaridad.json')
p.add_argument('--revision',default='v3',help='Etiqueta del resultado para no mezclar métodos')
a=p.parse_args()

def normalizar(texto):
    texto = unicodedata.normalize('NFKD', texto or '')
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r'\s+', ' ', texto)

def regla_enunciado(pregunta):
    texto = normalizar(pregunta.get('enunciado'))
    patrones_falsa = (
        r'\bincorrect[ao]\b', r'\bno se corresponde\b',
        r'\bno es una?\b', r'\bno es verdader[ao]\b',
        r'\bexcepto\b', r'\bmenos\b',
    )
    return 'ELEGIR_OPCION_FALSA' if any(re.search(p, texto) for p in patrones_falsa) else 'ELEGIR_OPCION_VERDADERA'

def validar_resultado(candidato, esperados):
    resultados = candidato.get('resultados')
    if not isinstance(resultados, list):
        return 'Falta la lista resultados.'
    if {x.get('pregunta_id') for x in resultados} != esperados:
        return 'Los identificadores de pregunta no coinciden con el bloque solicitado.'
    for resultado in resultados:
        if not {'pregunta_id', 'opciones', 'opcion_evidencia', 'certeza', 'motivo'} <= set(resultado):
            return f"Faltan campos de resultado principal en pregunta {resultado.get('pregunta_id')}."
        if resultado['certeza'] not in {'UNICA', 'NO_DETERMINABLE'}:
            return f"Certeza no válida en pregunta {resultado['pregunta_id']}."
        if resultado['opcion_evidencia'] not in {'A', 'B', 'C', 'D', None}:
            return f"Opción de evidencia no válida en pregunta {resultado['pregunta_id']}."
        opciones = resultado['opciones']
        if not isinstance(opciones, list) or {x.get('letra') for x in opciones} != {'A', 'B', 'C', 'D'}:
            return f"No hay una valoración A-D completa en pregunta {resultado['pregunta_id']}."
        if any(x.get('veredicto') not in {'VERDADERA', 'FALSA', 'NO_DETERMINABLE'} for x in opciones):
            return f"Veredicto de opción no válido en pregunta {resultado['pregunta_id']}."
    return None

base=ROOT/'outputs'/'auditoria_control_tests_v1'
entrada=base/f'lote_{a.lote:02d}_polaridad.json'
todas=json.loads(entrada.read_text(encoding='utf-8'))
final=min(a.inicio + (a.serie or a.tamano), len(todas))
for inicio in range(a.inicio, final, a.tamano):
    xs=[{**q, 'regla_enunciado': regla_enunciado(q)} for q in todas[inicio:min(inicio+a.tamano,final)]]
    out=base/f'resultado_control_lote_{a.lote:02d}_{inicio}_{inicio+len(xs)-1}_{a.revision}.json'
    if out.exists():
        print('YA_EXISTE',out)
        continue
    prompt='Devuelve JSON estricto. Cada objeto de resultados debe contener, en su nivel principal, pregunta_id, opciones, opcion_evidencia, certeza y motivo; nunca coloques opcion_evidencia, certeza o motivo dentro de opciones. Esquema: {"resultados":[{"pregunta_id":int,"opciones":[{"letra":"A|B|C|D","veredicto":"VERDADERA|FALSA|NO_DETERMINABLE","justificacion":"máximo 12 palabras, sin comillas"}],"opcion_evidencia":"A|B|C|D|null","certeza":"UNICA|NO_DETERMINABLE","motivo":"máximo 20 palabras, sin comillas"}]}. Usa solo evidencia y regla_enunciado. Primero evalúa las cuatro opciones completas: si una tiene varias condiciones, comprueba cada condición. «Todas las anteriores» será verdadera solo si todas A-C son verdaderas; «ninguna» será verdadera solo si todas A-C son falsas. Solo después identifica la única opción verdadera o falsa solicitada por regla_enunciado. Si hay dos opciones que responden al enunciado, o una no puede justificarse, certeza debe ser NO_DETERMINABLE. No clasifiques la respuesta almacenada.\n'+json.dumps(xs,ensure_ascii=False)
    r = None
    esperados = {x['pregunta_id'] for x in xs}
    for intento in range(1, 4):
        try:
            candidato=seleccionar_fragmento_json(prompt,modelo='gpt-5.4-nano',operacion='auditoria_control_muestra',max_output_tokens=1800)
            error_validacion = validar_resultado(candidato, esperados)
            if error_validacion:
                raise ValueError(error_validacion)
            r = candidato
            break
        except ValueError as exc:
            if intento == 3:
                raise SystemExit(f'No se guarda el bloque tras tres intentos: {exc}')
            print(f'REINTENTO_{intento}: {exc}')
            time.sleep(1)
    out.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    print('OK',out)
