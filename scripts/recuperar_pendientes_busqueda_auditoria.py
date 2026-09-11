
"""
Orquestador idempotente para recuperar preguntas PENDIENTES mediante:

1. Ejecución SIN MODIFICAR de buscar_norma_por_respuesta_correcta.py.
2. Auditoría independiente por IA de la propuesta obtenida.
3. Validación automática de una evidencia literal aportada por la auditoría.
4. Aplicación exclusiva de resultados APROBADA o CORREGIDA.
5. Registro de todas las preguntas procesadas en una base auxiliar:
   - APROBADA
   - CORREGIDA
   - NO_RESUELTA
   - ERROR

Las preguntas NO_RESUELTA permanecen como PENDIENTE, pero no vuelven a
procesarse en ejecuciones posteriores.

Por seguridad:
- la tabla de control se guarda fuera de oposiciones.sqlite3;
- crea una copia de seguridad antes de la primera modificación;
- procesa y confirma cada pregunta en una transacción independiente;
- los errores no se marcan como definitivos y pueden reintentarse.

Uso:
    python scripts/recuperar_pendientes_busqueda_auditoria.py
    python scripts/recuperar_pendientes_busqueda_auditoria.py --aplicar
    python scripts/recuperar_pendientes_busqueda_auditoria.py --limite 20 --aplicar
    python scripts/recuperar_pendientes_busqueda_auditoria.py --id 5123 --aplicar
    python scripts/recuperar_pendientes_busqueda_auditoria.py --reintentar-no-resueltas --aplicar
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from openai_api import seleccionar_fragmento_json


RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent

DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
DB_CONTROL_DEFECTO = RAIZ / "db" / "recuperacion_pendientes.sqlite3"

BUSCADOR = SCRIPTS / "buscar_norma_por_respuesta_correcta.py"
MODELO_DEFECTO = "gpt-5.4-nano"

TABLA_CONTROL = "auditoria_busqueda_norma"
CLAVE_PROCESO = "busqueda_respuesta_correcta_auditoria_v1"


def limpiar(valor: object) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).strip().split())


def conectar(db: Path) -> sqlite3.Connection:
    conexion = sqlite3.connect(db, timeout=30)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.execute("PRAGMA busy_timeout = 30000")
    return conexion


def columnas_tabla(
    conexion: sqlite3.Connection,
    tabla: str,
) -> set[str]:
    return {
        str(fila["name"])
        for fila in conexion.execute(f"PRAGMA table_info({tabla})")
    }


def validar_entorno(db: Path, db_control: Path) -> set[str]:
    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")

    if db == db_control:
        raise ValueError("--db-control debe ser distinto de --db.")

    if not BUSCADOR.is_file():
        raise FileNotFoundError(
            f"No existe el buscador requerido: {BUSCADOR}"
        )

    with conectar(db) as conexion:
        existe = conexion.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'lote_preguntas'
            """
        ).fetchone()

        if existe is None:
            raise RuntimeError("No existe la tabla lote_preguntas.")

        columnas = columnas_tabla(conexion, "lote_preguntas")

    obligatorias = {
        "id",
        "enunciado",
        "opcion_a",
        "opcion_b",
        "opcion_c",
        "opcion_d",
        "respuesta_correcta",
        "tipo_clasificacion",
        "tipo_norma",
        "nombre_norma",
        "articulo",
    }
    faltan = sorted(obligatorias - columnas)
    if faltan:
        raise RuntimeError(
            "Faltan columnas obligatorias en lote_preguntas: "
            + ", ".join(faltan)
        )

    return columnas


