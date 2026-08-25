"""
Enlaza lote_preguntas y temario_referencias con normas.

- lote_preguntas mantiene la correspondencia textual consolidada.
- temario_referencias usa prioritariamente la identidad documental
  articulo_fuente -> id_boe -> norma_fuentes -> norma_id.
  Solo si no existe esa identidad utiliza la clave textual como respaldo.
"""
from pathlib import Path
import sqlite3

from normalizador_normas import normalizar_norma
from norma_fuentes import asegurar_esquema

RUTA_BD = Path(__file__).resolve().parent.parent / 'db' / 'oposiciones.sqlite3'


def cargar_catalogo(conexion):
    return {clave: nid for nid, clave in conexion.execute('SELECT id,clave_normalizada FROM normas')}


def enlazar_lote_preguntas(conexion, catalogo):
    filas = conexion.execute(
        """SELECT id,nombre_norma_normalizado,norma_id_normalizada
           FROM lote_preguntas
           WHERE nombre_norma_normalizado IS NOT NULL
             AND TRIM(nombre_norma_normalizado)<>''"""
    ).fetchall()
    actualizadas=sin_correspondencia=0
    for pid,nombre,actual in filas:
        nid=catalogo.get(normalizar_norma(nombre))
        if nid is None:
            sin_correspondencia+=1; continue
        if actual==nid: continue
        conexion.execute('UPDATE lote_preguntas SET norma_id_normalizada=? WHERE id=?',(nid,pid))
        actualizadas+=1
    return actualizadas,sin_correspondencia


def enlazar_temario_referencias(conexion, catalogo):
    filas = conexion.execute(
        """
        SELECT tr.id, tr.nombre_norma_normalizada, tr.norma_id,
               nf.norma_id AS norma_id_fuente
        FROM temario_referencias tr
        LEFT JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        LEFT JOIN norma_fuentes nf ON nf.id_fuente=af.id_boe
        WHERE tr.nombre_norma_normalizada IS NOT NULL
          AND TRIM(tr.nombre_norma_normalizada)<>''
        """
    ).fetchall()
    actualizadas=sin_correspondencia=por_fuente=por_texto=0
    for rid,nombre,actual,nid_fuente in filas:
        if nid_fuente is not None:
            nid=int(nid_fuente); por_fuente+=1
        else:
            nid=catalogo.get(normalizar_norma(nombre)); por_texto+=1
        if nid is None:
            sin_correspondencia+=1; continue
        if actual==nid: continue
        conexion.execute('UPDATE temario_referencias SET norma_id=? WHERE id=?',(nid,rid))
        actualizadas+=1
    return actualizadas,sin_correspondencia,por_fuente,por_texto


def main():
    if not RUTA_BD.exists(): raise FileNotFoundError(f'No existe la base de datos: {RUTA_BD}')
    with sqlite3.connect(RUTA_BD) as conexion:
        asegurar_esquema(conexion)
        catalogo=cargar_catalogo(conexion)
        if not catalogo: raise RuntimeError('La tabla normas está vacía.')
        la,ls=enlazar_lote_preguntas(conexion,catalogo)
        ta,ts,pf,pt=enlazar_temario_referencias(conexion,catalogo)
        conexion.commit()
    print('Lote de preguntas')
    print('-----------------')
    print(f'Actualizadas:          {la}')
    print(f'Sin correspondencia:   {ls}')
    print()
    print('Temario referencias')
    print('-------------------')
    print(f'Actualizadas:          {ta}')
    print(f'Sin correspondencia:   {ts}')
    print(f'Resueltas por fuente:  {pf}')
    print(f'Resueltas por texto:   {pt}')


if __name__=='__main__': main()
