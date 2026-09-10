"""
Sincronización aditiva de bancos compatible con Constructor Temario V2.

No sustituye sincronizar_bancos.py:
1. ejecuta el sincronizador consolidado para REAL/NO_JURIDICA sin validación final;
2. revisa/aplica mantener_banco_preguntas_v2.py sólo en convocatorias con
   pregunta_contextos_v2;
3. termina con validacion_completa_v2.py.

Sin --aplicar no modifica bancos.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from sincronizar_bancos import sincronizar_todos_bancos

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SCRIPT_BANCO_V2 = RAIZ / "scripts" / "mantener_banco_preguntas_v2.py"
VALIDADOR_V2 = RAIZ / "scripts" / "validacion_completa_v2.py"
AUDITORIA_V2 = RAIZ / "auditorias" / "mantener_banco_v2" / "resumen.json"


def existe_tabla(con: sqlite3.Connection, nombre: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nombre,),
    ).fetchone() is not None


def convocatorias_v2(db: Path) -> list[tuple[int,str]]:
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        if not existe_tabla(con, "pregunta_contextos_v2"):
            return []
        filas = con.execute(
            """
            SELECT DISTINCT c.id,c.codigo
            FROM pregunta_contextos_v2 pc
            JOIN temario_temas tt ON tt.id=pc.tema_id
            JOIN temarios t ON t.id=tt.temario_id
            JOIN convocatorias c ON c.id=t.convocatoria_id
            WHERE COALESCE(c.activa,1)=1
            ORDER BY c.id
            """
        ).fetchall()
    return [(int(i),str(c)) for i,c in filas]


def ejecutar_banco_v2(db: Path, cid: int, guardar: bool) -> dict:
    cmd = [
        sys.executable,
        str(SCRIPT_BANCO_V2),
        "--db", str(db),
        "--convocatoria-id", str(cid),
    ]
    if guardar:
        cmd.append("--guardar")
    r = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        if (r.stdout+r.stderr).strip():
            print((r.stdout+r.stderr).strip())
        raise RuntimeError(f"Banco V2 convocatoria {cid}: código {r.returncode}")
    if not AUDITORIA_V2.is_file():
        raise RuntimeError("No se generó resumen de mantener_banco_preguntas_v2.py")
    datos = json.loads(AUDITORIA_V2.read_text(encoding="utf-8"))
    if int(datos.get("convocatoria_id",-1)) != cid:
        raise RuntimeError("El resumen V2 no corresponde a la convocatoria esperada")
    if int(datos.get("v2_invalidas",0)) != 0:
        raise RuntimeError(f"Convocatoria {cid}: contextos V2 inválidos")
    return datos


def validar_final(db: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(VALIDADOR_V2), "--db", str(db)],
        cwd=RAIZ,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError("validacion_completa_v2.py detectó incidencias")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        print(f"ERROR: no existe la BD: {db}", file=sys.stderr)
        return 1

    try:
        print("="*78)
        print("SINCRONIZACION DE BANCOS V2")
        print("="*78)
        print(f"BD............................... {db}")
        print(f"Modo............................. {'APLICAR' if args.aplicar else 'SOLO REVISION'}")

        # Camino consolidado sin validación final legacy: esa validación no conoce
        # PSEUDO/MIXTA y sería incorrecta después de incorporar preguntas V2.
        sincronizar_todos_bancos(db, aplicar=args.aplicar, validar_final=False)

        cands = convocatorias_v2(db)
        print(f"\nConvocatorias con contexto V2.... {len(cands)}")
        revisiones: list[tuple[int,str,dict]] = []
        for cid,codigo in cands:
            d = ejecutar_banco_v2(db,cid,False)
            revisiones.append((cid,codigo,d))
            print(f"  {cid} | {codigo:<24} nuevas={int(d['v2_nuevas'])}")

        if args.aplicar:
            for cid,codigo,d in revisiones:
                if int(d["v2_nuevas"]) <= 0:
                    continue
                ejecutar_banco_v2(db,cid,True)
                print(f"  {codigo}: V2 guardado")

        validar_final(db)
        print("\nSINCRONIZACION V2: CORRECTA")
        return 0
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
