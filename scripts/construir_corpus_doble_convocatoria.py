"""
OpoCoach-Mantenimiento
Construcción controlada de los dos corpus de una convocatoria.

1) Corpus IA:
   exclusivamente los artículos citados en el temario de la convocatoria.

2) Corpus RAG Chat:
   todos los artículos de todas las normas citadas en ese temario.
   El almacenamiento físico sigue siendo compartido en articulos_fuente; este
   orquestador limita la ampliación a los documentos realmente vinculados al
   temario seleccionado y reutiliza los textos ya existentes.

Sin --aplicar trabaja en SOLO VALIDACIÓN.
Con --aplicar construye primero el corpus IA y, sólo si queda completo, amplía
el corpus RAG de esa misma convocatoria mediante los proveedores existentes.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def ejecutar(cmd: list[str]) -> int:
    print("\n" + "=" * 78)
    print(" ".join(f'"{x}"' if " " in x else x for x in cmd))
    print("=" * 78)
    return int(subprocess.run(cmd, cwd=RAIZ, check=False).returncode)


def seleccionar(con: sqlite3.Connection, convocatoria_id: int | None, codigo: str | None):
    if convocatoria_id is not None:
        conv = con.execute(
            "SELECT id, codigo FROM convocatorias WHERE id=?",
            (convocatoria_id,),
        ).fetchone()
    else:
        conv = con.execute(
            "SELECT id, codigo FROM convocatorias WHERE codigo=?",
            (codigo,),
        ).fetchone()
    if conv is None:
        raise RuntimeError("No existe la convocatoria solicitada.")

    temarios = con.execute(
        "SELECT id, nombre FROM temarios WHERE convocatoria_id=? ORDER BY id",
        (conv["id"],),
    ).fetchall()
    if len(temarios) != 1:
        raise RuntimeError(
            "La convocatoria debe tener exactamente un temario; "
            f"encontrados: {len(temarios)}."
        )
    return conv, temarios[0]


def comprobar_estructura(con: sqlite3.Connection) -> None:
    necesarias = {
        "convocatorias", "temarios", "temario_temas",
        "temario_referencias", "articulos_fuente",
    }
    existentes = {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    faltan = sorted(necesarias - existentes)
    if faltan:
        raise RuntimeError("Faltan tablas requeridas: " + ", ".join(faltan))


def estado_corpus_ia(con: sqlite3.Connection, temario_id: int) -> dict[str, int]:
    fila = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN tr.estado='COMPLETADO' THEN 1 ELSE 0 END) AS completadas,
            SUM(CASE WHEN tr.estado='COMPLETADO'
                      AND tr.articulo_fuente_id IS NOT NULL THEN 1 ELSE 0 END) AS enlazadas,
            COUNT(DISTINCT CASE WHEN tr.estado='COMPLETADO'
                                THEN tr.articulo_fuente_id END) AS articulos_distintos
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        WHERE tt.temario_id=?
        """,
        (temario_id,),
    ).fetchone()
    return {k: int(fila[k] or 0) for k in fila.keys()}


def documentos_temario(con: sqlite3.Connection, temario_id: int) -> list[str]:
    filas = con.execute(
        """
        SELECT DISTINCT af.id_boe
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE tt.temario_id=?
          AND tr.estado='COMPLETADO'
          AND tr.articulo_fuente_id IS NOT NULL
        ORDER BY af.id_boe
        """,
        (temario_id,),
    ).fetchall()
    return [str(r[0]).strip() for r in filas if str(r[0] or "").strip()]


def nombres_documentos_temario(con: sqlite3.Connection, temario_id: int) -> dict[str, list[str]]:
    filas = con.execute(
        """
        SELECT DISTINCT af.id_boe, tr.nombre_norma_csv
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE tt.temario_id=?
          AND tr.estado='COMPLETADO'
          AND tr.articulo_fuente_id IS NOT NULL
        ORDER BY af.id_boe, tr.nombre_norma_csv
        """,
        (temario_id,),
    ).fetchall()
    resultado: dict[str, list[str]] = {}
    for fila in filas:
        fuente = str(fila[0] or "").strip()
        nombre = str(fila[1] or "").strip()
        if fuente and nombre:
            resultado.setdefault(fuente, [])
            if nombre not in resultado[fuente]:
                resultado[fuente].append(nombre)
    return resultado


