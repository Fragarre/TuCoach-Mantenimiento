"""
OpoCoach — revisión independiente de importaciones problemáticas.

RESPONSABILIDAD
---------------
Analiza, en modo exclusivamente lectura, las filas de importaciones_ficheros
que requieren revisión y relaciona cada importación con las preguntas que aún
están vinculadas a ella en lote_preguntas.

No modifica la base de datos, no reimporta PDF y no llama a la IA.

IMPORTANTE
----------
La base actual no registra el resultado individual de cada página. Por ello,
una página sin pregunta vinculada no puede identificarse automáticamente como
error: también puede corresponder a una omisión deliberada o a una pregunta
duplicada. El informe usa siempre la denominación conservadora "SIN_VINCULO".

USO
---
    python scripts/revisar_importaciones.py

    python scripts/revisar_importaciones.py --db db/oposiciones.sqlite3

    python scripts/revisar_importaciones.py --id 42

    python scripts/revisar_importaciones.py --id 12 --id 42

    python scripts/revisar_importaciones.py --todas

La salida se crea, por defecto, en:
    auditorias/revision_importaciones_YYYYMMDD_HHMMSS/
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


def localizar_raiz() -> Path:
    script = Path(__file__).resolve()
    candidatos = [script.parent.parent, Path.cwd().resolve()]

    for candidato in candidatos:
        if (candidato / "db").is_dir():
            return candidato

    return Path.cwd().resolve()


RAIZ = localizar_raiz()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
CARPETA_AUDITORIAS = RAIZ / "auditorias"

ESTADOS_TERMINADOS_CORRECTOS = {"COMPLETADA", "COMPLETADO"}

COLUMNAS_IMPORTACIONES = {
    "id",
    "ruta_relativa",
    "nombre_fichero",
    "hash_sha256",
    "tipo_fuente",
    "estado",
    "paginas_totales",
    "paginas_insertadas",
    "paginas_omitidas",
    "paginas_error",
    "fecha_inicio",
    "fecha_fin",
    "reimportar",
    "ultimo_error",
}

COLUMNAS_LOTE = {
    "id",
    "enunciado",
    "respuesta_correcta",
    "tipo_clasificacion",
    "tipo_fuente",
    "importacion_fichero_id",
    "pagina_origen",
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def texto(valor: object) -> str:
    return "" if valor is None else str(valor).strip()


def entero(valor: object) -> int:
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def nombre_seguro(nombre: str) -> str:
    base = Path(nombre).stem
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return base or "importacion"


def columnas_tabla(con: sqlite3.Connection, tabla: str) -> set[str]:
    return {fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")}


def validar_esquema(con: sqlite3.Connection) -> None:
    tablas = {
        fila[0]
        for fila in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    necesarias = {"importaciones_ficheros", "lote_preguntas"}
    faltan_tablas = sorted(necesarias - tablas)
    if faltan_tablas:
        raise RuntimeError(
            "Faltan tablas obligatorias: " + ", ".join(faltan_tablas)
        )

    faltan_importaciones = sorted(
        COLUMNAS_IMPORTACIONES - columnas_tabla(con, "importaciones_ficheros")
    )
    faltan_lote = sorted(
        COLUMNAS_LOTE - columnas_tabla(con, "lote_preguntas")
    )

    if faltan_importaciones:
        raise RuntimeError(
            "Faltan columnas en importaciones_ficheros: "
            + ", ".join(faltan_importaciones)
        )
    if faltan_lote:
        raise RuntimeError(
            "Faltan columnas en lote_preguntas: " + ", ".join(faltan_lote)
        )


def requiere_revision(fila: sqlite3.Row) -> bool:
    estado = texto(fila["estado"]).upper()
    return (
        estado not in ESTADOS_TERMINADOS_CORRECTOS
        or entero(fila["paginas_error"]) > 0
        or bool(texto(fila["ultimo_error"]))
    )


def clasificar_recomendacion(fila: sqlite3.Row, vinculadas: int) -> str:
    estado = texto(fila["estado"]).upper()
    errores = entero(fila["paginas_error"])
    totales = entero(fila["paginas_totales"])

    if estado == "EN_PROCESO":
        if vinculadas == 0:
            return "EJECUCION_INTERRUMPIDA_SIN_PREGUNTAS_VINCULADAS"
        return "EJECUCION_INTERRUMPIDA_CON_DATOS_PARCIALES"

    if estado == "ERROR":
        return "ERROR_DE_FICHERO_REVISAR_ULTIMO_ERROR"

    if errores > 0 and totales > 0 and errores >= totales:
        return "FALLO_TOTAL_DE_PAGINAS"

    if errores > 0:
        return "ERRORES_PARCIALES_DE_PAGINA"

    return "REVISAR_ESTADO_O_DETALLE"


def escribir_csv(ruta: Path, cabeceras: list[str], filas: Iterable[Iterable]) -> None:
    with ruta.open("w", encoding="utf-8-sig", newline="") as fichero:
        escritor = csv.writer(fichero, delimiter=";")
        escritor.writerow(cabeceras)
        escritor.writerows(filas)


# ---------------------------------------------------------------------------
# Consulta y análisis
# ---------------------------------------------------------------------------


def obtener_importaciones(
    con: sqlite3.Connection,
    ids: list[int] | None,
    todas: bool,
) -> list[sqlite3.Row]:
    if ids:
        marcadores = ",".join("?" for _ in ids)
        filas = con.execute(
            f"""
            SELECT *
            FROM importaciones_ficheros
            WHERE id IN ({marcadores})
            ORDER BY id
            """,
            ids,
        ).fetchall()

        encontrados = {int(fila["id"]) for fila in filas}
        inexistentes = sorted(set(ids) - encontrados)
        if inexistentes:
            raise RuntimeError(
                "No existen las importaciones: "
                + ", ".join(str(valor) for valor in inexistentes)
            )
        return filas

    filas = con.execute("SELECT * FROM importaciones_ficheros ORDER BY id").fetchall()
    if todas:
        return filas
    return [fila for fila in filas if requiere_revision(fila)]


def obtener_preguntas_vinculadas(
    con: sqlite3.Connection,
    importacion_id: int,
) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            id,
            pagina_origen,
            tipo_clasificacion,
            tipo_fuente,
            respuesta_correcta,
            enunciado
        FROM lote_preguntas
        WHERE importacion_fichero_id = ?
        ORDER BY pagina_origen, id
        """,
        (importacion_id,),
    ).fetchall()


