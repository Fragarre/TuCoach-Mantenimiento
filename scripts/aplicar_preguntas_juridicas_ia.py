"""APPLY protegido de un lote REVIEW de preguntas jurídicas IA.

Publica exclusivamente candidatas VALIDADA_IA ya generadas. No invoca la IA.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from generar_preguntas_juridicas_ia import (
    aprobar,
    configurar_db_auxiliar,
    conectar_maestra,
)
from preparar_mantenimiento_temario import sha256_fichero
from sincronizar_bancos_protegido import aplicar as aplicar_sincronizacion_bancos
from sincronizar_bancos_protegido import bloqueo_exclusivo

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
LOTES = RAIZ / "registros" / "lotes_preguntas_ia"
BACKUPS = RAIZ / "backups" / "generacion_preguntas_ia"


def validar_sha(valor: str, nombre: str) -> str:
    valor = str(valor or "").strip().lower()
    if len(valor) != 64 or any(c not in "0123456789abcdef" for c in valor):
        raise RuntimeError(f"{nombre} no es un SHA-256 válido.")
    return valor


def ruta_lote(lote_id: str) -> Path:
    import re
    lote_id = str(lote_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", lote_id):
        raise RuntimeError("Identificador de lote REVIEW no válido.")
    return (LOTES / f"{lote_id}.sqlite3").resolve()


def backup_sqlite(db: Path, lote_id: str) -> tuple[Path, str]:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destino = BACKUPS / f"oposiciones_antes_preguntas_ia_{lote_id}_{sello}.sqlite3"
    if destino.exists():
        raise RuntimeError(f"Ya existe el backup de destino: {destino}")
    with sqlite3.connect(db) as origen, sqlite3.connect(destino) as copia:
        origen.backup(copia)
    return destino, sha256_fichero(destino)


def aplicar(db: Path, lote_id: str, db_sha: str, lote_sha: str) -> dict[str, object]:
    db_sha = validar_sha(db_sha, "db_sha256_esperado")
    lote_sha = validar_sha(lote_sha, "lote_sha256_esperado")
    lote = ruta_lote(lote_id)
    if not db.is_file():
        raise RuntimeError(f"No existe la base maestra: {db}")
    if not lote.is_file():
        raise RuntimeError(f"No existe el lote REVIEW: {lote}")

    with bloqueo_exclusivo():
        if sha256_fichero(db) != db_sha:
            raise RuntimeError("La SQLite maestra cambió desde REVIEW. APPLY cancelado.")
        if sha256_fichero(lote) != lote_sha:
            raise RuntimeError("El lote REVIEW cambió desde REVIEW. APPLY cancelado.")

        backup, backup_sha = backup_sqlite(db, lote_id)

        if sha256_fichero(db) != db_sha or sha256_fichero(lote) != lote_sha:
            raise RuntimeError("BD o lote cambiaron durante la preparación. APPLY cancelado.")

        configurar_db_auxiliar(lote_id)
        with sqlite3.connect(lote) as aux:
            aux.row_factory = sqlite3.Row
            filas = aux.execute(
                "SELECT id FROM generaciones_preguntas_ia WHERE estado='VALIDADA_IA' ORDER BY id"
            ).fetchall()
        ids = [int(f["id"]) for f in filas]
        if not ids:
            raise RuntimeError("El lote REVIEW no contiene candidatas VALIDADA_IA.")

        publicados: list[tuple[int, int]] = []
        with conectar_maestra(db) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                for generacion_id in ids:
                    pregunta_id = aprobar(
                        con,
                        generacion_id,
                        modo_publicacion="IA_REVIEW_CONFIRMADA",
                        gestionar_transaccion=False,
                        actualizar_auxiliar=False,
                    )
                    publicados.append((generacion_id, pregunta_id))
                if con.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("El lote produciría errores de claves foráneas.")
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("El lote no supera integrity_check.")
                con.commit()
            except Exception:
                con.rollback()
                raise

        # La maestra ya quedó confirmada. Ahora se refleja el resultado en el
        # artefacto REVIEW. Si esta fase auxiliar falla, el backup permite
        # reconstrucción, pero nunca se repite automáticamente la publicación.
        with sqlite3.connect(lote) as aux:
            for generacion_id, pregunta_id in publicados:
                aux.execute(
                    """UPDATE generaciones_preguntas_ia
                       SET estado='APROBADA', fecha_revision=?,
                           lote_pregunta_id=?, tipo_publicacion=?,
                           observaciones=?
                       WHERE id=? AND estado='VALIDADA_IA'""",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        pregunta_id,
                        "IA_REVIEW_CONFIRMADA",
                        f"lote_preguntas.id={pregunta_id}; bancos=PENDIENTE_SINCRONIZACION_COMUN",
                        generacion_id,
                    ),
                )
            aux.commit()

        # La publicación ha cambiado legítimamente la maestra. Se congela ese
        # nuevo SHA y se delega la selección/sincronización en el wrapper común,
        # reutilizando el lock que ya posee esta operación de alto nivel.
        db_sha_post_publicacion = sha256_fichero(db)
        sincronizacion = aplicar_sincronizacion_bancos(
            db,
            db_sha_post_publicacion,
            gestionar_lock=False,
        )

        return {
            "fase": "APPLY",
            "lote_id": lote_id,
            "db_sha256_revisado": db_sha,
            "lote_sha256_revisado": lote_sha,
            "backup_db": str(backup.relative_to(RAIZ)),
            "backup_db_sha256": backup_sha,
            "publicadas": len(publicados),
            "lote_preguntas_ids": [p for _, p in publicados],
            "sincronizacion_bancos": sincronizacion,
            "aplicado": True,
        }


def main() -> int:
    p = argparse.ArgumentParser(description="APPLY protegido de preguntas jurídicas IA ya revisadas.")
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--lote-id", required=True)
    p.add_argument("--db-sha256-esperado", required=True)
    p.add_argument("--lote-sha256-esperado", required=True)
    a = p.parse_args()
    try:
        resultado = aplicar(
            Path(a.db).expanduser().resolve(),
            a.lote_id,
            a.db_sha256_esperado,
            a.lote_sha256_esperado,
        )
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