def pendientes_temario(con: sqlite3.Connection, temario_id: int) -> list[dict[str, object]]:
    filas = con.execute(
        """
        SELECT
            tr.nombre_norma_csv AS norma,
            tr.estado AS estado,
            COUNT(*) AS referencias
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        WHERE tt.temario_id=?
          AND COALESCE(tr.estado,'') <> 'COMPLETADO'
        GROUP BY tr.nombre_norma_csv, tr.estado
        ORDER BY tr.nombre_norma_csv, tr.estado
        """,
        (temario_id,),
    ).fetchall()
    return [dict(r) for r in filas]


def imprimir_documentos_necesarios(pendientes: list[dict[str, object]]) -> None:
    if not pendientes:
        print("Documentos normativos pendientes: 0")
        return
    print("\nDOCUMENTOS NORMATIVOS QUE SIGUEN PENDIENTES")
    print("-" * 78)
    agrupados: dict[str, int] = {}
    for fila in pendientes:
        norma = str(fila.get("norma") or "<SIN NOMBRE>").strip()
        agrupados[norma] = agrupados.get(norma, 0) + int(fila.get("referencias") or 0)
    for norma, cantidad in sorted(agrupados.items()):
        print(f"  - {norma} | referencias pendientes: {cantidad}")
    print(
        "Si alguna de estas normas no puede obtenerse por su proveedor, "
        "debe añadirse su PDF completo a fuentes_normativas/."
    )


