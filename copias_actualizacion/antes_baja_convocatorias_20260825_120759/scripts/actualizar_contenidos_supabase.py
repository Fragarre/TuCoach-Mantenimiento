#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]

TABLAS_ORDEN = [
    "convocatorias",
    "normas",
    "articulos_fuente",
    "documentos_corpus",
    "convocatoria_partes",
    "temarios",
    "convocatoria_documentos_corpus",
    "temario_temas",
    "convocatoria_parte_reglas",
    "convocatoria_modelo_bloques",
    "temario_referencias",
    "equivalencias_temas_no_juridicos",
    "equivalencias_normas",
    "lote_preguntas",
    "banco_preguntas",
    "banco_preguntas_temas",
]

COLUMNAS_BOOLEANAS = {
    ("convocatorias", "tiene_partes"),
    ("convocatorias", "activa"),
    ("banco_preguntas_temas", "es_principal"),
}

ESQUEMA_DESTINO = "contenidos"


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def conectar_sqlite_solo_lectura(ruta: Path) -> sqlite3.Connection:
    uri = ruta.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def cargar_entorno(ruta_env: Path) -> None:
    ruta_env = ruta_env.resolve()
    if not ruta_env.is_file():
        raise RuntimeError(f"No existe el fichero de entorno: {ruta_env}")

    cargado = load_dotenv(dotenv_path=ruta_env, override=False)
    if not cargado:
        raise RuntimeError(
            f"No se pudo cargar el fichero de entorno: {ruta_env}"
        )


def database_url() -> str:
    valor = os.getenv("DATABASE_URL", "").strip()
    if not valor:
        raise RuntimeError(
            "DATABASE_URL no está configurada después de cargar el fichero .env."
        )
    if valor.startswith("postgres://"):
        valor = "postgresql://" + valor[len("postgres://"):]
    return valor


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

    real = sha256_archivo(snapshot).lower()
    if real != esperado:
        raise RuntimeError(
            "El SHA256 del snapshot no coincide con el registrado en "
            "el informe de publicación."
        )

    snapshot_informe = datos.get("snapshot")
    if snapshot_informe:
        if Path(str(snapshot_informe)).name != snapshot.name:
            raise RuntimeError(
                "El informe JSON corresponde a otro archivo snapshot."
            )

    return datos


def columnas_sqlite(con: sqlite3.Connection, tabla: str) -> list[str]:
    filas = con.execute(f'PRAGMA table_info("{tabla}")').fetchall()
    if not filas:
        raise RuntimeError(f"No existe la tabla SQLite: {tabla}")
    return [str(f["name"]) for f in filas]


def filas_sqlite(con: sqlite3.Connection, tabla: str) -> list[tuple[Any, ...]]:
    columnas = columnas_sqlite(con, tabla)
    select = ", ".join('"' + c.replace('"', '""') + '"' for c in columnas)
    filas = con.execute(f'SELECT {select} FROM "{tabla}"').fetchall()

    salida: list[tuple[Any, ...]] = []
    for fila in filas:
        valores = []
        for columna in columnas:
            valor = fila[columna]
            if (tabla, columna) in COLUMNAS_BOOLEANAS and valor is not None:
                if valor not in (0, 1):
                    raise RuntimeError(
                        f"{tabla}.{columna} contiene un valor no booleano: {valor!r}"
                    )
                valor = bool(valor)
            valores.append(valor)
        salida.append(tuple(valores))
    return salida


def validar_origen(con: sqlite3.Connection) -> dict[str, int]:
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    if integridad != "ok":
        raise RuntimeError(f"SQLite integrity_check falló: {integridad}")

    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        raise RuntimeError(
            f"SQLite contiene {len(fk)} infracciones de claves foráneas."
        )

    recuentos: dict[str, int] = {}
    for tabla in TABLAS_ORDEN:
        recuentos[tabla] = int(
            con.execute(f'SELECT COUNT(*) FROM "{tabla}"').fetchone()[0]
        )
    return recuentos


def validar_relaciones_logicas_sqlite(
    con: sqlite3.Connection,
) -> dict[str, int]:
    checks = {
        "lote_preguntas.norma_id_normalizada -> normas.id": """
            SELECT COUNT(*)
            FROM lote_preguntas lp
            LEFT JOIN normas n ON n.id = lp.norma_id_normalizada
            WHERE lp.norma_id_normalizada IS NOT NULL
              AND n.id IS NULL
        """,
        "temario_referencias.norma_id -> normas.id": """
            SELECT COUNT(*)
            FROM temario_referencias tr
            LEFT JOIN normas n ON n.id = tr.norma_id
            WHERE tr.norma_id IS NOT NULL
              AND n.id IS NULL
        """,
    }

    resultado: dict[str, int] = {}
    for nombre, query in checks.items():
        huerfanos = int(con.execute(query).fetchone()[0])
        resultado[nombre] = huerfanos
        if huerfanos:
            raise RuntimeError(
                f"Relación lógica inválida en origen: {nombre}: "
                f"{huerfanos} huérfanos."
            )
    return resultado


def validar_tablas_destino(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            """,
            (ESQUEMA_DESTINO,),
        )
        existentes = {str(r[0]) for r in cur.fetchall()}

    faltantes = [t for t in TABLAS_ORDEN if t not in existentes]
    if faltantes:
        raise RuntimeError(
            "Faltan tablas en Supabase: " + ", ".join(faltantes)
        )


def asegurar_columna_activa_destino(
    con_sqlite: sqlite3.Connection,
    conn_pg: psycopg.Connection,
) -> None:
    """Migra contenidos.convocatorias.activa solo si el snapshot ya la contiene."""
    if "activa" not in columnas_sqlite(con_sqlite, "convocatorias"):
        return

    with conn_pg.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = 'convocatorias'
              AND column_name = 'activa'
            """,
            (ESQUEMA_DESTINO,),
        )
        if cur.fetchone() is None:
            cur.execute(
                sql.SQL(
                    "ALTER TABLE {}.convocatorias "
                    "ADD COLUMN activa boolean NOT NULL DEFAULT true"
                ).format(sql.Identifier(ESQUEMA_DESTINO))
            )


