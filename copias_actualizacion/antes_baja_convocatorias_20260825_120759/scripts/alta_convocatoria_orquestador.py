r"""
OpoCoach - Orquestador seguro para alta de convocatorias.

Flujo:
1. Valida la configuración, la base de datos y el CSV del temario.
2. Crea una única copia de seguridad de la base.
3. Da de alta la convocatoria y sus partes en una transacción.
4. Ejecuta importar_temario.py sin crear una segunda copia.
5. Si falla la importación del temario, restaura automáticamente la copia.

El CSV del temario se abre únicamente en modo lectura. No se modifica,
no se convierte y no se reescribe.

Uso:
    python scripts/alta_convocatoria_orquestador.py ^
        --config "data_convocatorias\NUEVA\convocatoria.json"

Validación sin modificar la base:
    python scripts/alta_convocatoria_orquestador.py ^
        --config "data_convocatorias\NUEVA\convocatoria.json" ^
        --solo-validar

Para actualizar deliberadamente una convocatoria ya existente:
    añadir --actualizar-existente
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
from typing import Any


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
DB_POR_DEFECTO = RAIZ_PROYECTO / "db" / "oposiciones.sqlite3"
IMPORTADOR_POR_DEFECTO = RAIZ_PROYECTO / "scripts" / "importar_temario.py"
CARPETA_COPIAS = RAIZ_PROYECTO / "db" / "copias_seguridad"

COLUMNAS_CSV_OBLIGATORIAS = {
    "parte",
    "tema",
    "titulo",
    "LEY",
    "articulo",
    "tipo",
}

COLUMNAS_CONVOCATORIA = {
    "puesto",
    "numero",
    "anio",
    "codigo",
    "numero_preguntas",
    "tiene_partes",
    "valoracion_test_acierto",
    "valoracion_test_fallo",
    "valoracion_test_no_contesta",
    "formula_nota",
    "factor_escala_nota",
    "temario_csv",
}

COLUMNAS_PARTE = {
    "nombre",
    "numero_preguntas",
    "orden",
}


def resolver_ruta(valor: str | Path, base: Path = RAIZ_PROYECTO) -> Path:
    ruta = Path(valor)
    if not ruta.is_absolute():
        ruta = base / ruta
    return ruta.resolve()


def cargar_configuracion(ruta: Path) -> dict[str, Any]:
    if not ruta.exists():
        raise FileNotFoundError(f"No existe la configuración: {ruta}")

    try:
        with ruta.open("r", encoding="utf-8-sig") as fichero:
            datos = json.load(fichero)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON inválido en {ruta}, línea {exc.lineno}: {exc.msg}"
        ) from exc

    if not isinstance(datos, dict):
        raise ValueError("La raíz de la configuración debe ser un objeto JSON.")

    return datos


def exigir_claves(
    datos: dict[str, Any],
    obligatorias: set[str],
    nombre: str,
) -> None:
    faltantes = sorted(obligatorias - set(datos))
    if faltantes:
        raise ValueError(
            f"Faltan campos obligatorios en {nombre}: "
            + ", ".join(faltantes)
        )


def validar_configuracion(
    configuracion: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    convocatoria = configuracion.get("convocatoria")
    partes = configuracion.get("partes")
    temario = configuracion.get("temario")

    if not isinstance(convocatoria, dict):
        raise ValueError("'convocatoria' debe ser un objeto JSON.")
    if not isinstance(partes, list):
        raise ValueError("'partes' debe ser una lista JSON.")
    if not isinstance(temario, dict):
        raise ValueError("'temario' debe ser un objeto JSON.")

    exigir_claves(
        convocatoria,
        COLUMNAS_CONVOCATORIA,
        "convocatoria",
    )

    if not isinstance(convocatoria["codigo"], str) or not convocatoria[
        "codigo"
    ].strip():
        raise ValueError("convocatoria.codigo no puede estar vacío.")

    if not isinstance(convocatoria["puesto"], str) or not convocatoria[
        "puesto"
    ].strip():
        raise ValueError("convocatoria.puesto no puede estar vacío.")

    try:
        anio = int(convocatoria["anio"])
        numero_preguntas = int(convocatoria["numero_preguntas"])
        tiene_partes = int(convocatoria["tiene_partes"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "anio, numero_preguntas y tiene_partes deben ser enteros."
        ) from exc

    if anio <= 0:
        raise ValueError("convocatoria.anio debe ser positivo.")
    if numero_preguntas <= 0:
        raise ValueError(
            "convocatoria.numero_preguntas debe ser mayor que cero."
        )
    if tiene_partes not in (0, 1):
        raise ValueError("convocatoria.tiene_partes debe ser 0 o 1.")

    nombres: list[str] = []
    ordenes: list[int] = []
    suma_partes = 0

    for indice, parte in enumerate(partes, start=1):
        if not isinstance(parte, dict):
            raise ValueError(f"La parte {indice} no es un objeto JSON.")

        exigir_claves(parte, COLUMNAS_PARTE, f"partes[{indice}]")

        nombre = str(parte["nombre"]).strip()
        if not nombre:
            raise ValueError(f"partes[{indice}].nombre no puede estar vacío.")

        try:
            preguntas_parte = int(parte["numero_preguntas"])
            orden = int(parte["orden"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Los números de la parte {indice} deben ser enteros."
            ) from exc

        if preguntas_parte < 0:
            raise ValueError(
                f"partes[{indice}].numero_preguntas no puede ser negativo."
            )
        if orden <= 0:
            raise ValueError(f"partes[{indice}].orden debe ser positivo.")

        nombres.append(nombre)
        ordenes.append(orden)
        suma_partes += preguntas_parte

    if tiene_partes == 1 and not partes:
        raise ValueError(
            "La convocatoria indica que tiene partes, pero no se definió ninguna."
        )

    if tiene_partes == 0 and partes:
        raise ValueError(
            "La convocatoria indica que no tiene partes, pero se definieron partes."
        )

    if len(nombres) != len(set(nombres)):
        raise ValueError("Hay nombres de parte duplicados.")

    if len(ordenes) != len(set(ordenes)):
        raise ValueError("Hay órdenes de parte duplicados.")

    if partes and suma_partes != numero_preguntas:
        raise ValueError(
            "La suma de preguntas de las partes "
            f"({suma_partes}) no coincide con numero_preguntas "
            f"({numero_preguntas})."
        )

    csv_config = temario.get("csv")
    if not isinstance(csv_config, str) or not csv_config.strip():
        raise ValueError("temario.csv es obligatorio.")

    nombre_temario = temario.get("nombre")
    if nombre_temario is not None and not isinstance(nombre_temario, str):
        raise ValueError("temario.nombre debe ser texto.")

    encoding = temario.get("encoding")
    if encoding is not None and not isinstance(encoding, str):
        raise ValueError("temario.encoding debe ser texto o null.")

    sincronizar = temario.get("sincronizar_eliminaciones", False)
    if not isinstance(sincronizar, bool):
        raise ValueError(
            "temario.sincronizar_eliminaciones debe ser true o false."
        )

    return convocatoria, partes, temario


def leer_y_validar_csv(
    ruta_csv: Path,
    encoding_indicado: str | None,
) -> tuple[str, int, int, int]:
    if not ruta_csv.exists():
        raise FileNotFoundError(f"No existe el CSV del temario: {ruta_csv}")
    if not ruta_csv.is_file():
        raise ValueError(f"La ruta del temario no es un fichero: {ruta_csv}")

    codificaciones = (
        [encoding_indicado]
        if encoding_indicado
        else ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    )

    ultimo_error: UnicodeDecodeError | None = None

    for encoding in codificaciones:
        try:
            with ruta_csv.open("r", encoding=encoding, newline="") as fichero:
                lector = csv.DictReader(fichero)

                if lector.fieldnames is None:
                    raise ValueError("El CSV no contiene cabecera.")

                columnas = set(lector.fieldnames)
                faltantes = sorted(COLUMNAS_CSV_OBLIGATORIAS - columnas)
                if faltantes:
                    raise ValueError(
                        "Faltan columnas obligatorias en el CSV: "
                        + ", ".join(faltantes)
                    )

                numero_filas = 0
                temas: set[tuple[str, int]] = set()
                titulos_por_tema: dict[tuple[str, int], str] = {}

                for numero_linea, registro in enumerate(lector, start=2):
                    numero_filas += 1

                    parte = str(registro.get("parte") or "").strip().upper()
                    tema_texto = str(registro.get("tema") or "").strip()
                    titulo = str(registro.get("titulo") or "").strip()
                    tipo = str(registro.get("tipo") or "").strip().upper()
                    ley = str(registro.get("LEY") or "").strip()
                    articulo = str(registro.get("articulo") or "").strip()

                    if not parte:
                        raise ValueError(
                            f"CSV línea {numero_linea}: parte vacía."
                        )
                    if not tema_texto:
                        raise ValueError(
                            f"CSV línea {numero_linea}: tema vacío."
                        )
                    try:
                        numero_tema = int(tema_texto)
                    except ValueError as exc:
                        raise ValueError(
                            f"CSV línea {numero_linea}: "
                            f"tema no entero: {tema_texto!r}."
                        ) from exc

                    if numero_tema <= 0:
                        raise ValueError(
                            f"CSV línea {numero_linea}: "
                            "el número de tema debe ser positivo."
                        )
                    if not titulo:
                        raise ValueError(
                            f"CSV línea {numero_linea}: título vacío."
                        )
                    if tipo not in {"JURIDICO", "INFORMATICA"}:
                        raise ValueError(
                            f"CSV línea {numero_linea}: "
                            f"tipo no admitido: {tipo!r}."
                        )
                    if articulo and not ley:
                        raise ValueError(
                            f"CSV línea {numero_linea}: "
                            "referencia con artículo pero sin LEY."
                        )

                    clave = (parte, numero_tema)
                    titulo_anterior = titulos_por_tema.get(clave)
                    if titulo_anterior is not None and titulo_anterior != titulo:
                        raise ValueError(
                            f"CSV línea {numero_linea}: el tema "
                            f"{parte} {numero_tema} tiene títulos diferentes."
                        )

                    titulos_por_tema[clave] = titulo
                    temas.add(clave)

                if numero_filas == 0:
                    raise ValueError("El CSV del temario no contiene filas.")

                return encoding, numero_filas, len(temas), len(columnas)

        except UnicodeDecodeError as exc:
            ultimo_error = exc

    if ultimo_error is not None:
        raise UnicodeError(
            "No se pudo leer el CSV con las codificaciones previstas."
        ) from ultimo_error

    raise RuntimeError("No se pudo validar el CSV.")


def comprobar_esquema(conexion: sqlite3.Connection) -> None:
    tablas_requeridas = {
        "convocatorias",
        "convocatoria_partes",
        "lote_preguntas",
    }

    tablas = {
        fila[0]
        for fila in conexion.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }

    faltantes = sorted(tablas_requeridas - tablas)
    if faltantes:
        raise RuntimeError(
            "Faltan tablas necesarias en la base: " + ", ".join(faltantes)
        )

    columnas_convocatorias = {
        fila[1]
        for fila in conexion.execute(
            "PRAGMA table_info(convocatorias)"
        ).fetchall()
    }

    faltan_columnas = sorted(
        COLUMNAS_CONVOCATORIA - columnas_convocatorias
    )
    if faltan_columnas:
        raise RuntimeError(
            "La tabla convocatorias no tiene las columnas esperadas: "
            + ", ".join(faltan_columnas)
        )


def crear_copia_seguridad(ruta_db: Path, codigo: str) -> Path:
    CARPETA_COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    codigo_seguro = "".join(
        caracter if caracter.isalnum() or caracter in "-_" else "_"
        for caracter in codigo
    )
    destino = CARPETA_COPIAS / (
        f"{ruta_db.stem}_antes_alta_{codigo_seguro}_{marca}{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, destino)
    return destino


def restaurar_copia(copia: Path, ruta_db: Path) -> None:
    shutil.copy2(copia, ruta_db)


def guardar_convocatoria(
    ruta_db: Path,
    convocatoria: dict[str, Any],
    partes: list[dict[str, Any]],
    actualizar_existente: bool,
) -> int:
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(ruta_db) as conexion:
        conexion.execute("PRAGMA foreign_keys = ON")
        comprobar_esquema(conexion)

        columnas_conv = {
            str(fila[1])
            for fila in conexion.execute("PRAGMA table_info(convocatorias)").fetchall()
        }
        if "activa" in columnas_conv:
            existente = conexion.execute(
                "SELECT id, activa FROM convocatorias WHERE codigo = ?",
                (convocatoria["codigo"],),
            ).fetchone()
        else:
            existente = conexion.execute(
                "SELECT id, 1 AS activa FROM convocatorias WHERE codigo = ?",
                (convocatoria["codigo"],),
            ).fetchone()

        if existente is not None and int(existente[1] or 0) != 1:
            raise RuntimeError(
                "Ya existe una convocatoria INACTIVA con el código "
                f"{convocatoria['codigo']}. Reactívela primero desde el menú; "
                "el alta no reactiva convocatorias de forma implícita."
            )

        if existente is not None and not actualizar_existente:
            raise RuntimeError(
                "Ya existe una convocatoria con el código "
                f"{convocatoria['codigo']}. Para actualizarla de forma "
                "deliberada debe usarse --actualizar-existente."
            )

        conexion.execute("BEGIN")

        if existente is None:
            cursor = conexion.execute(
                """
                INSERT INTO convocatorias (
                    puesto,
                    numero,
                    anio,
                    codigo,
                    numero_preguntas,
                    tiene_partes,
                    valoracion_test_acierto,
                    valoracion_test_fallo,
                    valoracion_test_no_contesta,
                    formula_nota,
                    factor_escala_nota,
                    temario_csv,
                    examen_modelo,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    convocatoria["puesto"],
                    convocatoria["numero"],
                    int(convocatoria["anio"]),
                    convocatoria["codigo"],
                    int(convocatoria["numero_preguntas"]),
                    int(convocatoria["tiene_partes"]),
                    float(convocatoria["valoracion_test_acierto"]),
                    float(convocatoria["valoracion_test_fallo"]),
                    float(convocatoria["valoracion_test_no_contesta"]),
                    convocatoria["formula_nota"],
                    float(convocatoria["factor_escala_nota"]),
                    convocatoria["temario_csv"],
                    ahora,
                    ahora,
                ),
            )
            convocatoria_id = int(cursor.lastrowid)
        else:
            convocatoria_id = int(existente[0])

            vinculaciones = conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas
                WHERE convocatoria_id = ?
                """,
                (convocatoria_id,),
            ).fetchone()[0]

            if vinculaciones:
                raise RuntimeError(
                    "La convocatoria existente ya tiene preguntas vinculadas. "
                    "El orquestador no modificará sus partes automáticamente."
                )

            conexion.execute(
                """
                UPDATE convocatorias
                SET puesto = ?,
                    numero = ?,
                    anio = ?,
                    numero_preguntas = ?,
                    tiene_partes = ?,
                    valoracion_test_acierto = ?,
                    valoracion_test_fallo = ?,
                    valoracion_test_no_contesta = ?,
                    formula_nota = ?,
                    factor_escala_nota = ?,
                    temario_csv = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    convocatoria["puesto"],
                    convocatoria["numero"],
                    int(convocatoria["anio"]),
                    int(convocatoria["numero_preguntas"]),
                    int(convocatoria["tiene_partes"]),
                    float(convocatoria["valoracion_test_acierto"]),
                    float(convocatoria["valoracion_test_fallo"]),
                    float(convocatoria["valoracion_test_no_contesta"]),
                    convocatoria["formula_nota"],
                    float(convocatoria["factor_escala_nota"]),
                    convocatoria["temario_csv"],
                    ahora,
                    convocatoria_id,
                ),
            )

            conexion.execute(
                "DELETE FROM convocatoria_partes WHERE convocatoria_id = ?",
                (convocatoria_id,),
            )

        for parte in partes:
            conexion.execute(
                """
                INSERT INTO convocatoria_partes (
                    convocatoria_id,
                    nombre,
                    numero_preguntas,
                    orden,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    convocatoria_id,
                    str(parte["nombre"]).strip(),
                    int(parte["numero_preguntas"]),
                    int(parte["orden"]),
                    ahora,
                    ahora,
                ),
            )

        partes_guardadas = conexion.execute(
            """
            SELECT nombre, numero_preguntas, orden
            FROM convocatoria_partes
            WHERE convocatoria_id = ?
            ORDER BY orden
            """,
            (convocatoria_id,),
        ).fetchall()

        if len(partes_guardadas) != len(partes):
            raise RuntimeError(
                "No se guardó el número esperado de partes."
            )

        if sum(int(fila[1]) for fila in partes_guardadas) != int(
            convocatoria["numero_preguntas"]
        ):
            raise RuntimeError(
                "La suma guardada de preguntas por parte no es correcta."
            )

        conexion.commit()
        return convocatoria_id


def ejecutar_importador_temario(
    importador: Path,
    ruta_db: Path,
    convocatoria: dict[str, Any],
    temario: dict[str, Any],
    ruta_csv: Path,
    encoding_detectado: str,
) -> None:
    if not importador.exists():
        raise FileNotFoundError(
            f"No existe importar_temario.py: {importador}"
        )

    comando = [
        sys.executable,
        str(importador),
        "--convocatoria",
        str(convocatoria["codigo"]),
        "--csv",
        str(ruta_csv),
        "--db",
        str(ruta_db),
        "--encoding",
        str(temario.get("encoding") or encoding_detectado),
        "--sin-copia-seguridad",
    ]

    nombre = temario.get("nombre")
    if isinstance(nombre, str) and nombre.strip():
        comando.extend(["--nombre", nombre.strip()])

    if temario.get("sincronizar_eliminaciones", False):
        comando.append("--sincronizar-eliminaciones")

    resultado = subprocess.run(
        comando,
        cwd=RAIZ_PROYECTO,
        check=False,
    )

    if resultado.returncode != 0:
        raise RuntimeError(
            "importar_temario.py terminó con código "
            f"{resultado.returncode}."
        )


def verificar_resultado(
    ruta_db: Path,
    codigo: str,
    temas_esperados: int,
) -> tuple[int, int, int]:
    with sqlite3.connect(ruta_db) as conexion:
        fila = conexion.execute(
            """
            SELECT
                c.id,
                COUNT(DISTINCT t.id),
                COUNT(r.id)
            FROM convocatorias AS c
            LEFT JOIN temarios AS tm
              ON tm.convocatoria_id = c.id
            LEFT JOIN temario_temas AS t
              ON t.temario_id = tm.id
            LEFT JOIN temario_referencias AS r
              ON r.tema_id = t.id
            WHERE c.codigo = ?
            GROUP BY c.id
            """,
            (codigo,),
        ).fetchone()

        if fila is None:
            raise RuntimeError(
                "No se encuentra la convocatoria después del alta."
            )

        convocatoria_id = int(fila[0])
        temas_guardados = int(fila[1])
        referencias_guardadas = int(fila[2])

        if temas_guardados != temas_esperados:
            raise RuntimeError(
                "El número de temas guardados no coincide con el CSV: "
                f"esperados={temas_esperados}, guardados={temas_guardados}."
            )

        return convocatoria_id, temas_guardados, referencias_guardadas


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Valida y da de alta una convocatoria y su temario "
            "sin modificar el CSV fuente."
        )
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Fichero JSON con los datos de la convocatoria.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_POR_DEFECTO,
        help=f"Base SQLite. Predeterminada: {DB_POR_DEFECTO}",
    )
    parser.add_argument(
        "--importador-temario",
        type=Path,
        default=IMPORTADOR_POR_DEFECTO,
        help=(
            "Ruta de importar_temario.py. "
            f"Predeterminada: {IMPORTADOR_POR_DEFECTO}"
        ),
    )
    parser.add_argument(
        "--solo-validar",
        action="store_true",
        help="Comprueba datos, CSV, base e importador sin escribir nada.",
    )
    parser.add_argument(
        "--actualizar-existente",
        action="store_true",
        help=(
            "Permite actualizar una convocatoria existente que todavía "
            "no tenga preguntas vinculadas."
        ),
    )
    return parser


def main() -> None:
    args = construir_parser().parse_args()

    ruta_config = args.config.resolve()
    ruta_db = args.db.resolve()
    importador = args.importador_temario.resolve()

    copia: Path | None = None

    try:
        configuracion = cargar_configuracion(ruta_config)
        convocatoria, partes, temario = validar_configuracion(configuracion)

        ruta_csv = resolver_ruta(temario["csv"])
        encoding, filas_csv, temas_csv, columnas_csv = leer_y_validar_csv(
            ruta_csv,
            temario.get("encoding"),
        )

        if not ruta_db.exists():
            raise FileNotFoundError(
                f"No existe la base de datos: {ruta_db}"
            )

        with sqlite3.connect(ruta_db) as conexion:
            conexion.execute("PRAGMA foreign_keys = ON")
            comprobar_esquema(conexion)

        if not importador.exists():
            raise FileNotFoundError(
                f"No existe el importador del temario: {importador}"
            )

        print()
        print("=" * 68)
        print("VALIDACIÓN DEL ALTA DE CONVOCATORIA")
        print("=" * 68)
        print(f"Configuración......... {ruta_config}")
        print(f"Base de datos......... {ruta_db}")
        print(f"Código................ {convocatoria['codigo']}")
        print(f"Preguntas............. {convocatoria['numero_preguntas']}")
        print(f"Partes................ {len(partes)}")
        print(f"CSV temario........... {ruta_csv}")
        print(f"Codificación detectada {encoding}")
        print(f"Filas CSV............. {filas_csv}")
        print(f"Temas distintos....... {temas_csv}")
        print(f"Columnas CSV.......... {columnas_csv}")
        print("CSV modificado........ NO")
        print("Validación previa...... OK")

        if args.solo_validar:
            print("Modo.................. SOLO VALIDACIÓN")
            print()
            return

        copia = crear_copia_seguridad(
            ruta_db,
            str(convocatoria["codigo"]),
        )

        guardar_convocatoria(
            ruta_db,
            convocatoria,
            partes,
            args.actualizar_existente,
        )

        ejecutar_importador_temario(
            importador,
            ruta_db,
            convocatoria,
            temario,
            ruta_csv,
            encoding,
        )

        convocatoria_id, temas_guardados, referencias = verificar_resultado(
            ruta_db,
            str(convocatoria["codigo"]),
            temas_csv,
        )

        print()
        print("=" * 68)
        print("ALTA DE CONVOCATORIA TERMINADA")
        print("=" * 68)
        print(f"Convocatoria ID....... {convocatoria_id}")
        print(f"Código................ {convocatoria['codigo']}")
        print(f"Temas guardados....... {temas_guardados}")
        print(f"Referencias jurídicas. {referencias}")
        print(f"Copia de seguridad.... {copia}")
        print("CSV modificado........ NO")
        print("Resultado.............. OK")
        print()

    except Exception as exc:
        if copia is not None:
            try:
                restaurar_copia(copia, ruta_db)
                print(
                    f"Base restaurada desde la copia: {copia}",
                    file=sys.stderr,
                )
            except Exception as error_restauracion:
                print(
                    "ERROR CRÍTICO al restaurar la copia: "
                    f"{error_restauracion}",
                    file=sys.stderr,
                )

        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
