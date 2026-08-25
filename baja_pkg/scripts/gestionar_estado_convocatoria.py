from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
COPIAS = RAIZ / "db" / "copias_seguridad"


def columnas(con: sqlite3.Connection, tabla: str) -> set[str]:
    return {str(r[1]) for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()}


def tiene_activa(con: sqlite3.Connection) -> bool:
    return "activa" in columnas(con, "convocatorias")


def asegurar_activa(con: sqlite3.Connection) -> None:
    if not tiene_activa(con):
        con.execute(
            "ALTER TABLE convocatorias "
            "ADD COLUMN activa INTEGER NOT NULL DEFAULT 1 CHECK(activa IN (0,1))"
        )


def backup(db: Path, codigo: str, accion: str) -> Path:
    COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    seguro = "".join(c if c.isalnum() or c in "-_" else "_" for c in codigo)
    destino = COPIAS / f"{db.stem}_antes_{accion}_{seguro}_{marca}{db.suffix}"
    shutil.copy2(db, destino)
    return destino


def listar(db: Path) -> int:
    uri = db.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        activa_existe = tiene_activa(con)
        campo = "c.activa" if activa_existe else "1"
        filas = con.execute(
            f"""
            SELECT c.id,c.codigo,c.puesto,{campo} AS activa,
                   (SELECT COUNT(*) FROM banco_preguntas bp WHERE bp.convocatoria_id=c.id) AS banco
            FROM convocatorias c
            ORDER BY c.id
            """
        ).fetchall()
        print("CONVOCATORIAS")
        print("-" * 78)
        for f in filas:
            estado = "ACTIVA" if int(f["activa"] or 0) == 1 else "INACTIVA"
            print(f"{f['id']:>3} | {f['codigo']:<24} | {estado:<8} | banco={f['banco']}")
        if not activa_existe:
            print("\nLa columna 'activa' aún no existe. Todas se consideran activas.")
    return 0


def resolver(con: sqlite3.Connection, cid: int | None, codigo: str | None) -> sqlite3.Row:
    campo = "activa" if tiene_activa(con) else "1 AS activa"
    if cid is not None:
        fila = con.execute(f"SELECT *, {campo} FROM convocatorias WHERE id=?", (cid,)).fetchone()
    else:
        fila = con.execute(f"SELECT *, {campo} FROM convocatorias WHERE codigo=?", ((codigo or '').strip(),)).fetchone()
    if fila is None:
        raise RuntimeError("La convocatoria indicada no existe.")
    return fila


def diagnostico(con: sqlite3.Connection, cid: int) -> dict[str, int]:
    return {
        "banco": int(con.execute("SELECT COUNT(*) FROM banco_preguntas WHERE convocatoria_id=?", (cid,)).fetchone()[0]),
        "partes": int(con.execute("SELECT COUNT(*) FROM convocatoria_partes WHERE convocatoria_id=?", (cid,)).fetchone()[0]),
        "temarios": int(con.execute("SELECT COUNT(*) FROM temarios WHERE convocatoria_id=?", (cid,)).fetchone()[0]),
        "temas": int(con.execute("SELECT COUNT(*) FROM temario_temas tt JOIN temarios t ON t.id=tt.temario_id WHERE t.convocatoria_id=?", (cid,)).fetchone()[0]),
        "referencias": int(con.execute("SELECT COUNT(*) FROM temario_referencias tr JOIN temario_temas tt ON tt.id=tr.tema_id JOIN temarios t ON t.id=tt.temario_id WHERE t.convocatoria_id=?", (cid,)).fetchone()[0]),
        "docs_rag": int(con.execute("SELECT COUNT(*) FROM convocatoria_documentos_corpus WHERE convocatoria_id=?", (cid,)).fetchone()[0]) if "convocatoria_documentos_corpus" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} else 0,
    }


def ejecutar(db: Path, cid: int | None, codigo: str | None, accion: str, aplicar: bool) -> int:
    if not db.is_file():
        raise FileNotFoundError(db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        fila = resolver(con, cid, codigo)
        convocatoria_id = int(fila["id"])
        codigo_real = str(fila["codigo"])
        actual = int(fila["activa"] or 0)
        d = diagnostico(con, convocatoria_id)

        print("=" * 78)
        print("GESTIÓN DE ESTADO DE CONVOCATORIA")
        print("=" * 78)
        print(f"Convocatoria: {convocatoria_id} | {codigo_real}")
        print(f"Estado actual: {'ACTIVA' if actual == 1 else 'INACTIVA'}")
        print(f"Banco vinculado: {d['banco']}")
        print(f"Partes: {d['partes']} | Temarios: {d['temarios']} | Temas: {d['temas']}")
        print(f"Referencias del temario: {d['referencias']} | Documentos RAG vinculados: {d['docs_rag']}")
        print()

        if accion == "baja":
            print("La baja marcará la convocatoria como INACTIVA y eliminará únicamente")
            print("sus vínculos de banco_preguntas. Se conservan lote_preguntas, temario,")
            print("reglas, modelo, normas, artículos fuente y corpus RAG.")
            cambios = (actual != 0) or d["banco"] > 0 or not tiene_activa(con)
        else:
            print("La reactivación marcará la convocatoria como ACTIVA.")
            print("No reconstruye el banco automáticamente.")
            cambios = actual != 1

        if not aplicar:
            print("\nModo SOLO VALIDAR: no se modifica la base.")
            print(f"Cambios previstos: {'SÍ' if cambios else 'NO'}")
            return 0

        if not cambios:
            print("\nOperación idempotente: no hay cambios que aplicar.")
            return 0

        copia = backup(db, codigo_real, accion)
        print(f"\nCopia de seguridad: {copia}")

        con.execute("BEGIN")
        asegurar_activa(con)
        if accion == "baja":
            con.execute("UPDATE convocatorias SET activa=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (convocatoria_id,))
            con.execute("DELETE FROM banco_preguntas WHERE convocatoria_id=?", (convocatoria_id,))
        else:
            con.execute("UPDATE convocatorias SET activa=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (convocatoria_id,))

        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check detectó {len(fk)} incidencias")
        con.commit()

        despues = diagnostico(con, convocatoria_id)
        estado = con.execute("SELECT activa FROM convocatorias WHERE id=?", (convocatoria_id,)).fetchone()[0]
        print("\nRESULTADO")
        print(f"Estado final: {'ACTIVA' if int(estado)==1 else 'INACTIVA'}")
        print(f"Banco final: {despues['banco']}")
        print("Corpus normativo/RAG: conservado")
        print("Temario y configuración histórica: conservados")
        print("foreign_key_check: OK")
        if accion == "reactivar":
            print("Siguiente paso: sincronizar bancos y ejecutar validación completa.")
        return 0
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Baja lógica o reactivación de convocatorias.")
    p.add_argument("--db", type=Path, default=DB_DEFECTO)
    p.add_argument("--listar", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--baja", action="store_true")
    g.add_argument("--reactivar", action="store_true")
    ident = p.add_mutually_exclusive_group()
    ident.add_argument("--convocatoria-id", type=int)
    ident.add_argument("--codigo")
    p.add_argument("--aplicar", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    db = args.db.expanduser().resolve()
    if args.listar:
        return listar(db)
    if not (args.baja or args.reactivar):
        raise RuntimeError("Indique --baja, --reactivar o --listar.")
    if args.convocatoria_id is None and not args.codigo:
        raise RuntimeError("Indique --convocatoria-id o --codigo.")
    return ejecutar(db, args.convocatoria_id, args.codigo, "baja" if args.baja else "reactivar", args.aplicar)


if __name__ == "__main__":
    raise SystemExit(main())
