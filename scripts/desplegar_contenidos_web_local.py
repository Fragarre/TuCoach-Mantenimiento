#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]

DESTINO_PREDETERMINADO = (
    RAIZ.parent
    / "TuCoach-Web"
    / "backend"
    / "data"
    / "oposiciones.sqlite3"
)

COPIAS_PREDETERMINADAS = (
    RAIZ.parent
    / "TuCoach-Web"
    / "backend"
    / "data"
    / "copias_seguridad"
)

# Tablas que la Web utiliza actualmente o que forman parte directa de
# convocatorias, temarios, corpus y bancos. El snapshot sigue conservando
# TODAS las tablas; esta lista sólo sirve como control de compatibilidad.
TABLAS_WEB_OBLIGATORIAS = {
    "convocatorias",
    "convocatoria_partes",
    "convocatoria_parte_reglas",
    "convocatoria_modelo_bloques",
    "temarios",
    "temario_temas",
    "temario_referencias",
    "normas",
    "articulos_fuente",
    "lote_preguntas",
    "banco_preguntas",
    "banco_preguntas_temas",
}


# Tablas solo de mantenimiento que la Web nunca lee (verificado en el código de TuCoach-Web,
# 17/10/2026). Se quitan de la copia de la Web para que la SQLite del repositorio no supere
# el límite de 100 MB por fichero de GitHub. El snapshot y Supabase no cambian.
TABLAS_SOLO_MANTENIMIENTO = (
    "importaciones_ficheros",
    "lote_revision_preguntas",
    "preguntas_exclusiones",
    "resoluciones_boe",
    "sesiones_coste_mantenimiento",
    "perfil_generacion_ia",
    "preguntas_examen_oficial",
    "preguntas_reescritas",
)


def reducir_para_web(ruta: Path) -> None:
    """Deja en la copia de la Web solo lo que la Web usa.

    - quita las tablas de mantenimiento (y los disparadores que las referencian);
    - quita los vínculos de banco que no están INCLUIDA (la Web solo sirve INCLUIDA);
    - compacta el fichero (VACUUM).
    lote_preguntas se conserva completo (la Web muestra su recuento total).
    """
    con = sqlite3.connect(ruta)
    try:
        con.execute("PRAGMA foreign_keys = OFF")
        with con:
            for nombre, sql in con.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall():
                if any(t in (sql or "") for t in TABLAS_SOLO_MANTENIMIENTO):
                    con.execute(f'DROP TRIGGER "{nombre}"')
            for tabla in TABLAS_SOLO_MANTENIMIENTO:
                con.execute(f'DROP TABLE IF EXISTS "{tabla}"')
            con.execute(
                "DELETE FROM banco_preguntas_temas WHERE banco_pregunta_id IN "
                "(SELECT id FROM banco_preguntas WHERE estado <> 'INCLUIDA')")
            con.execute("DELETE FROM banco_preguntas WHERE estado <> 'INCLUIDA'")
        con.execute("VACUUM")
    finally:
        con.close()


def huella_contenido_web(ruta: Path) -> dict[str, int]:
    """Recuentos de lo que la Web sirve; deben coincidir entre snapshot y copia reducida."""
    con = conectar_solo_lectura(ruta)
    try:
        salida = {}
        for tabla in sorted(TABLAS_WEB_OBLIGATORIAS - {"banco_preguntas", "banco_preguntas_temas"}):
            salida[tabla] = int(con.execute(f'SELECT COUNT(*) FROM "{tabla}"').fetchone()[0])
        salida["banco_preguntas_incluidas"] = int(con.execute(
            "SELECT COUNT(*) FROM banco_preguntas WHERE estado='INCLUIDA'").fetchone()[0])
        salida["banco_temas_incluidas"] = int(con.execute(
            "SELECT COUNT(*) FROM banco_preguntas_temas bpt JOIN banco_preguntas bp "
            "ON bp.id = bpt.banco_pregunta_id WHERE bp.estado='INCLUIDA'").fetchone()[0])
        return salida
    finally:
        con.close()


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


