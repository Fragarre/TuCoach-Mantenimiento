"""
Validación completa de solo lectura de OpoCoach-Mantenimiento.

Comprueba:
1. Integridad SQLite y claves foráneas.
2. Estado básico de banco_preguntas (duplicados y partes nulas).
3. Normalización jurídica: las incompletas pueden permanecer en lote_preguntas,
   pero nunca en bancos.
4. Vigencia: ningún estado OBSOLETA* puede permanecer en bancos.
5. mantener_banco_preguntas.py en modo SOLO REVISIÓN para cada convocatoria.
6. auditar_bancos_seleccion.py contra el constructor vigente.
7. Modelos de examen configurados (si existe la nueva tabla).
8. auditar_bd.py.

No usa --guardar ni modifica tablas.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


RUTA_SCRIPT = Path(__file__).resolve()
RAIZ = RUTA_SCRIPT.parent.parent
CARPETA_SCRIPTS = RAIZ / "scripts"
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
RUTA_LOG = RAIZ / "logs" / "validacion_completa.log"

SCRIPT_CONSTRUCTOR = CARPETA_SCRIPTS / "mantener_banco_preguntas.py"
SCRIPT_AUDITORIA_BANCOS = CARPETA_SCRIPTS / "auditar_bancos_seleccion.py"
SCRIPT_AUDITORIA_DB = CARPETA_SCRIPTS / "auditar_bd.py"
SCRIPT_MODELO_EXAMEN = CARPETA_SCRIPTS / "configurar_modelo_examen.py"


PATRONES_CONSTRUCTOR = {
    "norma_articulo_repetidos": r"Norma \+ artículo repetidos:\s*(\d+)",
    "categorias_repetidas": r"Categorías no jurídicas repetidas:\s*(\d+)",
    "referencias_invalidas": r"Referencias o equivalencias inválidas:\s*(\d+)",
    "incidencias_banco": r"Incidencias en el banco actual:\s*(\d+)",
    "juridicas_nuevas": r"Jurídicas nuevas:\s*(\d+)",
    "no_juridicas_nuevas": r"No jurídicas nuevas:\s*(\d+)",
    "total_nuevas": r"Total nuevas:\s*(\d+)",
}


def registrar(texto: str) -> None:
    RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUTA_LOG.open("a", encoding="utf-8") as f:
        f.write(texto)
        if not texto.endswith("\n"):
            f.write("\n")


def ejecutar(comando: list[str], titulo: str) -> tuple[int, str]:
    resultado = subprocess.run(
        comando,
        cwd=RAIZ,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    salida_bytes = resultado.stdout or b""
    try:
        salida = salida_bytes.decode("utf-8")
    except UnicodeDecodeError:
        salida = salida_bytes.decode("cp1252", errors="replace")
    registrar("\n" + "=" * 78)
    registrar(titulo)
    registrar("=" * 78)
    registrar("COMANDO: " + " ".join(comando))
    registrar(f"CÓDIGO DE SALIDA: {resultado.returncode}")
    registrar(salida)
    return resultado.returncode, salida


def extraer_contadores_constructor(salida: str) -> dict[str, int] | None:
    valores: dict[str, int] = {}
    for nombre, patron in PATRONES_CONSTRUCTOR.items():
        m = re.search(patron, salida, flags=re.IGNORECASE)
        if m is None:
            return None
        valores[nombre] = int(m.group(1))
    return valores


def comprobar_sqlite() -> tuple[bool, dict[str, int | str]]:
    datos: dict[str, int | str] = {}
    try:
        conexion = sqlite3.connect(f"file:{RUTA_DB.as_posix()}?mode=ro", uri=True)
        try:
            integridad = conexion.execute("PRAGMA integrity_check").fetchone()[0]
            fk = conexion.execute("PRAGMA foreign_key_check").fetchall()
            duplicados = conexion.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT convocatoria_id, pregunta_id
                    FROM banco_preguntas
                    GROUP BY convocatoria_id, pregunta_id
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            partes_nulas = conexion.execute(
                "SELECT COUNT(*) FROM banco_preguntas WHERE convocatoria_parte_id IS NULL"
            ).fetchone()[0]
            convocatorias = conexion.execute(
                "SELECT COUNT(*) FROM convocatorias"
            ).fetchone()[0]
            ia = conexion.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN UPPER(TRIM(COALESCE(tipo_clasificacion, ''))) = 'JURIDICA' THEN 1 ELSE 0 END) AS juridicas,
                    SUM(CASE WHEN UPPER(TRIM(COALESCE(tipo_clasificacion, ''))) <> 'JURIDICA' THEN 1 ELSE 0 END) AS no_juridicas,
                    SUM(CASE
                        WHEN UPPER(TRIM(COALESCE(tipo_clasificacion, ''))) = 'JURIDICA'
                         AND TRIM(COALESCE(tipo_norma_normalizado, '')) <> ''
                         AND norma_id_normalizada IS NOT NULL
                         AND TRIM(COALESCE(nombre_norma_normalizado, '')) <> ''
                         AND TRIM(COALESCE(articulo_normalizado, '')) <> ''
                        THEN 1 ELSE 0 END
                    ) AS juridicas_correctas,
                    SUM(CASE
                        WHEN UPPER(TRIM(COALESCE(tipo_clasificacion, ''))) = 'JURIDICA'
                         AND (
                             TRIM(COALESCE(tipo_norma_normalizado, '')) = ''
                             OR norma_id_normalizada IS NULL
                             OR TRIM(COALESCE(nombre_norma_normalizado, '')) = ''
                             OR TRIM(COALESCE(articulo_normalizado, '')) = ''
                         )
                        THEN 1 ELSE 0 END
                    ) AS juridicas_sin_referencia
                FROM lote_preguntas
                WHERE LOWER(TRIM(COALESCE(tipo_fuente, ''))) = 'ia_generada'
                """
            ).fetchone()
            juridicas_norm = conexion.execute(
                """
                SELECT
                    COUNT(*) AS total_juridicas,
                    SUM(CASE WHEN
                           TRIM(COALESCE(tipo_norma_normalizado, '')) = ''
                        OR TRIM(COALESCE(nombre_norma_normalizado, '')) = ''
                        OR norma_id_normalizada IS NULL
                        OR TRIM(COALESCE(articulo_normalizado, '')) = ''
                        THEN 1 ELSE 0 END
                    ) AS incompletas_lote
                FROM lote_preguntas
                WHERE UPPER(TRIM(COALESCE(tipo_clasificacion, '')))='JURIDICA'
                """
            ).fetchone()

            prohibidas_banco = conexion.execute(
                """
                SELECT
                    SUM(CASE WHEN
                        UPPER(TRIM(COALESCE(lp.estado_vigencia, ''))) LIKE 'OBSOLETA%'
                        THEN 1 ELSE 0 END
                    ) AS obsoletas_banco,
                    SUM(CASE WHEN
                           TRIM(COALESCE(lp.tipo_norma_normalizado, '')) = ''
                        OR TRIM(COALESCE(lp.nombre_norma_normalizado, '')) = ''
                        OR lp.norma_id_normalizada IS NULL
                        OR TRIM(COALESCE(lp.articulo_normalizado, '')) = ''
                        THEN 1 ELSE 0 END
                    ) AS incompletas_banco
                FROM banco_preguntas bp
                JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
                WHERE UPPER(TRIM(COALESCE(lp.tipo_clasificacion, '')))='JURIDICA'
                """
            ).fetchone()

        finally:
            conexion.close()
    except Exception as exc:
        datos["error"] = str(exc)
        return False, datos

    datos["integrity_check"] = integridad
    datos["foreign_key_errors"] = len(fk)
    datos["duplicados_banco"] = int(duplicados)
    datos["partes_nulas"] = int(partes_nulas)
    datos["convocatorias"] = int(convocatorias)
    datos["ia_total"] = int(ia[0] or 0)
    datos["ia_juridicas"] = int(ia[1] or 0)
    datos["ia_no_juridicas"] = int(ia[2] or 0)
    datos["ia_juridicas_correctas"] = int(ia[3] or 0)
    datos["ia_juridicas_sin_referencia"] = int(ia[4] or 0)
    datos["juridicas_total"] = int(juridicas_norm[0] or 0)
    datos["juridicas_incompletas_lote"] = int(juridicas_norm[1] or 0)
    datos["juridicas_obsoletas_banco"] = int(prohibidas_banco[0] or 0)
    datos["juridicas_incompletas_banco"] = int(prohibidas_banco[1] or 0)

    ok = (
        str(integridad).lower() == "ok"
        and len(fk) == 0
        and int(duplicados) == 0
        and int(partes_nulas) == 0
        and int(ia[4] or 0) == 0
        and int(prohibidas_banco[0] or 0) == 0
        and int(prohibidas_banco[1] or 0) == 0
    )
    return ok, datos


