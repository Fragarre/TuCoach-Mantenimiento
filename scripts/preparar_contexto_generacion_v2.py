"""Prepara contexto V2 trazable para generación de preguntas. SOLO LECTURA.

No llama a IA y no modifica BD, lote_preguntas ni bancos. Solo exporta unidades
pseudonormativas aceptadas que tengan síntesis y respaldo PERTINENTE/VERIFICADO.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SALIDA_DEFECTO = RAIZ / "auditorias" / "constructor_v2" / "generacion"
ESTADOS_FINALES = {"RESUELTO", "ACEPTADO", "ACEPTADA", "APROBADO_PARA_PERSISTENCIA", "ACEPTABLE_FORMALMENTE"}
TABLAS = {
    "convocatorias", "temarios", "temario_temas", "temario_pseudonormas",
    "temario_pseudoarticulos", "temario_pseudoarticulo_proposiciones",
    "temario_pseudoarticulo_respaldos", "fuentes_conocimiento", "articulos_fuente",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def tablas(con: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def seleccionar(con: sqlite3.Connection, codigo: str):
    conv = con.execute("SELECT id,codigo FROM convocatorias WHERE codigo=?", (codigo,)).fetchone()
    if conv is None:
        raise RuntimeError(f"No existe convocatoria {codigo}.")
    temarios = con.execute("SELECT id,nombre FROM temarios WHERE convocatoria_id=? ORDER BY id", (conv[0],)).fetchall()
    if len(temarios) != 1:
        raise RuntimeError(f"La convocatoria debe tener exactamente un temario; encontrados={len(temarios)}.")
    return conv, temarios[0]


def respaldos(con: sqlite3.Connection, pseudoarticulo_id: int) -> list[dict]:
    filas = con.execute(
        """
        SELECT r.id, r.proposicion_id, r.articulo_fuente_id, r.fuente_conocimiento_id,
               r.evidencia_texto, r.evidencia_localizador,
               p.codigo AS proposicion_codigo, p.texto AS proposicion_texto,
               af.id_boe AS articulo_fuente_documento, af.articulo_boe AS articulo_fuente_articulo,
               af.titulo_bloque AS articulo_fuente_titulo, af.texto AS articulo_fuente_texto,
               fc.tipo_fuente, fc.organismo, fc.nombre AS fuente_nombre,
               fc.identificador_oficial, fc.localizador_oficial
        FROM temario_pseudoarticulo_respaldos r
        LEFT JOIN temario_pseudoarticulo_proposiciones p ON p.id=r.proposicion_id
        LEFT JOIN articulos_fuente af ON af.id=r.articulo_fuente_id
        LEFT JOIN fuentes_conocimiento fc ON fc.id=r.fuente_conocimiento_id
        WHERE r.pseudoarticulo_id=?
          AND r.pertinencia='PERTINENTE'
          AND r.estado='VERIFICADO'
        ORDER BY r.id
        """,
        (pseudoarticulo_id,),
    ).fetchall()
    resultado = []
    for r in filas:
        fuente = (
            {
                "clase": "ARTICULO_FUENTE",
                "id": r[2],
                "documento": r[8],
                "articulo": r[9],
                "titulo": r[10],
                "texto": r[11],
            }
            if r[2] is not None else
            {
                "clase": "FUENTE_CONOCIMIENTO",
                "id": r[3],
                "tipo": r[12],
                "organismo": r[13],
                "nombre": r[14],
                "identificador_oficial": r[15],
                "localizador_oficial": r[16],
            }
        )
        resultado.append({
            "respaldo_id": r[0],
            "proposicion_id": r[1],
            "proposicion_codigo": r[6],
            "proposicion_texto": r[7],
            "fuente": fuente,
            "evidencia_texto": r[4],
            "evidencia_localizador": r[5],
        })
    return resultado


def construir(con: sqlite3.Connection, temario_id: int) -> list[dict]:
    filas = con.execute(
        """
        SELECT tt.id,tt.parte,tt.numero_tema,tt.titulo,
               pn.id,pn.codigo,pn.estado,
               pa.id,pa.codigo,pa.orden,pa.texto_temario,pa.tipo_contenido,pa.sintesis,pa.estado
        FROM temario_pseudoarticulos pa
        JOIN temario_pseudonormas pn ON pn.id=pa.pseudonorma_id
        JOIN temario_temas tt ON tt.id=pn.tema_id
        WHERE tt.temario_id=?
        ORDER BY tt.parte,tt.numero_tema,pa.orden,pa.id
        """,
        (temario_id,),
    ).fetchall()
    salida = []
    for r in filas:
        if str(r[6] or "").strip() not in ESTADOS_FINALES:
            continue
        if str(r[13] or "").strip() not in ESTADOS_FINALES:
            continue
        sintesis = str(r[12] or "").strip()
        if not sintesis:
            continue
        evidencias = respaldos(con, int(r[7]))
        if not evidencias:
            continue
        proposiciones = [dict(x) for x in con.execute(
            "SELECT id,codigo,texto,esencial,tipo,estado FROM temario_pseudoarticulo_proposiciones WHERE pseudoarticulo_id=? ORDER BY id",
            (int(r[7]),),
        ).fetchall()]
        salida.append({
            "tipo_registro": "CONTEXTO_GENERACION_PSEUDO_V2",
            "tipo_contexto_propuesto": "PSEUDO",
            "tema_id": int(r[0]),
            "parte": str(r[1]),
            "numero_tema": int(r[2]),
            "texto_oficial_tema": str(r[3] or ""),
            "pseudonorma_id": int(r[4]),
            "pseudonorma": str(r[5]),
            "pseudoarticulo_id": int(r[7]),
            "pseudoarticulo": str(r[8]),
            "orden": int(r[9]),
            "texto_temario": str(r[10]),
            "tipo_contenido": str(r[11]),
            "sintesis": sintesis,
            "proposiciones": proposiciones,
            "respaldos_verificados": evidencias,
            "regla_mixta": "NO_INFERIR; requiere temario_referencia_id explícito y del mismo tema",
        })
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepara contexto V2 de generación sin llamar IA ni escribir BD.")
    ap.add_argument("--codigo", required=True)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--salida")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD: {db}")
    antes = sha256(db)
    uri = db.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        faltan = sorted(TABLAS - tablas(con))
        if faltan:
            raise RuntimeError("Faltan tablas requeridas: " + ", ".join(faltan))
        conv, temario = seleccionar(con, args.codigo)
        unidades = construir(con, int(temario[0]))

    salida = Path(args.salida).expanduser().resolve() if args.salida else SALIDA_DEFECTO / f"{args.codigo}_contextos_v2.jsonl"
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8") as f:
        for u in unidades:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")

    if sha256(db) != antes:
        raise RuntimeError("La BD cambió durante una operación SOLO LECTURA.")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - CONTEXTO GENERACION - SOLO LECTURA")
    print("=" * 78)
    print(f"Convocatoria..................... {conv[1]}")
    print(f"Temario.......................... {temario[0]} | {temario[1]}")
    print(f"Unidades V2 aptas................ {len(unidades)}")
    print(f"Salida........................... {salida}")
    print("IA............................... NO EJECUTADA")
    print("lote_preguntas................... SIN CAMBIOS")
    print("Banco............................ SIN CAMBIOS")
    print("BD............................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
