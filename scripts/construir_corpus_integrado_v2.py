"""
Constructor Temario V2 - orquestador aditivo del corpus por convocatoria.

Mantiene intacto el flujo consolidado existente:
1. ejecuta construir_corpus_doble_convocatoria.py con los mismos argumentos;
2. si ese flujo no presenta error estructural, genera además el artefacto
   pseudonormativo V2 de la misma convocatoria;
3. la capa V2 es siempre de solo lectura respecto de la BD y nunca se mezcla con
   articulos_fuente ni con los proveedores normativos BOE/DOGV/DOUE.

Convocatorias sin datos V2 siguen funcionando: producen un JSONL V2 vacío.
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
CONSOLIDADO = SCRIPTS / "construir_corpus_doble_convocatoria.py"
CORPUS_V2 = SCRIPTS / "construir_corpus_v2_convocatoria.py"


def resolver_codigo(db: Path, convocatoria_id: int | None, codigo: str | None) -> str:
    with sqlite3.connect(db) as con:
        if convocatoria_id is not None:
            fila = con.execute(
                "SELECT codigo FROM convocatorias WHERE id=?",
                (convocatoria_id,),
            ).fetchone()
        else:
            fila = con.execute(
                "SELECT codigo FROM convocatorias WHERE codigo=?",
                (codigo,),
            ).fetchone()
    if fila is None:
        raise RuntimeError("No existe la convocatoria solicitada.")
    return str(fila[0])


def ejecutar(cmd: list[str]) -> int:
    print("\n" + "=" * 78)
    print(" ".join(f'\"{x}\"' if " " in x else x for x in cmd))
    print("=" * 78)
    return int(subprocess.run(cmd, cwd=RAIZ, check=False).returncode)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Ejecuta corpus consolidado + capa pseudonormativa V2 por convocatoria."
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--convocatoria-id", type=int)
    g.add_argument("--codigo")
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--reintentar-pendientes", action="store_true")
    p.add_argument(
        "--salida-v2",
        help="Ruta opcional del JSONL pseudonormativo V2.",
    )
    args = p.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1
    for script in (CONSOLIDADO, CORPUS_V2):
        if not script.is_file():
            print(f"ERROR: falta {script}")
            return 1

    codigo = resolver_codigo(db, args.convocatoria_id, args.codigo)

    cmd_base = [sys.executable, str(CONSOLIDADO), "--db", str(db)]
    if args.convocatoria_id is not None:
        cmd_base += ["--convocatoria-id", str(args.convocatoria_id)]
    else:
        cmd_base += ["--codigo", codigo]
    if args.aplicar:
        cmd_base.append("--aplicar")
    if args.reintentar_pendientes:
        cmd_base.append("--reintentar-pendientes")

    print("=" * 78)
    print("CORPUS INTEGRADO POR CONVOCATORIA - CONSOLIDADO + V2")
    print("=" * 78)
    print(f"Convocatoria..................... {codigo}")
    print(f"Modo consolidado................. {'APLICAR' if args.aplicar else 'SOLO VALIDACION'}")
    print("Capa V2.......................... SOLO LECTURA / ADITIVA")

    rc_base = ejecutar(cmd_base)
    if rc_base not in {0, 3}:
        print(f"RESULTADO: flujo consolidado no apto para continuar; rc={rc_base}")
        return rc_base

    cmd_v2 = [
        sys.executable,
        str(CORPUS_V2),
        "--codigo", codigo,
        "--db", str(db),
    ]
    if args.salida_v2:
        cmd_v2 += ["--salida", str(Path(args.salida_v2).expanduser().resolve())]

    rc_v2 = ejecutar(cmd_v2)
    if rc_v2 != 0:
        print(f"RESULTADO: capa V2 fallida; rc={rc_v2}")
        return 20 + rc_v2

    print("\n" + "=" * 78)
    print("RESULTADO INTEGRADO: OK")
    print("=" * 78)
    print("Corpus consolidado............... ejecutado sin cambiar su contrato")
    print("Corpus pseudonormativo V2........ generado de forma aditiva")
    print("Mezcla con articulos_fuente...... NO")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR FATAL: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise