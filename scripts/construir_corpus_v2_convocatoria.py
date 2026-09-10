"""
Constructor Temario V2 - capa aditiva de corpus pseudonormativo.

NO modifica la BD ni los corpus consolidados existentes. Genera un artefacto JSONL
por convocatoria a partir de las tablas V2, y sólo incluye pseudoartículos que:
- tengan síntesis no vacía;
- pertenezcan a una pseudonorma en estado final admitido;
- tengan estado final admitido;
- tengan al menos un respaldo PERTINENTE y VERIFICADO.

Las convocatorias sin filas V2 producen corpus pseudonormativo vacío y siguen
utilizando exclusivamente el flujo consolidado actual.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SALIDA_DEFECTO = RAIZ / "auditorias" / "constructor_v2" / "corpus"

TABLAS_V2 = {
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_respaldos",
}
ESTADOS_FINAL_PSEUDONORMA = {
    "RESUELTO", "ACEPTADO", "ACEPTADA", "APROBADO_PARA_PERSISTENCIA", "ACEPTABLE_FORMALMENTE"
}
ESTADOS_FINAL_PSEUDOARTICULO = {
    "RESUELTO", "ACEPTADO", "ACEPTADA", "APROBADO_PARA_PERSISTENCIA", "ACEPTABLE_FORMALMENTE"
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def tablas(con: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def seleccionar_convocatoria(con: sqlite3.Connection, codigo: str):
    conv = con.execute("SELECT id,codigo FROM convocatorias WHERE codigo=?", (codigo,)).fetchone()
    if conv is None:
        raise RuntimeError(f"No existe convocatoria {codigo}.")
    temarios = con.execute(
        "SELECT id,nombre FROM temarios WHERE convocatoria_id=? ORDER BY id",
        (conv[0],),
    ).fetchall()
    if len(temarios) != 1:
        raise RuntimeError(
            f"La convocatoria debe tener exactamente un temario; encontrados={len(temarios)}."
        )
    return conv, temarios[0]


def filas_pseudo(con: sqlite3.Connection, temario_id: int) -> tuple[list[dict], dict]:
    existentes = tablas(con)
    if not TABLAS_V2.issubset(existentes):
        return [], {
            "v2_disponible": False,
            "candidatos": 0,
            "incluidos": 0,
            "excluidos": 0,
        }

    candidatos = con.execute(
        """
        SELECT
            tt.parte,
            tt.numero_tema,
            tt.titulo AS titulo_tema,
            pn.codigo AS pseudonorma_codigo,
            pn.estado AS pseudonorma_estado,
            pa.id AS pseudoarticulo_id,
            pa.codigo AS pseudoarticulo_codigo,
            pa.orden,
            pa.texto_temario,
            pa.tipo_contenido,
            pa.sintesis,
            pa.estado AS pseudoarticulo_estado
        FROM temario_pseudoarticulos pa
        JOIN temario_pseudonormas pn ON pn.id=pa.pseudonorma_id
        JOIN temario_temas tt ON tt.id=pn.tema_id
        WHERE tt.temario_id=?
        ORDER BY tt.parte, tt.numero_tema, pa.orden, pa.id
        """,
        (temario_id,),
    ).fetchall()

    incluidos = []
    for r in candidatos:
        sintesis = str(r[10] or "").strip()
        if not sintesis:
            continue
        if str(r[4] or "") not in ESTADOS_FINAL_PSEUDONORMA:
            continue
        if str(r[11] or "") not in ESTADOS_FINAL_PSEUDOARTICULO:
            continue
        respaldos = con.execute(
            """
            SELECT COUNT(*)
            FROM temario_pseudoarticulo_respaldos
            WHERE pseudoarticulo_id=?
              AND pertinencia='PERTINENTE'
              AND estado='VERIFICADO'
            """,
            (int(r[5]),),
        ).fetchone()[0]
        if int(respaldos or 0) <= 0:
            continue
        incluidos.append({
            "tipo_registro": "PSEUDOARTICULO_V2",
            "parte": str(r[0]),
            "numero_tema": int(r[1]),
            "titulo_tema": str(r[2]),
            "pseudonorma": str(r[3]),
            "pseudoarticulo": str(r[6]),
            "orden": int(r[7]),
            "texto_temario": str(r[8]),
            "tipo_contenido": str(r[9]),
            "sintesis": sintesis,
            "respaldos_verificados": int(respaldos),
        })

    return incluidos, {
        "v2_disponible": True,
        "candidatos": len(candidatos),
        "incluidos": len(incluidos),
        "excluidos": len(candidatos) - len(incluidos),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Construye artefacto de corpus pseudonormativo V2 sin modificar BD."
    )
    ap.add_argument("--codigo", required=True)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--salida")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD: {db}")
    hash_antes = sha256(db)

    uri = db.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        conv, temario = seleccionar_convocatoria(con, args.codigo)
        filas, resumen = filas_pseudo(con, int(temario[0]))

    salida = (
        Path(args.salida).expanduser().resolve()
        if args.salida
        else (SALIDA_DEFECTO / f"{args.codigo}_pseudo_v2.jsonl")
    )
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8") as f:
        for fila in filas:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")

    if sha256(db) != hash_antes:
        raise RuntimeError("La BD cambió durante una operación declarada de solo lectura.")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - CORPUS PSEUDONORMATIVO - SOLO LECTURA")
    print("=" * 78)
    print(f"Convocatoria..................... {conv[1]}")
    print(f"Temario.......................... {temario[0]} | {temario[1]}")
    print(f"Tablas V2 disponibles............ {'SI' if resumen['v2_disponible'] else 'NO'}")
    print(f"Pseudoartículos candidatos....... {resumen['candidatos']}")
    print(f"Pseudoartículos incluidos........ {resumen['incluidos']}")
    print(f"Pseudoartículos excluidos........ {resumen['excluidos']}")
    print(f"Salida........................... {salida}")
    print("Criterio......................... síntesis + estado final + respaldo PERTINENTE/VERIFICADO")
    print("BD............................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())