def asegurar_tabla_control(db_control: Path) -> None:
    db_control.parent.mkdir(parents=True, exist_ok=True)

    with conectar(db_control) as conexion:
        conexion.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLA_CONTROL} (
                pregunta_id INTEGER NOT NULL,
                proceso TEXT NOT NULL,
                estado TEXT NOT NULL,
                propuesta_inicial TEXT,
                tipo_norma_final TEXT,
                nombre_norma_final TEXT,
                articulo_final TEXT,
                evidencia_literal TEXT,
                confianza REAL,
                motivo TEXT,
                error TEXT,
                procesada_en TEXT NOT NULL,
                PRIMARY KEY (pregunta_id, proceso)
            )
            """
        )
        columnas_control = columnas_tabla(conexion, TABLA_CONTROL)
        if "evidencia_literal" not in columnas_control:
            conexion.execute(
                f"""
                ALTER TABLE {TABLA_CONTROL}
                ADD COLUMN evidencia_literal TEXT
                """
            )

        conexion.execute(
            f"""
            CREATE INDEX IF NOT EXISTS
                idx_{TABLA_CONTROL}_estado
            ON {TABLA_CONTROL}(proceso, estado)
            """
        )


def obtener_ids(
    db: Path,
    db_control: Path,
    pregunta_id: int | None,
    limite: int | None,
    reintentar_no_resueltas: bool,
) -> list[int]:
    condiciones = ["tipo_clasificacion = 'PENDIENTE'"]
    parametros: list[object] = []

    if pregunta_id is not None:
        condiciones.append("id = ?")
        parametros.append(pregunta_id)

    with conectar(db) as conexion:
        filas = conexion.execute(
            f"""
            SELECT id
            FROM lote_preguntas
            WHERE {' AND '.join(condiciones)}
            ORDER BY id
            """,
            parametros,
        ).fetchall()

    ids = [int(fila["id"]) for fila in filas]

    if db_control.is_file():
        with conectar(db_control) as conexion:
            filas_control = conexion.execute(
                f"""
                SELECT pregunta_id, estado
                FROM {TABLA_CONTROL}
                WHERE proceso = ?
                """,
                (CLAVE_PROCESO,),
            ).fetchall()

        estados = {
            int(fila["pregunta_id"]): str(fila["estado"])
            for fila in filas_control
        }

        ids_filtrados: list[int] = []
        for identificador in ids:
            estado = estados.get(identificador)

            if estado is None:
                ids_filtrados.append(identificador)
            elif reintentar_no_resueltas and estado == "NO_RESUELTA":
                ids_filtrados.append(identificador)
            elif estado == "ERROR":
                # Los errores son siempre reintentables.
                ids_filtrados.append(identificador)

        ids = ids_filtrados

    if limite is not None:
        ids = ids[:limite]

    return ids


def obtener_pregunta(db: Path, pregunta_id: int) -> sqlite3.Row:
    with conectar(db) as conexion:
        fila = conexion.execute(
            """
            SELECT
                id,
                enunciado,
                opcion_a,
                opcion_b,
                opcion_c,
                opcion_d,
                respuesta_correcta,
                tipo_clasificacion
            FROM lote_preguntas
            WHERE id = ?
            """,
            (pregunta_id,),
        ).fetchone()

    if fila is None:
        raise RuntimeError(f"No existe la pregunta {pregunta_id}.")

    if fila["tipo_clasificacion"] != "PENDIENTE":
        raise RuntimeError(
            f"La pregunta {pregunta_id} ya no está PENDIENTE."
        )

    return fila


def texto_respuesta_correcta(fila: sqlite3.Row) -> tuple[str, str]:
    letra = limpiar(fila["respuesta_correcta"]).upper()

    mapa = {
        "A": "opcion_a",
        "B": "opcion_b",
        "C": "opcion_c",
        "D": "opcion_d",
    }
    columna = mapa.get(letra)
    if columna is None:
        raise ValueError(
            f"Respuesta correcta no válida: {fila['respuesta_correcta']!r}"
        )

    texto = limpiar(fila[columna])
    if not texto:
        raise ValueError(
            f"La opción correcta {letra} no contiene texto."
        )

    return letra, texto


def carpetas_busqueda() -> set[Path]:
    raiz = RAIZ / "auditorias"
    if not raiz.is_dir():
        return set()

    return {
        ruta.resolve()
        for ruta in raiz.glob("buscar_norma_respuesta_correcta_*")
        if ruta.is_dir()
    }


def ejecutar_busqueda(
    db: Path,
    pregunta_id: int,
    modelo: str,
) -> dict[str, str]:
    anteriores = carpetas_busqueda()

    comando = [
        sys.executable,
        str(BUSCADOR),
        "--db",
        str(db),
        "--id",
        str(pregunta_id),
        "--modelo",
        modelo,
    ]

    resultado = subprocess.run(
        comando,
        cwd=str(RAIZ),
        check=False,
    )

    if resultado.returncode != 0:
        raise RuntimeError(
            "El buscador terminó con código "
            f"{resultado.returncode} para la pregunta {pregunta_id}."
        )

    posteriores = carpetas_busqueda()
    nuevas = sorted(
        posteriores - anteriores,
        key=lambda ruta: ruta.stat().st_mtime,
        reverse=True,
    )

    if nuevas:
        carpeta = nuevas[0]
    else:
        candidatas = sorted(
            posteriores,
            key=lambda ruta: ruta.stat().st_mtime,
            reverse=True,
        )
        if not candidatas:
            raise RuntimeError(
                "El buscador terminó correctamente, pero no se encontró "
                "su carpeta de resultados."
            )
        carpeta = candidatas[0]

    ruta_csv = carpeta / "resultados.csv"
    if not ruta_csv.is_file():
        raise FileNotFoundError(
            f"No existe el resultado esperado: {ruta_csv}"
        )

    with ruta_csv.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as fichero:
        filas = list(csv.DictReader(fichero))

    coincidentes = [
        fila
        for fila in filas
        if int(fila.get("pregunta_id") or 0) == pregunta_id
    ]

    if len(coincidentes) != 1:
        raise RuntimeError(
            f"Se esperaban 1 resultado para la pregunta {pregunta_id} "
            f"y se encontraron {len(coincidentes)}."
        )

    return {
        clave: limpiar(valor)
        for clave, valor in coincidentes[0].items()
    }


def construir_prompt_auditoria(
    fila: sqlite3.Row,
    resultado_busqueda: dict[str, str],
) -> str:
    letra, texto_correcto = texto_respuesta_correcta(fila)

    return f"""
