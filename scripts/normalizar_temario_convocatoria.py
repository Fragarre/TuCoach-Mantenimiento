"""
Normaliza la identidad normativa de una convocatoria sin tocar lote_preguntas.

Contrato:
- trabaja solo sobre la convocatoria indicada;
- reutiliza `normas` y `norma_fuentes` existentes;
- crea nuevas identidades solo cuando la fuente/nombre contiene una identidad
  estructurada inequívoca;
- ante conflicto o pendiente, hace rollback y termina con error;
- actualiza únicamente temario_referencias.norma_id de esa convocatoria;
- es idempotente: una segunda ejecución correcta no crea ni actualiza nada.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

from normalizador_normas import normalizar_norma
from norma_fuentes import asegurar_esquema, resolver_o_crear_fuente

RUTA_BD = Path(__file__).resolve().parent.parent / 'db' / 'oposiciones.sqlite3'


def referencias_convocatoria(con: sqlite3.Connection, codigo: str):
    return con.execute(
        """
        SELECT tr.id,
               tr.nombre_norma_normalizada,
               tr.norma_id,
               af.id_boe
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        JOIN temarios t ON t.id=tt.temario_id
        JOIN convocatorias cv ON cv.id=t.convocatoria_id
        LEFT JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE cv.codigo=?
          AND tr.nombre_norma_normalizada IS NOT NULL
          AND TRIM(tr.nombre_norma_normalizada)<>''
        ORDER BY tr.id
        """,
        (codigo,),
    ).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--codigo', required=True)
    ap.add_argument('--db', type=Path, default=RUTA_BD)
    args = ap.parse_args()

    if not args.db.exists():
        raise FileNotFoundError(f'No existe la base de datos: {args.db}')

    con = sqlite3.connect(args.db)
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN')
        asegurar_esquema(con)

        refs = referencias_convocatoria(con, args.codigo)
        if not refs:
            raise RuntimeError(f'No hay referencias para la convocatoria {args.codigo}.')

        normas_antes = con.execute('SELECT COUNT(*) FROM normas').fetchone()[0]
        fuentes_antes = con.execute('SELECT COUNT(*) FROM norma_fuentes').fetchone()[0]

        # Agrupar nombres por fuente, pero solo dentro de esta convocatoria.
        fuentes: dict[str, list[str]] = {}
        for _rid, nombre, _actual, id_fuente in refs:
            if id_fuente and str(id_fuente).strip():
                fuentes.setdefault(str(id_fuente).strip(), [])
                if nombre and nombre not in fuentes[str(id_fuente).strip()]:
                    fuentes[str(id_fuente).strip()].append(str(nombre))

        conflictos = []
        pendientes = []
        for id_fuente, nombres in sorted(fuentes.items()):
            _nid, estado = resolver_o_crear_fuente(con, id_fuente, nombres)
            if estado == 'CONFLICTO':
                conflictos.append((id_fuente, nombres))
            elif estado == 'PENDIENTE':
                pendientes.append((id_fuente, nombres))

        if conflictos or pendientes:
            detalle = []
            for f, nombres in conflictos:
                detalle.append(f'CONFLICTO {f}: {nombres}')
            for f, nombres in pendientes:
                detalle.append(f'PENDIENTE {f}: {nombres}')
            raise RuntimeError(
                f'Normalización bloqueada para {args.codigo}: '
                f'{len(conflictos)} conflictos, {len(pendientes)} pendientes.\n- '
                + '\n- '.join(detalle)
            )

        catalogo = {
            str(clave): int(nid)
            for nid, clave in con.execute('SELECT id,clave_normalizada FROM normas')
        }

        actualizadas = 0
        sin_correspondencia = []
        for rid, nombre, actual, id_fuente in refs:
            nid = None
            if id_fuente and str(id_fuente).strip():
                fila = con.execute(
                    'SELECT norma_id FROM norma_fuentes WHERE id_fuente=?',
                    (str(id_fuente).strip(),),
                ).fetchone()
                if fila is not None:
                    nid = int(fila[0])
            if nid is None:
                nid = catalogo.get(normalizar_norma(str(nombre)))
            if nid is None:
                sin_correspondencia.append((rid, nombre, id_fuente))
                continue
            if actual != nid:
                con.execute('UPDATE temario_referencias SET norma_id=? WHERE id=?', (nid, rid))
                actualizadas += 1

        if sin_correspondencia:
            raise RuntimeError(
                f'Quedan {len(sin_correspondencia)} referencias sin norma_id en {args.codigo}: '
                + repr(sin_correspondencia[:20])
            )

        nulos = con.execute(
            """
            SELECT COUNT(*)
            FROM temario_referencias tr
            JOIN temario_temas tt ON tt.id=tr.tema_id
            JOIN temarios t ON t.id=tt.temario_id
            JOIN convocatorias cv ON cv.id=t.convocatoria_id
            WHERE cv.codigo=? AND tr.norma_id IS NULL
            """,
            (args.codigo,),
        ).fetchone()[0]
        if nulos:
            raise RuntimeError(f'Postcondición incumplida: {nulos} referencias con norma_id NULL.')

        normas_despues = con.execute('SELECT COUNT(*) FROM normas').fetchone()[0]
        fuentes_despues = con.execute('SELECT COUNT(*) FROM norma_fuentes').fetchone()[0]
        con.commit()

        print(f'Normalización de temario: {args.codigo}')
        print(f'Referencias revisadas:      {len(refs)}')
        print(f'Normas nuevas:              {normas_despues - normas_antes}')
        print(f'Fuentes nuevas identificadas: {fuentes_despues - fuentes_antes}')
        print(f'Referencias actualizadas:   {actualizadas}')
        print(f'Referencias sin norma_id:   {nulos}')
        print('RESULTADO: OK')
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == '__main__':
    main()
