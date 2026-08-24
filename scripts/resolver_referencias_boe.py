"""
OpoCoach - Resolución de referencias jurídicas del temario mediante el BOE.

Responsabilidades:
- recorrer referencias jurídicas pendientes;
- localizar la norma y obtener el artículo consolidado con scripts/boe_api.py;
- guardar cada bloque/artículo BOE una sola vez en articulos_fuente;
- cachear cada resolución norma+artículo en resoluciones_boe;
- reutilizar resoluciones previas sin volver a consultar el BOE;
- vincular temario_referencias con articulos_fuente;
- actualizar el estado de cada referencia sin detener el lote por errores.

No utiliza IA.

Ejemplos:

    python scripts/resolver_referencias_boe.py

    python scripts/resolver_referencias_boe.py --limite 20

    python scripts/resolver_referencias_boe.py --referencia-id 123

    python scripts/resolver_referencias_boe.py --reintentar-pendientes

    python scripts/resolver_referencias_boe.py --sin-copia-seguridad
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from boe_api import (
    BOEError,
    ArticuloBOE,
    buscar_norma,
    extraer_cita,
    obtener_articulo,
    texto_articulo_suficiente,
)
from pdf_normas import (
    buscar_norma as buscar_norma_pdf,
    obtener_articulo as obtener_articulo_pdf,
    tiene_pdf_local,
)


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
DB_POR_DEFECTO = RAIZ_PROYECTO / "db" / "oposiciones.sqlite3"

ESTADO_SIN_RESOLVER = "SIN_RESOLVER"
ESTADO_COMPLETADO = "COMPLETADO"
ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_ERROR_CONSULTA = "ERROR_CONSULTA_BOE"


@dataclass(frozen=True)
class Referencia:
    id: int
    tema_id: int
    nombre_norma_csv: str
    nombre_norma_normalizada: str
    articulo_solicitado: str
    estado: str


@dataclass
class ResultadoResolucion:
    articulo: ArticuloBOE | None
    estado: str
    mensaje: str | None


def limpiar(texto: object | None) -> str:
    if texto is None:
        return ""
    return " ".join(str(texto).split()).strip()


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def crear_copia_seguridad(ruta_db: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = ruta_db.with_name(
        f"{ruta_db.stem}_antes_resolver_boe_{marca}{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, destino)
    return destino


def columnas_tabla(
    conexion: sqlite3.Connection,
    tabla: str,
) -> set[str]:
    filas = conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
    return {str(fila[1]) for fila in filas}


def crear_estructura(conexion: sqlite3.Connection) -> None:
    conexion.executescript(
        """
        CREATE TABLE IF NOT EXISTS articulos_fuente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_boe TEXT NOT NULL,
            id_bloque TEXT NOT NULL,
            articulo_boe TEXT NOT NULL,
            titulo_bloque TEXT NOT NULL,
            departamento TEXT,
            texto TEXT NOT NULL,
            hash_texto TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (id_boe, id_bloque)
        );

        CREATE INDEX IF NOT EXISTS idx_articulos_fuente_id_boe
            ON articulos_fuente(id_boe);

        CREATE TABLE IF NOT EXISTS resoluciones_boe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_norma_normalizada TEXT NOT NULL,
            articulo_solicitado_normalizado TEXT NOT NULL,
            id_boe TEXT NOT NULL,
            id_bloque TEXT NOT NULL,
            articulo_fuente_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (articulo_fuente_id)
                REFERENCES articulos_fuente(id)
                ON DELETE CASCADE,
            UNIQUE (
                nombre_norma_normalizada,
                articulo_solicitado_normalizado
            )
        );

        CREATE INDEX IF NOT EXISTS idx_resoluciones_boe_articulo
            ON resoluciones_boe(articulo_fuente_id);
        """
    )

    columnas = columnas_tabla(conexion, "temario_referencias")

    if "articulo_fuente_id" not in columnas:
        conexion.execute(
            """
            ALTER TABLE temario_referencias
            ADD COLUMN articulo_fuente_id INTEGER
            REFERENCES articulos_fuente(id)
            """
        )

    conexion.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_temario_referencias_articulo_fuente
        ON temario_referencias(articulo_fuente_id)
        """
    )

    # Recupera resoluciones históricas únicamente cuando TODAS las
    # referencias completadas de una misma clave normalizada norma+artículo
    # apuntan al mismo documento y bloque.
    #
    # Es importante no usar SELECT DISTINCT sin consenso: dos normas
    # diferentes pueden compartir tipo+número+año y, por tanto, la misma
    # nombre_norma_normalizada. En ese caso la caché histórica no es
    # documentalmente inequívoca y debe resolverse desde la cita original.
    conexion.execute(
        """
        INSERT INTO resoluciones_boe (
            nombre_norma_normalizada,
            articulo_solicitado_normalizado,
            id_boe,
            id_bloque,
            articulo_fuente_id
        )
        SELECT
            r.nombre_norma_normalizada,
            REPLACE(TRIM(r.articulo_solicitado), ',', '.'),
            MAX(a.id_boe),
            MAX(a.id_bloque),
            MAX(a.id)
        FROM temario_referencias AS r
        JOIN articulos_fuente AS a
          ON a.id = r.articulo_fuente_id
        WHERE r.estado = 'COMPLETADO'
          AND r.nombre_norma_normalizada <> ''
          AND r.articulo_solicitado <> ''
        GROUP BY
            r.nombre_norma_normalizada,
            REPLACE(TRIM(r.articulo_solicitado), ',', '.')
        HAVING COUNT(
            DISTINCT (
                a.id_boe || '|' ||
                a.id_bloque || '|' ||
                CAST(a.id AS TEXT)
            )
        ) = 1
        ON CONFLICT(
            nombre_norma_normalizada,
            articulo_solicitado_normalizado
        ) DO NOTHING
        """
    )


