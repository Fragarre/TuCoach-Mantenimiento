"""
OpoCoach - Importación idempotente del temario de una convocatoria.

Este script:
- importa el CSV del temario;
- crea o actualiza temas y referencias jurídicas;
- no consulta el BOE;
- no descarga artículos;
- deja las referencias jurídicas en estado SIN_RESOLVER;
- permite sincronizar eliminaciones opcionalmente.

Uso:

    python scripts/importar_temario.py ^
        --convocatoria C1-01_58_26 ^
        --csv "data_convocatorias\\C1-01_58_26\\temario.csv"

Opciones:
    --db RUTA
    --encoding utf-8-sig
    --nombre "Temario oficial"
    --sincronizar-eliminaciones
    --sin-copia-seguridad
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
DB_POR_DEFECTO = RAIZ_PROYECTO / "db" / "oposiciones.sqlite3"


@dataclass(frozen=True)
class FilaTemario:
    parte: str
    numero_tema: int
    titulo: str
    nombre_norma: str
    articulo: str
    tipo: str
    tema_no_juridico: str


def limpiar(texto: str | None) -> str:
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto)).strip()


def normalizar(texto: str | None) -> str:
    valor = unicodedata.normalize("NFKD", limpiar(texto))
    valor = "".join(
        caracter
        for caracter in valor
        if not unicodedata.combining(caracter)
    )
    valor = valor.lower()
    valor = re.sub(r"[^a-z0-9]+", " ", valor)
    return limpiar(valor)


def sha256(ruta: Path) -> str:
    resumen = hashlib.sha256()

    with ruta.open("rb") as fichero:
        for bloque in iter(lambda: fichero.read(1024 * 1024), b""):
            resumen.update(bloque)

    return resumen.hexdigest()


def crear_copia_seguridad(ruta_db: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = ruta_db.with_name(
        f"{ruta_db.stem}_antes_temario_{marca}{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, destino)
    return destino

def leer_csv(ruta_csv: Path, encoding: str | None = None) -> list[FilaTemario]:

    codificaciones = (
        [encoding]
        if encoding
        else ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    )

    ultimo_error = None

    for enc in codificaciones:
        try:
            with ruta_csv.open(
                "r",
                encoding=enc,
                newline=""
            ) as fichero:

                lector = csv.DictReader(fichero)

                filas = []

                for registro in lector:
                    filas.append(
                        FilaTemario(
                            parte=limpiar(registro.get("parte")).upper(),
                            numero_tema=int(limpiar(registro.get("tema"))),
                            titulo=limpiar(registro.get("titulo")),
                            nombre_norma=limpiar(registro.get("LEY")),
                            articulo=limpiar(registro.get("articulo")),
                            tipo=limpiar(registro.get("tipo")).upper(),
                            tema_no_juridico=limpiar(
                                registro.get("tema_no_juridico")
                            ).upper(),
                        )
                    )

                print(f"CSV leído usando {enc}")
                return filas

        except UnicodeDecodeError as e:
            ultimo_error = e

    raise ultimo_error

def crear_tablas(conexion: sqlite3.Connection) -> None:
    conexion.executescript(
        """
        CREATE TABLE IF NOT EXISTS temarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            convocatoria_id INTEGER NOT NULL UNIQUE,
            nombre TEXT NOT NULL,
            fichero_origen TEXT,
            hash_fichero TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (convocatoria_id)
                REFERENCES convocatorias(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS temario_temas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            temario_id INTEGER NOT NULL,
            parte TEXT NOT NULL,
            numero_tema INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            tipo_contenido TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (temario_id)
                REFERENCES temarios(id)
                ON DELETE CASCADE,
            UNIQUE (temario_id, parte, numero_tema)
        );

        CREATE TABLE IF NOT EXISTS temario_referencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tema_id INTEGER NOT NULL,
            nombre_norma_csv TEXT NOT NULL,
            nombre_norma_normalizada TEXT NOT NULL,
            articulo_solicitado TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'SIN_RESOLVER',
            mensaje_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tema_id)
                REFERENCES temario_temas(id)
                ON DELETE CASCADE,
            UNIQUE (
                tema_id,
                nombre_norma_normalizada,
                articulo_solicitado
            )
        );

        CREATE TABLE IF NOT EXISTS equivalencias_temas_no_juridicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tema_no_juridico TEXT NOT NULL,
            tema_id INTEGER NOT NULL,
            FOREIGN KEY (tema_id)
                REFERENCES temario_temas(id)
                ON DELETE CASCADE,
            UNIQUE (tema_no_juridico, tema_id)
        );

        CREATE INDEX IF NOT EXISTS idx_equivalencias_tema
            ON equivalencias_temas_no_juridicos(tema_id);

        CREATE INDEX IF NOT EXISTS idx_temario_temas_temario
            ON temario_temas(temario_id);

        CREATE INDEX IF NOT EXISTS idx_temario_referencias_tema
            ON temario_referencias(tema_id);

        CREATE INDEX IF NOT EXISTS idx_temario_referencias_estado
            ON temario_referencias(estado);
        """
    )


def obtener_convocatoria_id(
    conexion: sqlite3.Connection,
    codigo: str,
) -> int:
    fila = conexion.execute(
        """
        SELECT id
        FROM convocatorias
        WHERE codigo = ?
        """,
        (codigo,),
    ).fetchone()

    if fila is None:
        raise RuntimeError(
            f"No existe una convocatoria con código: {codigo}"
        )

    return int(fila[0])


def upsert_temario(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
    nombre: str,
    ruta_csv: Path,
    hash_csv: str,
) -> int:
    conexion.execute(
        """
        INSERT INTO temarios (
            convocatoria_id,
            nombre,
            fichero_origen,
            hash_fichero
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(convocatoria_id) DO UPDATE SET
            nombre = excluded.nombre,
            fichero_origen = excluded.fichero_origen,
            hash_fichero = excluded.hash_fichero,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            convocatoria_id,
            nombre,
            ruta_csv.as_posix(),
            hash_csv,
        ),
    )

    fila = conexion.execute(
        """
        SELECT id
        FROM temarios
        WHERE convocatoria_id = ?
        """,
        (convocatoria_id,),
    ).fetchone()

    return int(fila[0])