def analizar_importacion(
    fila: sqlite3.Row,
    preguntas: list[sqlite3.Row],
) -> dict:
    total = entero(fila["paginas_totales"])
    paginas_validas = sorted(
        {
            entero(pregunta["pagina_origen"])
            for pregunta in preguntas
            if entero(pregunta["pagina_origen"]) > 0
        }
    )

    fuera_rango = [pagina for pagina in paginas_validas if total and pagina > total]
    dentro_rango = [pagina for pagina in paginas_validas if not total or pagina <= total]

    if total > 0:
        paginas_sin_vinculo = sorted(set(range(1, total + 1)) - set(dentro_rango))
    else:
        paginas_sin_vinculo = []

    contadores = {
        "preguntas_vinculadas": len(preguntas),
        "paginas_con_vinculo": len(dentro_rango),
        "paginas_sin_vinculo": len(paginas_sin_vinculo),
        "paginas_fuera_rango": len(fuera_rango),
    }

    return {
        "fila": fila,
        "preguntas": preguntas,
        "paginas_con_vinculo": dentro_rango,
        "paginas_sin_vinculo": paginas_sin_vinculo,
        "paginas_fuera_rango": fuera_rango,
        "contadores": contadores,
        "recomendacion": clasificar_recomendacion(
            fila,
            contadores["preguntas_vinculadas"],
        ),
    }


# ---------------------------------------------------------------------------
# Informes
# ---------------------------------------------------------------------------


