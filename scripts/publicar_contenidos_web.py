#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
SALIDA_PREDETERMINADA = RAIZ / "publicaciones_web"
SCRIPT_VALIDACION = RAIZ / "scripts" / "validacion_completa.py"


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def conectar_solo_lectura(ruta: Path) -> sqlite3.Connection:
    uri = ruta.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def integridad(con: sqlite3.Connection) -> tuple[bool, list[str]]:
    filas = [str(r[0]) for r in con.execute("PRAGMA integrity_check").fetchall()]
    return filas == ["ok"], filas


def foreign_keys(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute("PRAGMA foreign_key_check").fetchall()]


def objetos_esquema(con: sqlite3.Connection) -> list[dict[str, Any]]:
    filas = con.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return [
        {
            "type": r["type"],
            "name": r["name"],
            "tbl_name": r["tbl_name"],
            "sql": r["sql"],
        }
        for r in filas
    ]


def tablas_usuario(con: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in con.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    ]


def recuentos_tablas(con: sqlite3.Connection) -> dict[str, int]:
    resultado: dict[str, int] = {}
    for tabla in tablas_usuario(con):
        nombre_sql = '"' + tabla.replace('"', '""') + '"'
        resultado[tabla] = int(
            con.execute(f"SELECT COUNT(*) FROM {nombre_sql}").fetchone()[0]
        )
    return resultado