def upsert_tema(
    conexion: sqlite3.Connection,
    temario_id: int,
    fila: FilaTemario,
) -> int:
    conexion.execute(
        """
        INSERT INTO temario_temas (
            temario_id,
            parte,
            numero_tema,
            titulo,
            tipo_contenido
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(temario_id, parte, numero_tema) DO UPDATE SET
            titulo = excluded.titulo,
            tipo_contenido = excluded.tipo_contenido,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            temario_id,
            fila.parte,
            fila.numero_tema,
            fila.titulo,
            fila.tipo,
        ),
    )

    registro = conexion.execute(
        """
        SELECT id
        FROM temario_temas
        WHERE temario_id = ?
          AND parte = ?
          AND numero_tema = ?
        """,
        (
            temario_id,
            fila.parte,
            fila.numero_tema,
        ),
    ).fetchone()

    return int(registro[0])


def upsert_referencia(
    conexion: sqlite3.Connection,
    tema_id: int,
    fila: FilaTemario,
) -> None:
    conexion.execute(
        """
        INSERT INTO temario_referencias (
            tema_id,
            nombre_norma_csv,
            nombre_norma_normalizada,
            articulo_solicitado,
            estado,
            mensaje_error
        )
        VALUES (?, ?, ?, ?, 'SIN_RESOLVER', NULL)
        ON CONFLICT(
            tema_id,
            nombre_norma_normalizada,
            articulo_solicitado
        ) DO UPDATE SET
            nombre_norma_csv = excluded.nombre_norma_csv,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            tema_id,
            fila.nombre_norma,
            normalizar(fila.nombre_norma),
            fila.articulo,
        ),
    )


