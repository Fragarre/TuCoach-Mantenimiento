"""Orquestador genérico del mantenimiento de temario de una convocatoria.

Propaga un temario.csv ya aprobado a BD, corpus, normalización y banco.
No deduce ni corrige el contenido material del CSV y nunca modifica lote_preguntas.

Sin --aplicar compara CSV y BD en solo lectura y muestra el delta previsto.
Con --aplicar ejecuta la cadena completa y sus postcondiciones de idempotencia.
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


def ejecutar(nombre: str, *args: str) -> None:
    cmd = [sys.executable, str(SCRIPTS / nombre), *args]
    print("\n" + "=" * 78)
    print(" ".join(str(x) for x in cmd))
    print("=" * 78)
    rc = subprocess.run(cmd, cwd=RAIZ, check=False).returncode
    if rc != 0:
        raise RuntimeError(f"Falló {nombre} (código {rc}). Proceso detenido.")


def comprobar_convocatoria(db: Path, codigo: str) -> None:
    with sqlite3.connect(db) as con:
        fila = con.execute("SELECT id FROM convocatorias WHERE codigo=?", (codigo,)).fetchone()
        if fila is None:
            raise RuntimeError(f"No existe la convocatoria {codigo}.")


def main() -> int:
    p = argparse.ArgumentParser(description="Actualiza BD, corpus, normalización y banco desde un temario.csv aprobado.")
    p.add_argument("--codigo", required=True)
    p.add_argument("--csv", required=True)
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--aplicar", action="store_true")
    args = p.parse_args()

    db = Path(args.db).expanduser().resolve()
    csv = Path(args.csv).expanduser().resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 2
    if not csv.is_file():
        print(f"ERROR: no existe el CSV: {csv}")
        return 2

    try:
        comprobar_convocatoria(db, args.codigo)
        print("=" * 78)
        print("ORQUESTADOR DE MANTENIMIENTO DE TEMARIO")
        print("=" * 78)
        print(f"Convocatoria: {args.codigo}")
        print(f"CSV:          {csv}")
        print(f"BD:           {db}")
        print(f"Modo:         {'APLICAR' if args.aplicar else 'SOLO REVISIÓN'}")
        print("lote_preguntas: NO SE MODIFICA")
        print("\nCadena:")
        print("  1. Importar/sincronizar temario CSV en BD")
        print("  2. Construir/actualizar corpus incremental de la convocatoria")
        print("  3. Normalizar identidades normativas")
        print("  4. Validar temario/corpus/normalización")
        print("  5. Eliminar del banco vínculos jurídicos sobrantes")
        print("  6. Añadir al banco preguntas que correspondan")
        print("  7. Comprobar idempotencia del banco")
        print("  8. Validación final")

        db_s = str(db)
        if not args.aplicar:
            ejecutar("revisar_delta_temario.py", "--db", db_s, "--codigo", args.codigo, "--csv", str(csv))
            print("\nLa base NO ha sido modificada. Use --aplicar para ejecutar la cadena.")
            return 0

        ejecutar("importar_temario.py", "--db", db_s, "--convocatoria", args.codigo, "--csv", str(csv), "--sincronizar-eliminaciones")
        ejecutar("construir_corpus_incremental_convocatoria.py", "--db", db_s, "--codigo", args.codigo, "--aplicar")
        ejecutar("normalizar_temario_convocatoria.py", "--db", db_s, "--codigo", args.codigo)
        ejecutar("validar_temario_convocatoria.py", "--db", db_s, "--codigo", args.codigo)
        ejecutar("reconciliar_sobrantes_banco.py", "--db", db_s, "--constructor", str(SCRIPTS / "mantener_banco_preguntas.py"), "--codigo", args.codigo, "--guardar")
        ejecutar("mantener_banco_preguntas.py", "--db", db_s, "--codigo", args.codigo, "--guardar")
        ejecutar("reconciliar_sobrantes_banco.py", "--db", db_s, "--constructor", str(SCRIPTS / "mantener_banco_preguntas.py"), "--codigo", args.codigo)
        ejecutar("mantener_banco_preguntas.py", "--db", db_s, "--codigo", args.codigo)
        ejecutar("validar_temario_convocatoria.py", "--db", db_s, "--codigo", args.codigo)

        print("\n" + "=" * 78)
        print(f"RESULTADO: OK - mantenimiento completo de {args.codigo}")
        print("lote_preguntas: NO MODIFICADO por este orquestador")
        print("=" * 78)
        return 0
    except Exception as exc:
        print(f"\nERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
