"""Reformula localmente preguntas `tests` sin usar IA.

Alcance estricto: solo `lote_preguntas.tipo_fuente = 'tests'`.
No altera metadatos jurídicos, bancos ni vínculos. Antes de cualquier escritura
genera una auditoría CSV completa y, al aplicar, una copia de seguridad de la
base. La simulación es el modo predeterminado.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from generar_muestra_reformulacion_tests import propuesta


ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
AUDITORIAS = ROOT / "auditorias" / "reformulacion_tests"
BACKUPS = ROOT / "backups"
COLUMNAS_AUDITORIA = (
    "id", "acciones", "enunciado_original", "enunciado_propuesto",
    "opcion_a_original", "opcion_b_original", "opcion_c_original", "opcion_d_original",
    "opcion_a_propuesta", "opcion_b_propuesta", "opcion_c_propuesta", "opcion_d_propuesta",
    "respuesta_correcta_original", "respuesta_correcta_propuesta",
)


def cargar_filas(db: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        return con.execute(
            "SELECT * FROM lote_preguntas "
            "WHERE LOWER(TRIM(tipo_fuente))='tests' ORDER BY id"
        ).fetchall()


def preparar_propuestas(filas: list[sqlite3.Row]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    propuestas: list[dict[str, object]] = []
    omitidas: list[dict[str, object]] = []
    for fila in filas:
        try:
            p = propuesta(fila)
        except Exception as exc:
            omitidas.append({"id": int(fila["id"]), "motivo": str(exc)})
            continue
        propuestas.append(p)
    return propuestas, omitidas


def fila_auditoria(p: dict[str, object]) -> dict[str, str]:
    originales = list(p["opciones_originales"])
    propuestas = list(p["opciones_propuestas"])
    return {
        "id": str(p["id"]),
        "acciones": "; ".join(p["acciones"]),
        "enunciado_original": str(p["enunciado_original"]),
        "enunciado_propuesto": str(p["enunciado_propuesto"]),
        "opcion_a_original": originales[0], "opcion_b_original": originales[1],
        "opcion_c_original": originales[2], "opcion_d_original": originales[3],
        "opcion_a_propuesta": propuestas[0], "opcion_b_propuesta": propuestas[1],
        "opcion_c_propuesta": propuestas[2], "opcion_d_propuesta": propuestas[3],
        "respuesta_correcta_original": str(p["correcta_original"]),
        "respuesta_correcta_propuesta": str(p["correcta_propuesta"]),
    }


def escribir_auditoria(
    propuestas: list[dict[str, object]], omitidas: list[dict[str, object]], sello: str,
) -> Path:
    carpeta = AUDITORIAS / sello
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "propuestas.csv"
    with ruta.open("w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS_AUDITORIA, delimiter=";")
        escritor.writeheader()
        escritor.writerows(fila_auditoria(p) for p in propuestas)

    acciones = Counter(accion for p in propuestas for accion in p["acciones"])
    resumen = carpeta / "resumen.txt"
    resumen.write_text(
        "\n".join([
            "REFORMULACIÓN LOCAL DE PREGUNTAS TESTS",
            f"Propuestas: {len(propuestas)}",
            f"Omitidas: {len(omitidas)}",
            *[f"{accion}: {cantidad}" for accion, cantidad in sorted(acciones.items())],
            "",
            "No se modifica norma, artículo, normalización, origen, fuente ni bancos.",
        ]),
        encoding="utf-8",
    )
    if omitidas:
        (carpeta / "omitidas.csv").write_text(
            "id;motivo\n" + "\n".join(
                f'{item["id"]};{str(item["motivo"]).replace(";", ",")}' for item in omitidas
            ) + "\n",
            encoding="utf-8-sig",
        )
    return carpeta


def copia_seguridad(db: Path, sello: str) -> Path:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    destino = BACKUPS / f"oposiciones_pre_reformulacion_tests_{sello}.sqlite3"
    shutil.copy2(db, destino)
    return destino


def aplicar(db: Path, propuestas: list[dict[str, object]], lote: int) -> int:
    actualizadas = 0
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA foreign_keys=ON")
        for inicio in range(0, len(propuestas), lote):
            bloque = propuestas[inicio:inicio + lote]
            con.execute("BEGIN IMMEDIATE")
            try:
                for p in bloque:
                    opciones = list(p["opciones_propuestas"])
                    cur = con.execute(
                        """UPDATE lote_preguntas
                           SET enunciado=?, opcion_a=?, opcion_b=?, opcion_c=?, opcion_d=?,
                               respuesta_correcta=?
                           WHERE id=? AND LOWER(TRIM(tipo_fuente))='tests'""",
                        (
                            p["enunciado_propuesto"], opciones[0], opciones[1], opciones[2], opciones[3],
                            p["correcta_propuesta"], p["id"],
                        ),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError(f"ID {p['id']}: actualización fuera de alcance o inexistente.")
                con.commit()
            except Exception:
                con.rollback()
                raise
            actualizadas += len(bloque)
    return actualizadas


def main() -> int:
    parser = argparse.ArgumentParser(description="Reformular localmente preguntas con tipo_fuente tests.")
    parser.add_argument("--db", type=Path, default=DB_DEFECTO)
    parser.add_argument("--aplicar", action="store_true", help="Aplica las propuestas tras crear auditoría y copia.")
    parser.add_argument("--lote", type=int, default=250)
    args = parser.parse_args()
    if args.lote < 1:
        raise RuntimeError("--lote debe ser positivo.")
    db = args.db.resolve()
    if not db.is_file():
        raise FileNotFoundError(db)

    filas = cargar_filas(db)
    propuestas, omitidas = preparar_propuestas(filas)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    auditoria = escribir_auditoria(propuestas, omitidas, sello)
    print(f"Preguntas tests leídas: {len(filas)}")
    print(f"Propuestas seguras: {len(propuestas)} | omitidas: {len(omitidas)}")
    print(f"Auditoría: {auditoria}")
    if not args.aplicar:
        print("SIMULACIÓN: no se ha modificado la base.")
        return 0

    backup = copia_seguridad(db, sello)
    print(f"Copia previa: {backup}")
    actualizadas = aplicar(db, propuestas, args.lote)
    print(f"APLICADO: {actualizadas} preguntas actualizadas en lotes de {args.lote}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
