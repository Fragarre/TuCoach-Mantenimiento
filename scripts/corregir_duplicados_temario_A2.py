from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"

# Referencias duplicadas tras normalizar 47.1 -> 47 y 48.1 -> 48.
# Se conservan las referencias completas del tema ESPECIAL 3 (8215, 8216)
# y se retiran únicamente las duplicadas del tema ESPECIAL 6 (8300, 8301).
ELIMINAR = {
    8300: {
        "tema_id": 8385,
        "parte": "ESPECIAL",
        "numero_tema": 6,
        "norma_id": 43,
        "articulo_solicitado": "47.1",
        "articulo_fuente_id": 128,
    },
    8301: {
        "tema_id": 8385,
        "parte": "ESPECIAL",
        "numero_tema": 6,
        "norma_id": 43,
        "articulo_solicitado": "48.1",
        "articulo_fuente_id": 129,
    },
}

CONSERVAR = {
    8215: {
        "tema_id": 8323,
        "parte": "ESPECIAL",
        "numero_tema": 3,
        "norma_id": 43,
        "articulo_solicitado": "47",
        "articulo_fuente_id": 128,
    },
    8216: {
        "tema_id": 8323,
        "parte": "ESPECIAL",
        "numero_tema": 3,
        "norma_id": 43,
        "articulo_solicitado": "48",
        "articulo_fuente_id": 129,
    },
}


def leer_referencia(con: sqlite3.Connection, ref_id: int):
    return con.execute(
        """
        SELECT
            tr.id,
            tr.tema_id,
            tt.parte,
            tt.numero_tema,
            tr.norma_id,
            tr.articulo_solicitado,
            tr.articulo_fuente_id
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id = tr.tema_id
        JOIN temarios t ON t.id = tt.temario_id
        WHERE tr.id = ?
          AND t.convocatoria_id = 3
        """,
        (ref_id,),
    ).fetchone()


def validar_fila(fila, esperado: dict, ref_id: int) -> None:
    if fila is None:
        raise RuntimeError(f"No existe la referencia esperada id={ref_id}.")
    for campo, valor in esperado.items():
        if fila[campo] != valor:
            raise RuntimeError(
                f"La referencia id={ref_id} no coincide con el estado esperado: "
                f"{campo}={fila[campo]!r}, esperado={valor!r}."
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retira dos referencias duplicadas del temario A2 sin alterar el resto."
    )
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        raise RuntimeError(f"No existe la base de datos: {db}")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    try:
        for ref_id, esperado in CONSERVAR.items():
            validar_fila(leer_referencia(con, ref_id), esperado, ref_id)
        for ref_id, esperado in ELIMINAR.items():
            validar_fila(leer_referencia(con, ref_id), esperado, ref_id)

        print("=" * 78)
        print("CORRECCIÓN DUPLICIDADES TEMARIO A2")
        print("=" * 78)
        print(f"Base: {db}")
        print("Se conservarán:")
        print("  - id=8215 | ESPECIAL tema 3 | LEY 39/2015 | art. 47")
        print("  - id=8216 | ESPECIAL tema 3 | LEY 39/2015 | art. 48")
        print("Se retirarán:")
        print("  - id=8300 | ESPECIAL tema 6 | LEY 39/2015 | art. 47.1")
        print("  - id=8301 | ESPECIAL tema 6 | LEY 39/2015 | art. 48.1")

        if not args.aplicar:
            print("\nMODO: SOLO VALIDACIÓN")
            print("La base de datos no ha sido modificada.")
            print("Para aplicar: vuelva a ejecutar con --aplicar")
            return 0

        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db.with_name(f"{db.stem}_backup_antes_quitar_duplicados_A2_{marca}{db.suffix}")
        shutil.copy2(db, backup)

        con.execute("BEGIN IMMEDIATE")
        for ref_id in ELIMINAR:
            cur = con.execute("DELETE FROM temario_referencias WHERE id = ?", (ref_id,))
            if cur.rowcount != 1:
                raise RuntimeError(f"No se pudo eliminar exactamente una fila para id={ref_id}.")

        # Las referencias que deben conservarse siguen presentes e intactas.
        for ref_id, esperado in CONSERVAR.items():
            validar_fila(leer_referencia(con, ref_id), esperado, ref_id)

        # Las eliminadas deben haber desaparecido.
        for ref_id in ELIMINAR:
            if leer_referencia(con, ref_id) is not None:
                raise RuntimeError(f"La referencia id={ref_id} sigue presente tras el DELETE.")

        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check detectó {len(fk)} incidencia(s).")

        integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integridad != "ok":
            raise RuntimeError(f"integrity_check: {integridad}")

        con.commit()

        print("\nAPLICADO CORRECTAMENTE")
        print(f"Referencias eliminadas: {len(ELIMINAR)}")
        print(f"Backup: {backup}")
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