def upsert_equivalencia(
    conexion: sqlite3.Connection,
    tema_id: int,
    tema_no_juridico: str,
) -> None:
    conexion.execute(
        """
        INSERT INTO equivalencias_temas_no_juridicos (
            tema_no_juridico,
            tema_id
        )
        VALUES (?, ?)
        ON CONFLICT(tema_no_juridico, tema_id) DO NOTHING
        """,
        (tema_no_juridico, tema_id),
    )


def claves_presentes(
    filas: Iterable[FilaTemario],
) -> tuple[
    set[tuple[str, int]],
    set[tuple[str, int, str, str]],
    set[tuple[str, int, str]],
]:
    temas: set[tuple[str, int]] = set()
    referencias: set[tuple[str, int, str, str]] = set()
    equivalencias: set[tuple[str, int, str]] = set()

    for fila in filas:
        temas.add((fila.parte, fila.numero_tema))

        if fila.articulo:
            referencias.add(
                (
                    fila.parte,
                    fila.numero_tema,
                    normalizar(fila.nombre_norma),
                    fila.articulo,
                )
            )

        if fila.tema_no_juridico:
            equivalencias.add(
                (
                    fila.parte,
                    fila.numero_tema,
                    fila.tema_no_juridico,
                )
            )

    return temas, referencias, equivalencias


def sincronizar_eliminaciones(
    conexion: sqlite3.Connection,
    temario_id: int,
    filas: list[FilaTemario],
) -> tuple[int, int, int]:
    temas_csv, referencias_csv, equivalencias_csv = claves_presentes(filas)

    referencias_eliminadas = 0
    equivalencias_eliminadas = 0
    temas_eliminados = 0

    referencias_db = conexion.execute(
        """
        SELECT
            r.id,
            t.parte,
            t.numero_tema,
            r.nombre_norma_normalizada,
            r.articulo_solicitado
        FROM temario_referencias AS r
        JOIN temario_temas AS t
          ON t.id = r.tema_id
        WHERE t.temario_id = ?
        """,
        (temario_id,),
    ).fetchall()

    for registro in referencias_db:
        clave = (
            registro["parte"],
            int(registro["numero_tema"]),
            registro["nombre_norma_normalizada"],
            registro["articulo_solicitado"],
        )

        if clave not in referencias_csv:
            conexion.execute(
                """
                DELETE FROM temario_referencias
                WHERE id = ?
                """,
                (registro["id"],),
            )
            referencias_eliminadas += 1

    equivalencias_db = conexion.execute(
        """
        SELECT
            e.id,
            t.parte,
            t.numero_tema,
            e.tema_no_juridico
        FROM equivalencias_temas_no_juridicos AS e
        JOIN temario_temas AS t
          ON t.id = e.tema_id
        WHERE t.temario_id = ?
        """,
        (temario_id,),
    ).fetchall()

    for registro in equivalencias_db:
        clave = (
            registro["parte"],
            int(registro["numero_tema"]),
            registro["tema_no_juridico"],
        )

        if clave not in equivalencias_csv:
            conexion.execute(
                """
                DELETE FROM equivalencias_temas_no_juridicos
                WHERE id = ?
                """,
                (registro["id"],),
            )
            equivalencias_eliminadas += 1

    temas_db = conexion.execute(
        """
        SELECT id, parte, numero_tema
        FROM temario_temas
        WHERE temario_id = ?
        """,
        (temario_id,),
    ).fetchall()

    for registro in temas_db:
        clave = (
            registro["parte"],
            int(registro["numero_tema"]),
        )

        if clave not in temas_csv:
            conexion.execute(
                """
                DELETE FROM temario_temas
                WHERE id = ?
                """,
                (registro["id"],),
            )
            temas_eliminados += 1

    return (
        referencias_eliminadas,
        equivalencias_eliminadas,
        temas_eliminados,
    )