def validar_sqlite(ruta: Path) -> dict[str, Any]:
    if not ruta.is_file():
        raise RuntimeError(f"No existe la base SQLite: {ruta}")

    con = conectar_solo_lectura(ruta)
    try:
        integridad = [str(r[0]) for r in con.execute("PRAGMA integrity_check")]
        if integridad != ["ok"]:
            raise RuntimeError(
                "PRAGMA integrity_check ha fallado: " + "; ".join(integridad)
            )

        fk = [dict(r) for r in con.execute("PRAGMA foreign_key_check")]
        if fk:
            raise RuntimeError(
                f"La base contiene {len(fk)} infracciones de claves foráneas."
            )

        tablas = {
            str(r[0])
            for r in con.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                  AND name NOT LIKE 'sqlite_%'
                """
            )
        }

        faltantes = sorted(TABLAS_WEB_OBLIGATORIAS - tablas)
        if faltantes:
            raise RuntimeError(
                "Faltan tablas obligatorias para TuCoach-Web: "
                + ", ".join(faltantes)
            )

        convocatorias = [
            dict(r)
            for r in con.execute(
                """
                SELECT id, codigo, puesto
                FROM convocatorias
                ORDER BY id
                """
            ).fetchall()
        ]

        recuentos = {
            "convocatorias": len(convocatorias),
            "temas": int(
                con.execute("SELECT COUNT(*) FROM temario_temas").fetchone()[0]
            ),
            "referencias": int(
                con.execute("SELECT COUNT(*) FROM temario_referencias").fetchone()[0]
            ),
            "lote_preguntas": int(
                con.execute("SELECT COUNT(*) FROM lote_preguntas").fetchone()[0]
            ),
            "banco_preguntas": int(
                con.execute("SELECT COUNT(*) FROM banco_preguntas").fetchone()[0]
            ),
            "normas": int(
                con.execute("SELECT COUNT(*) FROM normas").fetchone()[0]
            ),
            "articulos_fuente": int(
                con.execute("SELECT COUNT(*) FROM articulos_fuente").fetchone()[0]
            ),
        }
    finally:
        con.close()

    return {
        "tablas": len(tablas),
        "convocatorias": convocatorias,
        "recuentos": recuentos,
    }


def localizar_informe(snapshot: Path) -> Path:
    candidatos = sorted(snapshot.parent.glob("publicacion_*.json"))
    if len(candidatos) != 1:
        raise RuntimeError(
            "La carpeta del snapshot debe contener exactamente un "
            "informe publicacion_*.json."
        )
    return candidatos[0]


def validar_informe(snapshot: Path, informe: Path) -> dict[str, Any]:
    try:
        datos = json.loads(informe.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"No se puede leer el informe JSON: {exc}") from exc

    esperado = str(datos.get("sha256_snapshot") or "").strip().lower()
    if not esperado:
        raise RuntimeError("El informe no contiene sha256_snapshot.")

    real = sha256_archivo(snapshot)
    if real.lower() != esperado:
        raise RuntimeError(
            "El SHA256 del snapshot no coincide con el registrado en "
            "el informe de publicación."
        )

    snapshot_informe = datos.get("snapshot")
    if snapshot_informe:
        nombre_informe = Path(str(snapshot_informe)).name
        if nombre_informe != snapshot.name:
            raise RuntimeError(
                "El informe JSON corresponde a otro archivo snapshot."
            )

    return datos


def crear_backup(destino: Path, carpeta_copias: Path) -> Path | None:
    if not destino.exists():
        return None

    carpeta_copias.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = carpeta_copias / f"oposiciones_web_antes_{marca}.sqlite3"

    shutil.copy2(destino, backup)
    validar_sqlite(backup)

    if sha256_archivo(destino) != sha256_archivo(backup):
        raise RuntimeError("El backup no coincide byte a byte con la BD Web actual.")

    return backup


def copiar_a_temporal(snapshot: Path, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(destino.name + ".nuevo")

    if temporal.exists():
        temporal.unlink()

    shutil.copy2(snapshot, temporal)

    if temporal.stat().st_size != snapshot.stat().st_size:
        temporal.unlink(missing_ok=True)
        raise RuntimeError("El tamaño de la copia temporal no coincide con el snapshot.")

    if sha256_archivo(temporal) != sha256_archivo(snapshot):
        temporal.unlink(missing_ok=True)
        raise RuntimeError("El SHA256 de la copia temporal no coincide con el snapshot.")

    validar_sqlite(temporal)
    return temporal


def restaurar_backup(backup: Path, destino: Path) -> None:
    temporal_rollback = destino.with_name(destino.name + ".rollback")
    temporal_rollback.unlink(missing_ok=True)

    shutil.copy2(backup, temporal_rollback)
    validar_sqlite(temporal_rollback)
    os.replace(temporal_rollback, destino)
    validar_sqlite(destino)


def desplegar(
    snapshot: Path,
    destino: Path,
    carpeta_copias: Path,
    informe: Path | None = None,
) -> None:
    snapshot = snapshot.resolve()
    destino = destino.resolve()
    carpeta_copias = carpeta_copias.resolve()

    if destino == snapshot:
        raise RuntimeError("Origen y destino no pueden ser el mismo archivo.")

    if not snapshot.is_file():
        raise RuntimeError(f"No existe el snapshot: {snapshot}")

    informe = (informe or localizar_informe(snapshot)).resolve()

    print("\nValidando publicación preparada...")
    datos_informe = validar_informe(snapshot, informe)
    resumen_snapshot = validar_sqlite(snapshot)

    print(f"Versión:      {datos_informe.get('version', 'desconocida')}")
    print(f"Snapshot:     {snapshot}")
    print(f"SHA256:       {sha256_archivo(snapshot)}")
    print(f"Tablas:       {resumen_snapshot['tablas']}")
    print(f"Convocatorias:{resumen_snapshot['recuentos']['convocatorias']}")
    print(f"Temas:        {resumen_snapshot['recuentos']['temas']}")
    print(f"Referencias:  {resumen_snapshot['recuentos']['referencias']}")
    print(f"Lote:         {resumen_snapshot['recuentos']['lote_preguntas']}")
    print(f"Banco:        {resumen_snapshot['recuentos']['banco_preguntas']}")
    print(f"Normas:       {resumen_snapshot['recuentos']['normas']}")
    print(f"Artículos:    {resumen_snapshot['recuentos']['articulos_fuente']}")

    print("\nDestino local TuCoach-Web:")
    print(destino)

    respuesta = input(
        "\n¿Desplegar ESTE snapshot en TuCoach-Web local? [s/N]: "
    ).strip().lower()
    if respuesta not in {"s", "si", "sí"}:
        print("Operación cancelada. No se ha modificado TuCoach-Web.")
        return

    print("\nCreando backup de la versión Web actual...")
    backup = crear_backup(destino, carpeta_copias)
    if backup is None:
        print("No existía una BD Web previa; no se crea backup.")
    else:
        print(f"Backup validado:\n{backup}")

    print("\nPreparando copia temporal...")
    temporal = copiar_a_temporal(snapshot, destino)
    print(f"Temporal validado:\n{temporal}")

    print("\nReduciendo la copia para la Web (sin tablas de mantenimiento ni vínculos en revisión)...")
    tamano_antes = temporal.stat().st_size
    reducir_para_web(temporal)
    validar_sqlite(temporal)
    huella_snapshot = huella_contenido_web(snapshot)
    if huella_contenido_web(temporal) != huella_snapshot:
        temporal.unlink(missing_ok=True)
        raise RuntimeError("La copia reducida no conserva el mismo contenido Web que el snapshot.")
    print(f"Tamaño: {tamano_antes / 1e6:.1f} MB -> {temporal.stat().st_size / 1e6:.1f} MB "
          "(límite de GitHub: 100 MB)")

    try:
        print("\nSustituyendo la BD Web...")
        try:
            os.replace(temporal, destino)
        except PermissionError as exc:
            raise RuntimeError(
                "Windows no permite sustituir la BD Web. "
                "Probablemente el backend/Uvicorn la mantiene abierta. "
                "Detén el backend y vuelve a ejecutar el despliegue."
            ) from exc

        print("Validando la BD instalada...")
        resumen_destino = validar_sqlite(destino)

        hash_destino = sha256_archivo(destino)
        if huella_contenido_web(destino) != huella_snapshot:
            raise RuntimeError(
                "La BD instalada no conserva el contenido Web del snapshot publicado."
            )
        if destino.stat().st_size > 95_000_000:
            print("AVISO: la BD Web supera 95 MB; GitHub rechaza ficheros de más de 100 MB.")

        print("\n" + "=" * 78)
        print("DESPLIEGUE LOCAL COMPLETADO")
        print("=" * 78)
        print(f"Destino:      {destino}")
        print(f"SHA256:       {hash_destino}")
        print(f"Tablas:       {resumen_destino['tablas']}")
        print(
            f"Convocatorias:{resumen_destino['recuentos']['convocatorias']}"
        )
        print("\nReinicia el backend de TuCoach-Web y realiza la prueba funcional.")

    except Exception:
        temporal.unlink(missing_ok=True)

        if backup is not None and backup.is_file():
            print("\nERROR durante/post despliegue. Intentando rollback...")
            try:
                restaurar_backup(backup, destino)
                print("Rollback completado correctamente.")
            except Exception as rollback_error:
                print(
                    "\nERROR CRÍTICO: también ha fallado el rollback automático:"
                )
                print(rollback_error)

        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Despliega LOCALMENTE en TuCoach-Web un snapshot previamente "
            "preparado por publicar_contenidos_web.py. "
            "No sirve como despliegue de producción."
        )
    )
    parser.add_argument(
        "snapshot",
        type=Path,
        help="Ruta a oposiciones_web_YYYYMMDD_HHMMSS.sqlite3",
    )
    parser.add_argument(
        "--informe",
        type=Path,
        default=None,
        help=(
            "Informe publicacion_*.json. Si se omite, se exige exactamente "
            "uno en la carpeta del snapshot."
        ),
    )
    parser.add_argument(
        "--destino",
        type=Path,
        default=DESTINO_PREDETERMINADO,
        help=f"BD Web local [predeterminado: {DESTINO_PREDETERMINADO}]",
    )
    parser.add_argument(
        "--copias",
        type=Path,
        default=COPIAS_PREDETERMINADAS,
        help=f"Carpeta de backups [predeterminado: {COPIAS_PREDETERMINADAS}]",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 78)
    print("DESPLEGAR CONTENIDOS EN TuCoach-Web LOCAL")
    print("=" * 78)
    print("Este script NO publica en Internet.")
    print("Sólo sustituye la SQLite del proyecto TuCoach-Web local.")

    try:
        desplegar(
            snapshot=args.snapshot,
            destino=args.destino,
            carpeta_copias=args.copias,
            informe=args.informe,
        )
    except Exception as exc:
        print(f"\nERROR: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