def resumen_convocatorias(con: sqlite3.Connection) -> list[dict[str, Any]]:
    tablas = set(tablas_usuario(con))
    necesarias = {
        "convocatorias",
        "convocatoria_partes",
        "temarios",
        "temario_temas",
        "temario_referencias",
        "banco_preguntas",
    }
    if not necesarias.issubset(tablas):
        return []

    filas = con.execute(
        """
        SELECT id, codigo, puesto
        FROM convocatorias
        ORDER BY id
        """
    ).fetchall()

    salida: list[dict[str, Any]] = []
    for fila in filas:
        cid = int(fila["id"])

        partes = int(
            con.execute(
                "SELECT COUNT(*) FROM convocatoria_partes WHERE convocatoria_id = ?",
                (cid,),
            ).fetchone()[0]
        )

        temas = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM temario_temas tt
                JOIN temarios t ON t.id = tt.temario_id
                WHERE t.convocatoria_id = ?
                """,
                (cid,),
            ).fetchone()[0]
        )

        referencias = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM temario_referencias tr
                JOIN temario_temas tt ON tt.id = tr.tema_id
                JOIN temarios t ON t.id = tt.temario_id
                WHERE t.convocatoria_id = ?
                """,
                (cid,),
            ).fetchone()[0]
        )

        banco = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas
                WHERE convocatoria_id = ?
                """,
                (cid,),
            ).fetchone()[0]
        )

        salida.append(
            {
                "id": cid,
                "codigo": fila["codigo"],
                "puesto": fila["puesto"],
                "partes": partes,
                "temas": temas,
                "referencias": referencias,
                "banco_preguntas": banco,
            }
        )

    return salida


def inventario(con: sqlite3.Connection) -> dict[str, Any]:
    ok, detalle_integridad = integridad(con)
    return {
        "integridad_ok": ok,
        "integridad_detalle": detalle_integridad,
        "foreign_key_violations": foreign_keys(con),
        "tablas": recuentos_tablas(con),
        "objetos_esquema": objetos_esquema(con),
        "convocatorias": resumen_convocatorias(con),
        "pragma_user_version": int(con.execute("PRAGMA user_version").fetchone()[0]),
        "pragma_application_id": int(
            con.execute("PRAGMA application_id").fetchone()[0]
        ),
    }


def ejecutar_validacion_completa() -> None:
    if not SCRIPT_VALIDACION.is_file():
        raise RuntimeError(
            f"No existe el script obligatorio de validación: {SCRIPT_VALIDACION}"
        )

    print("\nEjecutando validacion_completa.py...")
    resultado = subprocess.run(
        [sys.executable, str(SCRIPT_VALIDACION)],
        cwd=RAIZ,
        check=False,
    )
    if resultado.returncode != 0:
        raise RuntimeError(
            "validacion_completa.py ha terminado con error. "
            "La publicación queda cancelada."
        )


def crear_snapshot_sqlite(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()

    origen_uri = origen.resolve().as_uri() + "?mode=ro"
    con_origen = sqlite3.connect(origen_uri, uri=True)
    try:
        con_destino = sqlite3.connect(destino)
        try:
            con_origen.backup(con_destino)
            con_destino.commit()
        finally:
            con_destino.close()
    finally:
        con_origen.close()


def comparar_inventarios(
    origen: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[str]:
    errores: list[str] = []

    if origen["tablas"] != snapshot["tablas"]:
        errores.append("Los nombres o recuentos de las tablas no coinciden.")

    if origen["objetos_esquema"] != snapshot["objetos_esquema"]:
        errores.append("El esquema SQLite del snapshot no coincide con el origen.")

    if origen["pragma_user_version"] != snapshot["pragma_user_version"]:
        errores.append("PRAGMA user_version no coincide.")

    if origen["pragma_application_id"] != snapshot["pragma_application_id"]:
        errores.append("PRAGMA application_id no coincide.")

    if origen["convocatorias"] != snapshot["convocatorias"]:
        errores.append("El resumen por convocatorias no coincide.")

    if not snapshot["integridad_ok"]:
        errores.append("El snapshot no supera PRAGMA integrity_check.")

    if snapshot["foreign_key_violations"]:
        errores.append("El snapshot contiene infracciones de claves foráneas.")

    return errores


def escribir_informe_txt(informe: dict[str, Any], ruta: Path) -> None:
    lineas: list[str] = []
    sep = "=" * 78
    lineas.extend(
        [
            sep,
            "PUBLICACIÓN DE CONTENIDOS OPOCOACH-WEB",
            sep,
            f"Versión:                  {informe['version']}",
            f"Fecha:                    {informe['fecha']}",
            f"Origen:                   {informe['origen']}",
            f"Snapshot:                 {informe['snapshot']}",
            f"SHA256 snapshot:          {informe['sha256_snapshot']}",
            f"Tamaño snapshot (bytes):  {informe['tamano_snapshot_bytes']}",
            "",
            "VALIDACIÓN",
            "-" * 78,
            "Integridad origen:         OK",
            "Foreign keys origen:       OK",
            "Integridad snapshot:       OK",
            "Foreign keys snapshot:     OK",
            "Esquema/recuentos:         COINCIDEN",
            "",
            "CONVOCATORIAS",
            "-" * 78,
        ]
    )

    convocatorias = informe["inventario"]["convocatorias"]
    if convocatorias:
        for c in convocatorias:
            lineas.extend(
                [
                    f"{c['id']} · {c['codigo']} · {c['puesto']}",
                    f"  Partes:       {c['partes']}",
                    f"  Temas:        {c['temas']}",
                    f"  Referencias:  {c['referencias']}",
                    f"  Banco:        {c['banco_preguntas']}",
                ]
            )
    else:
        lineas.append("No se ha podido construir el resumen por convocatorias.")

    lineas.extend(["", "TABLAS", "-" * 78])
    for tabla, cantidad in informe["inventario"]["tablas"].items():
        lineas.append(f"{tabla:<42} {cantidad:>10}")

    lineas.extend(
        [
            "",
            sep,
            "SNAPSHOT PREPARADO Y VALIDADO. NO SE HA DESPLEGADO EN OPOCOACH-WEB.",
            sep,
            "",
        ]
    )
    ruta.write_text("\n".join(lineas), encoding="utf-8")


def preparar_publicacion(
    origen: Path,
    carpeta_salida: Path,
    *,
    ejecutar_validacion_externa: bool = True,
) -> tuple[Path, Path, Path]:
    origen = origen.resolve()
    carpeta_salida = carpeta_salida.resolve()

    if not origen.is_file():
        raise RuntimeError(f"No existe la base de datos de origen: {origen}")

    if ejecutar_validacion_externa:
        ejecutar_validacion_completa()

    print("\nAuditando base maestra...")
    con = conectar_solo_lectura(origen)
    try:
        inv_origen = inventario(con)
    finally:
        con.close()

    if not inv_origen["integridad_ok"]:
        raise RuntimeError(
            "La base maestra no supera PRAGMA integrity_check: "
            + "; ".join(inv_origen["integridad_detalle"])
        )
    if inv_origen["foreign_key_violations"]:
        raise RuntimeError(
            "La base maestra contiene infracciones de claves foráneas."
        )

    version = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta_version = carpeta_salida / version
    carpeta_version.mkdir(parents=True, exist_ok=False)

    snapshot = carpeta_version / f"oposiciones_web_{version}.sqlite3"
    informe_json = carpeta_version / f"publicacion_{version}.json"
    informe_txt = carpeta_version / f"publicacion_{version}.txt"

    print(f"\nCreando snapshot consistente:\n{snapshot}")
    crear_snapshot_sqlite(origen, snapshot)

    print("\nValidando snapshot...")
    con = conectar_solo_lectura(snapshot)
    try:
        inv_snapshot = inventario(con)
    finally:
        con.close()

    errores = comparar_inventarios(inv_origen, inv_snapshot)
    if errores:
        raise RuntimeError(
            "El snapshot no coincide con la base maestra:\n- "
            + "\n- ".join(errores)
        )

    informe = {
        "version": version,
        "fecha": datetime.now().astimezone().isoformat(),
        "origen": str(origen),
        "snapshot": str(snapshot),
        "sha256_origen": sha256_archivo(origen),
        "sha256_snapshot": sha256_archivo(snapshot),
        "tamano_origen_bytes": origen.stat().st_size,
        "tamano_snapshot_bytes": snapshot.stat().st_size,
        "inventario": inv_snapshot,
    }

    informe_json.write_text(
        json.dumps(informe, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    escribir_informe_txt(informe, informe_txt)

    return snapshot, informe_json, informe_txt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Valida la base maestra de OpoCoach-Mantenimiento y genera un "
            "snapshot íntegro, consistente y versionado para OpoCoach-Web. "
            "No despliega ni modifica la Web."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PREDETERMINADA,
        help=f"Base maestra [predeterminado: {DB_PREDETERMINADA}]",
    )
    parser.add_argument(
        "--salida",
        type=Path,
        default=SALIDA_PREDETERMINADA,
        help=f"Carpeta de snapshots [predeterminado: {SALIDA_PREDETERMINADA}]",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 78)
    print("PREPARAR PUBLICACIÓN DE CONTENIDOS OPOCOACH-WEB")
    print("=" * 78)
    print(f"Base maestra: {args.db.resolve()}")
    print(f"Salida:       {args.salida.resolve()}")
    print("\nLa Web NO se modificará en esta fase.")

    try:
        snapshot, informe_json, informe_txt = preparar_publicacion(
            args.db,
            args.salida,
            ejecutar_validacion_externa=True,
        )
    except Exception as exc:
        print(f"\nERROR: {exc}")
        return 1

    print("\n" + "=" * 78)
    print("PUBLICACIÓN PREPARADA Y VALIDADA")
    print("=" * 78)
    print(f"Snapshot:     {snapshot}")
    print(f"Informe JSON: {informe_json}")
    print(f"Informe TXT:  {informe_txt}")
    print("\nNo se ha desplegado ningún archivo en OpoCoach-Web.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