def clasificar_documentos(ids: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    boe: list[str] = []
    dogv: list[str] = []
    doue: list[str] = []
    desconocidos: list[str] = []
    for x in ids:
        u = x.upper()
        if u.startswith("BOE-A-"):
            boe.append(x)
        elif u.startswith("DOUE-"):
            doue.append(x)
        elif u.startswith("DOGV-") or u.startswith("LOCAL-DOGV-"):
            dogv.append(x)
        else:
            desconocidos.append(x)
    return boe, dogv, doue, desconocidos


def ejecutar_pdf_local(db: Path, id_fuente: str, aplicar: bool) -> int:
    cmd = [
        sys.executable,
        str(SCRIPTS / "ampliar_corpus_pdf_local.py"),
        "--db", str(db),
        "--id-fuente", id_fuente,
    ]
    if aplicar:
        cmd.append("--aplicar")
    return ejecutar(cmd)


def ejecutar_rag(db: Path, documentos: list[str], aplicar: bool) -> tuple[int, list[str]]:
    boe, dogv, doue, desconocidos = clasificar_documentos(documentos)
    faltantes_pdf: list[str] = []

    print("\nDOCUMENTOS DEL TEMARIO")
    print(f"BOE:  {len(boe)}")
    print(f"DOGV: {len(dogv)}")
    print(f"DOUE: {len(doue)}")

    for x in desconocidos:
        print(f"  FUENTE SIN PROVEEDOR: {x}")
        if ejecutar_pdf_local(db, x, aplicar) != 0:
            faltantes_pdf.append(x)

    # Se procesa documento a documento: un fallo no impide completar los demás.
    for id_boe in boe:
        cmd = [
            sys.executable,
            str(SCRIPTS / "ampliar_corpus_chat.py"),
            "--db", str(db),
            "--id-boe", id_boe,
        ]
        if aplicar:
            cmd.append("--aplicar")
        rc = ejecutar(cmd)
        if rc != 0:
            print(f"Proveedor BOE no completó {id_boe}; se prueba PDF local.")
            if ejecutar_pdf_local(db, id_boe, aplicar) != 0:
                faltantes_pdf.append(id_boe)

    for fuente in dogv:
        cmd = [
            sys.executable,
            str(SCRIPTS / "ampliar_corpus_dogv.py"),
            "--db", str(db),
            "--id-fuente", fuente,
        ]
        if aplicar:
            cmd.append("--aplicar")
        rc = ejecutar(cmd)
        if rc != 0:
            print(f"Proveedor DOGV no completó {fuente}; se prueba PDF local.")
            if ejecutar_pdf_local(db, fuente, aplicar) != 0:
                faltantes_pdf.append(fuente)

    for fuente in doue:
        cmd = [
            sys.executable,
            str(SCRIPTS / "ampliar_corpus_doue.py"),
            "--db", str(db),
            "--id-fuente", fuente,
        ]
        if aplicar:
            cmd.append("--aplicar")
        rc = ejecutar(cmd)
        if rc != 0:
            print(f"Proveedor DOUE no completó {fuente}; se prueba PDF local.")
            if ejecutar_pdf_local(db, fuente, aplicar) != 0:
                faltantes_pdf.append(fuente)

    return 0, sorted(set(faltantes_pdf))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Construye/valida los corpus IA y RAG de una convocatoria."
    )
    parser.add_argument("--db", default=str(DB_DEFECTO))
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--convocatoria-id", type=int)
    grupo.add_argument("--codigo")
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Construye ambos corpus. Sin esta opción: solo validación.",
    )
    parser.add_argument("--reintentar-pendientes", action="store_true")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1

    for requerido in (
        "construir_corpus_convocatoria.py",
        "ampliar_corpus_chat.py",
        "ampliar_corpus_dogv.py",
        "ampliar_corpus_doue.py",
        "ampliar_corpus_pdf_local.py",
    ):
        if not (SCRIPTS / requerido).is_file():
            print(f"ERROR: falta scripts/{requerido}")
            return 1

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        comprobar_estructura(con)
        conv, temario = seleccionar(con, args.convocatoria_id, args.codigo)
        inicial = estado_corpus_ia(con, int(temario["id"]))

    print("=" * 78)
    print("CORPUS DE CONVOCATORIA: IA + RAG CHAT")
    print("=" * 78)
    print(f"Base:         {db}")
    print(f"Convocatoria: {conv['id']} | {conv['codigo']}")
    print(f"Temario:      {temario['id']} | {temario['nombre']}")
    print(f"Modo:         {'APLICAR' if args.aplicar else 'SOLO VALIDACIÓN'}")
    print(f"Referencias:  {inicial['total']}")

    cmd = [
        sys.executable,
        str(SCRIPTS / "construir_corpus_convocatoria.py"),
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

    rc_ia = ejecutar(cmd)
    if rc_ia not in {0, 2, 3}:
        print("\nRESULTADO: ERROR ESTRUCTURAL AL CONSTRUIR EL CORPUS IA.")
        return 2

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        _, temario2 = seleccionar(con, args.convocatoria_id, args.codigo)
        ia = estado_corpus_ia(con, int(temario2["id"]))
        docs = documentos_temario(con, int(temario2["id"]))
        nombres_docs = nombres_documentos_temario(con, int(temario2["id"]))
        pendientes = pendientes_temario(con, int(temario2["id"]))

    print("\nCORPUS IA")
    print(f"Referencias totales........ {ia['total']}")
    print(f"Completadas................. {ia['completadas']}")
    print(f"Enlazadas a texto........... {ia['enlazadas']}")
    print(f"Artículos distintos......... {ia['articulos_distintos']}")

    if ia["total"] == 0:
        print("RESULTADO: el temario no contiene referencias.")
        return 3

    ia_completo = ia["completadas"] == ia["total"] and ia["enlazadas"] == ia["total"]
    if not ia_completo:
        print("RESULTADO: CORPUS IA PARCIAL. Se continúa con todo lo que sí está resuelto.")
        imprimir_documentos_necesarios(pendientes)

    faltantes_rag: list[str] = []
    if docs:
        _, faltantes_rag = ejecutar_rag(db, docs, args.aplicar)
    else:
        print("No hay todavía documentos enlazados que puedan ampliarse para el RAG.")

    # Revalidación idempotente del ámbito que sí pudo construirse.
    if args.aplicar and docs:
        print("\n" + "=" * 78)
        print("REVALIDACIÓN DEL RAG DE LA CONVOCATORIA")
        print("=" * 78)
        _, faltantes_revalidacion = ejecutar_rag(db, docs, aplicar=False)
        faltantes_rag = sorted(set(faltantes_rag) | set(faltantes_revalidacion))

    print("\n" + "=" * 78)
    if ia_completo and not faltantes_rag:
        print("RESULTADO GLOBAL: OK")
    else:
        print("RESULTADO GLOBAL: PARCIAL - PROCESO TERMINADO SIN DETENER EL RESTO")
    print("=" * 78)
    print(f"Corpus IA: {ia['completadas']}/{ia['total']} referencias completadas.")
    print(f"Corpus RAG: {len(docs)} documentos identificados para esta convocatoria.")
    imprimir_documentos_necesarios(pendientes)
    if faltantes_rag:
        print("\nDOCUMENTOS COMPLETOS NO ENCONTRADOS PARA EL RAG")
        print("-" * 78)
        for fuente in faltantes_rag:
            nombres = "; ".join(nombres_docs.get(fuente, []))
            if nombres:
                print(f"  - {nombres} [{fuente}]")
            else:
                print(f"  - {fuente}")
        print("Añada esos PDF completos a fuentes_normativas/ y vuelva a ejecutar.")
    else:
        print("Documentos completos pendientes para RAG: 0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR FATAL: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise
