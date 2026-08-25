from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"

CONVOCATORIA_ID = 3
PARTE_ID = 11
REGLA_ID = 10

def main() -> int:
    p = argparse.ArgumentParser(
        description="Hace que toda pregunta jurídica PRACTICA de A2 se asigne a ESPECIAL PRACTICA A201, con independencia de la parte del temario."
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--aplicar", action="store_true")
    args = p.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        raise RuntimeError(f"No existe la base de datos: {db}")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    try:
        fila = con.execute(
            """
            SELECT r.id, r.convocatoria_parte_id, r.temario_parte,
                   r.tipo_contenido, r.teorica_practica,
                   cp.convocatoria_id, cp.nombre
            FROM convocatoria_parte_reglas r
            JOIN convocatoria_partes cp ON cp.id = r.convocatoria_parte_id
            WHERE r.id = ?
            """,
            (REGLA_ID,),
        ).fetchone()

        if fila is None:
            raise RuntimeError(f"No existe la regla id={REGLA_ID}.")

        esperado = {
            "convocatoria_parte_id": PARTE_ID,
            "convocatoria_id": CONVOCATORIA_ID,
            "nombre": "ESPECIAL PRACTICA A201",
            "tipo_contenido": "JURIDICO",
            "teorica_practica": "PRACTICA",
        }
        for campo, valor in esperado.items():
            if fila[campo] != valor:
                raise RuntimeError(
                    f"Estado inesperado de la regla: {campo}={fila[campo]!r}, esperado={valor!r}"
                )

        print("=" * 78)
        print("REGLA PRACTICA A2")
        print("=" * 78)
        print(f"Base: {db}")
        print(f"Regla: id={REGLA_ID} -> {fila['nombre']}")
        print(f"Estado actual: temario_parte={fila['temario_parte']!r}")
        print("Estado objetivo: temario_parte=NULL")
        print("Efecto: toda JURIDICA + PRACTICA del temario A2 se asigna a")
        print("        ESPECIAL PRACTICA A201, venga de GENERAL o ESPECIAL.")

        if not args.aplicar:
            print("\nMODO: SOLO VALIDACION")
            print("La base de datos no ha sido modificada.")
            print("Para aplicar: vuelva a ejecutar con --aplicar")
            return 0

        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db.with_name(
            f"{db.stem}_backup_antes_regla_practica_A2_{marca}{db.suffix}"
        )
        shutil.copy2(db, backup)

        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            """
            UPDATE convocatoria_parte_reglas
            SET temario_parte = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND convocatoria_parte_id = ?
              AND tipo_contenido = 'JURIDICO'
              AND teorica_practica = 'PRACTICA'
            """,
            (REGLA_ID, PARTE_ID),
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                f"Se esperaba actualizar exactamente 1 regla y se actualizaron {cur.rowcount}."
            )

        comprobacion = con.execute(
            """
            SELECT temario_parte
            FROM convocatoria_parte_reglas
            WHERE id = ?
            """,
            (REGLA_ID,),
        ).fetchone()
        if comprobacion is None or comprobacion["temario_parte"] is not None:
            raise RuntimeError("La regla no ha quedado con temario_parte=NULL.")

        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check detecto {len(fk)} incidencia(s).")

        integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integridad != "ok":
            raise RuntimeError(f"integrity_check: {integridad}")

        con.commit()

        print("\nAPLICADO CORRECTAMENTE")
        print(f"Backup: {backup}")
        print("Regla practica A2: temario_parte=NULL")
        print("foreign_key_check: OK")
        print("integrity_check: OK")
        return 0

    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()

if __name__ == "__main__":
    raise SystemExit(main())