def validar_columnas_destino(
    con_sqlite: sqlite3.Connection,
    conn_pg: psycopg.Connection,
) -> None:
    with conn_pg.cursor() as cur:
        for tabla in TABLAS_ORDEN:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                ORDER BY ordinal_position
                """,
                (ESQUEMA_DESTINO, tabla),
            )
            columnas_pg = [str(r[0]) for r in cur.fetchall()]
            columnas_origen = columnas_sqlite(con_sqlite, tabla)

            faltantes = [c for c in columnas_origen if c not in columnas_pg]
            if faltantes:
                raise RuntimeError(
                    f"{ESQUEMA_DESTINO}.{tabla} no contiene columnas del "
                    f"snapshot: {', '.join(faltantes)}"
                )


def recuentos_postgres(conn: psycopg.Connection) -> dict[str, int]:
    resultado: dict[str, int] = {}
    with conn.cursor() as cur:
        for tabla in TABLAS_ORDEN:
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(ESQUEMA_DESTINO),
                    sql.Identifier(tabla),
                )
            )
            resultado[tabla] = int(cur.fetchone()[0])
    return resultado


def insertar_tabla(
    con_sqlite: sqlite3.Connection,
    conn_pg: psycopg.Connection,
    tabla: str,
) -> int:
    columnas = columnas_sqlite(con_sqlite, tabla)
    filas = filas_sqlite(con_sqlite, tabla)
    if not filas:
        return 0

    insert = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(ESQUEMA_DESTINO),
        sql.Identifier(tabla),
        sql.SQL(", ").join(map(sql.Identifier, columnas)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columnas),
    )

    with conn_pg.cursor() as cur:
        cur.executemany(insert, filas)

    return len(filas)


def vaciar_destino(conn: psycopg.Connection) -> None:
    tablas = [
        sql.SQL("{}.{}").format(
            sql.Identifier(ESQUEMA_DESTINO),
            sql.Identifier(tabla),
        )
        for tabla in TABLAS_ORDEN
    ]
    sentencia = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY").format(
        sql.SQL(", ").join(tablas)
    )
    with conn.cursor() as cur:
        cur.execute(sentencia)


def validar_recuentos_postgres(
    conn: psycopg.Connection,
    esperados: dict[str, int],
) -> dict[str, int]:
    obtenidos = recuentos_postgres(conn)
    diferencias = {
        tabla: (esperados[tabla], obtenidos[tabla])
        for tabla in TABLAS_ORDEN
        if esperados[tabla] != obtenidos[tabla]
    }
    if diferencias:
        detalle = ", ".join(
            f"{t}: SQLite={a}, Supabase={b}"
            for t, (a, b) in diferencias.items()
        )
        raise RuntimeError("Los recuentos no coinciden: " + detalle)
    return obtenidos


def validar_fk_postgres(conn: psycopg.Connection) -> None:
    queries = {
        "lote_preguntas.norma_id_normalizada -> normas.id": """
            SELECT COUNT(*)
            FROM contenidos.lote_preguntas lp
            LEFT JOIN contenidos.normas n ON n.id = lp.norma_id_normalizada
            WHERE lp.norma_id_normalizada IS NOT NULL
              AND n.id IS NULL
        """,
        "temario_referencias.norma_id -> normas.id": """
            SELECT COUNT(*)
            FROM contenidos.temario_referencias tr
            LEFT JOIN contenidos.normas n ON n.id = tr.norma_id
            WHERE tr.norma_id IS NOT NULL
              AND n.id IS NULL
        """,
    }

    with conn.cursor() as cur:
        for nombre, query in queries.items():
            cur.execute(query)
            huerfanos = int(cur.fetchone()[0])
            if huerfanos:
                raise RuntimeError(
                    f"Relación lógica inválida en Supabase: "
                    f"{nombre}: {huerfanos} huérfanos."
                )


def guardar_informe(
    ruta: Path,
    *,
    snapshot: Path,
    informe_publicacion: Path,
    antes: dict[str, int],
    despues: dict[str, int],
    relaciones_logicas: dict[str, int],
) -> None:
    informe = {
        "fecha": datetime.now().astimezone().isoformat(),
        "snapshot": str(snapshot.resolve()),
        "informe_publicacion": str(informe_publicacion.resolve()),
        "sha256_snapshot": sha256_archivo(snapshot),
        "esquema_destino": ESQUEMA_DESTINO,
        "recuentos_antes": antes,
        "recuentos_despues": despues,
        "relaciones_logicas_origen": relaciones_logicas,
        "resultado": "OK",
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(informe, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def actualizar(
    snapshot: Path,
    informe_salida: Path,
    ruta_env: Path,
    informe_publicacion: Path | None = None,
    confirmar: bool = True,
) -> None:
    snapshot = snapshot.resolve()
    if not snapshot.is_file():
        raise RuntimeError(f"No existe el snapshot: {snapshot}")

    informe_publicacion = (
        informe_publicacion or localizar_informe(snapshot)
    ).resolve()

    print("=" * 78)
    print("ACTUALIZACIÓN DE CONTENIDOS SQLITE → SUPABASE")
    print("=" * 78)

    validar_informe(snapshot, informe_publicacion)
    cargar_entorno(ruta_env)

    print(f"Origen:      {snapshot}")
    print(f"Publicación: {informe_publicacion}")
    print(f"Destino:     PostgreSQL / esquema {ESQUEMA_DESTINO}")
    print(f"Entorno:     {ruta_env.resolve()}")
    print()
    print("Sólo se modificará el esquema contenidos.*.")
    print("La sustitución completa se ejecutará en UNA SOLA TRANSACCIÓN.")
    print("Si algo falla antes del COMMIT, PostgreSQL hará ROLLBACK completo.")
    print("Durante la transacción, las consultas concurrentes pueden quedar")
    print("esperando brevemente; nunca deben ver una carga parcial.")
    print()

    con_sqlite = conectar_sqlite_solo_lectura(snapshot)
    try:
        recuentos_origen = validar_origen(con_sqlite)
        relaciones = validar_relaciones_logicas_sqlite(con_sqlite)

        print("Snapshot e informe validados.")
        print(f"Filas publicables: {sum(recuentos_origen.values())}")

        with psycopg.connect(database_url()) as conn:
            validar_tablas_destino(conn)
            asegurar_columna_activa_destino(con_sqlite, conn)
            validar_columnas_destino(con_sqlite, conn)
            antes = recuentos_postgres(conn)

            print(f"Filas actuales en Supabase: {sum(antes.values())}")

            if confirmar:
                respuesta = input(
                    "\n¿Sustituir contenidos.* por ESTE snapshot? [s/N]: "
                ).strip().lower()
                if respuesta not in {"s", "si", "sí"}:
                    print("Operación cancelada. No se ha modificado Supabase.")
                    return

            print("\nIniciando transacción de publicación...")
            vaciar_destino(conn)

            print("Cargando tablas...")
            for tabla in TABLAS_ORDEN:
                n = insertar_tabla(con_sqlite, conn, tabla)
                print(f"  {tabla:<40} {n:>8}")

            print("\nValidando recuentos dentro de la transacción...")
            despues = validar_recuentos_postgres(conn, recuentos_origen)

            print("Validando relaciones...")
            validar_fk_postgres(conn)

            # El context manager confirma aquí. Cualquier excepción anterior
            # provoca rollback de toda la sustitución.
        guardar_informe(
            informe_salida,
            snapshot=snapshot,
            informe_publicacion=informe_publicacion,
            antes=antes,
            despues=despues,
            relaciones_logicas=relaciones,
        )

        print("\n" + "=" * 78)
        print("ACTUALIZACIÓN SUPABASE COMPLETADA Y VALIDADA")
        print("=" * 78)
        print(f"Filas publicadas: {sum(despues.values())}")
        print(f"Informe: {informe_salida.resolve()}")
        print()
        print("No se han modificado datos de usuarios, suscripciones,")
        print("simulacros ni tests guardados.")

    finally:
        con_sqlite.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sustituye de forma transaccional las 16 tablas publicables del "
            "esquema contenidos de Supabase por un snapshot validado."
        )
    )
    parser.add_argument(
        "snapshot",
        type=Path,
        help="Ruta a oposiciones_web_YYYYMMDD_HHMMSS.sqlite3",
    )
    parser.add_argument(
        "--env",
        type=Path,
        required=True,
        help="Ruta al .env que contiene DATABASE_URL.",
    )
    parser.add_argument(
        "--informe-publicacion",
        type=Path,
        default=None,
        help=(
            "Informe publicacion_*.json. Si se omite, se exige exactamente "
            "uno en la carpeta del snapshot."
        ),
    )
    parser.add_argument(
        "--informe",
        type=Path,
        default=RAIZ / "publicaciones_web" / "actualizacion_supabase.json",
        help="Ruta del informe JSON de la actualización.",
    )
    parser.add_argument(
        "--si",
        action="store_true",
        help="Omite la confirmación interactiva (para invocación desde el menú).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        actualizar(
            snapshot=args.snapshot,
            informe_salida=args.informe,
            ruta_env=args.env,
            informe_publicacion=args.informe_publicacion,
            confirmar=not args.si,
        )
    except Exception as exc:
        print(f"\nERROR: {exc}")
        print("La actualización no se ha confirmado.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