Actúa como auditor jurídico extremadamente conservador.

Debes revisar una pregunta tipo test y la propuesta obtenida por otro proceso
que intentó identificar la norma y el artículo a partir del texto de la
respuesta correcta.

Tu tarea NO es aceptar la propuesta por defecto. Debes:

1. Comprobar si la propuesta es correcta.
2. Corregirla cuando sea errónea y puedas identificar inequívocamente la
   referencia correcta.
3. Intentar resolver también los casos cuya propuesta sea NO ENCONTRADO.
4. Declarar NO_RESUELTA cuando no exista certeza suficiente.

Solo puedes aprobar o corregir cuando la referencia sea inequívoca. No
inventes artículos, apartados ni denominaciones.

PREGUNTA
ID: {fila["id"]}
Enunciado: {limpiar(fila["enunciado"])}
A: {limpiar(fila["opcion_a"])}
B: {limpiar(fila["opcion_b"])}
C: {limpiar(fila["opcion_c"])}
D: {limpiar(fila["opcion_d"])}
Respuesta correcta: {letra}
Texto correcto: {texto_correcto}

RESULTADO DE LA BÚSQUEDA INICIAL
Estado: {resultado_busqueda.get("estado", "")}
Propuesta: {resultado_busqueda.get("respuesta_ia", "")}
Error: {resultado_busqueda.get("error", "")}

Devuelve EXCLUSIVAMENTE un objeto JSON válido con estas claves:

{{
  "estado": "APROBADA | CORREGIDA | NO_RESUELTA",
  "tipo_norma": "tipo formal de norma o cadena vacía",
  "nombre_norma": "denominación suficientemente identificativa o cadena vacía",
  "articulo": "artículo y, si procede, apartado; o cadena vacía",
  "evidencia_literal": "fragmento literal copiado exactamente del enunciado o de la respuesta correcta",
  "motivo": "explicación breve y concreta"
}}

Reglas obligatorias:
- APROBADA: la propuesta inicial es correcta.
- CORREGIDA: la propuesta inicial es incorrecta o NO ENCONTRADO, pero has
  identificado una referencia correcta e inequívoca.
- NO_RESUELTA: no hay seguridad suficiente.
- APROBADA y CORREGIDA requieren tipo_norma, nombre_norma, articulo y una
  evidencia_literal de al menos 12 caracteres.
- evidencia_literal debe copiarse literalmente, sin reformular, del enunciado
  o del texto de la respuesta correcta suministrados arriba.
- NO_RESUELTA debe llevar tipo_norma, nombre_norma, articulo y
  evidencia_literal vacíos.
