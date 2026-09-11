from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / 'db' / 'oposiciones.sqlite3'
REGISTROS = ROOT / 'registros'


def norm_art(v: str | None) -> str:
    s = (v or '').strip().lower()
    if not s:
        return ''
    s = re.sub(r'^art(?:ículo|iculo)?\.?\s*', '', s, flags=re.I)
    # Prefer the first article-like number/number-bis etc.
    m = re.search(r'\b(\d+(?:\s*(?:bis|ter|quater))?)\b', s, flags=re.I)
    if m:
        return re.sub(r'\s+', '', m.group(1).lower())
    return re.sub(r'\s+', '', s)


def options_dict(r: sqlite3.Row) -> dict[str, str]:
    return {k: (r[f'opcion_{k.lower()}'] or '').strip() for k in 'ABCD'}


def construir_indice_textos(con: sqlite3.Connection):
    idx = {}
    def add(norma_id, articulo, origen, afid, id_fuente, texto):
        a = norm_art(articulo)
        if not norma_id or not a or not (texto or '').strip():
            return
        idx.setdefault((int(norma_id), a), []).append((origen, afid, id_fuente, texto))

    for x in con.execute("""
        SELECT tr.norma_id, tr.articulo_solicitado, af.id, af.id_boe, af.articulo_boe, af.texto
        FROM temario_referencias tr
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE tr.norma_id IS NOT NULL AND tr.articulo_fuente_id IS NOT NULL
    """):
        add(x['norma_id'], x['articulo_boe'], 'temario_referencias', x['id'], x['id_boe'], x['texto'])
        add(x['norma_id'], x['articulo_solicitado'], 'temario_referencias', x['id'], x['id_boe'], x['texto'])

    for x in con.execute("""
        SELECT nf.norma_id, af.id, af.id_boe, af.articulo_boe, af.texto
        FROM norma_fuentes nf
        JOIN articulos_fuente af ON af.id_boe=nf.id_fuente
        WHERE nf.norma_id IS NOT NULL
    """):
        add(x['norma_id'], x['articulo_boe'], 'norma_fuentes', x['id'], x['id_boe'], x['texto'])

    # deduplicate each key
    for k, vals in list(idx.items()):
        seen=set(); out=[]
        for v in vals:
            key=(v[1], v[3])
            if key not in seen:
                seen.add(key); out.append(v)
        idx[k]=out
    return idx


def resolver_textos(indice, r: sqlite3.Row) -> list[tuple[str, int, str, str]]:
    norma_id=r['norma_id_normalizada']
    art=norm_art(r['articulo_normalizado'] or r['articulo'])
    if not norma_id or not art:
        return []
    return indice.get((int(norma_id), art), [])

def cobertura(db: Path, exportar: bool = True) -> tuple[Counter, list[dict]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    indice = construir_indice_textos(con)
    filas = con.execute('''
        SELECT id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d,respuesta_correcta,
               tipo_fuente,nombre_norma,nombre_norma_normalizado,norma_id_normalizada,
               articulo,articulo_normalizado,estado_vigencia
        FROM lote_preguntas
        WHERE tipo_clasificacion='JURIDICA'
        ORDER BY id
    ''').fetchall()

    counts = Counter()
    detalles = []
    for r in filas:
        counts['JURIDICAS'] += 1
        if not r['norma_id_normalizada']:
            estado = 'SIN_NORMA_NORMALIZADA'
        elif not norm_art(r['articulo_normalizado'] or r['articulo']):
            estado = 'SIN_ARTICULO_NORMALIZADO'
        else:
            textos = resolver_textos(indice, r)
            if not textos:
                estado = 'SIN_TEXTO_OFICIAL_ENLAZADO'
            elif len({t[3] for t in textos}) > 1:
                estado = 'MULTIPLES_TEXTOS_OFICIALES'
            else:
                estado = 'VERIFICABLE'
        counts[estado] += 1
        detalles.append({
            'id': r['id'], 'tipo_fuente': r['tipo_fuente'], 'estado': estado,
            'norma_id': r['norma_id_normalizada'],
            'norma': r['nombre_norma_normalizado'] or r['nombre_norma'],
            'articulo': r['articulo_normalizado'] or r['articulo'],
            'respuesta_correcta': r['respuesta_correcta'],
        })
    con.close()

    if exportar:
        REGISTROS.mkdir(parents=True, exist_ok=True)
        out = REGISTROS / 'auditoria_respuestas_juridicas_cobertura.csv'
        with out.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=detalles[0].keys() if detalles else ['id'])
            w.writeheader(); w.writerows(detalles)
        counts['CSV'] = str(out)
    return counts, detalles