def cargar_referencias(
    conexion: sqlite3.Connection,
    referencia_id: int | None,
    temario_id: int | None,
    limite: int | None,
    reintentar_pendientes: bool,
    reparar_textos_incompletos: bool,
    reparar_mezclas_versiones: bool,
) -> list[Referencia]:
    estados = [ESTADO_SIN_RESOLVER, ESTADO_ERROR_CONSULTA]

    if reintentar_pendientes:
        estados.append(ESTADO_PENDIENTE)

    condiciones = [
        "r.nombre_norma_csv <> ''",
        "r.articulo_solicitado <> ''",
    ]
    parametros: list[object] = []

    if referencia_id is not None:
        condiciones.append("r.id = ?")
        parametros.append(referencia_id)
    elif reparar_textos_incompletos or reparar_mezclas_versiones:
        condiciones.append("r.estado = ?")
        parametros.append(ESTADO_COMPLETADO)
        condiciones.append("r.articulo_fuente_id IS NOT NULL")
    else:
        marcadores = ", ".join("?" for _ in estados)
        condiciones.append(f"r.estado IN ({marcadores})")
        parametros.extend(estados)

    if temario_id is not None:
        condiciones.append(
            "EXISTS (SELECT 1 FROM temario_temas tt "
            "WHERE tt.id=r.tema_id AND tt.temario_id=?)"
        )
        parametros.append(temario_id)

    sql = f"""
        SELECT
            r.id,
            r.tema_id,
            r.nombre_norma_csv,
            r.nombre_norma_normalizada,
            r.articulo_solicitado,
            r.estado,
            af.texto AS texto_fuente_actual,
            af.titulo_bloque AS titulo_fuente_actual
        FROM temario_referencias AS r
        LEFT JOIN articulos_fuente AS af
          ON af.id = r.articulo_fuente_id
        WHERE {' AND '.join(condiciones)}
        ORDER BY
            r.nombre_norma_normalizada,
            r.articulo_solicitado,
            r.id
    """

    filas = conexion.execute(sql, parametros).fetchall()

    if reparar_textos_incompletos:
        filas = [
            fila for fila in filas
            if not texto_articulo_suficiente(
                fila["texto_fuente_actual"], fila["titulo_fuente_actual"]
            )
        ]

    if limite is not None:
        filas = filas[:limite]

    return [
        Referencia(
            id=int(fila["id"]),
            tema_id=int(fila["tema_id"]),
            nombre_norma_csv=limpiar(fila["nombre_norma_csv"]),
            nombre_norma_normalizada=limpiar(
                fila["nombre_norma_normalizada"]
            ),
            articulo_solicitado=limpiar(fila["articulo_solicitado"]),
            estado=limpiar(fila["estado"]),
        )
        for fila in filas
    ]


def articulo_base(articulo_solicitado: str) -> str:
    valor = limpiar(articulo_solicitado).replace(",", ".")
    return valor.split(".", 1)[0]


def articulo_normalizado(articulo_solicitado: str) -> str:
    return limpiar(articulo_solicitado).replace(",", ".")


def clave_documental_referencia(
    referencia: Referencia,
) -> str:
    """
    Devuelve una identidad documental más precisa que
    nombre_norma_normalizada.

    - Para PDFs locales utiliza el identificador real de la fuente.
    - Para normas BOE utiliza la cita completa extraída por boe_api
      (tipo, número, año, fecha y ámbito).
    - Para normas especiales sin patrón número/año utiliza el id_boe
      validado por buscar_norma().
    """
    if tiene_pdf_local(referencia.nombre_norma_csv):
        norma = buscar_norma_pdf(referencia.nombre_norma_csv)
        return f"pdf|{limpiar(norma.id_boe)}"

    try:
        cita = extraer_cita(referencia.nombre_norma_csv)
        return f"boe-cita|{cita.clave}"
    except BOEError:
        norma = buscar_norma(referencia.nombre_norma_csv)
        return f"boe-id|{limpiar(norma.id_boe)}"


def id_fuente_esperada(
    referencia: Referencia,
) -> str:
    """
    Resuelve la identidad documental real de la norma usando la cita
    original del temario. Esta comprobación impide reutilizar una
    resolución cacheada de otra norma que comparta tipo+número+año.
    """
    if tiene_pdf_local(referencia.nombre_norma_csv):
        return limpiar(
            buscar_norma_pdf(referencia.nombre_norma_csv).id_boe
        )

    return limpiar(buscar_norma(referencia.nombre_norma_csv).id_boe)


def buscar_resolucion_bd(
    conexion: sqlite3.Connection,
    referencia: Referencia,
) -> sqlite3.Row | None:
    fila = conexion.execute(
        """
        SELECT
            rb.articulo_fuente_id,
            rb.id_boe,
            rb.id_bloque,
            a.articulo_boe,
            a.titulo_bloque
        FROM resoluciones_boe AS rb
        JOIN articulos_fuente AS a
          ON a.id = rb.articulo_fuente_id
        WHERE rb.nombre_norma_normalizada = ?
          AND rb.articulo_solicitado_normalizado = ?
        """,
        (
            referencia.nombre_norma_normalizada,
            articulo_normalizado(referencia.articulo_solicitado),
        ),
    ).fetchone()

    if fila is None:
        return None

    # Defensa crítica:
    # la clave histórica de resoluciones_boe no distingue normas distintas
    # que comparten tipo+número+año (por ejemplo dos "Ley 4/2023").
    # Antes de reutilizar la resolución se valida el id de la fuente contra
    # la cita ORIGINAL del temario.
    esperado = id_fuente_esperada(referencia)
    almacenado = limpiar(fila["id_boe"])

    if almacenado != esperado:
        print(
            "  CACHÉ BOE RECHAZADA: "
            f"almacenada={almacenado} | esperada={esperado} | "
            f"cita={referencia.nombre_norma_csv}"
        )
        return None

    return fila


def guardar_resolucion_bd(
    conexion: sqlite3.Connection,
    referencia: Referencia,
    articulo: ArticuloBOE,
    articulo_fuente_id: int,
) -> None:
    conexion.execute(
        """
        INSERT INTO resoluciones_boe (
            nombre_norma_normalizada,
            articulo_solicitado_normalizado,
            id_boe,
            id_bloque,
            articulo_fuente_id
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(
            nombre_norma_normalizada,
            articulo_solicitado_normalizado
        ) DO UPDATE SET
            id_boe = excluded.id_boe,
            id_bloque = excluded.id_bloque,
            articulo_fuente_id = excluded.articulo_fuente_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            referencia.nombre_norma_normalizada,
            articulo_normalizado(referencia.articulo_solicitado),
            articulo.id_boe,
            articulo.id_bloque,
            articulo_fuente_id,
        ),
    )


def upsert_articulo_fuente(
    conexion: sqlite3.Connection,
    articulo: ArticuloBOE,
) -> tuple[int, bool]:
    existente = conexion.execute(
        """
        SELECT id
        FROM articulos_fuente
        WHERE id_boe = ?
          AND id_bloque = ?
        """,
        (articulo.id_boe, articulo.id_bloque),
    ).fetchone()

    creado = existente is None

    conexion.execute(
        """
        INSERT INTO articulos_fuente (
            id_boe,
            id_bloque,
            articulo_boe,
            titulo_bloque,
            departamento,
            texto,
            hash_texto
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id_boe, id_bloque) DO UPDATE SET
            articulo_boe = excluded.articulo_boe,
            titulo_bloque = excluded.titulo_bloque,
            departamento = excluded.departamento,
            texto = excluded.texto,
            hash_texto = excluded.hash_texto,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            articulo.id_boe,
            articulo.id_bloque,
            articulo_base(articulo.articulo),
            articulo.titulo_bloque,
            articulo.departamento,
            articulo.texto,
            hash_texto(articulo.texto),
        ),
    )

    fila = conexion.execute(
        """
        SELECT id
        FROM articulos_fuente
        WHERE id_boe = ?
          AND id_bloque = ?
        """,
        (articulo.id_boe, articulo.id_bloque),
    ).fetchone()

    if fila is None:
        raise RuntimeError("No se pudo recuperar el artículo guardado.")

    return int(fila[0]), creado


def marcar_completada(
    conexion: sqlite3.Connection,
    referencia_id: int,
    articulo_fuente_id: int,
) -> None:
    conexion.execute(
        """
        UPDATE temario_referencias
        SET articulo_fuente_id = ?,
            estado = ?,
            mensaje_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            articulo_fuente_id,
            ESTADO_COMPLETADO,
            referencia_id,
        ),
    )


def marcar_error(
    conexion: sqlite3.Connection,
    referencia_id: int,
    estado: str,
    mensaje: str,
) -> None:
    conexion.execute(
        """
        UPDATE temario_referencias
        SET articulo_fuente_id = NULL,
            estado = ?,
            mensaje_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (estado, mensaje[:4000], referencia_id),
    )


def contiene_error_red(exc: BaseException) -> bool:
    actual: BaseException | None = exc

    while actual is not None:
        nombre = actual.__class__.__name__.lower()
        modulo = actual.__class__.__module__.lower()
        texto = str(actual).lower()

        if "requests" in modulo:
            return True

        if any(
            fragmento in nombre
            for fragmento in (
                "timeout",
                "connection",
                "proxy",
                "http",
            )
        ):
            return True

        if any(
            fragmento in texto
            for fragmento in (
                "error al consultar el boe",
                "no se pudo buscar la norma en el boe",
                "timeout",
                "timed out",
                "connection",
                "http 5",
                "http: 5",
                "temporarily unavailable",
            )
        ):
            return True

        actual = actual.__cause__ or actual.__context__

    return False


def resolver_una(
    referencia: Referencia,
) -> ResultadoResolucion:
    try:
        if tiene_pdf_local(referencia.nombre_norma_csv):
            buscar_norma_pdf(referencia.nombre_norma_csv)
            articulo = obtener_articulo_pdf(
                referencia.nombre_norma_csv,
                referencia.articulo_solicitado,
            )
        else:
            buscar_norma(referencia.nombre_norma_csv)
            articulo = obtener_articulo(
                referencia.nombre_norma_csv,
                referencia.articulo_solicitado,
            )

        if not texto_articulo_suficiente(articulo.texto, articulo.titulo_bloque):
            raise BOEError(
                "El artículo recuperado no contiene cuerpo normativo completo; "
                "solo se obtuvo el título/rúbrica o texto vacío."
            )

        return ResultadoResolucion(
            articulo=articulo,
            estado=ESTADO_COMPLETADO,
            mensaje=None,
        )

    except BOEError as exc:
        estado = (
            ESTADO_ERROR_CONSULTA
            if contiene_error_red(exc)
            else ESTADO_PENDIENTE
        )
        return ResultadoResolucion(
            articulo=None,
            estado=estado,
            mensaje=limpiar(exc),
        )

    except (ValueError, TypeError) as exc:
        return ResultadoResolucion(
            articulo=None,
            estado=ESTADO_PENDIENTE,
            mensaje=limpiar(exc),
        )

    except Exception as exc:
        return ResultadoResolucion(
            articulo=None,
            estado=ESTADO_ERROR_CONSULTA,
            mensaje=(
                f"Error inesperado {exc.__class__.__name__}: "
                f"{limpiar(exc)}"
            ),
        )


def clave_cache(referencia: Referencia) -> tuple[str, str]:
    # La caché del lote debe usar identidad documental y no únicamente
    # nombre_norma_normalizada, para evitar mezclar normas homónimas.
    return (
        clave_documental_referencia(referencia),
        articulo_normalizado(referencia.articulo_solicitado),
    )


def limpiar_articulos_huerfanos_incompletos(ruta_db: Path, sin_copia_seguridad: bool = False) -> None:
    """
    Elimina exclusivamente registros de articulos_fuente que cumplen A LA VEZ:
    - no están referenciados por temario_referencias;
    - no están referenciados por resoluciones_boe;
    - su texto es manifiestamente incompleto según texto_articulo_suficiente().

    No elimina artículos huérfanos válidos ni modifica referencias del temario.
    """
    if not ruta_db.exists():
        raise FileNotFoundError(f"No existe la base de datos: {ruta_db}")

    copia = None
    if not sin_copia_seguridad:
        copia = crear_copia_seguridad(ruta_db)

    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")
        crear_estructura(conexion)

        filas = conexion.execute(
            """
            SELECT af.id, af.id_boe, af.id_bloque, af.articulo_boe, af.titulo_bloque, af.texto
            FROM articulos_fuente AS af
            LEFT JOIN temario_referencias AS tr
              ON tr.articulo_fuente_id = af.id
            LEFT JOIN resoluciones_boe AS rb
              ON rb.articulo_fuente_id = af.id
            WHERE tr.id IS NULL
              AND rb.id IS NULL
            ORDER BY af.id
            """
        ).fetchall()

        candidatos = [
            fila for fila in filas
            if not texto_articulo_suficiente(fila["texto"], fila["titulo_bloque"])
        ]

        print("LIMPIEZA DE ARTÍCULOS FUENTE HUÉRFANOS E INCOMPLETOS")
        print(f"Base de datos: {ruta_db}")
        if copia is not None:
            print(f"Copia de seguridad: {copia}")
        print(f"Artículos huérfanos totales: {len(filas)}")
        print(f"Huérfanos manifiestamente incompletos: {len(candidatos)}")

        if not candidatos:
            print("No hay registros que cumplan simultáneamente los tres criterios.")
            return

        ids = [int(fila["id"]) for fila in candidatos]
        marcadores = ",".join("?" for _ in ids)

        # Comprobación inmediata antes de borrar: ninguno puede haber adquirido
        # una referencia entre la selección y el DELETE dentro de esta conexión.
        usados = conexion.execute(
            f"""
            SELECT af.id
            FROM articulos_fuente AS af
            WHERE af.id IN ({marcadores})
              AND (
                    EXISTS (
                        SELECT 1 FROM temario_referencias tr
                        WHERE tr.articulo_fuente_id = af.id
                    )
                    OR EXISTS (
                        SELECT 1 FROM resoluciones_boe rb
                        WHERE rb.articulo_fuente_id = af.id
                    )
                  )
            """,
            ids,
        ).fetchall()
        if usados:
            raise RuntimeError(
                "La limpieza se ha cancelado: uno o más candidatos han pasado a estar referenciados."
            )

        conexion.execute(
            f"DELETE FROM articulos_fuente WHERE id IN ({marcadores})",
            ids,
        )

        fk = conexion.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            conexion.rollback()
            raise RuntimeError(
                "La limpieza produciría incidencias de claves foráneas; se ha revertido."
            )

        conexion.commit()

        restantes = conexion.execute(
            """
            SELECT COUNT(*)
            FROM articulos_fuente AS af
            LEFT JOIN temario_referencias AS tr
              ON tr.articulo_fuente_id = af.id
            LEFT JOIN resoluciones_boe AS rb
              ON rb.articulo_fuente_id = af.id
            WHERE tr.id IS NULL
              AND rb.id IS NULL
            """
        ).fetchone()[0]

        print(f"Registros eliminados: {len(ids)}")
        print(f"Artículos huérfanos restantes: {restantes}")



def reparar_articulo_fuente_por_id(
    ruta_db: Path,
    articulo_fuente_id: int,
    sin_copia_seguridad: bool = False,
) -> None:
    """Repara de forma controlada un único articulos_fuente.id existente."""
    if not ruta_db.exists():
        raise FileNotFoundError(f"No existe la base de datos: {ruta_db}")

    copia = None
    if not sin_copia_seguridad:
        copia = crear_copia_seguridad(ruta_db)

    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")
        crear_estructura(conexion)
        conexion.commit()

        fuente = conexion.execute(
            """
            SELECT
                id, id_boe, id_bloque, articulo_boe, titulo_bloque,
                departamento, texto, hash_texto
            FROM articulos_fuente
            WHERE id = ?
            """,
            (articulo_fuente_id,),
        ).fetchone()
        if fuente is None:
            raise ValueError(
                f"No existe articulos_fuente.id = {articulo_fuente_id}."
            )

        filas = conexion.execute(
            """
            SELECT
                r.id, r.tema_id, r.nombre_norma_csv,
                r.nombre_norma_normalizada, r.articulo_solicitado, r.estado
            FROM temario_referencias AS r
            WHERE r.articulo_fuente_id = ?
            ORDER BY r.id
            """,
            (articulo_fuente_id,),
        ).fetchall()
        if not filas:
            raise ValueError(
                f"El artículo fuente {articulo_fuente_id} no está enlazado "
                "a ninguna referencia del temario; no se modifica."
            )

        estados = {limpiar(fila["estado"]) for fila in filas}
        if estados != {ESTADO_COMPLETADO}:
            raise ValueError(
                f"El artículo fuente {articulo_fuente_id} tiene referencias "
                f"con estados distintos de COMPLETADO: {sorted(estados)}."
            )

        claves = {
            (
                limpiar(fila["nombre_norma_normalizada"]),
                articulo_normalizado(fila["articulo_solicitado"]),
            )
            for fila in filas
        }
        if len(claves) != 1:
            raise ValueError(
                f"El artículo fuente {articulo_fuente_id} está compartido por "
                "referencias de norma/artículo distintos; no se modifica."
            )

        referencia = Referencia(
            id=int(filas[0]["id"]),
            tema_id=int(filas[0]["tema_id"]),
            nombre_norma_csv=limpiar(filas[0]["nombre_norma_csv"]),
            nombre_norma_normalizada=limpiar(
                filas[0]["nombre_norma_normalizada"]
            ),
            articulo_solicitado=limpiar(filas[0]["articulo_solicitado"]),
            estado=limpiar(filas[0]["estado"]),
        )

        resoluciones = conexion.execute(
            """
            SELECT
                id, nombre_norma_normalizada,
                articulo_solicitado_normalizado, id_boe, id_bloque
            FROM resoluciones_boe
            WHERE articulo_fuente_id = ?
            ORDER BY id
            """,
            (articulo_fuente_id,),
        ).fetchall()

        clave_objetivo = next(iter(claves))
        claves_resolucion = {
            (
                limpiar(fila["nombre_norma_normalizada"]),
                articulo_normalizado(fila["articulo_solicitado_normalizado"]),
            )
            for fila in resoluciones
        }
        if claves_resolucion and claves_resolucion != {clave_objetivo}:
            raise ValueError(
                f"El artículo fuente {articulo_fuente_id} está compartido por "
                "resoluciones BOE de norma/artículo distintos; no se modifica."
            )

        print("REPARACIÓN CONTROLADA DE UN ARTÍCULO FUENTE")
        print(f"Base de datos: {ruta_db}")
        if copia is not None:
            print(f"Copia de seguridad: {copia}")
        print(f"Artículo fuente ID: {articulo_fuente_id}")
        print(f"Referencias enlazadas: {len(filas)}")
        print(
            f"Objetivo: {referencia.nombre_norma_csv} | "
            f"art. {referencia.articulo_solicitado}"
        )
        print(
            f"Actual: {fuente['id_boe']} | bloque {fuente['id_bloque']} "
            f"| caracteres {len(limpiar(fuente['texto']))}"
        )

        resultado = resolver_una(referencia)
        if resultado.articulo is None:
            raise BOEError(
                "No se obtuvo una nueva resolución válida; se conserva el "
                f"artículo existente. {resultado.mensaje or ''}"
            )

        articulo = resultado.articulo
        if limpiar(articulo.id_boe) != limpiar(fuente["id_boe"]):
            raise ValueError(
                "La nueva resolución corresponde a un BOE distinto del "
                "registro objetivo; no se modifica."
            )
        if articulo_base(articulo.articulo) != articulo_base(
            referencia.articulo_solicitado
        ):
            raise ValueError(
                "La nueva resolución corresponde a un artículo distinto del "
                "solicitado; no se modifica."
            )
        if limpiar(articulo.id_bloque) != limpiar(fuente["id_bloque"]):
            raise ValueError(
                "La nueva resolución devuelve un bloque BOE distinto "
                f"({articulo.id_bloque}) del bloque almacenado "
                f"({fuente['id_bloque']}); no se modifica automáticamente."
            )

        hash_anterior = hash_texto(limpiar(fuente["texto"]))
        hash_nuevo = hash_texto(articulo.texto)
        if hash_anterior == hash_nuevo:
            raise ValueError(
                "La nueva resolución produce exactamente el mismo texto que "
                "el registro existente; no hay nada que reparar."
            )

        try:
            nuevo_id, creado = upsert_articulo_fuente(conexion, articulo)
            if creado or nuevo_id != articulo_fuente_id:
                raise RuntimeError(
                    "La actualización no se realizaría sobre el mismo registro "
                    "de articulos_fuente; operación cancelada."
                )
            conexion.execute(
                """
                UPDATE resoluciones_boe
                SET id_boe = ?,
                    id_bloque = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE articulo_fuente_id = ?
                """,
                (articulo.id_boe, articulo.id_bloque, articulo_fuente_id),
            )
            conexion.commit()
        except Exception:
            conexion.rollback()
            raise

        print(
            f"NUEVO: {articulo.id_boe} | bloque {articulo.id_bloque} "
            f"| caracteres {len(articulo.texto)}"
        )
        print(f"Hash anterior: {hash_anterior}")
        print(f"Hash nuevo:    {hash_nuevo}")
        print("Resultado: REPARADO EN EL MISMO articulos_fuente.id")

def resolver(args: argparse.Namespace) -> None:
    ruta_db = Path(args.db).resolve()

    if not ruta_db.exists():
        raise FileNotFoundError(f"No existe la base de datos: {ruta_db}")

    copia = None
    if not args.sin_copia_seguridad:
        copia = crear_copia_seguridad(ruta_db)

    estadisticas = {
        "seleccionadas": 0,
        "completadas": 0,
        "pendientes": 0,
        "errores_consulta": 0,
        "articulos_nuevos": 0,
        "articulos_reutilizados": 0,
        "resoluciones_reutilizadas_bd": 0,
        "resoluciones_reutilizadas_lote": 0,
        "reparaciones_correctas": 0,
        "reparaciones_no_resueltas": 0,
        "mezclas_reparadas": 0,
        "mezclas_no_resueltas": 0,
    }

    cache_lote: dict[tuple[str, str], ResultadoResolucion] = {}

    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")

        crear_estructura(conexion)
        conexion.commit()

        referencias = cargar_referencias(
            conexion=conexion,
            referencia_id=args.referencia_id,
            temario_id=args.temario_id,
            limite=args.limite,
            reintentar_pendientes=args.reintentar_pendientes,
            reparar_textos_incompletos=args.reparar_textos_incompletos,
            reparar_mezclas_versiones=args.reparar_mezclas_versiones,
        )
        if args.solo_pdf_local:
            total_antes_filtro = len(referencias)
            referencias = [
                ref for ref in referencias
                if tiene_pdf_local(ref.nombre_norma_csv)
            ]
            print(
                "Fallback PDF local: "
                f"{len(referencias)} de {total_antes_filtro} referencias pendientes "
                "tienen un PDF local inequívoco."
            )

        estadisticas["seleccionadas"] = len(referencias)

        if not referencias:
            print("No hay referencias que cumplan los criterios.")
            if copia is not None:
                print(f"Copia de seguridad: {copia}")
            return

        total = len(referencias)

        for posicion, referencia in enumerate(referencias, start=1):
            print(
                f"[{posicion}/{total}] Referencia {referencia.id}: "
                f"{referencia.nombre_norma_csv} | "
                f"art. {referencia.articulo_solicitado}"
            )

            resolucion_bd = (
                None
                if (args.reparar_textos_incompletos or args.reparar_mezclas_versiones)
                else buscar_resolucion_bd(conexion, referencia)
            )

            if resolucion_bd is not None:
                articulo_id = int(resolucion_bd["articulo_fuente_id"])
                marcar_completada(conexion, referencia.id, articulo_id)
                estadisticas["completadas"] += 1
                estadisticas["resoluciones_reutilizadas_bd"] += 1
                print(
                    f"  COMPLETADO SIN BOE: {resolucion_bd['id_boe']} "
                    f"| bloque {resolucion_bd['id_bloque']} "
                    "| resolución reutilizada desde BD."
                )
                conexion.commit()
                continue

            clave = clave_cache(referencia)
            resultado = cache_lote.get(clave)

            if resultado is None:
                resultado = resolver_una(referencia)
                cache_lote[clave] = resultado
            else:
                estadisticas["resoluciones_reutilizadas_lote"] += 1
                print("  Resolución reutilizada dentro del lote.")

            if resultado.articulo is not None:
                articulo_id, creado = upsert_articulo_fuente(
                    conexion,
                    resultado.articulo,
                )
                guardar_resolucion_bd(
                    conexion,
                    referencia,
                    resultado.articulo,
                    articulo_id,
                )
                marcar_completada(
                    conexion,
                    referencia.id,
                    articulo_id,
                )
                estadisticas["completadas"] += 1
                if args.reparar_textos_incompletos:
                    estadisticas["reparaciones_correctas"] += 1
                if args.reparar_mezclas_versiones:
                    estadisticas["mezclas_reparadas"] += 1

                if creado:
                    estadisticas["articulos_nuevos"] += 1
                    print(
                        f"  COMPLETADO: {resultado.articulo.id_boe} "
                        f"| bloque {resultado.articulo.id_bloque} "
                        "| artículo nuevo."
                    )
                else:
                    estadisticas["articulos_reutilizados"] += 1
                    print(
                        f"  COMPLETADO: {resultado.articulo.id_boe} "
                        f"| bloque {resultado.articulo.id_bloque} "
                        "| artículo reutilizado."
                    )

            else:
                if args.reparar_textos_incompletos or args.reparar_mezclas_versiones:
                    # Reparación conservadora: si no conseguimos una fuente
                    # mejor, NO se borra ni degrada la vinculación existente.
                    if args.reparar_textos_incompletos:
                        estadisticas["reparaciones_no_resueltas"] += 1
                    if args.reparar_mezclas_versiones:
                        estadisticas["mezclas_no_resueltas"] += 1
                    print(
                        "  NO REPARADO: se conserva el artículo existente. "
                        f"{resultado.mensaje or 'Error sin detalle.'}"
                    )
                else:
                    marcar_error(
                        conexion,
                        referencia.id,
                        resultado.estado,
                        resultado.mensaje or "Error sin detalle.",
                    )

                    if resultado.estado == ESTADO_PENDIENTE:
                        estadisticas["pendientes"] += 1
                    else:
                        estadisticas["errores_consulta"] += 1

                    print(
                        f"  {resultado.estado}: "
                        f"{resultado.mensaje or 'Error sin detalle.'}"
                    )

            # Se confirma cada referencia para conservar el progreso si el
            # proceso se interrumpe durante un lote largo.
            conexion.commit()

    print()
    print("RESOLUCIÓN BOE TERMINADA")
    print(f"Base de datos: {ruta_db}")

    if copia is not None:
        print(f"Copia de seguridad: {copia}")

    print(f"Referencias seleccionadas: {estadisticas['seleccionadas']}")
    print(f"Completadas: {estadisticas['completadas']}")
    print(f"Pendientes: {estadisticas['pendientes']}")
    print(f"Errores de consulta: {estadisticas['errores_consulta']}")
    print(f"Artículos nuevos: {estadisticas['articulos_nuevos']}")
    print(
        "Artículos reutilizados tras consultar BOE: "
        f"{estadisticas['articulos_reutilizados']}"
    )
    print(
        "Resoluciones reutilizadas desde BD (sin BOE): "
        f"{estadisticas['resoluciones_reutilizadas_bd']}"
    )
    print(
        "Resoluciones reutilizadas dentro del lote: "
        f"{estadisticas['resoluciones_reutilizadas_lote']}"
    )
    if args.reparar_textos_incompletos:
        print(
            "Textos incompletos reparados: "
            f"{estadisticas['reparaciones_correctas']}"
        )
        print(
            "Textos incompletos no resueltos (conservados): "
            f"{estadisticas['reparaciones_no_resueltas']}"
        )
    if args.reparar_mezclas_versiones:
        print(
            "Posibles mezclas de versiones reparadas: "
            f"{estadisticas['mezclas_reparadas']}"
        )
        print(
            "Posibles mezclas no resueltas (conservadas): "
            f"{estadisticas['mezclas_no_resueltas']}"
        )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resuelve referencias jurídicas del temario mediante la API "
            "oficial del BOE y guarda los artículos sin duplicarlos."
        )
    )

    parser.add_argument(
        "--db",
        default=str(DB_POR_DEFECTO),
        help=f"Base SQLite. Por defecto: {DB_POR_DEFECTO}",
    )

    parser.add_argument(
        "--limite",
        type=int,
        help="Número máximo de referencias que se procesarán.",
    )

    parser.add_argument(
        "--referencia-id",
        type=int,
        help="Procesa exclusivamente una referencia concreta.",
    )
    parser.add_argument(
        "--temario-id",
        type=int,
        help="Limita el lote exclusivamente a las referencias de un temario.",
    )

    parser.add_argument(
        "--articulo-fuente-id",
        type=int,
        help=(
            "Repara exclusivamente un articulos_fuente.id concreto. "
            "No usa el detector de mezclas de versiones y exige actualización "
            "segura sobre el mismo registro."
        ),
    )

    parser.add_argument(
        "--reintentar-pendientes",
        action="store_true",
        help=(
            "Incluye también referencias en estado PENDIENTE. "
            "Por defecto solo procesa SIN_RESOLVER y "
            "ERROR_CONSULTA_BOE."
        ),
    )

    parser.add_argument(
        "--solo-pdf-local",
        action="store_true",
        help=(
            "Procesa únicamente referencias para las que existe un PDF local "
            "inequívoco en fuentes_normativas/. No consulta proveedores remotos."
        ),
    )

    parser.add_argument(
        "--reparar-textos-incompletos",
        action="store_true",
        help=(
            "Reprocesa únicamente referencias COMPLETADO cuyo artículo "
            "fuente sea manifiestamente incompleto (vacío o solo encabezado). "
            "No reutiliza la resolución guardada y conserva el dato anterior "
            "si la reparación no obtiene un texto mejor."
        ),
    )

    parser.add_argument(
        "--reparar-mezclas-versiones",
        action="store_true",
        help=(
            "MODO DESHABILITADO: dependía de un detector que ya no está disponible. "
            "Use --articulo-fuente-id para reparaciones controladas."
        ),
    )

    parser.add_argument(
        "--limpiar-huerfanos-incompletos",
        action="store_true",
        help=(
            "Elimina únicamente articulos_fuente no referenciados ni por "
            "temario_referencias ni por resoluciones_boe y cuyo texto sea "
            "manifiestamente incompleto. Crea copia previa salvo --sin-copia-seguridad."
        ),
    )

    parser.add_argument(
        "--sin-copia-seguridad",
        action="store_true",
        help="No crea una copia previa de la base de datos.",
    )

    return parser



