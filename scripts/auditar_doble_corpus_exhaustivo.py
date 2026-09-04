"""
OpoCoach-Mantenimiento
Auditoría SOLO LECTURA de los dos corpus conceptuales por convocatoria.

Definición:
1) Corpus IA:
   - exclusivamente los artículos citados expresamente en el temario;
   - cada referencia debe estar COMPLETADA, enlazada a articulos_fuente y con texto no vacío.

2) Corpus RAG:
   - todas las normas citadas en el temario;
   - para cada norma, el corpus debe contener el texto íntegro de todos sus artículos;
   - la comprobación de completitud se delega en los proveedores existentes:
       BOE  -> ampliar_corpus_chat.py
       DOGV -> ampliar_corpus_dogv.py
       DOUE -> ampliar_corpus_doue.py
       otras/locales -> ampliar_corpus_pdf_local.py

No modifica la base de datos. No usa --aplicar.
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
    print()
    print("=" * 78)
    print(" ".join(f'"{x}"' if " " in x else x for x in cmd))
    print("=" * 78)
    return int(subprocess.run(cmd, cwd=RAIZ, check=False).returncode)


def tablas(con: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def validar_estructura(con: sqlite3.Connection) -> None:
    necesarias = {
        "convocatorias",
        "temarios",
        "temario_temas",
        "temario_referencias",
        "articulos_fuente",
    }
    faltan = sorted(necesarias - tablas(con))
    if faltan:
        raise RuntimeError("Faltan tablas requeridas: " + ", ".join(faltan))


def convocatorias_objetivo(
    con: sqlite3.Connection,
    convocatoria_id: int | None,
    incluir_inactivas: bool,
) -> list[sqlite3.Row]:
    if convocatoria_id is not None:
        filas = con.execute(
            "SELECT id, codigo, COALESCE(activa,1) activa "
            "FROM convocatorias WHERE id=?",
            (convocatoria_id,),
        ).fetchall()
    elif incluir_inactivas:
        filas = con.execute(
            "SELECT id, codigo, COALESCE(activa,1) activa "
            "FROM convocatorias ORDER BY id"
        ).fetchall()
    else:
        filas = con.execute(
            "SELECT id, codigo, COALESCE(activa,1) activa "
            "FROM convocatorias "
            "WHERE COALESCE(activa,1)=1 ORDER BY id"
        ).fetchall()
    return filas


def temario_unico(con: sqlite3.Connection, convocatoria_id: int) -> sqlite3.Row:
    filas = con.execute(
        "SELECT id, nombre FROM temarios WHERE convocatoria_id=? ORDER BY id",
        (convocatoria_id,),
    ).fetchall()
    if len(filas) != 1:
        raise RuntimeError(
            f"Convocatoria {convocatoria_id}: se esperaba exactamente un temario; "
            f"encontrados={len(filas)}"
        )
    return filas[0]


def auditar_corpus_ia(con: sqlite3.Connection, temario_id: int) -> dict[str, int]:
    r = con.execute(
        """
        SELECT
            COUNT(*) total,
            SUM(CASE WHEN tr.estado='COMPLETADO' THEN 1 ELSE 0 END) completadas,
            SUM(CASE WHEN tr.estado='COMPLETADO'
                      AND tr.articulo_fuente_id IS NOT NULL THEN 1 ELSE 0 END) enlazadas,
            SUM(CASE WHEN tr.estado='COMPLETADO'
                      AND tr.articulo_fuente_id IS NOT NULL
                      AND TRIM(COALESCE(af.texto,''))<>'' THEN 1 ELSE 0 END) con_texto,
            COUNT(DISTINCT CASE WHEN tr.estado='COMPLETADO'
                                AND tr.articulo_fuente_id IS NOT NULL
                                THEN tr.articulo_fuente_id END) articulos_distintos
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        LEFT JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE tt.temario_id=?
        """,
        (temario_id,),
    ).fetchone()
    return {k: int(r[k] or 0) for k in r.keys()}


def documentos_temario(con: sqlite3.Connection, temario_id: int) -> list[tuple[str, str]]:
    filas = con.execute(
        """
        SELECT DISTINCT
            TRIM(COALESCE(af.id_boe,'')) AS fuente,
            TRIM(COALESCE(tr.nombre_norma_csv,'')) AS norma
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE tt.temario_id=?
          AND tr.estado='COMPLETADO'
          AND tr.articulo_fuente_id IS NOT NULL
          AND TRIM(COALESCE(af.id_boe,''))<>''
        ORDER BY fuente, norma
        """,
        (temario_id,),
    ).fetchall()

    # Una misma fuente puede aparecer con varias variantes del nombre.
    # Para RAG importa la fuente/documento físico completo.
    vistos: set[str] = set()
    resultado: list[tuple[str, str]] = []
    for r in filas:
        fuente = str(r["fuente"]).strip()
        norma = str(r["norma"]).strip()
        if fuente and fuente not in vistos:
            vistos.add(fuente)
            resultado.append((fuente, norma))
    return resultado


def clasificar_fuente(fuente: str) -> str:
    u = fuente.upper()
    if u.startswith("BOE-A-"):
        return "BOE"
    if u.startswith("DOGV-") or u.startswith("LOCAL-DOGV-"):
        return "DOGV"
    if u.startswith("DOUE-"):
        return "DOUE"
    return "LOCAL"


def comando_proveedor(db: Path, fuente: str) -> list[str]:
    tipo = clasificar_fuente(fuente)

    if tipo == "BOE":
        script = SCRIPTS / "ampliar_corpus_chat.py"
        return [
            sys.executable, str(script),
            "--db", str(db),
            "--id-boe", fuente,
        ]

    if tipo == "DOGV":
        script = SCRIPTS / "ampliar_corpus_dogv.py"
        return [
            sys.executable, str(script),
            "--db", str(db),
            "--id-fuente", fuente,
        ]

    if tipo == "DOUE":
        script = SCRIPTS / "ampliar_corpus_doue.py"
        return [
            sys.executable, str(script),
            "--db", str(db),
            "--id-fuente", fuente,
        ]

    script = SCRIPTS / "ampliar_corpus_pdf_local.py"
    return [
        sys.executable, str(script),
        "--db", str(db),
        "--id-fuente", fuente,
    ]


def comprobar_scripts(documentos: list[tuple[str, str]]) -> None:
    requeridos: set[str] = set()
    for fuente, _ in documentos:
        tipo = clasificar_fuente(fuente)
        if tipo == "BOE":
            requeridos.add("ampliar_corpus_chat.py")
        elif tipo == "DOGV":
            requeridos.add("ampliar_corpus_dogv.py")
        elif tipo == "DOUE":
            requeridos.add("ampliar_corpus_doue.py")
        else:
            requeridos.add("ampliar_corpus_pdf_local.py")

    faltan = [x for x in sorted(requeridos) if not (SCRIPTS / x).is_file()]
    if faltan:
        raise RuntimeError("Faltan scripts proveedores: " + ", ".join(faltan))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--convocatoria-id", type=int)
    p.add_argument("--incluir-inactivas", action="store_true")
    args = p.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        validar_estructura(con)
        convocatorias = convocatorias_objetivo(
            con,
            args.convocatoria_id,
            args.incluir_inactivas,
        )

    if not convocatorias:
        print("No hay convocatorias en el ámbito solicitado.")
        return 1

    print("=" * 78)
    print("AUDITORÍA DOBLE CORPUS POR CONVOCATORIA - SOLO LECTURA")
    print("=" * 78)
    print(f"Base: {db}")
    print("Corpus IA : solo artículos citados por el temario.")
    print("Corpus RAG: normas completas del temario.")
    print("Escrituras en BD: 0")

    fallos_globales = 0

    for conv in convocatorias:
        cid = int(conv["id"])
        codigo = str(conv["codigo"])
        activa = int(conv["activa"] or 0)

        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            temario = temario_unico(con, cid)
            ia = auditar_corpus_ia(con, int(temario["id"]))
            documentos = documentos_temario(con, int(temario["id"]))

        print()
        print("#" * 78)
        print(f"CONVOCATORIA {cid} | {codigo} | {'ACTIVA' if activa else 'INACTIVA'}")
        print("#" * 78)
        print(f"Temario: {temario['id']} | {temario['nombre']}")
        print()
        print("CORPUS IA")
        print("-" * 78)
        print(f"Referencias del temario............. {ia['total']}")
        print(f"COMPLETADO.......................... {ia['completadas']}")
        print(f"Enlazadas a articulo_fuente......... {ia['enlazadas']}")
        print(f"Con texto no vacío.................. {ia['con_texto']}")
        print(f"Artículos distintos................. {ia['articulos_distintos']}")

        ia_ok = (
            ia["total"] > 0
            and ia["completadas"] == ia["total"]
            and ia["enlazadas"] == ia["total"]
            and ia["con_texto"] == ia["total"]
        )
        print(f"RESULTADO CORPUS IA................. {'OK' if ia_ok else 'INCOMPLETO'}")
        if not ia_ok:
            fallos_globales += 1

        print()
        print("CORPUS RAG")
        print("-" * 78)
        print(f"Normas/documentos del temario....... {len(documentos)}")
        if not documentos:
            print("RESULTADO CORPUS RAG................ INCOMPLETO")
            fallos_globales += 1
            continue

        comprobar_scripts(documentos)

        fallos_rag = 0
        for n, (fuente, norma) in enumerate(documentos, 1):
            tipo = clasificar_fuente(fuente)
            print()
            print(f"[{n}/{len(documentos)}] {norma or '<SIN NOMBRE>'}")
            print(f"Fuente: {fuente} | proveedor={tipo}")
            rc = ejecutar(comando_proveedor(db, fuente))
            if rc != 0:
                fallos_rag += 1
                print(f"RESULTADO DOCUMENTO: REQUIERE REVISIÓN (rc={rc})")
            else:
                print("RESULTADO DOCUMENTO: VALIDACIÓN/PLAN OK")

        if fallos_rag:
            print()
            print(
                f"RESULTADO CORPUS RAG................. REQUIERE REVISIÓN "
                f"({fallos_rag}/{len(documentos)} documentos)"
            )
            fallos_globales += 1
        else:
            print()
            print("RESULTADO CORPUS RAG................. VALIDACIÓN/PLAN OK")

    print()
    print("=" * 78)
    if fallos_globales:
        print("RESULTADO GLOBAL: REQUIERE REVISIÓN")
        print(
            "La auditoría no ha modificado la base. "
            "Revise los documentos señalados antes de aplicar cualquier ampliación."
        )
        return 2

    print("RESULTADO GLOBAL: OK")
    print("La auditoría no ha modificado la base.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR FATAL: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise
