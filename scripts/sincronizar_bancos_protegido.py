"""REVIEW/APPLY protegido de la sincronización global de bancos.

No contiene reglas de selección. Delega siempre en sincronizar_bancos.py.
REVIEW congela el SHA-256 de la SQLite revisada.
APPLY exige ese mismo SHA, toma un backup SQLite consistente y mantiene\nun lock exclusivo para esta operación protegida.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from aplicar_mantenimiento_temario import validar_sha
from preparar_mantenimiento_temario import sha256_fichero

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SINCRONIZADOR = RAIZ / "scripts" / "sincronizar_bancos.py"
BACKUPS = RAIZ / "backups" / "sincronizacion_bancos"
LOCK = RAIZ / "backups" / ".mantenimiento_apply.lock"


@contextmanager
def bloqueo_exclusivo():
    """Bloqueo común para APPLY protegidos que escriben la SQLite maestra."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Ya existe un APPLY de mantenimiento en curso o quedó un bloqueo pendiente.") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "inicio": datetime.now().isoformat()}))
        yield
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


def _ejecutar(db: Path, *, aplicar: bool) -> tuple[int, str]:
    cmd = [sys.executable, "-X", "utf8", str(SINCRONIZADOR), "--db", str(db)]
    if aplicar:
        cmd.append("--aplicar")
    entorno = os.environ.copy()
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["PYTHONUTF8"] = "1"
    proceso = subprocess.run(cmd, cwd=RAIZ, shell=False, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=entorno)
    return proceso.returncode, (proceso.stdout or b"").decode("utf-8", errors="replace")


def _backup_sqlite(db: Path) -> tuple[Path, str]:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destino = BACKUPS / f"oposiciones_antes_sincronizar_bancos_{sello}.sqlite3"
    if destino.exists():
        raise RuntimeError(f"Ya existe el backup de destino: {destino}")
    with sqlite3.connect(db) as origen, sqlite3.connect(destino) as copia:
        origen.backup(copia)
    return destino, sha256_fichero(destino)


def revisar(db: Path) -> dict[str, object]:
    if not db.is_file():
        raise RuntimeError(f"No existe la base: {db}")
    sha_antes = sha256_fichero(db)
    rc, salida = _ejecutar(db, aplicar=False)
    if rc != 0:
        raise RuntimeError(f"REVIEW de bancos falló con código {rc}.\n{salida[-12000:]}")
    sha_despues = sha256_fichero(db)
    if sha_despues != sha_antes:
        raise RuntimeError("La SQLite cambió durante el REVIEW. Repita la revisión.")
    return {"fase": "REVIEW", "db_sha256": sha_antes, "db_modificada": False, "sincronizador_returncode": rc, "salida_sincronizador": salida[-12000:]}


def aplicar(db: Path, db_esperado: str, *, gestionar_lock: bool = True) -> dict[str, object]:
    db_esperado = validar_sha(db_esperado, "db_sha256_esperado")
    if not db.is_file():
        raise RuntimeError(f"No existe la base: {db}")
    def _aplicar_bajo_lock() -> dict[str, object]:
        if sha256_fichero(db) != db_esperado:
            raise RuntimeError("La SQLite ha cambiado desde REVIEW. APPLY cancelado.")
        backup, backup_sha = _backup_sqlite(db)
        if sha256_fichero(db) != db_esperado:
            raise RuntimeError("La SQLite cambió durante la preparación. APPLY cancelado antes de sincronizar.")
        rc, salida = _ejecutar(db, aplicar=True)
        if rc != 0:
            raise RuntimeError(f"La sincronización falló con código {rc}. Backup global conservado: {backup.relative_to(RAIZ)}\n{salida[-12000:]}")
        return {"fase": "APPLY", "db_sha256_revisado": db_esperado, "backup_db": str(backup.relative_to(RAIZ)), "backup_db_sha256": backup_sha, "sincronizador_returncode": rc, "salida_sincronizador": salida[-12000:], "aplicado": True}

    if gestionar_lock:
        with bloqueo_exclusivo():
            return _aplicar_bajo_lock()
    return _aplicar_bajo_lock()


def main() -> int:
    p = argparse.ArgumentParser(description="REVIEW/APPLY protegido de sincronización global de bancos.")
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--fase", choices=("review", "apply"), required=True)
    p.add_argument("--db-sha256-esperado")
    a = p.parse_args()
    db = Path(a.db).expanduser().resolve()
    try:
        if a.fase == "review":
            if a.db_sha256_esperado:
                raise RuntimeError("REVIEW no acepta un SHA esperado.")
            resultado = revisar(db)
        else:
            if not a.db_sha256_esperado:
                raise RuntimeError("APPLY requiere el SHA-256 de SQLite obtenido en REVIEW.")
            resultado = aplicar(db, a.db_sha256_esperado)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