def obtener_convocatorias() -> list[tuple[int, str]]:
    conexion = sqlite3.connect(f"file:{RUTA_DB.as_posix()}?mode=ro", uri=True)
    try:
        filas = conexion.execute(
            "SELECT id, codigo FROM convocatorias ORDER BY id"
        ).fetchall()
    finally:
        conexion.close()
    return [(int(i), str(c)) for i, c in filas]


def validar_constructor(convocatoria_id: int, codigo: str) -> tuple[bool, str]:
    rc, salida = ejecutar(
        [
            sys.executable,
            str(SCRIPT_CONSTRUCTOR),
            "--db",
            str(RUTA_DB),
            "--convocatoria-id",
            str(convocatoria_id),
        ],
        f"CONSTRUCTOR EN REVISIÓN | {convocatoria_id} | {codigo}",
    )
    if rc != 0:
        return False, f"código de salida {rc}"

    contadores = extraer_contadores_constructor(salida)
    if contadores is None:
        return False, "no se pudieron leer los contadores esperados"

    incorrectos = {k: v for k, v in contadores.items() if v != 0}
    if incorrectos:
        detalle = ", ".join(f"{k}={v}" for k, v in incorrectos.items())
        return False, detalle

    return True, "0 incidencias y 0 nuevas"


def main() -> int:
    inicio = datetime.now()
    RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    registrar("\n\n" + "#" * 78)
    registrar(f"VALIDACIÓN COMPLETA | {inicio.isoformat(sep=' ', timespec='seconds')}")
    registrar("#" * 78)

    print("=" * 78)
    print("VALIDACIÓN COMPLETA OPOCOACH-MANTENIMIENTO")
    print("=" * 78)
    print("Modo: SOLO LECTURA")
    print(f"Base: {RUTA_DB}")

    requeridos = [RUTA_DB, SCRIPT_CONSTRUCTOR, SCRIPT_AUDITORIA_BANCOS, SCRIPT_AUDITORIA_DB, SCRIPT_MODELO_EXAMEN]
    faltantes = [str(p) for p in requeridos if not p.is_file()]
    if faltantes:
        print("\nERROR: faltan archivos necesarios:")
        for p in faltantes:
            print(f"  - {p}")
        return 1

    fallos = 0

    ok_sqlite, datos = comprobar_sqlite()
    if "error" in datos:
        print(f"SQLite............................... ERROR: {datos['error']}")
        return 1

    print(f"SQLite integrity_check............... {datos['integrity_check']}")
    print(f"Foreign key errors................... {datos['foreign_key_errors']}")
    print(f"Duplicados en banco.................. {datos['duplicados_banco']}")
    print(f"Partes de convocatoria NULAS......... {datos['partes_nulas']}")
    print("\nNormalización/vigencia jurídica:")
    print(f"  Jurídicas totales................... {datos['juridicas_total']}")
    print(f"  Incompletas en lote (rechazadas).... {datos['juridicas_incompletas_lote']}")
    print(f"  Incompletas presentes en bancos..... {datos['juridicas_incompletas_banco']}")
    print(f"  OBSOLETA* presentes en bancos....... {datos['juridicas_obsoletas_banco']}")
    print("  NO_VERIFICABLE/REVISAR.............. admitidas en banco")
    print("\nPreguntas generadas por IA:")
    print(f"  Jurídicas........................... {datos['ia_juridicas']}")
    print(f"    Referencia jurídica correcta...... {datos['ia_juridicas_correctas']}")
    print(f"    Sin referencia jurídica completa.. {datos['ia_juridicas_sin_referencia']}")
    print(f"  No jurídicas........................ {datos['ia_no_juridicas']}")
    print("    Norma/artículo..................... no aplicable")
    if not ok_sqlite:
        fallos += 1

    print("\nConstructores en modo revisión:")
    convocatorias = obtener_convocatorias()
    if not convocatorias:
        print("  ERROR: no hay convocatorias.")
        fallos += 1
    else:
        for convocatoria_id, codigo in convocatorias:
            ok, detalle = validar_constructor(convocatoria_id, codigo)
            estado = "OK" if ok else "ERROR"
            print(f"  {convocatoria_id} | {codigo:<24} {estado} | {detalle}")
            if not ok:
                fallos += 1

    rc_bancos, _ = ejecutar(
        [
            sys.executable,
            str(SCRIPT_AUDITORIA_BANCOS),
            "--db",
            str(RUTA_DB),
            "--constructor",
            str(SCRIPT_CONSTRUCTOR),
        ],
        "AUDITORÍA INDEPENDIENTE DE SELECCIÓN DE BANCOS",
    )
    print(f"\nAuditoría selección de bancos........ {'OK' if rc_bancos == 0 else 'ERROR'}")
    if rc_bancos != 0:
        fallos += 1

    rc_modelo, salida_modelo = ejecutar(
        [
            sys.executable,
            str(SCRIPT_MODELO_EXAMEN),
            "--db",
            str(RUTA_DB),
            "--validar-todos",
        ],
        "VALIDACIÓN DE MODELOS DE EXAMEN CONFIGURADOS",
    )
    print(
        f"Modelos de examen configurados........ "
        f"{'OK' if rc_modelo == 0 else 'ERROR'}"
    )
    if rc_modelo != 0:
        fallos += 1

    rc_bd, _ = ejecutar(
        [sys.executable, str(SCRIPT_AUDITORIA_DB)],
        "AUDITORÍA GENERAL DE LA BASE",
    )
    print(f"Auditoría general de la base.......... {'OK' if rc_bd == 0 else 'ERROR'}")
    if rc_bd != 0:
        fallos += 1

    fin = datetime.now()
    print("\n" + "=" * 78)
    if fallos == 0:
        print("RESULTADO FINAL....................... CORRECTO")
    else:
        print(f"RESULTADO FINAL....................... ERROR ({fallos} bloques)")
    print(f"Duración.............................. {fin - inicio}")
    print(f"Log detallado......................... {RUTA_LOG}")
    print("=" * 78)

    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())