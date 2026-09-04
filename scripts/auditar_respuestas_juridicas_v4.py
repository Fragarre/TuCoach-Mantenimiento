from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
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


def auditar_api(db: Path, limite: int | None, fuente: str | None, modelo: str, reanudar: Path | None = None) -> Path:
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
    campos = ['id','tipo_fuente','norma','articulo','respuesta_almacenada','respuesta_auditor','resultado','confianza','fundamento','opcion_a','opcion_b','opcion_c','opcion_d']

    ids_previos: set[int] = set()
    if reanudar is not None:
        out = reanudar
        if not out.is_file():
            raise RuntimeError(f'No existe el CSV indicado para reanudar: {out}')
        with out.open('r', encoding='utf-8-sig', newline='') as fr:
            for row in csv.DictReader(fr):
                try:
                    ids_previos.add(int(row.get('id', '')))
                except (TypeError, ValueError):
                    pass
        modo = 'a'
        escribir_cabecera = out.stat().st_size == 0
        print(f'Reanudando CSV.......................... {out}')
        print(f'IDs ya auditados que se omitirán....... {len(ids_previos)}')
    else:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = REGISTROS / f'auditoria_respuestas_juridicas_{stamp}.csv'
        modo = 'w'
        escribir_cabecera = True

    verificables_pendientes = []
    for r in filas:
        textos = resolver_textos(indice, r)
        textos_unicos = list(dict.fromkeys(t[3].strip() for t in textos if t[3].strip()))
        if len(textos_unicos) == 1 and int(r['id']) not in ids_previos:
            verificables_pendientes.append(r)
    total_pendientes = len(verificables_pendientes) if limite is None else min(len(verificables_pendientes), limite)

    procesadas = 0
    inicio = time.monotonic()
    with out.open(modo, encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=campos)
        if escribir_cabecera:
            w.writeheader()
        for r in verificables_pendientes:
            if limite is not None and procesadas >= limite:
                break
            textos = resolver_textos(indice, r)
            textos_unicos = list(dict.fromkeys(t[3].strip() for t in textos if t[3].strip()))
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

Reglas:
1. Tu única fuente de verdad es el TEXTO OFICIAL incluido en este mensaje. Está prohibido usar memoria, conocimiento jurídico general o información externa.
2. No conoces ni debes intentar inferir cuál es la respuesta almacenada en la base de datos. Resuelve la pregunta de forma independiente.
3. Determina primero si el texto suministrado, por sí solo, permite identificar inequívocamente una única respuesta correcta.
4. Ten en cuenta si el enunciado pide CORRECTA o INCORRECTA y evalúa las cuatro opciones contra el texto.
5. Si el texto permite demostrar una única respuesta, devuelve verificable=true y su letra A/B/C/D.
6. Si necesitas otro artículo, otra disposición, una regla no reproducida, contexto adicional o conocimiento externo, devuelve verificable=false y respuesta_auditor=null.
7. Si hay ambigüedad, varias opciones defendibles, ninguna demostrable o el artículo aislado no basta, devuelve verificable=false y respuesta_auditor=null.
8. Si en tu fundamento afirmas que el texto no basta o no permite corroborar la respuesta, obligatoriamente verificable=false y respuesta_auditor=null.
9. No decidas ningún estado final de auditoría. Solo determina verificabilidad y, cuando sea posible, la respuesta jurídica demostrada por el texto.

Devuelve SOLO JSON con estas claves:
{{"verificable":true|false,"respuesta_auditor":"A|B|C|D|null","confianza":"ALTA|MEDIA|BAJA","fundamento":"explicación breve basada solo en el texto"}}
'''
            try:
                res = seleccionar_fragmento_json(prompt=prompt, modelo=modelo, operacion='auditoria_respuesta_juridica', max_output_tokens=700)
                verificable = res.get('verificable') is True
                ra = res.get('respuesta_auditor')
                if isinstance(ra, str):
                    ra = ra.strip().upper()
                    if ra == 'NULL' or ra not in {'A','B','C','D'}:
                        ra = None
                else:
                    ra = None

                # El estado final NO lo decide la IA. Es una regla determinista.
                if not verificable or ra is None:
                    ra = None
                    resultado = 'NO_VERIFICABLE'
                else:
                    almacenada = str(r['respuesta_correcta'] or '').strip().upper()
                    resultado = 'VALIDADA' if ra == almacenada else 'CONFLICTO_RESPUESTA'

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
            transcurrido = time.monotonic() - inicio
            media = transcurrido / procesadas if procesadas else 0.0
            restantes = max(total_pendientes - procesadas, 0)
            eta_s = media * restantes
            print(f'[AUDITORÍA {procesadas}/{total_pendientes} | {procesadas/total_pendientes*100 if total_pendientes else 100:.1f}% | ETA {eta_s/3600:.1f} h | ID {r["id"]}]', flush=True)
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
    ap.add_argument('--modelo', default='gpt-5.4-mini')
    ap.add_argument('--reanudar', type=Path, default=None, help='CSV parcial existente. Omite sus IDs y añade al mismo archivo.')
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
    auditar_api(args.db,args.limite,args.fuente,args.modelo,args.reanudar)

if __name__=='__main__':
    main()
