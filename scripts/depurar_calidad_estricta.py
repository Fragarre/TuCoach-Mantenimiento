"""
OpoCoach-Mantenimiento - Depuración estricta de calidad de lote_preguntas.

Por defecto SOLO LECTURA. Con --aplicar:
- elimina TODA pregunta señalada por la auditoría de autosuficiencia;
- elimina TODA pregunta implicada en cualquier par duplicado/casi duplicado
  detectado por auditar_calidad_lote_preguntas.py con el umbral indicado;
- elimina previamente todos sus vínculos de banco_preguntas;
- crea backup completo de la base;
- exporta CSV con los IDs eliminados;
- ejecuta foreign_key_check e integrity_check antes de confirmar.

No intenta decidir cuál de dos preguntas dudosas es la correcta: elimina ambas.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
COPIAS = ROOT / "db" / "copias_seguridad"
REGISTROS = ROOT / "registros"
UMBRAL_DEFECTO = 0.94

# Importar exactamente el mismo detector que ya se ha probado.
sys.path.insert(0, str(SCRIPTS))
try:
    from auditar_calidad_lote_preguntas import auditar
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "ERROR: no se puede importar auditar_calidad_lote_preguntas.py: " + str(exc)
    )


def _placeholders(n: int) -> str:
    return ",".join("?" for _ in range(n))


def _crear_backup(db: Path) -> Path:
    COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = COPIAS / f"oposiciones_antes_depuracion_calidad_{marca}.sqlite3"
    shutil.copy2(db, destino)
    return destino


def _exportar_ids(db: Path, ids_auto: set[int], ids_dup: set[int]) -> Path:
    REGISTROS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    salida = REGISTROS / f"preguntas_eliminadas_calidad_{marca}.csv"
    ids = sorted(ids_auto | ids_dup)
    if not ids:
        return salida

    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        ph = _placeholders(len(ids))
        filas = con.execute(
            f"""
            SELECT id, tipo_fuente, tipo_clasificacion,
                   nombre_norma_normalizado, articulo_normalizado, enunciado
            FROM lote_preguntas
            WHERE id IN ({ph})
            ORDER BY id
            """,
            ids,
        ).fetchall()
    finally:
        con.close()

    with salida.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([
            "id", "motivo_autosuficiencia", "motivo_duplicidad",
            "tipo_fuente", "tipo_clasificacion", "norma", "articulo", "enunciado"
        ])
        for f in filas:
            pid = int(f["id"])
            w.writerow([
                pid,
                "SI" if pid in ids_auto else "NO",
                "SI" if pid in ids_dup else "NO",
                f["tipo_fuente"], f["tipo_clasificacion"],
                f["nombre_norma_normalizado"], f["articulo_normalizado"],
                f["enunciado"],
            ])
    return salida


def _contar_afectacion(db: Path, ids: set[int]) -> dict[str, int]:
    if not ids:
        return {"lote": 0, "vinculos": 0, "banco_preguntas": 0}
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        ph = _placeholders(len(ids))
        params = sorted(ids)
        return {
            "lote": con.execute(
                f"SELECT COUNT(*) FROM lote_preguntas WHERE id IN ({ph})", params
            ).fetchone()[0],
            "vinculos": con.execute(
                f"SELECT COUNT(*) FROM banco_preguntas WHERE pregunta_id IN ({ph})", params
            ).fetchone()[0],
            "banco_preguntas": con.execute(
                f"SELECT COUNT(DISTINCT pregunta_id) FROM banco_preguntas WHERE pregunta_id IN ({ph})", params
            ).fetchone()[0],
        }
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    ap.add_argument("--umbral", type=float, default=UMBRAL_DEFECTO)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    db = args.db.resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 2
    if not 0.80 <= args.umbral <= 1.0:
        print("ERROR: --umbral debe estar entre 0.80 y 1.0")
        return 2

    calidad, duplicados = auditar(db, args.umbral)
    ids_auto = {int(x["id"]) for x in calidad}
    ids_dup = {int(x[k]) for x in duplicados for k in ("id_a", "id_b")}
    ids = ids_auto | ids_dup
    afect = _contar_afectacion(db, ids)

    print("=" * 78)
    print("DEPURACIÓN ESTRICTA DE CALIDAD - lote_preguntas")
    print("=" * 78)
    print(f"Base: {db}")
    print(f"Umbral casi duplicado............... {args.umbral:.1%}")
    print(f"Preguntas por autosuficiencia....... {len(ids_auto)}")
    print(f"Preguntas implicadas en duplicidad.. {len(ids_dup)}")
    print(f"Solapamiento........................ {len(ids_auto & ids_dup)}")
    print(f"TOTAL preguntas a eliminar.......... {len(ids)}")
    print(f"Vínculos de banco a eliminar........ {afect['vinculos']}")

    if not ids:
        print("\nNo hay preguntas dudosas. No se modifica la base.")
        return 0

    if not args.aplicar:
        print("\nSOLO REVISIÓN: la base de datos NO ha sido modificada.")
        print("Para aplicar: añada --aplicar")
        return 0

    backup = _crear_backup(db)
    csv_eliminadas = _exportar_ids(db, ids_auto, ids_dup)

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        params = [(x,) for x in sorted(ids)]

        # El FK banco_preguntas.pregunta_id es RESTRICT: primero los vínculos.
        borrados_banco = 0
        for (pid,) in params:
            cur = con.execute("DELETE FROM banco_preguntas WHERE pregunta_id = ?", (pid,))
            borrados_banco += cur.rowcount

        borrados_lote = 0
        for (pid,) in params:
            cur = con.execute("DELETE FROM lote_preguntas WHERE id = ?", (pid,))
            borrados_lote += cur.rowcount

        if borrados_lote != len(ids):
            raise RuntimeError(
                f"Se esperaban {len(ids)} eliminaciones de lote_preguntas y se realizaron {borrados_lote}."
            )

        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check detectó {len(fk)} incidencia(s).")

        integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
        if str(integridad).lower() != "ok":
            raise RuntimeError(f"integrity_check: {integridad}")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    # Verificación de los criterios después del commit.
    calidad_final, duplicados_final = auditar(db, args.umbral)

    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        resto = con.execute("SELECT COUNT(*) FROM lote_preguntas").fetchone()[0]
        vinculos = con.execute("SELECT COUNT(*) FROM banco_preguntas").fetchone()[0]
    finally:
        con.close()

    print("\n" + "=" * 78)
    print("RESULTADO")
    print("=" * 78)
    print(f"Preguntas eliminadas................ {borrados_lote}")
    print(f"Vínculos eliminados................. {borrados_banco}")
    print(f"Preguntas restantes................. {resto}")
    print(f"Vínculos restantes.................. {vinculos}")
    print(f"Incidencias autosuficiencia restantes {len(calidad_final)}")
    print(f"Pares dudosos restantes............. {len(duplicados_final)}")
    print(f"Backup.............................. {backup}")
    print(f"CSV eliminadas...................... {csv_eliminadas}")

    if calidad_final or duplicados_final:
        print("\nERROR: la depuración no ha dejado a cero los criterios auditados.")
        print("Restaure el backup antes de continuar.")
        return 3

    print("\nVALIDACIÓN: OK. No quedan preguntas afectadas por estos criterios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