def importar(args: argparse.Namespace) -> None:
    ruta_db = Path(args.db).resolve()
    ruta_csv = Path(args.csv).resolve()

    if not ruta_db.exists():
        raise FileNotFoundError(
            f"No existe la base de datos: {ruta_db}"
        )

    if not ruta_csv.exists():
        raise FileNotFoundError(
            f"No existe el CSV: {ruta_csv}"
        )

    copia = None

    if not args.sin_copia_seguridad:
        copia = crear_copia_seguridad(ruta_db)

    filas = leer_csv(ruta_csv, args.encoding)
    nombre_temario = args.nombre or f"Temario {args.convocatoria}"

    temas_procesados: set[tuple[str, int]] = set()
    referencias_juridicas = 0
    equivalencias_no_juridicas = 0
    referencias_eliminadas = 0
    equivalencias_eliminadas = 0
    temas_eliminados = 0

    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")

        crear_tablas(conexion)

        convocatoria_id = obtener_convocatoria_id(
            conexion,
            args.convocatoria,
        )

        temario_id = upsert_temario(
            conexion,
            convocatoria_id,
            nombre_temario,
            ruta_csv,
            sha256(ruta_csv),
        )

        for fila in filas:
            tema_id = upsert_tema(
                conexion,
                temario_id,
                fila,
            )

            temas_procesados.add(
                (fila.parte, fila.numero_tema)
            )

            if fila.articulo:
                upsert_referencia(
                    conexion,
                    tema_id,
                    fila,
                )
                referencias_juridicas += 1

            if fila.tema_no_juridico:
                upsert_equivalencia(
                    conexion,
                    tema_id,
                    fila.tema_no_juridico,
                )
                equivalencias_no_juridicas += 1

        if args.sincronizar_eliminaciones:
            (
                referencias_eliminadas,
                equivalencias_eliminadas,
                temas_eliminados,
            ) = sincronizar_eliminaciones(
                conexion,
                temario_id,
                filas,
            )

        conexion.commit()

    print()
    print("IMPORTACIÓN TERMINADA")
    print(f"Convocatoria: {args.convocatoria}")
    print(f"CSV: {ruta_csv}")

    if copia is not None:
        print(f"Copia de seguridad: {copia}")

    print(f"Filas leídas: {len(filas)}")
    print(f"Temas tratados: {len(temas_procesados)}")
    print(f"Referencias jurídicas: {referencias_juridicas}")
    print(
        "Equivalencias no jurídicas: "
        f"{equivalencias_no_juridicas}"
    )
    print("Consultas al BOE: 0")

    if args.sincronizar_eliminaciones:
        print(
            "Referencias eliminadas: "
            f"{referencias_eliminadas}"
        )
        print(
            "Equivalencias eliminadas: "
            f"{equivalencias_eliminadas}"
        )
        print(
            "Temas eliminados: "
            f"{temas_eliminados}"
        )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Importa de forma idempotente el temario de una "
            "convocatoria sin consultar el BOE."
        )
    )

    parser.add_argument(
        "--convocatoria",
        required=True,
        help="Código de la convocatoria existente.",
    )

    parser.add_argument(
        "--csv",
        required=True,
        help="Ruta del CSV del temario.",
    )

    parser.add_argument(
        "--db",
        default=str(DB_POR_DEFECTO),
        help=f"Base de datos. Por defecto: {DB_POR_DEFECTO}",
    )

    parser.add_argument(
        "--encoding",
        default=None,
        help=(
            "Codificación del CSV. Si no se indica, se prueba "
            "utf-8-sig, utf-8, cp1252 y latin-1."
        ),
    )

    parser.add_argument(
        "--nombre",
        help="Nombre del temario.",
    )

    parser.add_argument(
        "--sincronizar-eliminaciones",
        action="store_true",
        help=(
            "Elimina las referencias y temas que ya no "
            "aparezcan en el CSV."
        ),
    )

    parser.add_argument(
        "--sin-copia-seguridad",
        action="store_true",
        help="No crea una copia previa de la base SQLite.",
    )

    return parser


def main() -> None:
    parser = construir_parser()
    args = parser.parse_args()

    try:
        importar(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()