- No incluyas texto fuera del JSON.
""".strip()


def normalizar_para_comparar(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", limpiar(texto)).casefold()
    return " ".join(texto.split())


def normalizar_auditoria(
    datos: Any,
    fila: sqlite3.Row,
) -> dict[str, Any]:
    if not isinstance(datos, dict):
        raise ValueError("La auditoría no devolvió un objeto JSON.")

    estado = limpiar(datos.get("estado")).upper()
    tipo_norma = limpiar(datos.get("tipo_norma"))
    nombre_norma = limpiar(datos.get("nombre_norma"))
    articulo = limpiar(datos.get("articulo"))
    evidencia_literal = limpiar(datos.get("evidencia_literal"))
    motivo = limpiar(datos.get("motivo"))

    if estado not in {"APROBADA", "CORREGIDA", "NO_RESUELTA"}:
        raise ValueError(f"Estado de auditoría no válido: {estado!r}")

    if estado in {"APROBADA", "CORREGIDA"}:
        faltan = [
            nombre
            for nombre, valor in (
                ("tipo_norma", tipo_norma),
                ("nombre_norma", nombre_norma),
                ("articulo", articulo),
                ("evidencia_literal", evidencia_literal),
            )
            if not valor
        ]

        texto_origen = " ".join(
            [
                limpiar(fila["enunciado"]),
                texto_respuesta_correcta(fila)[1],
            ]
        )
        evidencia_valida = (
            len(evidencia_literal) >= 12
            and normalizar_para_comparar(evidencia_literal)
            in normalizar_para_comparar(texto_origen)
        )

        if faltan or not evidencia_valida:
            estado = "NO_RESUELTA"
            detalle = []
            if faltan:
                detalle.append(
                    "faltan campos obligatorios: " + ", ".join(faltan)
                )
            if not evidencia_valida:
                detalle.append(
                    "la evidencia no es un fragmento literal verificable "
                    "del enunciado o de la respuesta correcta"
                )

            motivo = (
                "Resultado rechazado automáticamente porque "
                + "; ".join(detalle)
                + ". "
                + motivo
            ).strip()
            tipo_norma = ""
            nombre_norma = ""
            articulo = ""
            evidencia_literal = ""

    if estado == "NO_RESUELTA":
        tipo_norma = ""
        nombre_norma = ""
        articulo = ""
        evidencia_literal = ""

    return {
        "estado": estado,
        "tipo_norma": tipo_norma,
        "nombre_norma": nombre_norma,
        "articulo": articulo,
        "evidencia_literal": evidencia_literal,
        "motivo": motivo,
    }


def auditar(
    fila: sqlite3.Row,
    resultado_busqueda: dict[str, str],
    modelo: str,
) -> dict[str, Any]:
    prompt = construir_prompt_auditoria(
        fila=fila,
        resultado_busqueda=resultado_busqueda,
    )

    datos = seleccionar_fragmento_json(
        prompt=prompt,
        modelo=modelo,
        operacion="auditar_norma_respuesta_correcta",
    )

    return normalizar_auditoria(datos, fila)


def crear_backup(db: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = db.with_name(
        f"{db.stem}_antes_auditoria_norma_{marca}{db.suffix}"
    )
    shutil.copy2(db, destino)
    return destino


def aplicar_resultado(
    db: Path,
    columnas: set[str],
    pregunta_id: int,
    auditoria: dict[str, Any],
) -> None:
    asignaciones = [
        "tipo_clasificacion = ?",
        "tipo_norma = ?",
        "nombre_norma = ?",
        "articulo = ?",
    ]
    valores: list[object] = [
        "JURIDICA",
        auditoria["tipo_norma"],
        auditoria["nombre_norma"],
        auditoria["articulo"],
    ]

    # Se completan también los campos normalizados cuando existen, sin
    # inventar norma_id. El catálogo y enlazar_normas.py harán el enlace.
    if "tipo_norma_normalizado" in columnas:
        asignaciones.append("tipo_norma_normalizado = ?")
        valores.append(auditoria["tipo_norma"])

    if "nombre_norma_normalizado" in columnas:
        asignaciones.append("nombre_norma_normalizado = ?")
        valores.append(auditoria["nombre_norma"])

    if "articulo_normalizado" in columnas:
        asignaciones.append("articulo_normalizado = ?")
        valores.append(auditoria["articulo"])

    if "updated_at" in columnas:
        asignaciones.append("updated_at = ?")
        valores.append(datetime.now().isoformat(timespec="seconds"))

    valores.extend([pregunta_id, "PENDIENTE"])

    with conectar(db) as conexion:
        cursor = conexion.execute(
            f"""
            UPDATE lote_preguntas
            SET {', '.join(asignaciones)}
            WHERE id = ?
              AND tipo_clasificacion = ?
            """,
            valores,
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"No se pudo actualizar exactamente una fila para "
                f"la pregunta {pregunta_id}; filas: {cursor.rowcount}."
            )


def registrar_control(
    db_control: Path,
    pregunta_id: int,
    resultado_busqueda: dict[str, str],
    auditoria: dict[str, Any] | None,
    error: str = "",
) -> None:
    estado = "ERROR" if error else str(auditoria["estado"])

    with conectar(db_control) as conexion:
        conexion.execute(
            f"""
            INSERT INTO {TABLA_CONTROL} (
                pregunta_id,
                proceso,
                estado,
                propuesta_inicial,
                tipo_norma_final,
                nombre_norma_final,
                articulo_final,
                evidencia_literal,
                confianza,
                motivo,
                error,
                procesada_en
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pregunta_id, proceso) DO UPDATE SET
                estado = excluded.estado,
                propuesta_inicial = excluded.propuesta_inicial,
                tipo_norma_final = excluded.tipo_norma_final,
                nombre_norma_final = excluded.nombre_norma_final,
                articulo_final = excluded.articulo_final,
                evidencia_literal = excluded.evidencia_literal,
                confianza = excluded.confianza,
                motivo = excluded.motivo,
                error = excluded.error,
                procesada_en = excluded.procesada_en
            """,
            (
                pregunta_id,
                CLAVE_PROCESO,
                estado,
                resultado_busqueda.get("respuesta_ia", ""),
                "" if auditoria is None else auditoria["tipo_norma"],
                "" if auditoria is None else auditoria["nombre_norma"],
                "" if auditoria is None else auditoria["articulo"],
                "" if auditoria is None else auditoria["evidencia_literal"],
                None,
                "" if auditoria is None else auditoria["motivo"],
                error,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def escribir_auditoria_csv(
    ruta: Path,
    filas: list[dict[str, Any]],
) -> None:
    columnas = [
        "pregunta_id",
        "estado_busqueda",
        "propuesta_inicial",
        "estado_auditoria",
        "tipo_norma_final",
        "nombre_norma_final",
        "articulo_final",
        "evidencia_literal",
        "motivo",
        "aplicada",
        "error",
    ]

    with ruta.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as fichero:
        escritor = csv.DictWriter(fichero, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(filas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Busca, audita y recupera de forma idempotente preguntas "
            "jurídicas PENDIENTES."
        )
    )
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument(
        "--db-control",
        default=str(DB_CONTROL_DEFECTO),
        help="Base auxiliar de control; no es utilizada por Streamlit.",
    )
    parser.add_argument("--id", type=int)
    parser.add_argument("--limite", type=int, default=20)
    parser.add_argument("--modelo-busqueda", default=MODELO_DEFECTO)
    parser.add_argument("--modelo-auditoria", default=MODELO_DEFECTO)
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Guarda APROBADA/CORREGIDA y registra todo el proceso.",
    )
    parser.add_argument(
        "--reintentar-no-resueltas",
        action="store_true",
        help="Vuelve a procesar las preguntas registradas como NO_RESUELTA.",
    )
    args = parser.parse_args()

    if args.id is not None and args.id <= 0:
        raise ValueError("--id debe ser mayor que cero.")

    if args.limite is not None and args.limite <= 0:
        raise ValueError("--limite debe ser mayor que cero.")

    db = Path(args.db).resolve()
    db_control = Path(args.db_control).resolve()

    columnas = validar_entorno(db, db_control)
    asegurar_tabla_control(db_control)

    ids = obtener_ids(
        db=db,
        db_control=db_control,
        pregunta_id=args.id,
        limite=None if args.id is not None else args.limite,
        reintentar_no_resueltas=args.reintentar_no_resueltas,
    )

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = (
        RAIZ
        / "auditorias"
        / f"recuperar_pendientes_busqueda_auditoria_{marca}"
    )
    carpeta.mkdir(parents=True, exist_ok=True)

    print("RECUPERACIÓN POR BÚSQUEDA Y AUDITORÍA")
    print("=" * 70)
    print(f"Modo: {'APLICAR' if args.aplicar else 'SOLO REVISIÓN'}")
    print(f"Base: {db}")
    print(f"Control: {db_control}")
    print(f"Preguntas nuevas: {len(ids)}")
    print(f"Modelo búsqueda: {args.modelo_busqueda}")
    print(f"Modelo auditoría: {args.modelo_auditoria}")

    if not ids:
        print("No hay preguntas pendientes nuevas para este proceso.")
        return 0

    backup: Path | None = None
    registros_csv: list[dict[str, Any]] = []

    contadores = {
        "APROBADA": 0,
        "CORREGIDA": 0,
        "NO_RESUELTA": 0,
        "ERROR": 0,
        "APLICADAS": 0,
    }

    for posicion, pregunta_id in enumerate(ids, start=1):
        print("\n" + "-" * 70)
        print(f"[{posicion}/{len(ids)}] Pregunta {pregunta_id}")

        resultado_busqueda: dict[str, str] = {
            "estado": "",
            "respuesta_ia": "",
            "error": "",
        }
        auditoria: dict[str, Any] | None = None
        error = ""
        aplicada = False

        try:
            fila = obtener_pregunta(db, pregunta_id)

            resultado_busqueda = ejecutar_busqueda(
                db=db,
                pregunta_id=pregunta_id,
                modelo=args.modelo_busqueda,
            )
            print(
                "Búsqueda inicial: "
                f"{resultado_busqueda.get('respuesta_ia', '')}"
            )

            auditoria = auditar(
                fila=fila,
                resultado_busqueda=resultado_busqueda,
                modelo=args.modelo_auditoria,
            )
            estado = str(auditoria["estado"])
            contadores[estado] += 1

            print(f"Auditoría: {estado}")
            if estado in {"APROBADA", "CORREGIDA"}:
                print(
                    f"Referencia: {auditoria['nombre_norma']} | "
                    f"art. {auditoria['articulo']}"
                )

                if args.aplicar:
                    if backup is None:
                        backup = crear_backup(db)
                        print(f"Copia de seguridad: {backup}")

                    aplicar_resultado(
                        db=db,
                        columnas=columnas,
                        pregunta_id=pregunta_id,
                        auditoria=auditoria,
                    )
                    aplicada = True
                    contadores["APLICADAS"] += 1

            if args.aplicar:
                registrar_control(
                    db_control=db_control,
                    pregunta_id=pregunta_id,
                    resultado_busqueda=resultado_busqueda,
                    auditoria=auditoria,
                )

        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            contadores["ERROR"] += 1
            print(f"ERROR: {error}", file=sys.stderr)

            # Los errores se registran para auditoría, pero obtener_ids()
            # los considera reintentables en la siguiente ejecución.
            if args.aplicar:
                registrar_control(
                    db_control=db_control,
                    pregunta_id=pregunta_id,
                    resultado_busqueda=resultado_busqueda,
                    auditoria=auditoria,
                    error=error,
                )

        registros_csv.append(
            {
                "pregunta_id": pregunta_id,
                "estado_busqueda": resultado_busqueda.get("estado", ""),
                "propuesta_inicial": resultado_busqueda.get(
                    "respuesta_ia", ""
                ),
                "estado_auditoria": (
                    "" if auditoria is None else auditoria["estado"]
                ),
                "tipo_norma_final": (
                    "" if auditoria is None else auditoria["tipo_norma"]
                ),
                "nombre_norma_final": (
                    "" if auditoria is None else auditoria["nombre_norma"]
                ),
                "articulo_final": (
                    "" if auditoria is None else auditoria["articulo"]
                ),
                "evidencia_literal": (
                    ""
                    if auditoria is None
                    else auditoria["evidencia_literal"]
                ),
                "motivo": (
                    "" if auditoria is None else auditoria["motivo"]
                ),
                "aplicada": "SI" if aplicada else "NO",
                "error": error,
            }
        )

        escribir_auditoria_csv(
            carpeta / "resultados_auditoria.csv",
            registros_csv,
        )

    resumen = [
        "RECUPERACIÓN POR BÚSQUEDA Y AUDITORÍA",
        "=" * 60,
        f"Modo: {'APLICAR' if args.aplicar else 'SOLO REVISIÓN'}",
        f"Preguntas procesadas: {len(registros_csv)}",
        f"Aprobadas: {contadores['APROBADA']}",
        f"Corregidas: {contadores['CORREGIDA']}",
        f"No resueltas: {contadores['NO_RESUELTA']}",
        f"Errores reintentables: {contadores['ERROR']}",
        f"Modificaciones aplicadas: {contadores['APLICADAS']}",
        f"Backup: {backup or 'No necesario'}",
        f"Resultados: {carpeta / 'resultados_auditoria.csv'}",
    ]

    (carpeta / "resumen.txt").write_text(
        "\n".join(resumen) + "\n",
        encoding="utf-8-sig",
    )

    print("\n" + "\n".join(resumen))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nProceso interrumpido por el usuario.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(
            f"\nERROR: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)