def escribir_detalle_importacion(carpeta: Path, datos: dict) -> None:
    fila = datos["fila"]
    importacion_id = int(fila["id"])
    prefijo = f"{importacion_id:04d}_{nombre_seguro(texto(fila['nombre_fichero']))}"

    escribir_csv(
        carpeta / f"{prefijo}_preguntas_vinculadas.csv",
        [
            "importacion_id",
            "nombre_fichero",
            "pregunta_id",
            "pagina_origen",
            "tipo_clasificacion",
            "tipo_fuente",
            "respuesta_correcta",
            "enunciado",
        ],
        (
            [
                importacion_id,
                texto(fila["nombre_fichero"]),
                pregunta["id"],
                pregunta["pagina_origen"],
                texto(pregunta["tipo_clasificacion"]),
                texto(pregunta["tipo_fuente"]),
                texto(pregunta["respuesta_correcta"]),
                texto(pregunta["enunciado"]),
            ]
            for pregunta in datos["preguntas"]
        ),
    )

    escribir_csv(
        carpeta / f"{prefijo}_paginas_sin_vinculo.csv",
        [
            "importacion_id",
            "nombre_fichero",
            "pagina",
            "situacion",
            "motivo_determinable",
        ],
        (
            [
                importacion_id,
                texto(fila["nombre_fichero"]),
                pagina,
                "SIN_VINCULO",
                "NO: puede ser error, omisión o duplicado",
            ]
            for pagina in datos["paginas_sin_vinculo"]
        ),
    )


def escribir_resumen_csv(carpeta: Path, analisis: list[dict]) -> None:
    cabeceras = [
        "id",
        "nombre_fichero",
        "ruta_relativa",
        "tipo_fuente",
        "estado",
        "paginas_totales",
        "registros_insertados_registrados",
        "registros_omitidos_registrados",
        "paginas_error_registradas",
        "preguntas_vinculadas_actuales",
        "paginas_con_vinculo_actuales",
        "paginas_sin_vinculo_actuales",
        "paginas_fuera_rango",
        "fecha_inicio",
        "fecha_fin",
        "reimportar",
        "ultimo_error",
        "diagnostico",
    ]

    filas = []
    for datos in analisis:
        fila = datos["fila"]
        conteos = datos["contadores"]
        filas.append(
            [
                fila["id"],
                texto(fila["nombre_fichero"]),
                texto(fila["ruta_relativa"]),
                texto(fila["tipo_fuente"]),
                texto(fila["estado"]),
                entero(fila["paginas_totales"]),
                entero(fila["paginas_insertadas"]),
                entero(fila["paginas_omitidas"]),
                entero(fila["paginas_error"]),
                conteos["preguntas_vinculadas"],
                conteos["paginas_con_vinculo"],
                conteos["paginas_sin_vinculo"],
                conteos["paginas_fuera_rango"],
                texto(fila["fecha_inicio"]),
                texto(fila["fecha_fin"]),
                entero(fila["reimportar"]),
                texto(fila["ultimo_error"]),
                datos["recomendacion"],
            ]
        )

    escribir_csv(carpeta / "resumen_importaciones.csv", cabeceras, filas)


def lista_compacta(valores: list[int], limite: int = 80) -> str:
    if not valores:
        return "(ninguna)"
    mostrados = valores[:limite]
    salida = ", ".join(str(valor) for valor in mostrados)
    if len(valores) > limite:
        salida += f", ... ({len(valores) - limite} más)"
    return salida


