"""Salvaguardas para REVIEW/APPLY del mantenimiento integral de temario.

Este módulo NO ejecuta el mantenimiento ni modifica el temario.csv.
Permite:
- resolver de forma controlada una convocatoria activa y su temario.csv;
- calcular SHA-256 de los artefactos revisados;
- verificar en APPLY que el CSV coincide exactamente con el revisado;
- crear un backup consistente de la SQLite antes de cualquier escritura.

La habilitación del tipo de trabajo en TuCoach Agent se hará en un paso posterior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
BACKUPS = RAIZ / "backups" / "mantenimiento_temario"


def sha256_fichero(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def resolver_convocatoria(db: Path, convocatoria_id: int) -> tuple[str, Path]:
    if convocatoria_id <= 0:
        raise RuntimeError("convocatoria_id debe ser un entero positivo.")
    if not db.is_file():
        raise RuntimeError(f"No existe la base: {db}")

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        columnas = {r[1] for r in con.execute("PRAGMA table_info(convocatorias)")}
        if not {"id", "codigo"} <= columnas:
            raise RuntimeError("La tabla convocatorias no contiene id/codigo.")
        campos = ["id", "codigo"]
        if "temario_csv" in columnas:
            campos.append("temario_csv")
        sql = f"SELECT {', '.join(campos)} FROM convocatorias WHERE id=?"
        params: list[object] = [convocatoria_id]
        if "activa" in columnas:
            sql += " AND COALESCE(activa,1)=1"
        fila = con.execute(sql, params).fetchone()

    if fila is None:
        raise RuntimeError("La convocatoria no existe o no está activa.")

    codigo = str(fila["codigo"]).strip()
    guardada = (
        str(fila["temario_csv"] or "").strip()
        if "temario_csv" in fila.keys()
        else ""
    )
    relativa = (
        Path(guardada)
        if guardada
        else Path("data_convocatorias") / f"CONV_{codigo}" / "temario.csv"
    )
    csv = relativa if relativa.is_absolute() else RAIZ / relativa
    csv = csv.resolve()

    raiz = RAIZ.resolve()
    try:
        csv.relative_to(raiz)
    except ValueError as exc:
        raise RuntimeError("El temario_csv debe estar dentro del repositorio.") from exc
    if not csv.is_file():
        raise RuntimeError(f"No existe el temario.csv de {codigo}: {csv}")
    return codigo, csv


def crear_backup_sqlite(db: Path, codigo: str) -> tuple[Path, str]:
    """Copia consistente usando la API backup de SQLite."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = BACKUPS / f"oposiciones_antes_temario_{codigo}_{sello}.sqlite3"
    with sqlite3.connect(db) as origen, sqlite3.connect(destino) as copia:
        origen.backup(copia)
    return destino, sha256_fichero(destino)


def revisar(db: Path, convocatoria_id: int) -> dict[str, object]:
    codigo, csv = resolver_convocatoria(db, convocatoria_id)
    return {
        "fase": "REVIEW",
        "convocatoria_id": convocatoria_id,
        "codigo": codigo,
        "csv": str(csv.relative_to(RAIZ)),
        "csv_sha256": sha256_fichero(csv),
        "db_sha256": sha256_fichero(db),
        "db_modificada": False,
    }


def preparar_apply(
    db: Path,
    convocatoria_id: int,
    csv_sha256_esperado: str,
) -> dict[str, object]:
    codigo, csv = resolver_convocatoria(db, convocatoria_id)
    actual = sha256_fichero(csv)
    esperado = csv_sha256_esperado.strip().lower()
    if len(esperado) != 64 or any(c not in "0123456789abcdef" for c in esperado):
        raise RuntimeError("csv_sha256_esperado no es un SHA-256 válido.")
    if actual != esperado:
        raise RuntimeError(
            "El temario.csv ha cambiado desde REVIEW. APPLY cancelado antes de modificar datos."
        )
    backup, backup_sha = crear_backup_sqlite(db, codigo)
    return {
        "fase": "APPLY_PREPARADO",
        "convocatoria_id": convocatoria_id,
        "codigo": codigo,
        "csv": str(csv.relative_to(RAIZ)),
        "csv_sha256": actual,
        "backup_db": str(backup.relative_to(RAIZ)),
        "backup_db_sha256": backup_sha,
        "listo_para_aplicar": True,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Salvaguardas REVIEW/APPLY para mantenimiento de temario."
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--convocatoria-id", type=int, required=True)
    p.add_argument("--fase", choices=("review", "preparar-apply"), required=True)
    p.add_argument("--csv-sha256-esperado")
    a = p.parse_args()

    db = Path(a.db).expanduser().resolve()
    try:
        if a.fase == "review":
            if a.csv_sha256_esperado:
                raise RuntimeError("REVIEW no acepta csv_sha256_esperado.")
            resultado = revisar(db, a.convocatoria_id)
        else:
            if not a.csv_sha256_esperado:
                raise RuntimeError("APPLY requiere csv_sha256_esperado.")
            resultado = preparar_apply(
                db,
                a.convocatoria_id,
                a.csv_sha256_esperado,
            )
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
