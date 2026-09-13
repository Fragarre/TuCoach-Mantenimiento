"""
Diagnóstico de fuentes normativas pendientes de una convocatoria.

SOLO LECTURA:
- abre SQLite en modo read-only;
- no modifica ninguna tabla;
- no descarga corpus;
- no escribe en fuentes_normativas/.

Clasificación:
1. PDF local con identidad respaldada -> PDF_LOCAL_RESUELTA
2. Directiva/Reglamento UE -> DOUE_RESUELTA o REQUIERE_PDF_LOCAL
3. Resto -> un intento BOE conservador, también para normativa autonómica
   publicada en BOE -> BOE_RESUELTA o REQUIERE_PDF_LOCAL

Uso:
    python scripts/diagnosticar_fuentes_pendientes.py \
      --db db/oposiciones.sqlite3 \
      --codigo A1-01_01_26_ADM
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from pathlib import Path

from localizador_fuentes import (
    es_union_europea,
    localizar_boe_fuente,
    localizar_doue,
    localizar_pdf_local,
)


def abrir_ro(ruta: Path) -> sqlite3.Connection:
    uri = ruta.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def cargar_pendientes(con: sqlite3.Connection, codigo: str) -> list[str]:
    filas = con.execute(
        """
        SELECT DISTINCT tr.nombre_norma_csv
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id = tr.tema_id
        JOIN temarios t ON t.id = tt.temario_id
        JOIN convocatorias c ON c.id = t.convocatoria_id
        WHERE c.codigo = ?
          AND tr.estado <> 'COMPLETADO'
        ORDER BY tr.nombre_norma_csv
        """,
        (codigo,),
    ).fetchall()
    return [str(f["nombre_norma_csv"]) for f in filas]


def clasificar(nombre: str) -> tuple[str, str, str]:
    local = localizar_pdf_local(nombre)
    if local is not None:
        return "PDF_LOCAL_RESUELTA", local.id_fuente, local.metodo

    if es_union_europea(nombre):
        try:
            fuente = localizar_doue(nombre)
            return "DOUE_RESUELTA", fuente.id_fuente, fuente.metodo
        except Exception as exc:
            return "REQUIERE_PDF_LOCAL", "", f"DOUE: {exc}"

    try:
        fuente = localizar_boe_fuente(nombre)
        return "BOE_RESUELTA", fuente.id_fuente, fuente.metodo
    except Exception as exc:
        return "REQUIERE_PDF_LOCAL", "", f"BOE: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Clasifica fuentes pendientes sin modificar SQLite.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--codigo", required=True)
    args = parser.parse_args()

    ruta = Path(args.db)
    if not ruta.is_file():
        raise SystemExit(f"No existe la BD: {ruta}")

    con = abrir_ro(ruta)
    try:
        pendientes = cargar_pendientes(con, args.codigo)
    finally:
        con.close()

    print("=" * 100)
    print("DIAGNOSTICO DE FUENTES PENDIENTES - SOLO LECTURA")
    print("=" * 100)
    print(f"Convocatoria: {args.codigo}")
    print(f"BD: {ruta.resolve()}")
    print(f"Normas pendientes distintas: {len(pendientes)}")
    print()

    contador: Counter[str] = Counter()
    requiere_pdf: list[str] = []

    for i, nombre in enumerate(pendientes, start=1):
        estado, id_fuente, detalle = clasificar(nombre)
        contador[estado] += 1
        if estado == "REQUIERE_PDF_LOCAL":
            requiere_pdf.append(nombre)

        print(f"[{i:02d}/{len(pendientes):02d}] {estado}")
        print(f"  NORMA: {nombre}")
        if id_fuente:
            print(f"  FUENTE: {id_fuente}")
        print(f"  DETALLE: {detalle}")
        print()

    print("=" * 100)
    print("RESUMEN")
    print("=" * 100)
    for estado in (
        "BOE_RESUELTA",
        "DOUE_RESUELTA",
        "PDF_LOCAL_RESUELTA",
        "REQUIERE_PDF_LOCAL",
    ):
        print(f"{estado}: {contador.get(estado, 0)}")

    print()
    print("DOCUMENTOS A INCORPORAR EN fuentes_normativas/:")
    if not requiere_pdf:
        print("  Ninguno")
    else:
        for nombre in requiere_pdf:
            print(f"  - {nombre}")

    print()
    print("SQLite abierto exclusivamente en modo read-only.")
    print("No se ha modificado la BD ni se ha descargado corpus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
