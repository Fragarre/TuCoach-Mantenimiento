"""

===============================================================================
Proyecto : OpoCoach
Tipo     : Orquestador de mantenimiento con resumen final
Archivo  : mantenimiento_preguntas.py
Ubicación:
    scripts/mantenimiento_preguntas.py

OBJETIVO
--------
Ejecutar secuencialmente los procesos periódicos de mantenimiento de las
preguntas incorporadas a la aplicación y mostrar al final un resumen único
de los cambios realizados.

PROCESO
-------
1. Crear una sesión de coste de mantenimiento.
2. Importar exámenes.
3. Importar tests desde PDF o capturas PNG agrupadas de GoFullPage.
4. Importar tests estructurados de la carpeta data_academia.
5. Importar tests estructurados de data_academia_texto.
6. Importar preguntas de informática.
7. Eliminar preguntas duplicadas.
8. Normalizar normas y artículos y clasificar TEORICA/PRACTICA.
9. Construir el catálogo de normas.
10. Enlazar las preguntas con el catálogo.
11. Auditar la base de datos.
12. Sincronizar todos los bancos y ejecutar la validación completa.
13. Cerrar la sesión y mostrar su coste y saldo estimado.

IMPORTANTE
----------
Este orquestador no ejecuta normalizar_articulos.py, porque ese script
modifica el campo original articulo.

La normalización y la clasificación se realizan mediante:

    scripts/enriquecer_preguntas.py --aplicar

Durante esta ejecución, openai_api.py acumula los costes en una sola fila de
la tabla sesiones_coste_mantenimiento. No añade una línea al CSV por llamada.
Las llamadas de IA realizadas fuera de este mantenimiento mantienen el
registro habitual en logs/costes_ia.csv.

El saldo es estimado: parte de 12.06 USD en la primera sesión y, después,
del saldo estimado de la última sesión finalizada correctamente.

===============================================================================
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
CARPETA_SCRIPTS = RAIZ / "scripts"
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
RUTA_LOG_MANTENIMIENTO = RAIZ / "logs" / "mantenimiento_preguntas.log"
SALDO_INICIAL_PRIMERA_SESION = 12.06
VARIABLE_SESION_MANTENIMIENTO = "OPOCOACH_MANTENIMIENTO_SESION_ID"


def asegurar_tabla_sesiones_coste() -> None:
    with sqlite3.connect(RUTA_DB) as conexion:
        conexion.execute(
            """
            CREATE TABLE IF NOT EXISTS sesiones_coste_mantenimiento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_inicio TEXT NOT NULL,
                fecha_fin TEXT,
                coste_total REAL NOT NULL DEFAULT 0,
                saldo_inicial REAL NOT NULL,
                saldo_estimado REAL NOT NULL,
                numero_llamadas INTEGER NOT NULL DEFAULT 0,
                estado TEXT NOT NULL DEFAULT 'EN_CURSO'
                    CHECK (estado IN ('EN_CURSO', 'OK', 'ERROR')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def obtener_saldo_inicial(conexion: sqlite3.Connection) -> float:
    fila = conexion.execute(
        """
        SELECT saldo_estimado
        FROM sesiones_coste_mantenimiento
        WHERE estado = 'OK'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    if fila is None:
        return SALDO_INICIAL_PRIMERA_SESION

    return float(fila[0])


def crear_sesion_coste(inicio: datetime) -> int:
    with sqlite3.connect(RUTA_DB) as conexion:
        saldo_inicial = obtener_saldo_inicial(conexion)

        cursor = conexion.execute(
            """
            INSERT INTO sesiones_coste_mantenimiento (
                fecha_inicio,
                coste_total,
                saldo_inicial,
                saldo_estimado,
                numero_llamadas,
                estado
            )
            VALUES (?, 0, ?, ?, 0, 'EN_CURSO')
            """,
            (
                inicio.isoformat(sep=" ", timespec="seconds"),
                saldo_inicial,
                saldo_inicial,
            ),
        )

        return int(cursor.lastrowid)


def finalizar_sesion_coste(
    sesion_id: int,
    estado: str,
    fin: datetime,
) -> None:
    if estado not in {"OK", "ERROR"}:
        raise ValueError(f"Estado de sesión no válido: {estado!r}")

    with sqlite3.connect(RUTA_DB) as conexion:
        cursor = conexion.execute(
            """
            UPDATE sesiones_coste_mantenimiento
            SET fecha_fin = ?,
                saldo_estimado = saldo_inicial - coste_total,
                estado = ?
            WHERE id = ?
              AND estado = 'EN_CURSO'
            """,
            (
                fin.isoformat(sep=" ", timespec="seconds"),
                estado,
                sesion_id,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"No se pudo cerrar la sesión de coste {sesion_id}."
            )


def obtener_datos_sesion(sesion_id: int) -> tuple[float, float, float, int]:
    with sqlite3.connect(RUTA_DB) as conexion:
        fila = conexion.execute(
            """
            SELECT
                coste_total,
                saldo_inicial,
                saldo_estimado,
                numero_llamadas
            FROM sesiones_coste_mantenimiento
            WHERE id = ?
            """,
            (sesion_id,),
        ).fetchone()

    if fila is None:
        raise RuntimeError(
            f"No existe la sesión de coste {sesion_id}."
        )

    return (
        float(fila[0]),
        float(fila[1]),
        float(fila[2]),
        int(fila[3]),
    )


def contar_preguntas() -> int:
    with sqlite3.connect(RUTA_DB) as conexion:
        return conexion.execute(
            "SELECT COUNT(*) FROM lote_preguntas"
        ).fetchone()[0]


def registrar_salida_paso(
    nombre: str,
    script: str,
    salida: str,
    returncode: int,
) -> None:
    RUTA_LOG_MANTENIMIENTO.parent.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with RUTA_LOG_MANTENIMIENTO.open("a", encoding="utf-8") as log:
        log.write("\n" + "=" * 78 + "\n")
        log.write(f"{marca} | {nombre} | {script}\n")
        log.write(f"Código de salida: {returncode}\n")
        log.write("-" * 78 + "\n")
        if salida:
            log.write(salida)
            if not salida.endswith("\n"):
                log.write("\n")


def ejecutar_paso(
    nombre: str,
    script: str,
    *argumentos: str,
    sesion_id: int,
    mostrar_cabecera: bool = True,
    mostrar_progreso_imagen: bool = False,
) -> tuple[bool, str]:
    ruta_script = CARPETA_SCRIPTS / script

    if not ruta_script.is_file():
        mensaje = f"ERROR: no existe {ruta_script}"
        registrar_salida_paso(nombre, script, mensaje, 1)
        print(f"\n{mensaje}")
        print(f"Consulta el log: {RUTA_LOG_MANTENIMIENTO}")
        return False, mensaje

    if mostrar_cabecera:
        print()
        print("=" * 78)
        print(nombre.upper())
        print("=" * 78)

    entorno = os.environ.copy()
    entorno[VARIABLE_SESION_MANTENIMIENTO] = str(sesion_id)
    # Contrato de codificación entre el orquestador y todos los scripts hijos.
    # Evita mojibake en Windows cuando stdout se captura mediante PIPE.
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["PYTHONUTF8"] = "1"

    if mostrar_progreso_imagen:
        proceso = subprocess.Popen(
            [sys.executable, str(ruta_script), *argumentos],
            cwd=RAIZ,
            env=entorno,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        lineas: list[str] = []
        assert proceso.stdout is not None
        for linea in proceso.stdout:
            lineas.append(linea)
            if linea.startswith("PROGRESO_IMPORTACION_IMAGEN|"):
                print(linea.split("|", 1)[1].rstrip(), flush=True)
        returncode = proceso.wait()
        salida = "".join(lineas)
    else:
        resultado = subprocess.run(
            [sys.executable, str(ruta_script), *argumentos],
            cwd=RAIZ,
            env=entorno,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        returncode = resultado.returncode
        salida = resultado.stdout or ""

    registrar_salida_paso(
        nombre,
        script,
        salida,
        returncode,
    )

    if returncode != 0:
        print(
            f"ERROR en {script}. "
            f"Código de salida: {returncode}"
        )
        print(f"Consulta el log: {RUTA_LOG_MANTENIMIENTO}")
        return False, salida

    return True, salida


def extraer_contador_salida(
    salida: str,
    *etiquetas: str,
) -> int:
    for etiqueta in etiquetas:
        patron = re.compile(
            rf"(?im)^\s*{re.escape(etiqueta)}\s*:\s*(\d+)\s*$"
        )
        coincidencia = patron.search(salida)
        if coincidencia:
            return int(coincidencia.group(1))
    return 0


def mostrar_resumen_importacion(
    importadas: int,
    ya_existentes: int,
    duplicadas_bd: int,
) -> None:
    print(f"Preguntas importadas................ {importadas}")
    print(f"Preguntas ya existentes............ {ya_existentes}")
    print(f"Preguntas duplicadas en BD......... {duplicadas_bd}")


def mostrar_resumen(
    nuevas_examenes: int,
    nuevas_tests: int,
    nuevas_academia: int,
    nuevas_academia_texto: int,
    nuevas_informatica: int,
    duplicados_eliminados: int,
    total_preguntas: int,
    inicio: datetime,
    fin: datetime,
    coste_sesion: float,
    saldo_inicial: float,
    saldo_estimado: float,
    numero_llamadas: int,
) -> None:
    lineas = [
        "=" * 78,
        "RESUMEN DEL MANTENIMIENTO",
        "=" * 78,
        f"Preguntas de exámenes añadidas: {nuevas_examenes}",
        f"Preguntas de tests añadidas: {nuevas_tests}",
        f"Preguntas de academia añadidas: {nuevas_academia}",
        f"Preguntas academia texto añadidas: {nuevas_academia_texto}",
        f"Preguntas de informática añadidas: {nuevas_informatica}",
        f"Duplicados eliminados: {duplicados_eliminados}",
        f"Preguntas totales: {total_preguntas}",
        f"Duración: {fin - inicio}",
        f"Llamadas IA: {numero_llamadas}",
        f"Coste IA de esta sesión: ${coste_sesion:.6f}",
        f"Saldo inicial: ${saldo_inicial:.6f}",
        f"Saldo estimado restante: ${saldo_estimado:.6f}",
        "Estado: OK",
    ]
    salida = "\n".join(lineas) + "\n"

    print()
    print(salida, end="")

    RUTA_LOG_MANTENIMIENTO.parent.mkdir(parents=True, exist_ok=True)
    with RUTA_LOG_MANTENIMIENTO.open("a", encoding="utf-8") as log:
        log.write("\n" + salida)


def main() -> int:
    inicio = datetime.now()

    if not RUTA_DB.is_file():
        print(f"No existe la base de datos: {RUTA_DB}")
        return 1

    asegurar_tabla_sesiones_coste()
    sesion_id = crear_sesion_coste(inicio)

    print("=" * 78)
    print("MANTENIMIENTO OPOCOACH")
    print("=" * 78)

    try:
        antes = contar_preguntas()

        ok, salida = ejecutar_paso(
            "Importar exámenes",
            "importar_examenes.py",
            sesion_id=sesion_id,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        despues = contar_preguntas()
        nuevas_examenes = max(0, despues - antes)
        duplicadas_examenes = extraer_contador_salida(
            salida,
            "Preguntas ya existentes",
        )
        mostrar_resumen_importacion(
            nuevas_examenes,
            antes,
            duplicadas_examenes,
        )

        antes = despues

        ok, salida = ejecutar_paso(
            "Importar tests desde PDF/PNG GoFullPage",
            "importar_tests_imagen.py",
            sesion_id=sesion_id,
            mostrar_progreso_imagen=True,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        despues = contar_preguntas()
        nuevas_tests = max(0, despues - antes)
        duplicadas_tests = extraer_contador_salida(
            salida,
            "Preguntas duplicadas",
        )
        mostrar_resumen_importacion(
            nuevas_tests,
            antes,
            duplicadas_tests,
        )

        antes = despues

        ok, salida = ejecutar_paso(
            "Importar tests de academia",
            "importar_tests_academia.py",
            sesion_id=sesion_id,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        despues = contar_preguntas()
        nuevas_academia = max(0, despues - antes)
        duplicadas_academia = extraer_contador_salida(
            salida,
            "Preguntas duplicadas",
        )
        mostrar_resumen_importacion(
            nuevas_academia,
            antes,
            duplicadas_academia,
        )

        antes = despues

        ok, salida = ejecutar_paso(
            "Importar tests de academia con explicación",
            "importar_tests_academia_texto.py",
            sesion_id=sesion_id,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        despues = contar_preguntas()
        nuevas_academia_texto = max(0, despues - antes)
        duplicadas_academia_texto = extraer_contador_salida(
            salida,
            "Preguntas duplicadas",
        )
        mostrar_resumen_importacion(
            nuevas_academia_texto,
            antes,
            duplicadas_academia_texto,
        )

        antes = despues

        ok, salida = ejecutar_paso(
            "Importar preguntas de informática",
            "importar_preguntas_informatica.py",
            sesion_id=sesion_id,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        despues = contar_preguntas()
        nuevas_informatica = max(0, despues - antes)
        duplicadas_informatica = extraer_contador_salida(
            salida,
            "Duplicadas en el banco",
        )
        mostrar_resumen_importacion(
            nuevas_informatica,
            antes,
            duplicadas_informatica,
        )

        antes_depuracion = despues

        ok, _ = ejecutar_paso(
            "Eliminar preguntas duplicadas",
            "depurar_preguntas.py",
            sesion_id=sesion_id,
            mostrar_cabecera=False,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        despues_depuracion = contar_preguntas()
        duplicados_eliminados = max(
            0,
            antes_depuracion - despues_depuracion,
        )

        ok, _ = ejecutar_paso(
            "Normalizar normas y artículos y clasificar TEORICA/PRACTICA",
            "enriquecer_preguntas.py",
            "--aplicar",
            sesion_id=sesion_id,
            mostrar_cabecera=False,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        ok, _ = ejecutar_paso(
            "Construir catálogo de normas",
            "construir_catalogo_normas.py",
            sesion_id=sesion_id,
            mostrar_cabecera=False,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        ok, _ = ejecutar_paso(
            "Enlazar preguntas con el catálogo de normas",
            "enlazar_normas.py",
            sesion_id=sesion_id,
            mostrar_cabecera=False,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        ok, _ = ejecutar_paso(
            "Auditar base de datos",
            "auditar_bd.py",
            sesion_id=sesion_id,
            mostrar_cabecera=False,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        ok, salida_sincronizacion = ejecutar_paso(
            "Sincronizar todos los bancos y validar",
            "sincronizar_bancos.py",
            "--aplicar",
            sesion_id=sesion_id,
        )
        if not ok:
            finalizar_sesion_coste(sesion_id, "ERROR", datetime.now())
            return 1

        # Esta es la fase de cierre del mantenimiento y debe ser visible.
        if salida_sincronizacion.strip():
            print(salida_sincronizacion.rstrip())

        fin = datetime.now()
        total_preguntas = contar_preguntas()

        finalizar_sesion_coste(
            sesion_id=sesion_id,
            estado="OK",
            fin=fin,
        )

        (
            coste_sesion,
            saldo_inicial,
            saldo_estimado,
            numero_llamadas,
        ) = obtener_datos_sesion(sesion_id)

        mostrar_resumen(
            nuevas_examenes=nuevas_examenes,
            nuevas_tests=nuevas_tests,
            nuevas_academia=nuevas_academia,
            nuevas_academia_texto=nuevas_academia_texto,
            nuevas_informatica=nuevas_informatica,
            duplicados_eliminados=duplicados_eliminados,
            total_preguntas=total_preguntas,
            inicio=inicio,
            fin=fin,
            coste_sesion=coste_sesion,
            saldo_inicial=saldo_inicial,
            saldo_estimado=saldo_estimado,
            numero_llamadas=numero_llamadas,
        )

        return 0

    except Exception as exc:
        try:
            finalizar_sesion_coste(
                sesion_id=sesion_id,
                estado="ERROR",
                fin=datetime.now(),
            )
        except Exception:
            pass

        print(f"\nERROR inesperado en el mantenimiento: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())