"""Registra y aplica la cuarentena global de preguntas auditadas.

Sin ``--aplicar`` abre la base en modo de solo lectura y muestra el impacto.
Con ``--aplicar`` crea una copia de seguridad y ejecuta en una sola transacción:

1. la tabla y los disparadores de exclusión global;
2. el registro verificable de cada decisión;
3. el paso a ``REVISION`` de todos sus vínculos de banco.

No elimina preguntas ni vínculos de banco.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
MIGRACION_DEFECTO = RAIZ / "scripts" / "sql" / "crear_preguntas_exclusiones.sql"
CLASIFICACIONES = {"ERROR_DEMOSTRADO", "NO_DETERMINABLE"}
RESPUESTAS = {"A", "B", "C", "D"}


def sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as fichero:
        for bloque in iter(lambda: fichero.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def marcadores(cantidad: int) -> str:
    return ",".join("?" for _ in range(cantidad))


def cargar_casos(ruta: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    documento = json.loads(ruta.read_text(encoding="utf-8"))
    if documento.get("decision_usuario") is not True:
        raise RuntimeError("El lote no contiene decision_usuario=true.")
    origen = str(documento.get("origen_auditoria") or "").strip()
    if not origen:
        raise RuntimeError("Falta origen_auditoria.")
    casos = documento.get("casos")
    if not isinstance(casos, list) or not casos:
        raise RuntimeError("El lote no contiene casos.")

    ids: set[int] = set()
    normalizados: list[dict[str, Any]] = []
    for bruto in casos:
        pid = int(bruto["pregunta_id"])
        if pid in ids:
            raise RuntimeError(f"ID duplicado en el lote: {pid}.")
        ids.add(pid)
        clasificacion = str(bruto["clasificacion"]).strip().upper()
        almacenada = str(bruto["respuesta_almacenada"]).strip().upper()
        demostrada_bruta = bruto.get("respuesta_demostrada")
        demostrada = (
            str(demostrada_bruta).strip().upper()
            if demostrada_bruta is not None
            else None
        )
        motivo = str(bruto.get("motivo") or "").strip()
        if clasificacion not in CLASIFICACIONES:
            raise RuntimeError(f"Clasificación inválida para {pid}: {clasificacion}.")
        if almacenada not in RESPUESTAS:
            raise RuntimeError(f"Respuesta almacenada inválida para {pid}.")
        if clasificacion == "ERROR_DEMOSTRADO" and demostrada not in RESPUESTAS:
            raise RuntimeError(f"Falta respuesta demostrada para {pid}.")
        if clasificacion == "NO_DETERMINABLE" and demostrada is not None:
            raise RuntimeError(f"NO_DETERMINABLE no admite respuesta demostrada: {pid}.")
        if not motivo:
            raise RuntimeError(f"Falta motivo para {pid}.")
        normalizados.append(
            {
                "pregunta_id": pid,
                "clasificacion": clasificacion,
                "respuesta_almacenada": almacenada,
                "respuesta_demostrada": demostrada,
                "motivo": motivo,
            }
        )
    return {**documento, "origen_auditoria": origen}, normalizados


def conectar_solo_lectura(db: Path) -> sqlite3.Connection:
    conexion = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA query_only=ON")
    return conexion


def inspeccionar(db: Path, casos: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [caso["pregunta_id"] for caso in casos]
    ph = marcadores(len(ids))
    with conectar_solo_lectura(db) as conexion:
        preguntas = {
            int(fila["id"]): str(fila["respuesta_correcta"]).strip().upper()
            for fila in conexion.execute(
                f"SELECT id, respuesta_correcta FROM lote_preguntas WHERE id IN ({ph})",
                ids,
            )
        }
        faltantes = sorted(set(ids) - set(preguntas))
        claves_distintas = [
            caso["pregunta_id"]
            for caso in casos
            if preguntas.get(caso["pregunta_id"]) != caso["respuesta_almacenada"]
        ]
        vinculos = int(
            conexion.execute(
                f"SELECT COUNT(*) FROM banco_preguntas WHERE pregunta_id IN ({ph})",
                ids,
            ).fetchone()[0]
        )
        incluidos = int(
            conexion.execute(
                f"SELECT COUNT(*) FROM banco_preguntas WHERE pregunta_id IN ({ph}) AND estado='INCLUIDA'",
                ids,
            ).fetchone()[0]
        )
        simulacros = int(
            conexion.execute(
                f"""
                SELECT COUNT(*) FROM simulacro_preguntas
                WHERE pregunta_id IN ({ph})
                   OR banco_pregunta_id IN (
                       SELECT id FROM banco_preguntas WHERE pregunta_id IN ({ph})
                   )
                """,
                ids + ids,
            ).fetchone()[0]
        )
        existe_tabla = conexion.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='preguntas_exclusiones'"
        ).fetchone() is not None
        ya_registradas = 0
        if existe_tabla:
            ya_registradas = int(
                conexion.execute(
                    f"SELECT COUNT(*) FROM preguntas_exclusiones WHERE pregunta_id IN ({ph})",
                    ids,
                ).fetchone()[0]
            )
    return {
        "preguntas": len(preguntas),
        "faltantes": faltantes,
        "claves_distintas": claves_distintas,
        "vinculos_banco": vinculos,
        "vinculos_incluidos": incluidos,
        "referencias_simulacros": simulacros,
        "tabla_exclusiones_existe": existe_tabla,
        "ya_registradas": ya_registradas,
    }


def aplicar(
    db: Path,
    migracion: Path,
    evidencia: Path,
    documento: dict[str, Any],
    casos: list[dict[str, Any]],
    impacto: dict[str, Any],
) -> dict[str, Any]:
    if impacto["faltantes"] or impacto["claves_distintas"]:
        raise RuntimeError("La vista previa contiene preguntas ausentes o claves distintas.")
    if impacto["ya_registradas"]:
        raise RuntimeError("El lote contiene preguntas ya registradas en exclusiones.")

    hash_inicial = sha256(db)
    evidencia_hash = sha256(evidencia)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"{db.stem}_backup_antes_cuarentena_{marca}{db.suffix}")
    shutil.copy2(db, backup)
    if sha256(backup) != hash_inicial:
        raise RuntimeError("La copia de seguridad no coincide con la base.")

    conexion = sqlite3.connect(db)
    conexion.execute("PRAGMA foreign_keys=ON")
    ids = [caso["pregunta_id"] for caso in casos]
    ph = marcadores(len(ids))
    try:
        conexion.executescript("BEGIN IMMEDIATE;\n" + migracion.read_text(encoding="utf-8"))
        for caso in casos:
            conexion.execute(
                """
                INSERT INTO preguntas_exclusiones (
                    pregunta_id, clasificacion, estado,
                    respuesta_almacenada, respuesta_demostrada, motivo,
                    evidencia_ruta, evidencia_sha256, origen_auditoria,
                    decision_usuario
                ) VALUES (?, ?, 'CUARENTENA', ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    caso["pregunta_id"],
                    caso["clasificacion"],
                    caso["respuesta_almacenada"],
                    caso["respuesta_demostrada"],
                    caso["motivo"],
                    str(evidencia),
                    evidencia_hash,
                    documento["origen_auditoria"],
                ),
            )
            conexion.execute(
                """
                UPDATE banco_preguntas
                SET estado='REVISION', motivo_revision=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE pregunta_id=?
                """,
                (
                    f"AUDITORIA_RESPUESTA:{caso['clasificacion']}",
                    caso["pregunta_id"],
                ),
            )

        exclusiones = int(
            conexion.execute(
                f"SELECT COUNT(*) FROM preguntas_exclusiones WHERE pregunta_id IN ({ph})",
                ids,
            ).fetchone()[0]
        )
        incluidas = int(
            conexion.execute(
                f"SELECT COUNT(*) FROM banco_preguntas WHERE pregunta_id IN ({ph}) AND estado='INCLUIDA'",
                ids,
            ).fetchone()[0]
        )
        revisiones = int(
            conexion.execute(
                f"SELECT COUNT(*) FROM banco_preguntas WHERE pregunta_id IN ({ph}) AND estado='REVISION'",
                ids,
            ).fetchone()[0]
        )
        fk = conexion.execute("PRAGMA foreign_key_check").fetchall()
        integridad = conexion.execute("PRAGMA integrity_check").fetchone()[0]
        if (
            exclusiones != len(ids)
            or incluidas != 0
            or revisiones != impacto["vinculos_banco"]
            or fk
            or integridad != "ok"
        ):
            raise RuntimeError(
                "Validación final inesperada: "
                f"exclusiones={exclusiones}, incluidas={incluidas}, "
                f"revisiones={revisiones}, fk={len(fk)}, integridad={integridad}."
            )
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()

    return {
        "estado": "CUARENTENA_APLICADA",
        "backup": str(backup),
        "sha256_antes": hash_inicial,
        "sha256_despues": sha256(db),
        "evidencia_sha256": evidencia_hash,
        "preguntas": len(ids),
        "vinculos_revision": impacto["vinculos_banco"],
        "vinculos_incluidos": 0,
        "foreign_key_check": "ok",
        "integrity_check": "ok",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_DEFECTO)
    parser.add_argument("--casos", type=Path, required=True)
    parser.add_argument("--evidencia", type=Path, required=True)
    parser.add_argument("--migracion", type=Path, default=MIGRACION_DEFECTO)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()

    db = args.db.resolve()
    casos_path = args.casos.resolve()
    evidencia = args.evidencia.resolve()
    migracion = args.migracion.resolve()
    for ruta in (db, casos_path, evidencia, migracion):
        if not ruta.is_file():
            raise RuntimeError(f"No existe el archivo: {ruta}")

    documento, casos = cargar_casos(casos_path)
    impacto = inspeccionar(db, casos)
    print("CUARENTENA GLOBAL DE PREGUNTAS")
    print("=" * 76)
    print(f"Modo................................. {'APLICAR' if args.aplicar else 'SOLO REVISIÓN'}")
    print(f"Preguntas............................ {len(casos)}")
    print(f"Vínculos de banco.................... {impacto['vinculos_banco']}")
    print(f"Vínculos actualmente INCLUIDA........ {impacto['vinculos_incluidos']}")
    print(f"Referencias en simulacros............ {impacto['referencias_simulacros']}")
    print(f"Preguntas ausentes................... {len(impacto['faltantes'])}")
    print(f"Claves distintas..................... {len(impacto['claves_distintas'])}")
    print(f"Ya registradas....................... {impacto['ya_registradas']}")

    if impacto["faltantes"] or impacto["claves_distintas"] or impacto["ya_registradas"]:
        print(json.dumps(impacto, ensure_ascii=False, indent=2))
        return 1
    if not args.aplicar:
        print("\nSOLO REVISIÓN: no se ha modificado la base de datos.")
        return 0

    resultado = aplicar(db, migracion, evidencia, documento, casos, impacto)
    print("\n" + json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
