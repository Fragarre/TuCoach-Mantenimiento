from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def cargar_constructor(ruta: Path):
    spec = importlib.util.spec_from_file_location("mantener_banco_preguntas_reconciliar", ruta)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se puede cargar el constructor: {ruta}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def calcular_esperadas(con: sqlite3.Connection, constructor, convocatoria_id: int) -> set[int]:
    temarios = con.execute(
        "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id",
        (convocatoria_id,),
    ).fetchall()
    if len(temarios) != 1:
        raise RuntimeError(
            f"La convocatoria {convocatoria_id} debe tener exactamente un temario; encontrados={len(temarios)}"
        )
    temario_id = int(temarios[0]["id"])

    refs, inv_refs, dup_refs = constructor.cargar_referencias_juridicas(con, temario_id)
    eqs, inv_eqs, dup_eqs = constructor.cargar_equivalencias_no_juridicas(con, temario_id)
    if inv_refs or dup_refs or inv_eqs or dup_eqs:
        raise RuntimeError(
            "No se puede reconciliar: existen referencias/equivalencias inválidas o ambiguas en el temario."
        )

    # existentes={} es deliberado: aquí se reconstruye el universo que sería
    # incorporable desde cero con las reglas actuales, no solo las novedades.
    jur = constructor.seleccionar_juridicas(con, convocatoria_id, refs, {})
    nojur = constructor.seleccionar_no_juridicas(con, eqs, {})
    return {int(p["id"]) for p in jur["nuevas"] + nojur["nuevas"]}


def main() -> int:
    p = argparse.ArgumentParser(
        description="Revisa y, opcionalmente, elimina del banco vínculos que ya no cumplen el temario/reglas actuales."
    )
    p.add_argument("--db", required=True)
    p.add_argument("--constructor", required=True)
    p.add_argument("--convocatoria-id", type=int, required=True)
    p.add_argument("--guardar", action="store_true")
    args = p.parse_args()

    db = Path(args.db).resolve()
    constructor_path = Path(args.constructor).resolve()
    if not db.is_file():
        raise FileNotFoundError(db)
    if not constructor_path.is_file():
        raise FileNotFoundError(constructor_path)

    constructor = cargar_constructor(constructor_path)

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")

        conv = con.execute(
            "SELECT id,codigo FROM convocatorias WHERE id=?",
            (args.convocatoria_id,),
        ).fetchone()
        if conv is None:
            raise RuntimeError(f"No existe convocatoria id={args.convocatoria_id}")

        esperadas = calcular_esperadas(con, constructor, args.convocatoria_id)
        reales_rows = con.execute(
            """
            SELECT bp.id AS banco_id, bp.pregunta_id,
                   lp.tipo_clasificacion, lp.tipo_fuente,
                   lp.norma_id_normalizada, lp.articulo_normalizado,
                   lp.nombre_norma_normalizado,
                   tt.parte, tt.numero_tema, tt.titulo
            FROM banco_preguntas bp
            JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
            LEFT JOIN banco_preguntas_temas bpt
              ON bpt.banco_pregunta_id=bp.id AND bpt.es_principal=1
            LEFT JOIN temario_temas tt ON tt.id=bpt.tema_id
            WHERE bp.convocatoria_id=?
            ORDER BY bp.pregunta_id
            """,
            (args.convocatoria_id,),
        ).fetchall()
        reales = {int(r["pregunta_id"]): r for r in reales_rows}
        sobrantes_ids = sorted(set(reales) - esperadas)

        print("=" * 78)
        print("RECONCILIACIÓN DE SOBRANTES DEL BANCO")
        print("=" * 78)
        print(f"Convocatoria......................... {conv['id']} | {conv['codigo']}")
        print(f"Modo................................ {'GUARDAR' if args.guardar else 'SOLO REVISIÓN'}")
        print(f"Esperadas por reglas actuales........ {len(esperadas)}")
        print(f"Reales en banco...................... {len(reales)}")
        print(f"Sobrantes............................ {len(sobrantes_ids)}")

        if sobrantes_ids:
            print("\nDETALLE DE SOBRANTES")
            print("-" * 78)
            for pid in sobrantes_ids:
                r = reales[pid]
                print(
                    f"pregunta={pid} | banco={r['banco_id']} | {r['tipo_clasificacion']} | "
                    f"norma_id={r['norma_id_normalizada']} | art={r['articulo_normalizado'] or '-'} | "
                    f"{r['parte'] or '-'} tema {r['numero_tema'] if r['numero_tema'] is not None else '-'}"
                )

        if not args.guardar:
            print("\nLa base NO ha sido modificada.")
            return 1 if sobrantes_ids else 0

        if not sobrantes_ids:
            print("\nNo hay cambios que guardar.")
            return 0

    # Backup con la conexión cerrada.
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"{db.stem}_backup_reconciliacion_{marca}{db.suffix}")
    shutil.copy2(db, backup)
    print(f"\nBackup............................... {backup}")

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        marcadores = ",".join("?" for _ in sobrantes_ids)
        # Solo se elimina la vinculación del banco. banco_preguntas_temas cae por CASCADE.
        # lote_preguntas no se modifica.
        cur = con.execute(
            f"DELETE FROM banco_preguntas WHERE convocatoria_id=? AND pregunta_id IN ({marcadores})",
            (args.convocatoria_id, *sobrantes_ids),
        )
        if cur.rowcount != len(sobrantes_ids):
            con.rollback()
            raise RuntimeError(
                f"Se iban a eliminar {len(sobrantes_ids)} vínculos, pero DELETE afectó {cur.rowcount}. ROLLBACK."
            )

        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            con.rollback()
            raise RuntimeError("integrity_check no es ok. ROLLBACK.")
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            con.rollback()
            raise RuntimeError(f"foreign_key_check detectó {len(fk)} errores. ROLLBACK.")
        con.commit()

    print(f"Vínculos eliminados.................. {len(sobrantes_ids)}")
    print("lote_preguntas modificado............ NO")
    print("Resultado............................ OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