def escribir_resumen_txt(
    carpeta: Path,
    db: Path,
    analisis: list[dict],
) -> Path:
    ruta = carpeta / "resumen.txt"

    lineas: list[str] = []
    lineas.append("=" * 78)
    lineas.append("REVISIÓN DE IMPORTACIONES OPOCOACH")
    lineas.append("=" * 78)
    lineas.append(f"Base de datos: {db}")
    lineas.append(f"Generado: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    lineas.append(f"Importaciones revisadas: {len(analisis)}")
    lineas.append("")
    lineas.append("ADVERTENCIA")
    lineas.append("-----------")
    lineas.append(
        "La base no conserva el resultado individual de cada página. Una página "
        "sin pregunta vinculada puede ser un error, una omisión deliberada o una "
        "pregunta duplicada. Este informe no modifica la base."
    )

    for datos in analisis:
        fila = datos["fila"]
        conteos = datos["contadores"]
        total = entero(fila["paginas_totales"])
        errores = entero(fila["paginas_error"])
        porcentaje = (100.0 * conteos["paginas_con_vinculo"] / total) if total else 0.0

        lineas.append("")
        lineas.append("=" * 78)
        lineas.append(
            f"ID {fila['id']} | {texto(fila['nombre_fichero'])} | "
            f"{texto(fila['estado'])}"
        )
        lineas.append("=" * 78)
        lineas.append(f"Ruta relativa................ {texto(fila['ruta_relativa'])}")
        lineas.append(f"Tipo de fuente............... {texto(fila['tipo_fuente'])}")
        lineas.append(f"Inicio....................... {texto(fila['fecha_inicio']) or '(sin dato)'}")
        lineas.append(f"Fin.......................... {texto(fila['fecha_fin']) or '(sin dato)'}")
        lineas.append(f"Páginas totales registradas.. {total}")
        lineas.append(f"Registros insertados......... {entero(fila['paginas_insertadas'])}")
        lineas.append(f"Registros omitidos........... {entero(fila['paginas_omitidas'])}")
        lineas.append(f"Errores registrados.......... {errores}")
        lineas.append(f"Preguntas vinculadas actuales {conteos['preguntas_vinculadas']}")
        lineas.append(f"Páginas con vínculo actuales. {conteos['paginas_con_vinculo']}")
        lineas.append(f"Páginas sin vínculo actuales. {conteos['paginas_sin_vinculo']}")
        lineas.append(f"Cobertura vinculada.......... {porcentaje:.2f}%")
        lineas.append(f"Último error................. {texto(fila['ultimo_error']) or '(sin detalle)'}")
        lineas.append(f"Diagnóstico.................. {datos['recomendacion']}")
        lineas.append(
            "Páginas con vínculo.......... "
            + lista_compacta(datos["paginas_con_vinculo"])
        )
        lineas.append(
            "Páginas sin vínculo.......... "
            + lista_compacta(datos["paginas_sin_vinculo"])
        )
        if datos["paginas_fuera_rango"]:
            lineas.append(
                "Páginas fuera de rango....... "
                + lista_compacta(datos["paginas_fuera_rango"])
            )

    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def mostrar_resumen(analisis: list[dict], carpeta: Path) -> None:
    print("=" * 78)
    print("REVISIÓN DE IMPORTACIONES OPOCOACH")
    print("=" * 78)
    print(f"Importaciones revisadas: {len(analisis)}")
    print()

    for datos in analisis:
        fila = datos["fila"]
        conteos = datos["contadores"]
        print(
            f"ID {fila['id']:>3} | {texto(fila['nombre_fichero']):<24} | "
            f"estado={texto(fila['estado']):<23} | "
            f"errores={entero(fila['paginas_error']):>3} | "
            f"preguntas={conteos['preguntas_vinculadas']:>3} | "
            f"pag_con_vinculo={conteos['paginas_con_vinculo']:>3} | "
            f"pag_sin_vinculo={conteos['paginas_sin_vinculo']:>3}"
        )

    print()
    print("La base de datos no se ha modificado.")
    print(f"Informe: {carpeta}")


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------


def leer_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Revisa importaciones problemáticas sin modificar la base de datos."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PREDETERMINADA,
        help=f"Base SQLite. Predeterminada: {DB_PREDETERMINADA}",
    )
    parser.add_argument(
        "--salida",
        type=Path,
        help="Carpeta de salida. Si se omite, se crea bajo auditorias/.",
    )
    parser.add_argument(
        "--id",
        dest="ids",
        type=int,
        action="append",
        help="ID concreto de importación. Puede repetirse.",
    )
    parser.add_argument(
        "--todas",
        action="store_true",
        help="Incluye también las importaciones completadas correctamente.",
    )
    return parser.parse_args()


def main() -> int:
    args = leer_argumentos()
    db = args.db.expanduser().resolve()

    if not db.is_file():
        print(f"ERROR: no existe la base de datos: {db}", file=sys.stderr)
        return 2

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = (
        args.salida.expanduser().resolve()
        if args.salida
        else (CARPETA_AUDITORIAS / f"revision_importaciones_{marca}").resolve()
    )

    conexion = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conexion.row_factory = sqlite3.Row

    try:
        validar_esquema(conexion)
        importaciones = obtener_importaciones(conexion, args.ids, args.todas)

        if not importaciones:
            print("No hay importaciones que requieran revisión.")
            return 0

        carpeta.mkdir(parents=True, exist_ok=False)

        analisis: list[dict] = []
        for fila in importaciones:
            preguntas = obtener_preguntas_vinculadas(
                conexion,
                int(fila["id"]),
            )
            datos = analizar_importacion(fila, preguntas)
            analisis.append(datos)
            escribir_detalle_importacion(carpeta, datos)

        escribir_resumen_csv(carpeta, analisis)
        escribir_resumen_txt(carpeta, db, analisis)
        mostrar_resumen(analisis, carpeta)
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(main())
