"""APPLY protegido del mantenimiento de temario.

Mantiene un bloqueo local durante verificacion, backup y orquestacion real.
Solo acepta una convocatoria controlada y hashes procedentes de REVIEW.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from preparar_mantenimiento_temario import BACKUPS, DB_DEFECTO, RAIZ, crear_backup_sqlite, resolver_convocatoria, sha256_fichero

LOCK = BACKUPS / ".apply.lock"
ORQUESTADOR = RAIZ / "scripts" / "orquestar_mantenimiento_temario.py"


def validar_sha(valor: str, nombre: str) -> str:
    valor = valor.strip().lower()
    if len(valor) != 64 or any(c not in "0123456789abcdef" for c in valor):
        raise RuntimeError(f"{nombre} no es un SHA-256 valido.")
    return valor


@contextmanager
def bloqueo_exclusivo():
    BACKUPS.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Ya existe un APPLY en curso o quedo un bloqueo pendiente.") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "inicio": datetime.now().isoformat()}))
        yield
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


def aplicar(db: Path, convocatoria_id: int, csv_esperado: str, db_esperado: str) -> dict[str, object]:
    csv_esperado = validar_sha(csv_esperado, "csv_sha256_esperado")
    db_esperado = validar_sha(db_esperado, "db_sha256_esperado")
    with bloqueo_exclusivo():
        codigo, csv = resolver_convocatoria(db, convocatoria_id)
        if sha256_fichero(csv) != csv_esperado:
            raise RuntimeError("El temario.csv ha cambiado desde REVIEW. APPLY cancelado.")
        if sha256_fichero(db) != db_esperado:
            raise RuntimeError("La SQLite ha cambiado desde REVIEW. APPLY cancelado.")
        backup, backup_sha = crear_backup_sqlite(db, codigo)
        if sha256_fichero(csv) != csv_esperado or sha256_fichero(db) != db_esperado:
            raise RuntimeError("CSV o SQLite cambiaron durante la preparacion. APPLY cancelado antes de orquestar.")
        cmd = [sys.executable, str(ORQUESTADOR), "--codigo", codigo, "--csv", str(csv), "--db", str(db), "--aplicar"]
        proceso = subprocess.run(cmd, cwd=RAIZ, shell=False, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        salida = (proceso.stdout or b"").decode("utf-8", errors="replace")
        if proceso.returncode != 0:
            raise RuntimeError(f"El orquestador fallo con codigo {proceso.returncode}. Backup conservado: {backup.relative_to(RAIZ)}\n{salida[-12000:]}")
        return {
            "fase": "APPLY",
            "convocatoria_id": convocatoria_id,
            "codigo": codigo,
            "csv": str(csv.relative_to(RAIZ)),
            "csv_sha256_revisado": csv_esperado,
            "db_sha256_revisado": db_esperado,
            "backup_db": str(backup.relative_to(RAIZ)),
            "backup_db_sha256": backup_sha,
            "orquestador_returncode": proceso.returncode,
            "salida_orquestador": salida[-12000:],
            "aplicado": True,
        }


def main() -> int:
    p = argparse.ArgumentParser(description="APPLY protegido del mantenimiento de temario.")
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--convocatoria-id", type=int, required=True)
    p.add_argument("--csv-sha256-esperado", required=True)
    p.add_argument("--db-sha256-esperado", required=True)
    a = p.parse_args()
    try:
        print(json.dumps(aplicar(Path(a.db).expanduser().resolve(), a.convocatoria_id, a.csv_sha256_esperado, a.db_sha256_esperado), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
