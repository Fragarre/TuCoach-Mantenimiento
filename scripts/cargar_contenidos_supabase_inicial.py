#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    ("banco_preguntas_temas", "es_principal"),
}

ESQUEMA_DESTINO = "contenidos"


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

    # override=False evita sobrescribir una DATABASE_URL que el operador
    # haya definido explícitamente en el entorno de ejecución.
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

    recuentos = {}
    for tabla in TABLAS_ORDEN:
        recuentos[tabla] = int(
            con.execute(f'SELECT COUNT(*) FROM "{tabla}"').fetchone()[0]
        )
    return recuentos


def validar_relaciones_logicas_sqlite(con: sqlite3.Connection) -> dict[str, int]:
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

    resultado = {}
    for nombre, query in checks.items():
        huérfanos = int(con.execute(query).fetchone()[0])
        resultado[nombre] = huérfanos
        if huérfanos:
            raise RuntimeError(
                f"Relación lógica inválida en origen: {nombre}: {huérfanos} huérfanos."
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
        existentes = {r[0] for r in cur.fetchall()}

    faltantes = [t for t in TABLAS_ORDEN if t not in existentes]
    if faltantes:
        raise RuntimeError(
            "Faltan tablas en Supabase: " + ", ".join(faltantes)
        )


def asegurar_destino_vacio(conn: psycopg.Connection) -> None:
    no_vacias = []
    with conn.cursor() as cur:
        for tabla in TABLAS_ORDEN:
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(ESQUEMA_DESTINO),
                    sql.Identifier(tabla),
                )
            )
            n = int(cur.fetchone()[0])
            if n:
                no_vacias.append((tabla, n))

    if no_vacias:
        detalle = ", ".join(f"{t}={n}" for t, n in no_vacias)
        raise RuntimeError(
            "La carga inicial exige contenidos.* vacío. "
            f"Hay datos existentes: {detalle}"
        )


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


def validar_recuentos_postgres(
    conn: psycopg.Connection,
    esperados: dict[str, int],
) -> dict[str, int]:
    obtenidos = {}
    with conn.cursor() as cur:
        for tabla in TABLAS_ORDEN:
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(ESQUEMA_DESTINO),
                    sql.Identifier(tabla),
                )
            )
            obtenidos[tabla] = int(cur.fetchone()[0])

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
    # Si las FK declaradas no se respetaran, PostgreSQL habría abortado la carga.
    # Añadimos comprobaciones explícitas para las dos relaciones lógicas no
    # declaradas todavía como FK.
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
            huérfanos = int(cur.fetchone()[0])
            if huérfanos:
                raise RuntimeError(
                    f"Relación lógica inválida en Supabase: "
                    f"{nombre}: {huérfanos} huérfanos."
                )


def guardar_informe(
    ruta: Path,
    *,
    snapshot: Path,
    recuentos: dict[str, int],
    relaciones_logicas: dict[str, int],
) -> None:
    informe = {
        "fecha": datetime.now().astimezone().isoformat(),
        "snapshot": str(snapshot.resolve()),
        "esquema_destino": ESQUEMA_DESTINO,
        "tablas": recuentos,
        "relaciones_logicas_origen": relaciones_logicas,
        "resultado": "OK",
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(informe, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cargar(snapshot: Path, informe_salida: Path, ruta_env: Path) -> None:
    snapshot = snapshot.resolve()
    if not snapshot.is_file():
        raise RuntimeError(f"No existe el snapshot: {snapshot}")

    print("=" * 78)
    print("CARGA INICIAL SQLITE → SUPABASE")
    print("=" * 78)
    cargar_entorno(ruta_env)

    print(f"Origen:  {snapshot}")
    print(f"Destino: PostgreSQL / esquema {ESQUEMA_DESTINO}")
    print(f"Entorno: {ruta_env.resolve()}")
    print()
    print("Esta operación exige que contenidos.* esté VACÍO.")
    print("La carga se ejecutará dentro de UNA SOLA TRANSACCIÓN.")
    print("Si algo falla, PostgreSQL hará ROLLBACK completo.")
    print()

    con_sqlite = conectar_sqlite_solo_lectura(snapshot)
    try:
        recuentos_origen = validar_origen(con_sqlite)
        relaciones = validar_relaciones_logicas_sqlite(con_sqlite)

        print("SQLite validada.")
        print(f"Filas a cargar: {sum(recuentos_origen.values())}")

        respuesta = input(
            "\n¿Realizar la carga INICIAL en contenidos.* de Supabase? [s/N]: "
        ).strip().lower()
        if respuesta not in {"s", "si", "sí"}:
            print("Operación cancelada. No se ha modificado Supabase.")
            return

        with psycopg.connect(database_url()) as conn:
            # Psycopg 3 hace COMMIT al salir sin excepción y ROLLBACK si hay error.
            validar_tablas_destino(conn)
            asegurar_destino_vacio(conn)

            print("\nCargando tablas...")
            for tabla in TABLAS_ORDEN:
                n = insertar_tabla(con_sqlite, conn, tabla)
                print(f"  {tabla:<40} {n:>8}")

            print("\nValidando recuentos...")
            recuentos_pg = validar_recuentos_postgres(conn, recuentos_origen)

            print("Validando relaciones...")
            validar_fk_postgres(conn)

            # Al salir del context manager sin excepción, psycopg hace COMMIT.

        guardar_informe(
            informe_salida,
            snapshot=snapshot,
            recuentos=recuentos_pg,
            relaciones_logicas=relaciones,
        )

        print("\n" + "=" * 78)
        print("CARGA INICIAL COMPLETADA Y VALIDADA")
        print("=" * 78)
        print(f"Filas cargadas: {sum(recuentos_pg.values())}")
        print(f"Informe: {informe_salida.resolve()}")
        print()
        print("OpoCoach-Web NO ha sido modificada para leer desde Supabase.")

    finally:
        con_sqlite.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Carga inicial de las 16 tablas publicables de un snapshot SQLite "
            "validado al esquema contenidos de Supabase PostgreSQL."
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
        help=(
            "Ruta al fichero .env que contiene DATABASE_URL "
            "(por ejemplo OpoCoach-Web/backend/.env)."
        ),
    )
    parser.add_argument(
        "--informe",
        type=Path,
        default=RAIZ / "publicaciones_web" / "carga_supabase_inicial.json",
        help="Ruta del informe JSON de la carga.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cargar(args.snapshot, args.informe, args.env)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        print("La transacción no se ha confirmado.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
