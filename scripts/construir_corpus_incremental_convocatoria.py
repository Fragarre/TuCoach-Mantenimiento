"""Actualización incremental de corpus para el mantenimiento de un temario.

Procesa el corpus IA sólo para las referencias pendientes de la convocatoria y
amplía el RAG únicamente para los documentos a los que pertenecen esas
referencias. Las eliminaciones de referencias no provocan recorridos del RAG:
el almacenamiento físico de articulos_fuente es compartido y se conserva.

La validación del estado final del corpus IA sigue siendo global para toda la
convocatoria.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import construir_corpus_doble_convocatoria as base


def ids_referencias_pendientes(con: sqlite3.Connection, temario_id: int) -> list[int]:
    filas = con.execute(
        """
        SELECT tr.id
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        WHERE tt.temario_id=?
          AND (COALESCE(tr.estado,'') <> 'COMPLETADO'
               OR tr.articulo_fuente_id IS NULL)
        ORDER BY tr.id
        """,
        (temario_id,),
    ).fetchall()
    return [int(r[0]) for r in filas]


def documentos_de_referencias(con: sqlite3.Connection, referencia_ids: list[int]) -> list[str]:
    if not referencia_ids:
        return []
    marcadores = ",".join("?" for _ in referencia_ids)
    filas = con.execute(
        f"""
        SELECT DISTINCT af.id_boe
        FROM temario_referencias tr
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE tr.id IN ({marcadores})
          AND tr.estado='COMPLETADO'
          AND tr.articulo_fuente_id IS NOT NULL
        ORDER BY af.id_boe
        """,
        referencia_ids,
    ).fetchall()
    return [str(r[0]).strip() for r in filas if str(r[0] or "").strip()]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Actualiza incrementalmente los corpus de una convocatoria."
    )
    p.add_argument("--db", default=str(base.DB_DEFECTO))
    grupo = p.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--convocatoria-id", type=int)
    grupo.add_argument("--codigo")
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--reintentar-pendientes", action="store_true")
    args = p.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        base.comprobar_estructura(con)
        conv, temario = base.seleccionar(con, args.convocatoria_id, args.codigo)
        pendientes_antes = ids_referencias_pendientes(con, int(temario["id"]))
        inicial = base.estado_corpus_ia(con, int(temario["id"]))

    print("=" * 78)
    print("CORPUS INCREMENTAL DE CONVOCATORIA")
    print("=" * 78)
    print(f"Base:                    {db}")
    print(f"Convocatoria:            {conv['id']} | {conv['codigo']}")
    print(f"Temario:                 {temario['id']} | {temario['nombre']}")
    print(f"Modo:                    {'APLICAR' if args.aplicar else 'SOLO VALIDACIÓN'}")
    print(f"Referencias totales:     {inicial['total']}")
    print(f"Referencias a procesar:  {len(pendientes_antes)}")

    cmd = [
        sys.executable,
        str(base.SCRIPTS / "construir_corpus_convocatoria.py"),
        "--db", str(db),
    ]
    if args.convocatoria_id is not None:
        cmd += ["--convocatoria-id", str(args.convocatoria_id)]
    else:
        cmd += ["--codigo", str(args.codigo)]
    if args.reintentar_pendientes:
        cmd.append("--reintentar-pendientes")
    if not args.aplicar:
        cmd.append("--solo-validar")

    rc = base.ejecutar(cmd)
    if rc not in {0, 2, 3}:
        print("RESULTADO: ERROR ESTRUCTURAL AL CONSTRUIR EL CORPUS IA.")
        return 2

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        _, temario2 = base.seleccionar(con, args.convocatoria_id, args.codigo)
        ia = base.estado_corpus_ia(con, int(temario2["id"]))
        pendientes_despues = base.pendientes_temario(con, int(temario2["id"]))
        docs_afectados = documentos_de_referencias(con, pendientes_antes)

    ia_completo = ia["total"] > 0 and ia["completadas"] == ia["total"] and ia["enlazadas"] == ia["total"]

    print("\nCORPUS IA - VALIDACIÓN GLOBAL")
    print(f"Referencias totales........ {ia['total']}")
    print(f"Completadas................. {ia['completadas']}")
    print(f"Enlazadas a texto........... {ia['enlazadas']}")
    print(f"Artículos distintos......... {ia['articulos_distintos']}")

    if not ia_completo:
        print("RESULTADO: CORPUS IA PARCIAL.")
        base.imprimir_documentos_necesarios(pendientes_despues)
        return 3

    faltantes_rag: list[str] = []
    if args.aplicar and docs_afectados:
        print(f"\nRAG incremental: {len(docs_afectados)} documento(s) afectado(s).")
        _, faltantes_rag = base.ejecutar_rag(db, docs_afectados, aplicar=True)
    elif args.aplicar:
        print("\nRAG incremental: 0 documentos afectados; no se recorren las demás fuentes.")
    else:
        print(f"\nRAG incremental: {len(docs_afectados)} documento(s) requerirían comprobación al aplicar.")

    if faltantes_rag:
        print("RESULTADO GLOBAL: PARCIAL - faltan documentos RAG afectados.")
        for fuente in faltantes_rag:
            print(f"  - {fuente}")
        return 4

    print("\nRESULTADO GLOBAL: OK")
    print(f"Corpus IA: {ia['completadas']}/{ia['total']} referencias completadas.")
    print(f"RAG procesado en esta ejecución: {len(docs_afectados)} documento(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR FATAL: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise
