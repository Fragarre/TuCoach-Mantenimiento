"""
OpoCoach - Orquestador de construcción del corpus jurídico de una convocatoria.

Este script NO modifica los scripts existentes.

Entrada:
    - una convocatoria identificada por --convocatoria-id o --codigo;
    - el temario ya importado en la base de datos.

Proceso:
    1. valida la convocatoria y su único temario;
    2. obtiene exclusivamente las referencias jurídicas de ese temario;
    3. crea una única copia de seguridad;
    4. ejecuta resolver_referencias_boe.py referencia por referencia;
    5. verifica el resultado únicamente para la convocatoria seleccionada;
    6. genera informes CSV, TXT y JSON.

No lee ni modifica el CSV del temario.
No procesa preguntas.
No construye contenidos no jurídicos.

Ejemplos:

    python scripts/construir_corpus_convocatoria.py --convocatoria-id 2

    python scripts/construir_corpus_convocatoria.py --codigo C1-02

    python scripts/construir_corpus_convocatoria.py --codigo C1-02 --solo-validar

    python scripts/construir_corpus_convocatoria.py \
        --convocatoria-id 2 \
        --reintentar-pendientes

Puede indicarse otra base:

    python scripts/construir_corpus_convocatoria.py \
        --db db/oposiciones.sqlite3 \
        --convocatoria-id 2
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


RAIZ = Path(__file__).resolve().parent.parent
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
RESOLVEDOR_PREDETERMINADO = (
    RAIZ / "scripts" / "resolver_referencias_boe.py"
)

ESTADO_SIN_RESOLVER = "SIN_RESOLVER"
ESTADO_COMPLETADO = "COMPLETADO"
ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_ERROR_CONSULTA = "ERROR_CONSULTA_BOE"


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Construye el corpus jurídico de una convocatoria a partir "
            "del temario ya importado en la base de datos."
        )
    )
    parser.add_argument(
        "--db",
        default=str(DB_PREDETERMINADA),
        help=f"Base SQLite. Por defecto: {DB_PREDETERMINADA}",
    )

    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "--convocatoria-id",
        type=int,
        help="Identificador numérico de la convocatoria.",
    )
    grupo.add_argument(
        "--codigo",
        help="Código exacto de la convocatoria.",
    )

    parser.add_argument(
        "--resolvedor",
        default=str(RESOLVEDOR_PREDETERMINADO),
        help=(
            "Ruta de resolver_referencias_boe.py. "
            f"Por defecto: {RESOLVEDOR_PREDETERMINADO}"
        ),
    )
    parser.add_argument(
        "--solo-validar",
        action="store_true",
        help=(
            "No consulta el BOE ni modifica la base; solo valida y genera "
            "el informe del estado actual."
        ),
    )
    parser.add_argument(
        "--reintentar-pendientes",
        action="store_true",
        help=(
            "Además de SIN_RESOLVER y ERROR_CONSULTA_BOE, vuelve a intentar "
            "las referencias en estado PENDIENTE."
        ),
    )
    parser.add_argument(
        "--detener-en-error",
        action="store_true",
        help=(
            "Detiene el orquestador si una ejecución individual del "
            "resolvedor termina con error. Por defecto registra el error "
            "y continúa con las demás referencias."
        ),
    )
    return parser


def validar_argumentos(args: argparse.Namespace) -> None:
    if args.convocatoria_id is not None and args.convocatoria_id <= 0:
        raise ValueError("--convocatoria-id debe ser mayor que cero.")

    if args.codigo is not None and not args.codigo.strip():
        raise ValueError("--codigo no puede estar vacío.")


def existe_tabla(conexion: sqlite3.Connection, tabla: str) -> bool:
    return (
        conexion.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (tabla,),
        ).fetchone()
        is not None
    )


def columnas_tabla(
    conexion: sqlite3.Connection,
    tabla: str,
) -> set[str]:
    if not existe_tabla(conexion, tabla):
        return set()

    return {
        str(fila["name"])
        for fila in conexion.execute(f"PRAGMA table_info({tabla})")
    }


def validar_estructura(conexion: sqlite3.Connection) -> None:
    requeridas = {
        "convocatorias": {"id", "codigo"},
        "temarios": {"id", "convocatoria_id", "nombre"},
        "temario_temas": {
            "id",
            "temario_id",
            "parte",
            "numero_tema",
            "titulo",
        },
        "temario_referencias": {
            "id",
            "tema_id",
            "nombre_norma_csv",
            "nombre_norma_normalizada",
            "articulo_solicitado",
            "estado",
        },
    }

    errores: list[str] = []

    for tabla, columnas_requeridas in requeridas.items():
        columnas = columnas_tabla(conexion, tabla)
        if not columnas:
            errores.append(f"No existe la tabla {tabla}.")
            continue

        faltantes = sorted(columnas_requeridas - columnas)
        if faltantes:
            errores.append(
                f"En {tabla} faltan columnas: {', '.join(faltantes)}."
            )

    if errores:
        raise RuntimeError(" ".join(errores))


def buscar_convocatoria(
    conexion: sqlite3.Connection,
    convocatoria_id: int | None,
    codigo: str | None,
) -> sqlite3.Row:
    if convocatoria_id is not None:
        fila = conexion.execute(
            """
            SELECT *
            FROM convocatorias
            WHERE id = ?
            """,
            (convocatoria_id,),
        ).fetchone()
    else:
        fila = conexion.execute(
            """
            SELECT *
            FROM convocatorias
            WHERE codigo = ?
            """,
            (codigo.strip(),),
        ).fetchone()

    if fila is None:
        identificador = (
            f"id={convocatoria_id}"
            if convocatoria_id is not None
            else f"codigo={codigo!r}"
        )
        raise RuntimeError(
            f"No existe una convocatoria con {identificador}."
        )

    return fila


def buscar_temario(
    conexion: sqlite3.Connection,
    convocatoria_id: int,
) -> sqlite3.Row:
    filas = conexion.execute(
        """
        SELECT id, convocatoria_id, nombre
        FROM temarios
        WHERE convocatoria_id = ?
        ORDER BY id
        """,
        (convocatoria_id,),
    ).fetchall()

    if len(filas) != 1:
        raise RuntimeError(
            "La convocatoria debe tener exactamente un temario. "
            f"Encontrados: {len(filas)}."
        )

    return filas[0]


def cargar_referencias(
    conexion: sqlite3.Connection,
    temario_id: int,
) -> list[dict[str, Any]]:
    filas = conexion.execute(
        """
        SELECT
            tr.id AS referencia_id,
            tr.tema_id,
            tt.parte,
            tt.numero_tema,
            tt.titulo AS titulo_tema,
            tr.nombre_norma_csv,
            tr.nombre_norma_normalizada,
            tr.articulo_solicitado,
            tr.estado,
            tr.mensaje_error,
            tr.articulo_fuente_id
        FROM temario_referencias AS tr
        JOIN temario_temas AS tt
          ON tt.id = tr.tema_id
        WHERE tt.temario_id = ?
        ORDER BY
            tt.parte,
            tt.numero_tema,
            tr.nombre_norma_normalizada,
            tr.articulo_solicitado,
            tr.id
        """,
        (temario_id,),
    ).fetchall()

    return [dict(fila) for fila in filas]


def crear_copia_seguridad(ruta_db: Path, marca: str) -> Path:
    destino = ruta_db.with_name(
        f"{ruta_db.stem}_antes_corpus_{marca}{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, destino)
    return destino


def escribir_csv(
    ruta: Path,
    filas: Iterable[dict[str, Any]],
) -> None:
    datos = list(filas)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    if not datos:
        ruta.write_text("", encoding="utf-8-sig")
        return

    columnas: list[str] = []
    vistas: set[str] = set()

    for fila in datos:
        for columna in fila:
            if columna not in vistas:
                vistas.add(columna)
                columnas.append(columna)

    with ruta.open("w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.DictWriter(
            archivo,
            fieldnames=columnas,
            extrasaction="ignore",
        )
        escritor.writeheader()
        escritor.writerows(datos)


def estados_a_procesar(
    reintentar_pendientes: bool,
) -> set[str]:
    estados = {
        ESTADO_SIN_RESOLVER,
        ESTADO_ERROR_CONSULTA,
    }
    if reintentar_pendientes:
        estados.add(ESTADO_PENDIENTE)
    return estados


def ejecutar_resolvedor(
    python: str,
    resolvedor: Path,
    db: Path,
    referencia_id: int,
) -> subprocess.CompletedProcess[str]:
    comando = [
        python,
        str(resolvedor),
        "--db",
        str(db),
        "--referencia-id",
        str(referencia_id),
        "--sin-copia-seguridad",
    ]

    return subprocess.run(
        comando,
        cwd=RAIZ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def auditar_convocatoria(
    conexion: sqlite3.Connection,
    temario_id: int,
) -> dict[str, Any]:
    referencias = cargar_referencias(conexion, temario_id)

    por_estado: dict[str, int] = {}
    for fila in referencias:
        estado = str(fila.get("estado") or "<NULL>")
        por_estado[estado] = por_estado.get(estado, 0) + 1

    columnas_ref = columnas_tabla(conexion, "temario_referencias")
    tiene_articulo_fuente_id = "articulo_fuente_id" in columnas_ref

    completadas_sin_articulo: list[dict[str, Any]] = []
    pendientes_con_articulo: list[dict[str, Any]] = []
    enlaces_rotos: list[dict[str, Any]] = []
    articulos_vacios: list[dict[str, Any]] = []

    if tiene_articulo_fuente_id:
        completadas_sin_articulo = [
            fila
            for fila in referencias
            if fila["estado"] == ESTADO_COMPLETADO
            and fila["articulo_fuente_id"] is None
        ]
        pendientes_con_articulo = [
            fila
            for fila in referencias
            if fila["estado"] != ESTADO_COMPLETADO
            and fila["articulo_fuente_id"] is not None
        ]

    if (
        tiene_articulo_fuente_id
        and existe_tabla(conexion, "articulos_fuente")
    ):
        enlaces_rotos = [
            dict(fila)
            for fila in conexion.execute(
                """
                SELECT
                    tr.id AS referencia_id,
                    tr.tema_id,
                    tr.estado,
                    tr.articulo_fuente_id
                FROM temario_referencias AS tr
                JOIN temario_temas AS tt
                  ON tt.id = tr.tema_id
                LEFT JOIN articulos_fuente AS af
                  ON af.id = tr.articulo_fuente_id
                WHERE tt.temario_id = ?
                  AND tr.articulo_fuente_id IS NOT NULL
                  AND af.id IS NULL
                ORDER BY tr.id
                """,
                (temario_id,),
            )
        ]

        articulos_vacios = [
            dict(fila)
            for fila in conexion.execute(
                """
                SELECT DISTINCT
                    af.id,
                    af.id_boe,
                    af.id_bloque,
                    af.articulo_boe,
                    af.titulo_bloque,
                    af.texto
                FROM temario_referencias AS tr
                JOIN temario_temas AS tt
                  ON tt.id = tr.tema_id
                JOIN articulos_fuente AS af
                  ON af.id = tr.articulo_fuente_id
                WHERE tt.temario_id = ?
                  AND (
                      af.texto IS NULL
                      OR TRIM(af.texto) = ''
                  )
                ORDER BY af.id
                """,
                (temario_id,),
            )
        ]

    referencias_pendientes = [
        fila
        for fila in referencias
        if fila["estado"] != ESTADO_COMPLETADO
    ]

    incidencias_criticas = (
        len(referencias_pendientes)
        + len(completadas_sin_articulo)
        + len(pendientes_con_articulo)
        + len(enlaces_rotos)
        + len(articulos_vacios)
    )

    articulos_distintos = 0
    normas_distintas = len(
        {
            str(fila["nombre_norma_normalizada"])
            for fila in referencias
            if fila["nombre_norma_normalizada"]
        }
    )

    if (
        tiene_articulo_fuente_id
        and existe_tabla(conexion, "articulos_fuente")
    ):
        articulos_distintos = int(
            conexion.execute(
                """
                SELECT COUNT(DISTINCT af.id)
                FROM temario_referencias AS tr
                JOIN temario_temas AS tt
                  ON tt.id = tr.tema_id
                JOIN articulos_fuente AS af
                  ON af.id = tr.articulo_fuente_id
                WHERE tt.temario_id = ?
                """,
                (temario_id,),
            ).fetchone()[0]
        )

    return {
        "referencias": referencias,
        "referencias_total": len(referencias),
        "referencias_por_estado": por_estado,
        "referencias_pendientes": referencias_pendientes,
        "completadas_sin_articulo": completadas_sin_articulo,
        "pendientes_con_articulo": pendientes_con_articulo,
        "enlaces_rotos": enlaces_rotos,
        "articulos_vacios": articulos_vacios,
        "normas_distintas": normas_distintas,
        "articulos_distintos": articulos_distintos,
        "incidencias_criticas": incidencias_criticas,
        "estado": (
            "JURIDICO_COMPLETO"
            if incidencias_criticas == 0
            else "REVISAR"
        ),
    }


def contar_temas(
    conexion: sqlite3.Connection,
    temario_id: int,
) -> dict[str, Any]:
    total = int(
        conexion.execute(
            """
            SELECT COUNT(*)
            FROM temario_temas
            WHERE temario_id = ?
            """,
            (temario_id,),
        ).fetchone()[0]
    )

    por_parte = [
        dict(fila)
        for fila in conexion.execute(
            """
            SELECT
                COALESCE(parte, '<NULL>') AS parte,
                COUNT(*) AS total
            FROM temario_temas
            WHERE temario_id = ?
            GROUP BY COALESCE(parte, '<NULL>')
            ORDER BY parte
            """,
            (temario_id,),
        )
    ]

    temas_con_referencias = int(
        conexion.execute(
            """
            SELECT COUNT(DISTINCT tt.id)
            FROM temario_temas AS tt
            JOIN temario_referencias AS tr
              ON tr.tema_id = tt.id
            WHERE tt.temario_id = ?
            """,
            (temario_id,),
        ).fetchone()[0]
    )

    return {
        "total": total,
        "por_parte": por_parte,
        "con_referencias_juridicas": temas_con_referencias,
        "sin_referencias_juridicas": total - temas_con_referencias,
    }


def guardar_informes(
    carpeta: Path,
    resumen: dict[str, Any],
    auditoria: dict[str, Any],
    ejecuciones: list[dict[str, Any]],
) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)

    escribir_csv(
        carpeta / "referencias.csv",
        auditoria["referencias"],
    )
    escribir_csv(
        carpeta / "referencias_pendientes.csv",
        auditoria["referencias_pendientes"],
    )
    escribir_csv(
        carpeta / "completadas_sin_articulo.csv",
        auditoria["completadas_sin_articulo"],
    )
    escribir_csv(
        carpeta / "pendientes_con_articulo.csv",
        auditoria["pendientes_con_articulo"],
    )
    escribir_csv(
        carpeta / "enlaces_rotos.csv",
        auditoria["enlaces_rotos"],
    )
    escribir_csv(
        carpeta / "articulos_vacios.csv",
        auditoria["articulos_vacios"],
    )
    escribir_csv(
        carpeta / "ejecuciones_resolvedor.csv",
        ejecuciones,
    )

    resumen_json = dict(resumen)
    resumen_json["auditoria"] = {
        clave: valor
        for clave, valor in auditoria.items()
        if clave not in {
            "referencias",
            "referencias_pendientes",
            "completadas_sin_articulo",
            "pendientes_con_articulo",
            "enlaces_rotos",
            "articulos_vacios",
        }
    }

    (carpeta / "resumen.json").write_text(
        json.dumps(
            resumen_json,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temas = resumen["temas"]
    lineas = [
        "CONSTRUCCIÓN DEL CORPUS JURÍDICO DE LA CONVOCATORIA",
        "=" * 68,
        f"Base de datos: {resumen['base_datos']}",
        f"Fecha: {resumen['fecha']}",
        f"Convocatoria ID: {resumen['convocatoria_id']}",
        f"Código: {resumen['convocatoria_codigo']}",
        f"Temario ID: {resumen['temario_id']}",
        f"Temario: {resumen['temario_nombre']}",
        f"Modo: {resumen['modo']}",
        "",
        f"Temas totales: {temas['total']}",
        (
            "Temas con referencias jurídicas: "
            f"{temas['con_referencias_juridicas']}"
        ),
        (
            "Temas sin referencias jurídicas: "
            f"{temas['sin_referencias_juridicas']}"
        ),
        "",
        f"Referencias jurídicas: {auditoria['referencias_total']}",
        f"Normas distintas: {auditoria['normas_distintas']}",
        f"Artículos distintos: {auditoria['articulos_distintos']}",
    ]

    for estado, cantidad in sorted(
        auditoria["referencias_por_estado"].items()
    ):
        lineas.append(f"Referencias {estado}: {cantidad}")

    lineas.extend(
        [
            "",
            (
                "COMPLETADO sin artículo vinculado: "
                f"{len(auditoria['completadas_sin_articulo'])}"
            ),
            (
                "No completadas con artículo vinculado: "
                f"{len(auditoria['pendientes_con_articulo'])}"
            ),
            f"Enlaces rotos: {len(auditoria['enlaces_rotos'])}",
            f"Artículos vacíos: {len(auditoria['articulos_vacios'])}",
            (
                "Errores de ejecución del resolvedor: "
                f"{resumen['errores_ejecucion_resolvedor']}"
            ),
            "",
            f"Estado jurídico: {auditoria['estado']}",
            "",
            (
                "Los temas sin referencias jurídicas no se construyen "
                "con este mantenimiento."
            ),
            f"Carpeta de informes: {carpeta}",
        ]
    )

    (carpeta / "informe.txt").write_text(
        "\n".join(lineas) + "\n",
        encoding="utf-8-sig",
    )


def main() -> None:
    args = construir_parser().parse_args()
    validar_argumentos(args)

    ruta_db = Path(args.db).resolve()
    ruta_resolvedor = Path(args.resolvedor).resolve()

    if not ruta_db.exists():
        raise FileNotFoundError(
            f"No existe la base de datos: {ruta_db}"
        )

    if not args.solo_validar and not ruta_resolvedor.exists():
        raise FileNotFoundError(
            f"No existe el resolvedor: {ruta_resolvedor}"
        )

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = RAIZ / "auditorias" / f"corpus_{marca}"

    copia: Path | None = None
    ejecuciones: list[dict[str, Any]] = []
    errores_ejecucion = 0

    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")

        validar_estructura(conexion)
        convocatoria = buscar_convocatoria(
            conexion,
            args.convocatoria_id,
            args.codigo,
        )
        convocatoria_id = int(convocatoria["id"])
        temario = buscar_temario(conexion, convocatoria_id)
        temario_id = int(temario["id"])
        temas = contar_temas(conexion, temario_id)
        referencias_iniciales = cargar_referencias(
            conexion,
            temario_id,
        )

    seleccionables = estados_a_procesar(
        args.reintentar_pendientes
    )
    referencias_a_procesar = [
        fila
        for fila in referencias_iniciales
        if fila["estado"] in seleccionables
    ]

    print("CONSTRUCCIÓN DEL CORPUS JURÍDICO")
    print(f"Convocatoria: {convocatoria_id} | {convocatoria['codigo']}")
    print(f"Temario: {temario_id} | {temario['nombre']}")
    print(f"Referencias del temario: {len(referencias_iniciales)}")
    print(f"Referencias seleccionadas: {len(referencias_a_procesar)}")

    if args.solo_validar:
        print("Modo SOLO VALIDAR: no se modifica la base.")
    elif referencias_a_procesar:
        copia = crear_copia_seguridad(ruta_db, marca)
        print(f"Copia de seguridad: {copia}")

        total = len(referencias_a_procesar)

        for posicion, referencia in enumerate(
            referencias_a_procesar,
            start=1,
        ):
            referencia_id = int(referencia["referencia_id"])

            print(
                f"[{posicion}/{total}] Referencia {referencia_id}: "
                f"{referencia['nombre_norma_csv']} | "
                f"art. {referencia['articulo_solicitado']}"
            )

            resultado = ejecutar_resolvedor(
                python=sys.executable,
                resolvedor=ruta_resolvedor,
                db=ruta_db,
                referencia_id=referencia_id,
            )

            registro = {
                "referencia_id": referencia_id,
                "codigo_salida": resultado.returncode,
                "stdout": resultado.stdout.strip(),
                "stderr": resultado.stderr.strip(),
            }
            ejecuciones.append(registro)

            if resultado.returncode != 0:
                errores_ejecucion += 1
                print(
                    f"  ERROR DE EJECUCIÓN: código "
                    f"{resultado.returncode}"
                )
                if resultado.stderr.strip():
                    print(f"  {resultado.stderr.strip()}")

                if args.detener_en_error:
                    break
            else:
                print("  Ejecución terminada.")
    else:
        print("No hay referencias seleccionadas para procesar.")

    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")

        auditoria = auditar_convocatoria(
            conexion,
            temario_id,
        )

    resumen = {
        "base_datos": str(ruta_db),
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "convocatoria_id": convocatoria_id,
        "convocatoria_codigo": convocatoria["codigo"],
        "temario_id": temario_id,
        "temario_nombre": temario["nombre"],
        "modo": "SOLO_VALIDAR" if args.solo_validar else "CONSTRUIR",
        "copia_seguridad": str(copia) if copia else None,
        "resolvedor": str(ruta_resolvedor),
        "referencias_seleccionadas": len(referencias_a_procesar),
        "ejecuciones_realizadas": len(ejecuciones),
        "errores_ejecucion_resolvedor": errores_ejecucion,
        "temas": temas,
    }

    guardar_informes(
        carpeta=carpeta,
        resumen=resumen,
        auditoria=auditoria,
        ejecuciones=ejecuciones,
    )

    print()
    print("RESULTADO")
    print(f"Referencias totales: {auditoria['referencias_total']}")
    print(
        f"Completadas: "
        f"{auditoria['referencias_por_estado'].get(ESTADO_COMPLETADO, 0)}"
    )
    print(
        f"Pendientes o con error: "
        f"{len(auditoria['referencias_pendientes'])}"
    )
    print(f"Normas distintas: {auditoria['normas_distintas']}")
    print(f"Artículos distintos: {auditoria['articulos_distintos']}")
    print(f"Estado jurídico: {auditoria['estado']}")
    print(f"Informes: {carpeta}")

    if auditoria["estado"] != "JURIDICO_COMPLETO":
        raise SystemExit(2)

    if errores_ejecucion:
        raise SystemExit(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperación cancelada.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(
            f"\nERROR: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