def validar_argumentos(args: argparse.Namespace) -> None:
    if args.limite is not None and args.limite <= 0:
        raise ValueError("--limite debe ser mayor que cero.")

    if args.referencia_id is not None and args.referencia_id <= 0:
        raise ValueError("--referencia-id debe ser mayor que cero.")
    if args.temario_id is not None and args.temario_id <= 0:
        raise ValueError("--temario-id debe ser mayor que cero.")
    if args.referencia_id is not None and args.temario_id is not None:
        raise ValueError("--referencia-id y --temario-id no pueden combinarse.")

    if args.articulo_fuente_id is not None and args.articulo_fuente_id <= 0:
        raise ValueError("--articulo-fuente-id debe ser mayor que cero.")

    if args.reparar_mezclas_versiones:
        raise ValueError(
            "--reparar-mezclas-versiones está deshabilitado porque dependía "
            "de un detector cuya implementación ya no está disponible. "
            "Use --articulo-fuente-id para una reparación controlada."
        )

    if args.articulo_fuente_id is not None:
        incompatibles = []
        if args.limite is not None:
            incompatibles.append("--limite")
        if args.referencia_id is not None:
            incompatibles.append("--referencia-id")
        if args.temario_id is not None:
            incompatibles.append("--temario-id")
        if args.reintentar_pendientes:
            incompatibles.append("--reintentar-pendientes")
        if args.reparar_textos_incompletos:
            incompatibles.append("--reparar-textos-incompletos")
        if args.limpiar_huerfanos_incompletos:
            incompatibles.append("--limpiar-huerfanos-incompletos")
        if incompatibles:
            raise ValueError(
                "--articulo-fuente-id no puede combinarse con: "
                + ", ".join(incompatibles)
            )


def main() -> None:
    parser = construir_parser()
    args = parser.parse_args()

    try:
        validar_argumentos(args)
        if args.articulo_fuente_id is not None:
            reparar_articulo_fuente_por_id(
                Path(args.db).resolve(),
                args.articulo_fuente_id,
                sin_copia_seguridad=args.sin_copia_seguridad,
            )
        elif args.limpiar_huerfanos_incompletos:
            limpiar_articulos_huerfanos_incompletos(
                Path(args.db).resolve(),
                sin_copia_seguridad=args.sin_copia_seguridad,
            )
        else:
            resolver(args)
    except KeyboardInterrupt:
        print("\nProceso interrumpido por el usuario.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if getattr(args, "mostrar_traceback", False):
            traceback.print_exc()
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()