def auditar_api(db: Path, limite: int | None, fuente: str | None, modelo: str) -> Path:
    try:
        from openai_api import seleccionar_fragmento_json
    except Exception as e:
        raise RuntimeError('No se pudo importar scripts/openai_api.py. Coloque este script dentro de scripts/.') from e

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    indice = construir_indice_textos(con)
    q = '''
        SELECT id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d,respuesta_correcta,
               tipo_fuente,nombre_norma,nombre_norma_normalizado,norma_id_normalizada,
               articulo,articulo_normalizado
        FROM lote_preguntas
        WHERE tipo_clasificacion='JURIDICA'
    '''
    params = []
    if fuente:
        q += ' AND tipo_fuente=?'; params.append(fuente)
    q += ' ORDER BY id'
    filas = con.execute(q, params).fetchall()

    REGISTROS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = REGISTROS / f'auditoria_respuestas_juridicas_{stamp}.csv'
    campos = ['id','tipo_fuente','norma','articulo','respuesta_almacenada','respuesta_auditor','resultado','confianza','fundamento','opcion_a','opcion_b','opcion_c','opcion_d']

    procesadas = 0
    with out.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=campos); w.writeheader()
        for r in filas:
            textos = resolver_textos(indice, r)
            textos_unicos = list(dict.fromkeys(t[3].strip() for t in textos if t[3].strip()))
            if len(textos_unicos) != 1:
                continue
            if limite is not None and procesadas >= limite:
                break
            procesadas += 1
            norma = r['nombre_norma_normalizado'] or r['nombre_norma'] or ''
            art = r['articulo_normalizado'] or r['articulo'] or ''
            opts = options_dict(r)
            prompt = f'''Eres un auditor jurídico estricto. Debes resolver una pregunta tipo test EXCLUSIVAMENTE con el texto oficial proporcionado. No uses memoria, conocimiento general ni fuentes externas.

NORMA: {norma}
ARTÍCULO: {art}
TEXTO OFICIAL:
{textos_unicos[0]}

PREGUNTA:
{r['enunciado']}
A) {opts['A']}
B) {opts['B']}
C) {opts['C']}
D) {opts['D']}

RESPUESTA ALMACENADA EN BD: {r['respuesta_correcta']}

Reglas:
1. Determina primero, sin mirar la respuesta almacenada como autoridad, si el texto oficial permite resolver inequívocamente la pregunta.
2. Ten en cuenta si el enunciado pide CORRECTA o INCORRECTA.
3. Evalúa las cuatro opciones contra el texto.
4. Si una única opción resulta inequívoca, devuelve su letra.
5. Si el artículo aislado no basta, hay ambigüedad, varias opciones defendibles o ninguna, respuesta_auditor debe ser null y resultado debe ser NO_VERIFICABLE.
6. Si sí se puede resolver y coincide con la almacenada: VALIDADA.
7. Si sí se puede resolver y no coincide: CONFLICTO_RESPUESTA.
8. No intentes salvar una pregunta defectuosa.

Devuelve SOLO JSON con estas claves:
{{"respuesta_auditor":"A|B|C|D|null","resultado":"VALIDADA|CONFLICTO_RESPUESTA|NO_VERIFICABLE","confianza":"ALTA|MEDIA|BAJA","fundamento":"explicación breve basada solo en el texto"}}
'''
            try:
                res = seleccionar_fragmento_json(prompt=prompt, modelo=modelo, operacion='auditoria_respuesta_juridica', max_output_tokens=700)
                ra = res.get('respuesta_auditor')
                if isinstance(ra, str) and ra.lower() == 'null': ra = None
                resultado = str(res.get('resultado','')).strip().upper()
                conf = str(res.get('confianza','')).strip().upper()
                fund = str(res.get('fundamento','')).strip()
            except Exception as e:
                ra=None; resultado='ERROR_API'; conf=''; fund=str(e)
            w.writerow({
                'id':r['id'],'tipo_fuente':r['tipo_fuente'],'norma':norma,'articulo':art,
                'respuesta_almacenada':r['respuesta_correcta'],'respuesta_auditor':ra or '',
                'resultado':resultado,'confianza':conf,'fundamento':fund,
                'opcion_a':opts['A'],'opcion_b':opts['B'],'opcion_c':opts['C'],'opcion_d':opts['D']
            }); f.flush()
    con.close()
    print(f'Preguntas auditadas por IA............. {procesadas}')
    print(f'CSV auditoría.......................... {out}')
    print('La base de datos NO ha sido modificada.')
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db', type=Path, default=DB_DEFAULT)
    ap.add_argument('--auditar', action='store_true', help='Ejecuta auditoría semántica por API. Sin este parámetro solo mide cobertura.')
    ap.add_argument('--limite', type=int, default=None)
    ap.add_argument('--fuente', default=None)
    ap.add_argument('--modelo', default='gpt-5.4-nano')
    args=ap.parse_args()
    if not args.db.is_file():
        raise SystemExit(f'No existe la base: {args.db}')

    print('='*78)
    print('AUDITORÍA DE RESPUESTAS JURÍDICAS - SOLO LECTURA')
    print('='*78)
    print(f'Base: {args.db}')
    c,_=cobertura(args.db, exportar=True)
    print(f"Preguntas jurídicas.................... {c['JURIDICAS']}")
    print(f"Verificables con texto oficial......... {c['VERIFICABLE']}")
    print(f"Sin norma normalizada.................. {c['SIN_NORMA_NORMALIZADA']}")
    print(f"Sin artículo normalizado............... {c['SIN_ARTICULO_NORMALIZADO']}")
    print(f"Sin texto oficial enlazado............. {c['SIN_TEXTO_OFICIAL_ENLAZADO']}")
    print(f"Múltiples textos oficiales............. {c['MULTIPLES_TEXTOS_OFICIALES']}")
    print(f"CSV cobertura.......................... {c['CSV']}")
    print()
    if not args.auditar:
        print('SOLO COBERTURA: no se han realizado llamadas IA y la BD NO ha sido modificada.')
        print('Para una prueba semántica: añada --auditar --limite 20')
        return
    auditar_api(args.db,args.limite,args.fuente,args.modelo)

if __name__=='__main__':
    main()
