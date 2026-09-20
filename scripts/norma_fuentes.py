"""
Identidad documental de normas para TuCoach.

Mantiene la relaciÃ³n persistente entre un documento normativo real
(BOE/DOGV/DOUE/PDF local) y la norma canÃ³nica del catÃ¡logo `normas`.

Principios:
- data driven: usa primero la identidad documental ya resuelta en articulos_fuente;
- idempotente: UNIQUE(id_fuente), INSERT OR IGNORE / actualizaciones solo si faltan;
- conservador: si una fuente entra en conflicto con varias normas, no elige;
- no modifica lote_preguntas.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from normalizador_normas import normalizar_norma


@dataclass(frozen=True)
class MetadatosFuente:
    id_fuente: str
    titulo: str | None
    departamento: str | None


def asegurar_esquema(con: sqlite3.Connection) -> bool:
    existia = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='norma_fuentes'"
    ).fetchone() is not None
    con.execute("""
        CREATE TABLE IF NOT EXISTS norma_fuentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            norma_id INTEGER NOT NULL,
            id_fuente TEXT NOT NULL UNIQUE,
            titulo_fuente TEXT,
            departamento TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (norma_id) REFERENCES normas(id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_norma_fuentes_norma_id ON norma_fuentes(norma_id)")
    return not existia


def sembrar_desde_enlaces_existentes(con: sqlite3.Connection) -> tuple[int, list[str]]:
    filas = con.execute("""
        SELECT af.id_boe, GROUP_CONCAT(DISTINCT tr.norma_id), COUNT(DISTINCT tr.norma_id)
        FROM temario_referencias tr
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE tr.norma_id IS NOT NULL AND af.id_boe IS NOT NULL AND TRIM(af.id_boe)<>''
        GROUP BY af.id_boe
    """).fetchall()
    creadas=0; conflictos=[]
    for id_fuente,norma_ids,cuantos in filas:
        if int(cuantos)!=1:
            conflictos.append(f"{id_fuente}: norma_id={norma_ids}"); continue
        norma_id=int(str(norma_ids).split(',')[0])
        existente=con.execute("SELECT norma_id FROM norma_fuentes WHERE id_fuente=?",(id_fuente,)).fetchone()
        if existente is not None:
            if int(existente[0])!=norma_id:
                conflictos.append(f"{id_fuente}: norma_fuentes={existente[0]} vs temario={norma_id}")
            continue
        con.execute("INSERT INTO norma_fuentes(norma_id,id_fuente) VALUES (?,?)",(norma_id,id_fuente)); creadas+=1
    return creadas,conflictos


def _metadatos_pdf_local(id_fuente: str) -> MetadatosFuente | None:
    try:
        from pdf_normas import buscar_norma_por_id
        pdf=buscar_norma_por_id(id_fuente)
        return MetadatosFuente(pdf.id_fuente,pdf.titulo,pdf.departamento)
    except Exception:
        return None


def _metadatos_boe(id_fuente: str) -> MetadatosFuente | None:
    if not id_fuente.upper().startswith('BOE-A-'): return None
    try:
        from boe_api import campos_metadatos
        titulo,departamento,_fecha=campos_metadatos(id_fuente)
        if titulo: return MetadatosFuente(id_fuente,titulo,departamento or None)
    except Exception: return None
    return None


def obtener_metadatos_fuente(id_fuente: str) -> MetadatosFuente:
    id_fuente=(id_fuente or '').strip()
    local=_metadatos_pdf_local(id_fuente)
    if local is not None: return local
    boe=_metadatos_boe(id_fuente)
    if boe is not None: return boe
    return MetadatosFuente(id_fuente,None,None)

def _extraer_identidad_gen(id_fuente: str, nombres: list[str]) -> str | None:
    """
    Identidad interna para documentos GEN.

    Un LOCAL-PDF-GEN-* se trata como una norma interna del temario.
    Solo se acepta cuando todas las referencias de esa fuente identifican
    inequÃ­vocamente el mismo GEN.
    """
    id_fuente = (id_fuente or "").strip()
    if not id_fuente.upper().startswith("LOCAL-PDF-GEN-"):
        return None

    identidades = {
        normalizar_norma(nombre)
        for nombre in nombres
        if str(nombre or "").strip()
    }

    identidades = {
        identidad
        for identidad in identidades
        if identidad.startswith("gen-")
    }

    if len(identidades) != 1:
        return None

    return next(iter(identidades))

def _extraer_identidad_normativa(texto: str) -> str | None:
    """Devuelve la identidad jur?dica normalizada completa de la primera norma reconocible."""
    n = normalizar_norma(texto)
    if not n:
        return None

    especiales = (
        "constitucion espanola",
        "tratado de funcionamiento de la union europea",
        "tratado de la union europea",
        "reglamento de les corts valencianes",
    )
    if any(n.startswith(clave) for clave in especiales):
        return n

    patrones = (
        r"^ley organica\s+\d+/\d{4}\b",
        r"^ley\s+\d+/\d{4}\b",
        r"^real decreto legislativo\s+\d+/\d{4}\b",
        r"^real decreto\s+\d+/\d{4}\b",
        r"^decreto legislativo\s+\d+/\d{4}\b",
        r"^decreto ley\s+\d+/\d{4}\b",
        r"^decreto\s+\d+/\d{4}\b",
        r"^orden\s+\d+/\d{4}\b",
        r"^directiva(?: ue)?\s+\d{4}/\d+\b",
        r"^reglamento(?: ue euratom| ue)?\s+\d{4}/\d+\b",
    )

    return n if any(re.search(patron, n) for patron in patrones) else None


def _clave_estructurada(nombre: str) -> bool:
    return _extraer_identidad_normativa(nombre) is not None


def _catalogo_por_clave(con: sqlite3.Connection) -> dict[str,int]:
    return {str(clave):int(nid) for nid,clave in con.execute('SELECT id,clave_normalizada FROM normas')}


def resolver_o_crear_fuente(con: sqlite3.Connection,id_fuente: str,nombres_referencia: list[str]) -> tuple[int|None,str]:
    id_fuente=(id_fuente or '').strip()
    if not id_fuente: return None,'PENDIENTE'
    existente=con.execute('SELECT norma_id FROM norma_fuentes WHERE id_fuente=?',(id_fuente,)).fetchone()
    if existente is not None: return int(existente[0]),'YA_ENLAZADA'

    catalogo=_catalogo_por_clave(con)
    nombres=[str(x).strip() for x in nombres_referencia if str(x or '').strip()]
    meta=obtener_metadatos_fuente(id_fuente)
    identidades=[]

    for texto in ([meta.titulo] if meta.titulo else [])+nombres:
        identidad=_extraer_identidad_normativa(texto)
        if identidad and identidad not in identidades: identidades.append(identidad)

    identidad_gen = _extraer_identidad_gen(id_fuente, nombres)
    if identidad_gen and identidad_gen not in identidades:
        identidades.append(identidad_gen)

    candidatos={catalogo[i] for i in identidades if i in catalogo}

    if len(candidatos)>1: return None,'CONFLICTO'
    norma_id=next(iter(candidatos),None); estado='ENLAZADA_EXISTENTE'

    if norma_id is None:
        if len(set(identidades))!=1: return None,'PENDIENTE'
        clave=identidades[0]
        fila=con.execute('SELECT id FROM normas WHERE clave_normalizada=?',(clave,)).fetchone()
        if fila is None:
            cur=con.execute('INSERT INTO normas(nombre_canonico,clave_normalizada) VALUES (?,?)',(clave.upper(),clave))
            norma_id=int(cur.lastrowid); estado='CREADA'
        else:
            norma_id=int(fila[0]); estado='ENLAZADA_EXISTENTE'

    con.execute("""
        INSERT INTO norma_fuentes(norma_id,id_fuente,titulo_fuente,departamento) VALUES (?,?,?,?)
        ON CONFLICT(id_fuente) DO UPDATE SET
          titulo_fuente=COALESCE(norma_fuentes.titulo_fuente,excluded.titulo_fuente),
          departamento=COALESCE(norma_fuentes.departamento,excluded.departamento),updated_at=CURRENT_TIMESTAMP
    """,(norma_id,id_fuente,meta.titulo,meta.departamento))
    return norma_id,estado


def completar_fuentes_temario(con: sqlite3.Connection) -> dict[str,int]:
    filas=con.execute("""
        SELECT id_boe,GROUP_CONCAT(nombre_norma_normalizada,'|||') FROM (
          SELECT DISTINCT af.id_boe,tr.nombre_norma_normalizada
          FROM temario_referencias tr JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
          WHERE af.id_boe IS NOT NULL AND TRIM(af.id_boe)<>''
        ) GROUP BY id_boe ORDER BY id_boe
    """).fetchall()
    resumen={'fuentes':0,'ya_enlazadas':0,'enlazadas_existentes':0,'normas_creadas':0,'pendientes':0,'conflictos':0}
    for id_fuente,nombres_concat in filas:
        resumen['fuentes']+=1
        nombres=str(nombres_concat or '').split('|||') if nombres_concat else []
        _nid,estado=resolver_o_crear_fuente(con,id_fuente,nombres)
        if estado=='YA_ENLAZADA': resumen['ya_enlazadas']+=1
        elif estado=='ENLAZADA_EXISTENTE': resumen['enlazadas_existentes']+=1
        elif estado=='CREADA': resumen['normas_creadas']+=1
        elif estado=='CONFLICTO': resumen['conflictos']+=1
        else: resumen['pendientes']+=1
    return resumen
