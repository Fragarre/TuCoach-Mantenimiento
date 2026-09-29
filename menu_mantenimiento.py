"""
TuCoach - Menú de mantenimiento

Este menú no modifica la estructura del proyecto ni las rutas de los scripts.
Se limita a ejecutar los scripts existentes desde la raíz del proyecto.

Ubicación prevista:
    menu_mantenimiento.py

Ejecución:
    python menu_mantenimiento.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import shutil
import subprocess
import sys
from pathlib import Path


RAIZ = Path(__file__).resolve().parent
CARPETA_SCRIPTS = RAIZ / "scripts"


def limpiar_pantalla() -> None:
    """Limpia la consola al cambiar de pantalla de menú."""
    os.system("cls" if os.name == "nt" else "clear")


def pausa() -> None:
    input("\nPulsa INTRO para continuar...")


def pedir_texto(mensaje: str, obligatorio: bool = True) -> str:
    while True:
        valor = input(mensaje).strip()
        if valor or not obligatorio:
            return valor
        print("El valor es obligatorio.")


def pedir_si_no(mensaje: str, predeterminado: bool = False) -> bool:
    sufijo = " [S/n]: " if predeterminado else " [s/N]: "

    while True:
        valor = input(mensaje + sufijo).strip().lower()

        if not valor:
            return predeterminado
        if valor in {"s", "si", "sí"}:
            return True
        if valor in {"n", "no"}:
            return False

        print("Responde S o N.")


def ejecutar_script(nombre: str, *argumentos: str) -> int:
    ruta = CARPETA_SCRIPTS / nombre

    if not ruta.is_file():
        print(f"\nERROR: no existe el script:\n{ruta}")
        return 1

    comando = [sys.executable, str(ruta), *argumentos]

    print("\n" + "=" * 78)
    print("EJECUCIÓN")
    print("=" * 78)
    print(" ".join(f'"{x}"' if " " in x else x for x in comando))
    print("=" * 78 + "\n")

    resultado = subprocess.run(
        comando,
        cwd=RAIZ,
        check=False,
    )

    print("\n" + "=" * 78)
    if resultado.returncode == 0:
        print("Proceso terminado correctamente.")
    else:
        print(f"Proceso terminado con código de error {resultado.returncode}.")
    print("=" * 78)

    return resultado.returncode



def ejecutar_script_supabase(nombre: str, *argumentos: str) -> int:
    ruta = CARPETA_SCRIPTS / nombre

    if not ruta.is_file():
        print(f"\nERROR: no existe el script:\n{ruta}")
        return 1

    candidatos = [[sys.executable]]

    python311 = Path(r"C:\Program Files\Python311\python.exe")
    if python311.is_file():
        candidatos.append([str(python311)])

    py_launcher = shutil.which("py")
    if py_launcher:
        candidatos.append([py_launcher, "-3.11"])

    interprete = None
    probados = []

    for candidato in candidatos:
        etiqueta = " ".join(candidato)
        if etiqueta in probados:
            continue
        probados.append(etiqueta)

        prueba = subprocess.run(
            [*candidato, "-c", "import psycopg"],
            cwd=RAIZ,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if prueba.returncode == 0:
            interprete = candidato
            break

    if interprete is None:
        print("\nERROR: no se encontró ningún Python con psycopg disponible.")
        print("Intérpretes comprobados:")
        for candidato in probados:
            print(f"  - {candidato}")
        return 1

    comando = [*interprete, str(ruta), *argumentos]

    print("\n" + "=" * 78)
    print("EJECUCIÓN")
    print("=" * 78)
    print("Python Supabase: " + " ".join(interprete))
    print(" ".join(f'"{x}"' if " " in x else x for x in comando))
    print("=" * 78 + "\n")

    resultado = subprocess.run(
        comando,
        cwd=RAIZ,
        check=False,
    )

    print("\n" + "=" * 78)
    if resultado.returncode == 0:
        print("Proceso terminado correctamente.")
    else:
        print(f"Proceso terminado con código de error {resultado.returncode}.")
    print("=" * 78)

    return resultado.returncode


def argumentos_convocatoria() -> list[str]:
    while True:
        print("\nIdentificación de la convocatoria")
        print("1. Por ID")
        print("2. Por código")

        opcion = input("Opción: ").strip()

        if opcion == "1":
            valor = pedir_texto("ID de la convocatoria: ")
            if valor.isdigit() and int(valor) > 0:
                return ["--convocatoria-id", valor]
            print("El ID debe ser un número entero mayor que cero.")

        elif opcion == "2":
            codigo = pedir_texto("Código de la convocatoria: ")
            return ["--codigo", codigo]

        else:
            print("Opción no válida.")


def pedir_entero_positivo(mensaje: str) -> int:
    while True:
        valor = input(mensaje).strip()
        if valor.isdigit() and int(valor) > 0:
            return int(valor)
        print("Debe introducir un número entero mayor que cero.")


def pedir_entero_no_negativo(mensaje: str) -> int:
    while True:
        valor = input(mensaje).strip()
        if valor.isdigit():
            return int(valor)
        print("Debe introducir un número entero igual o mayor que cero.")


def pedir_numero_decimal(mensaje: str, predeterminado: float) -> float:
    while True:
        valor = input(f"{mensaje} [{predeterminado}]: ").strip()
        if not valor:
            return predeterminado
        try:
            return float(valor.replace(",", "."))
        except ValueError:
            print("Debe introducir un número válido.")


def crear_json_convocatoria() -> Path | None:
    print("\nCREAR UNA CONVOCATORIA NUEVA")
    print("-" * 78)

    codigo = pedir_texto(
        "Código de la convocatoria, por ejemplo C2-01_70_26: "
    )
    puesto = pedir_texto("Puesto: ")
    numero = pedir_texto("Número de convocatoria, por ejemplo 70/26: ")
    anio = pedir_entero_positivo("Año: ")
    numero_preguntas = pedir_entero_positivo(
        "Número total de preguntas: "
    )

    partes: list[dict[str, object]] = []
    suma_partes = 0
    orden = 1

    print("\nDefinición de las partes del examen")

    while True:
        nombre = pedir_texto(f"Nombre de la parte {orden}: ")
        cantidad = pedir_entero_no_negativo(
            f"Número de preguntas de '{nombre}': "
        )
        partes.append(
            {
                "nombre": nombre,
                "numero_preguntas": cantidad,
                "orden": orden,
            }
        )
        suma_partes += cantidad
        orden += 1

        if not pedir_si_no("¿Añadir otra parte?"):
            break

    if suma_partes != numero_preguntas:
        print(
            "\nERROR: la suma de preguntas de las partes "
            f"es {suma_partes}, pero el total indicado "
            f"es {numero_preguntas}."
        )
        print("No se ha creado el JSON.")
        return None

    print("\nValoración del examen")
    acierto = pedir_numero_decimal("Valor del acierto", 1.0)
    fallo = pedir_numero_decimal("Valor del fallo", 0.3333)
    no_contesta = pedir_numero_decimal(
        "Valor de la pregunta no contestada",
        0.0,
    )
    factor_escala = pedir_numero_decimal("Factor de escala", 10.0)

    carpeta_relativa = Path("data_convocatorias") / f"CONV_{codigo}"
    carpeta_absoluta = RAIZ / carpeta_relativa
    ruta_json = carpeta_absoluta / "convocatoria.json"
    ruta_temario = carpeta_relativa / "temario.csv"

    datos = {
        "convocatoria": {
            "puesto": puesto,
            "numero": numero,
            "anio": anio,
            "codigo": codigo,
            "numero_preguntas": numero_preguntas,
            "tiene_partes": 1 if partes else 0,
            "valoracion_test_acierto": acierto,
            "valoracion_test_fallo": fallo,
            "valoracion_test_no_contesta": no_contesta,
            "formula_nota": (
                "(acertadas - falladas * valoracion_test_fallo) "
                "/ numero_preguntas"
            ),
            "factor_escala_nota": factor_escala,
            "temario_csv": ruta_temario.as_posix(),
        },
        "partes": partes,
        "temario": {
            "csv": ruta_temario.as_posix(),
            "nombre": "Temario oficial",
            "encoding": None,
            "sincronizar_eliminaciones": False,
        },
    }

    print("\nRESUMEN")
    print("-" * 78)
    print(f"Código:           {codigo}")
    print(f"Puesto:           {puesto}")
    print(f"Número:           {numero}")
    print(f"Año:              {anio}")
    print(f"Preguntas:        {numero_preguntas}")
    print(f"Carpeta:          {carpeta_relativa.as_posix()}")
    print(f"JSON:             {ruta_json}")
    print(f"Temario previsto: {ruta_temario.as_posix()}")

    if not pedir_si_no("¿Crear este JSON?"):
        print("Operación cancelada.")
        return None

    if ruta_json.exists():
        print(f"\nYa existe el fichero:\n{ruta_json}")
        if not pedir_si_no("¿Sobrescribirlo?"):
            print("No se ha modificado el fichero existente.")
            return None

    carpeta_absoluta.mkdir(parents=True, exist_ok=True)
    ruta_json.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\nJSON creado correctamente:\n{ruta_json}")
    return ruta_json


def procesar_alta_desde_json(config: Path) -> None:
    if ejecutar_script(
        "alta_convocatoria_orquestador.py",
        "--config",
        str(config),
        "--solo-validar",
    ) != 0:
        return

    if not pedir_si_no(
        "La validación ha terminado. ¿Ejecutar el alta?"
    ):
        return

    argumentos = ["--config", str(config)]

    if pedir_si_no("¿Actualizar una convocatoria existente?"):
        argumentos.append("--actualizar-existente")

    ejecutar_script(
        "alta_convocatoria_orquestador.py",
        *argumentos,
    )



def extraer_temario_convocatoria() -> None:
    print("\nEXTRAER TEMARIO DESDE PDF")
    print("-" * 78)
    print(
        "El proceso genera un temario.csv a partir del PDF oficial. "
        "Las referencias que no puedan resolverse con seguridad quedarán "
        "como PENDIENTE para revisión manual."
    )

    pdf = Path(
        pedir_texto("Ruta del PDF oficial del temario: ")
    ).expanduser()
    if not pdf.is_absolute():
        pdf = RAIZ / pdf
    pdf = pdf.resolve()

    if not pdf.is_file():
        print(f"\nERROR: no existe el PDF:\n{pdf}")
        pausa()
        return

    salida_sugerida = pdf.with_name("temario.csv")
    salida_texto = pedir_texto(
        f"Ruta del CSV de salida [{salida_sugerida}]: ",
        obligatorio=False,
    )

    salida = (
        Path(salida_texto).expanduser()
        if salida_texto
        else salida_sugerida
    )
    if not salida.is_absolute():
        salida = RAIZ / salida
    salida = salida.resolve()

    if salida.exists():
        print(f"\nYa existe el fichero:\n{salida}")
        if not pedir_si_no("¿Sobrescribirlo?"):
            print("Operación cancelada.")
            pausa()
            return

    print("\nParámetros opcionales aplicados:")
    print(f"--pdf    {pdf}")
    print(f"--salida {salida}")

    if pedir_si_no("¿Ejecutar la extracción?"):
        ejecutar_script(
            "extraer_temario_convocatoria.py",
            "--pdf",
            str(pdf),
            "--salida",
            str(salida),
        )

    pausa()

def alta_convocatoria() -> None:
    print("\nALTA DE CONVOCATORIA")
    print("1. Crear una convocatoria nueva")
    print("2. Utilizar un JSON existente")
    print("0. Volver")

    opcion = input("Opción: ").strip()

    if opcion == "0":
        return

    if opcion == "1":
        ruta_json = crear_json_convocatoria()

        if ruta_json is None:
            pausa()
            return

        if pedir_si_no(
            "¿Validar y ejecutar ahora el alta de convocatoria?"
        ):
            procesar_alta_desde_json(ruta_json)

        pausa()
        return

    if opcion == "2":
        config = Path(
            pedir_texto("Ruta del JSON de configuración: ")
        ).expanduser()

        if not config.is_absolute():
            config = RAIZ / config

        config = config.resolve()

        if not config.is_file():
            print(f"\nERROR: no existe el fichero JSON:\n{config}")
            pausa()
            return

        procesar_alta_desde_json(config)
        pausa()
        return

    print("Opción no válida.")
    pausa()


def gestionar_estado_convocatoria_menu() -> None:
    cabecera_submenu(
        "BAJA / REACTIVAR CONVOCATORIA",
        "La baja es lógica: oculta la convocatoria de los procesos operativos y "
        "elimina únicamente sus vínculos de banco. Conserva temario, configuración "
        "histórica, lote de preguntas y corpus normativo/RAG.",
    )

    if ejecutar_script("gestionar_estado_convocatoria.py", "--listar") != 0:
        pausa()
        return

    cid = pedir_texto("ID de convocatoria: ")
    if not cid.isdigit() or int(cid) <= 0:
        print("ID no válido.")
        pausa()
        return

    print("\nOperación")
    print("1. Dar de baja")
    print("2. Reactivar")
    print("0. Cancelar")
    op = input("Opción: ").strip()
    if op == "0":
        return
    if op not in {"1", "2"}:
        print("Opción no válida.")
        pausa()
        return

    accion = "--baja" if op == "1" else "--reactivar"
    args = ["--convocatoria-id", cid, accion]

    print("\nPrimero se ejecutará una vista previa de solo lectura.")
    if ejecutar_script("gestionar_estado_convocatoria.py", *args) != 0:
        pausa()
        return

    texto = (
        "¿Aplicar la BAJA? Se creará backup y se eliminarán solo los vínculos del banco"
        if op == "1"
        else "¿Reactivar la convocatoria? Se creará backup"
    )
    if pedir_si_no(texto):
        if ejecutar_script("gestionar_estado_convocatoria.py", *args, "--aplicar") == 0:
            if op == "2":
                print(
                    "\nLa convocatoria está activa de nuevo. "
                    "Ejecute después 'Sincronizar todos los bancos' y la validación completa."
                )
    pausa()


def construir_corpus() -> None:
    cabecera_submenu(
        "CONSTRUIR CORPUS DE CONVOCATORIA",
        "Construye de forma encadenada los dos corpus: artículos citados por el "
        "temario para generación IA y normas completas del mismo temario para el RAG del Chat.",
    )
    argumentos = argumentos_convocatoria()

    print("\nModo")
    print("1. Solo validar el estado actual                         [SOLO LECTURA]")
    print("2. Construir/completar ambos corpus                     [ESCRIBE · BACKUP]")
    print("0. Cancelar")
    opcion = input("Opción: ").strip()

    if opcion == "0":
        return
    if opcion not in {"1", "2"}:
        print("Opción no válida.")
        pausa()
        return

    if pedir_si_no("¿Reintentar referencias pendientes?"):
        argumentos.append("--reintentar-pendientes")

    if opcion == "2":
        argumentos.append("--aplicar")
        print(
            "\nSe construirá primero el corpus IA. Solo si queda completo se ampliarán "
            "las normas del RAG correspondientes exclusivamente a esta convocatoria."
        )
        if not pedir_si_no("¿Continuar con la construcción de ambos corpus?"):
            pausa()
            return

    ejecutar_script(
        "construir_corpus_doble_convocatoria.py",
        *argumentos,
    )
    pausa()


def mantenimiento_preguntas() -> None:
    print(
        "\nMANTENIMIENTO COMPLETO. Se ejecutarán: todas las importaciones, "
        "depuración de duplicados, normalización/clasificación, catálogo y enlaces, "
        "auditoría de la base, sincronización de TODOS los bancos y validación completa."
    )
    print(
        "\nSi cualquier fase falla, el proceso se detiene. "
        "No es necesario actualizar después los bancos convocatoria por convocatoria."
    )

    if pedir_si_no("¿Continuar con el mantenimiento completo?"):
        ejecutar_script("mantenimiento_preguntas.py")

    pausa()

def recuperar_pendientes() -> None:
    print(
        "\nSe ejecutará la recuperación de preguntas pendientes por búsqueda y auditoría."
    )
    print("\nDespués se ejecutarán automáticamente:")
    print("1. enriquecer_preguntas.py --aplicar")
    print("2. construir_catalogo_normas.py")
    print("3. enlazar_normas.py")
    print("4. auditar_bd.py")
    print("5. sincronizar TODOS los bancos")
    print("6. validacion_completa.py")

    limite = pedir_texto(
        "Número máximo de preguntas a procesar [20]: ",
        obligatorio=False,
    )
    if not limite:
        limite = "20"
    elif not limite.isdigit() or int(limite) <= 0:
        print("El límite debe ser un número entero mayor que cero.")
        pausa()
        return

    argumentos = ["--limite", limite, "--aplicar"]

    if pedir_si_no("¿Reintentar también las NO_RESUELTAS anteriores?"):
        argumentos.append("--reintentar-no-resueltas")

    if not pedir_si_no("¿Continuar y aplicar las recuperaciones?"):
        pausa()
        return

    pasos = [
        ("recuperar_pendientes_busqueda_auditoria.py", argumentos),
        ("enriquecer_preguntas.py", ["--aplicar"]),
        ("construir_catalogo_normas.py", []),
        ("enlazar_normas.py", []),
        ("auditar_bd.py", []),
        ("sincronizar_bancos.py", ["--aplicar"]),
    ]
    for script, args in pasos:
        if ejecutar_script(script, *args) != 0:
            pausa()
            return
    pausa()

def actualizar_catalogo_y_enlaces() -> None:
    print(
        "\nUtilidad manual para reconstruir el catálogo de normas y sus enlaces "
        "después de una corrección manual de norma o artículo."
    )
    print("\nSe ejecutarán, en este orden:")
    print("1. construir_catalogo_normas.py")
    print("2. enlazar_normas.py")

    if not pedir_si_no("¿Continuar?"):
        pausa()
        return

    if ejecutar_script("construir_catalogo_normas.py") == 0:
        ejecutar_script("enlazar_normas.py")

    pausa()


def actualizar_banco() -> None:
    argumentos = argumentos_convocatoria()

    print("\nPrimero se ejecutará una vista previa.")
    if ejecutar_script(
        "mantener_banco_preguntas.py",
        *argumentos,
    ) != 0:
        pausa()
        return

    if pedir_si_no(
        "¿Guardar las nuevas vinculaciones en el banco?"
    ):
        ejecutar_script(
            "mantener_banco_preguntas.py",
            *argumentos,
            "--guardar",
        )

    pausa()


def auditar_banco() -> None:
    convocatoria_id = pedir_texto("ID de la convocatoria: ")

    if not convocatoria_id.isdigit() or int(convocatoria_id) <= 0:
        print("El ID debe ser un número entero mayor que cero.")
        pausa()
        return

    ejecutar_script(
        "auditar_banco_preguntas.py",
        "--convocatoria-id",
        convocatoria_id,
    )
    pausa()


def buscar_preguntas() -> None:
    norma = pedir_texto("Nombre normalizado exacto de la norma: ")
    articulo = pedir_texto("Artículo: ")

    argumentos = [norma, articulo]

    if pedir_si_no("¿Mostrar los identificadores encontrados?"):
        argumentos.append("--mostrar")

    ejecutar_script(
        "buscador_preguntas.py",
        *argumentos,
    )
    pausa()


def revisar_importaciones() -> None:
    argumentos: list[str] = []

    ids = pedir_texto(
        "IDs separados por comas, o INTRO para revisar las problemáticas: ",
        obligatorio=False,
    )

    if ids:
        for valor in ids.split(","):
            valor = valor.strip()
            if not valor.isdigit() or int(valor) <= 0:
                print(f"ID no válido: {valor}")
                pausa()
                return
            argumentos.extend(["--id", valor])

    elif pedir_si_no(
        "¿Incluir también las importaciones completadas correctamente?"
    ):
        argumentos.append("--todas")

    ejecutar_script(
        "revisar_importaciones.py",
        *argumentos,
    )
    pausa()


def listar_pendientes() -> None:
    ejecutar_script("listar_preguntas_pendientes.py")
    pausa()


def modificar_pregunta_manual() -> None:
    pregunta_id = pedir_texto(
        "ID de la pregunta, o INTRO para introducirlo después: ",
        obligatorio=False,
    )

    argumentos: list[str] = []

    if pregunta_id:
        if not pregunta_id.isdigit() or int(pregunta_id) <= 0:
            print("El ID debe ser un número entero mayor que cero.")
            pausa()
            return
        argumentos.extend(["--id", pregunta_id])

    ejecutar_script(
        "modificar_pregunta_manual.py",
        *argumentos,
    )
    pausa()


def mostrar_resumen_lote_preguntas() -> None:
    ejecutar_script("mostrar_resumen_lote_preguntas.py")
    pausa()


def mostrar_resumen_banco_convocatoria() -> None:
    convocatoria_id = pedir_texto("ID de la convocatoria: ")

    if not convocatoria_id.isdigit() or int(convocatoria_id) <= 0:
        print("El ID debe ser un número entero mayor que cero.")
        pausa()
        return

    ejecutar_script(
        "mostrar_resumen_banco_convocatoria.py",
        "--convocatoria-id",
        convocatoria_id,
    )
    pausa()




def auditar_vigencia_preguntas() -> None:
    print("\nAUDITAR VIGENCIA DE PREGUNTAS JURÍDICAS")
    print("-" * 78)
    print("Se comprobará conservadoramente la vigencia jurídica.")
    print("Solo los estados que empiezan por OBSOLETA se excluyen de los bancos.")
    print("Los casos no concluyentes quedan como NO_VERIFICABLE y NO se excluyen.")
    print("El detalle de NO_VERIFICABLE se exporta a registros/.")

    argumentos: list[str] = []

    if pedir_si_no(
        "¿Revisar únicamente las que actualmente son NO_VERIFICABLE?"
    ):
        argumentos.append("--solo-no-verificables")

    pregunta_id = pedir_texto(
        "ID de una única pregunta, o INTRO para todas: ",
        obligatorio=False,
    )
    if pregunta_id:
        if not pregunta_id.isdigit() or int(pregunta_id) <= 0:
            print("ID no válido.")
            pausa()
            return
        argumentos.extend(["--pregunta-id", pregunta_id])
    else:
        limite = pedir_texto(
            "Límite de preguntas, o INTRO sin límite: ",
            obligatorio=False,
        )
        if limite:
            if not limite.isdigit() or int(limite) <= 0:
                print("Límite no válido.")
                pausa()
                return
            argumentos.extend(["--limite", limite])

    if pedir_si_no("¿Aplicar los estados a lote_preguntas?"):
        argumentos.append("--aplicar")

    ejecutar_script(
        "auditar_vigencia_preguntas.py",
        *argumentos,
    )
    pausa()

def publicar_bd_streamlit_git(carpeta_streamlit: Path) -> bool:
    """Publica exclusivamente la SQLite de Streamlit en origin/main."""
    bd_relativa = "db/oposiciones.sqlite3"
    origin_esperado = "https://github.com/Fragarre/TuCoach-Streamlit.git"

    def git(*args: str, capturar: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=carpeta_streamlit,
            check=False,
            text=True,
            capture_output=capturar,
        )

    print("\nCOMPROBACION PREVIA DE GIT / STREAMLIT CLOUD")
    print("-" * 78)

    rama = git("branch", "--show-current")
    if rama.returncode != 0 or rama.stdout.strip() != "main":
        print("ERROR: TuCoach-Streamlit no esta en la rama main.")
        return False

    origin = git("remote", "get-url", "origin")
    if origin.returncode != 0 or origin.stdout.strip().rstrip("/") != origin_esperado.rstrip("/"):
        print("ERROR: el remote origin de TuCoach-Streamlit no es el esperado.")
        return False

    fetch = git("fetch", "origin", capturar=False)
    if fetch.returncode != 0:
        print("ERROR: git fetch origin ha fallado.")
        return False

    divergencia = git(
        "rev-list",
        "--left-right",
        "--count",
        "HEAD...origin/main",
    )
    if divergencia.returncode != 0:
        print("ERROR: no se pudo determinar la divergencia con origin/main.")
        return False

    partes = divergencia.stdout.split()
    if len(partes) != 2 or not all(x.isdigit() for x in partes):
        print("ERROR: respuesta Git inesperada al comprobar divergencia.")
        return False

    locales, remotos = map(int, partes)

    if (locales, remotos) == (1, 0):
        estado_pendiente = git("status", "--porcelain", "--untracked-files=all")
        staged_pendiente = git("diff", "--cached", "--name-only")
        if estado_pendiente.returncode != 0 or staged_pendiente.returncode != 0:
            print("ERROR: no se pudo validar el commit local pendiente.")
            return False
        if estado_pendiente.stdout.strip() or staged_pendiente.stdout.strip():
            print("ERROR: hay cambios adicionales junto al commit local pendiente.")
            return False

        mensaje_pendiente = git("log", "-1", "--pretty=%s")
        archivos_pendientes = git(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "HEAD",
        )
        padre_pendiente = git("rev-parse", "HEAD^")
        remoto_actual = git("rev-parse", "origin/main")

        if any(
            resultado.returncode != 0
            for resultado in (
                mensaje_pendiente,
                archivos_pendientes,
                padre_pendiente,
                remoto_actual,
            )
        ):
            print("ERROR: no se pudo identificar con seguridad el commit pendiente.")
            return False

        archivos = [
            x.strip()
            for x in archivos_pendientes.stdout.splitlines()
            if x.strip()
        ]
        commit_seguro = (
            mensaje_pendiente.stdout.strip() == "Actualizar contenidos Streamlit"
            and archivos == [bd_relativa]
            and padre_pendiente.stdout.strip() == remoto_actual.stdout.strip()
        )
        if not commit_seguro:
            print("ERROR: el commit local pendiente no pertenece a esta automatizacion.")
            print("No se realiza ningun push automatico.")
            return False

        print("\nHay un commit de Streamlit pendiente de publicar.")
        if not pedir_si_no("Reintentar git push origin main?"):
            print("Publicacion pendiente conservada sin cambios.")
            return False

        push_pendiente = git("push", "origin", "main", capturar=False)
        if push_pendiente.returncode != 0:
            print("ERROR: el reintento de git push ha fallado.")
            return False

        if git("fetch", "origin", capturar=False).returncode != 0:
            print("ERROR: push realizado, pero fallo la verificacion remota.")
            return False

        divergencia_final = git(
            "rev-list",
            "--left-right",
            "--count",
            "HEAD...origin/main",
        )
        if divergencia_final.returncode != 0 or divergencia_final.stdout.split() != ["0", "0"]:
            print("ERROR: no se pudo confirmar la sincronizacion tras el push.")
            return False

        print("Commit pendiente publicado y verificado.")
        return True

    if (locales, remotos) != (0, 0):
        print(
            "ERROR: divergencia Git no segura: "
            f"locales={locales}, remotos={remotos}. No se publica."
        )
        return False

    staged_previo = git("diff", "--cached", "--name-only")
    if staged_previo.returncode != 0:
        print("ERROR: no se pudo comprobar el staging previo.")
        return False
    if staged_previo.stdout.strip():
        print("ERROR: ya existen archivos en staging. No se publica.")
        return False

    estado = git("status", "--porcelain", "--untracked-files=all")
    if estado.returncode != 0:
        print("ERROR: no se pudo comprobar el estado Git.")
        return False

    lineas = [line for line in estado.stdout.splitlines() if line.strip()]
    permitidas = {f" M {bd_relativa}", f"M  {bd_relativa}"}
    inesperadas = [line for line in lineas if line not in permitidas]

    if inesperadas:
        print("ERROR: hay cambios inesperados en TuCoach-Streamlit:")
        for line in inesperadas:
            print(f"  {line}")
        print("No se ha preparado ni publicado ningun commit.")
        return False

    if not lineas:
        print("La base de Streamlit ya coincide con Git. No hay nada que publicar.")
        return True

    print(f"Unico cambio detectado: {bd_relativa}")
    if not pedir_si_no("Publicar esta base en GitHub / Streamlit Cloud?"):
        print("Publicacion Cloud cancelada. La copia local se conserva.")
        return True

    add = git("add", "--", bd_relativa, capturar=False)
    if add.returncode != 0:
        print("ERROR: git add de la SQLite ha fallado.")
        return False

    staged = git("diff", "--cached", "--name-only")
    staged_files = [x.strip() for x in staged.stdout.splitlines() if x.strip()]
    if staged.returncode != 0 or staged_files != [bd_relativa]:
        git("reset", "--", bd_relativa, capturar=False)
        print("ERROR: el staging no contiene exclusivamente la SQLite esperada.")
        return False

    commit = git("commit", "-m", "Actualizar contenidos Streamlit", capturar=False)
    if commit.returncode != 0:
        git("reset", "--", bd_relativa, capturar=False)
        print("ERROR: no se pudo crear el commit de Streamlit.")
        print("La SQLite se ha devuelto a estado no staged.")
        return False

    push = git("push", "origin", "main", capturar=False)
    if push.returncode != 0:
        print("ERROR: el commit existe localmente, pero git push ha fallado.")
        print("Revisa el repositorio antes de volver a publicar.")
        return False

    if git("fetch", "origin", capturar=False).returncode != 0:
        print("ERROR: push realizado, pero fallo la verificacion remota final.")
        return False

    head_final = git("rev-parse", "HEAD")
    remoto_final = git("rev-parse", "origin/main")
    if (
        head_final.returncode != 0
        or remoto_final.returncode != 0
        or head_final.stdout.strip() != remoto_final.stdout.strip()
    ):
        print("ERROR: no se pudo confirmar HEAD == origin/main tras el push.")
        return False

    print("\nPublicacion Git completada y verificada.")
    print(f"Commit: {head_final.stdout.strip()}")
    print("Streamlit Cloud recibira esta revision desde GitHub.")
    return True


def actualizar_bd_tucoach() -> None:
    """
    Copia la base de datos de TuCoach-Mantenimiento a TuCoach.

    Antes de sustituir la base de destino, crea una copia de seguridad en:
        TuCoach/db/copias_seguridad/
    """
    origen = RAIZ / "db" / "oposiciones.sqlite3"
    carpeta_tucoach = RAIZ.parent / "TuCoach-Streamlit"
    destino = carpeta_tucoach / "db" / "oposiciones.sqlite3"
    carpeta_copias = carpeta_tucoach / "db" / "copias_seguridad"

    print("\nACTUALIZAR BASE DE DATOS DE TUCOACH STREAMLIT")
    print("-" * 78)
    print(f"Origen:  {origen}")
    print(f"Destino: {destino}")

    print("\nValidación obligatoria previa al despliegue...")
    if ejecutar_script("validacion_completa.py") != 0:
        print(
            "\nERROR: la base de mantenimiento no supera la validación completa. "
            "No se copia a TuCoach."
        )
        pausa()
        return

    if not origen.is_file():
        print(f"\nERROR: no existe la base de datos de origen:\n{origen}")
        pausa()
        return

    if not carpeta_tucoach.is_dir():
        print(f"\nERROR: no existe la carpeta del proyecto TuCoach:\n{carpeta_tucoach}")
        pausa()
        return

    if not destino.parent.is_dir():
        print(f"\nERROR: no existe la carpeta de destino:\n{destino.parent}")
        pausa()
        return

    print(
        "\nSe sustituirá la base de datos de TuCoach por la versión "
        "actual de TuCoach-Mantenimiento."
    )

    if not pedir_si_no("¿Continuar?"):
        print("Operación cancelada.")
        pausa()
        return

    try:
        if destino.is_file():
            carpeta_copias.mkdir(parents=True, exist_ok=True)
            copia = carpeta_copias / "oposiciones_backup_unico.sqlite3"
            shutil.copy2(destino, copia)
            print(f"\nCopia de seguridad creada:\n{copia}")
        else:
            print(
                "\nAVISO: no existe una base de datos previa en el destino. "
                "Se creará una nueva copia."
            )

        shutil.copy2(origen, destino)

        if not destino.is_file():
            raise RuntimeError(
                "La copia terminó sin crear el archivo de destino."
            )

        if origen.stat().st_size != destino.stat().st_size:
            raise RuntimeError(
                "El tamaño del archivo copiado no coincide con el origen."
            )

        def sha256(ruta: Path) -> str:
            h = hashlib.sha256()
            with ruta.open("rb") as f:
                for bloque in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(bloque)
            return h.hexdigest()

        hash_origen = sha256(origen)
        hash_destino = sha256(destino)
        if hash_origen != hash_destino:
            raise RuntimeError(
                "La copia no es identica al origen: SHA256 diferente."
            )

        print("\nBase de datos actualizada correctamente.")
        print(f"Archivo actualizado:\n{destino}")
        print(f"SHA256 verificado: {hash_destino}")

        publicar_bd_streamlit_git(carpeta_tucoach)

    except Exception as error:
        print(f"\nERROR al actualizar la base de datos: {error}")

    pausa()




def preparar_publicacion_web_menu() -> None:
    """
    Genera una versión íntegra, validada y versionada de la base maestra
    para TuCoach-Web. NO modifica la Web.
    """
    cabecera_submenu(
        "PREPARAR PUBLICACIÓN DE CONTENIDOS TUCOACH-WEB",
        "[VALIDA → SNAPSHOT] Ejecuta la validación completa y genera una copia "
        "íntegra/versionada de db/oposiciones.sqlite3. No despliega nada.",
    )

    print("Se ejecutará:")
    print("1. validacion_completa.py")
    print("2. snapshot SQLite consistente de TODA la base")
    print("3. validación del snapshot")
    print("4. informes JSON/TXT y SHA256")
    print()
    print("Destino previsto: publicaciones_web/<version>/")
    print("TuCoach-Web NO se modificará.")

    if pedir_si_no("¿Preparar una nueva publicación para TuCoach-Web?"):
        ejecutar_script("publicar_contenidos_web.py")

    pausa()


def _listar_publicaciones_web() -> list[tuple[Path, Path, str]]:
    """
    Devuelve publicaciones preparadas válidas a nivel de estructura de ficheros:
    (snapshot, informe_json, version).
    La validación criptográfica/SQLite definitiva la hace el script de despliegue.
    """
    carpeta = RAIZ / "publicaciones_web"
    if not carpeta.is_dir():
        return []

    publicaciones: list[tuple[Path, Path, str]] = []

    for subcarpeta in sorted(
        (p for p in carpeta.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    ):
        snapshots = sorted(subcarpeta.glob("oposiciones_web_*.sqlite3"))
        informes = sorted(subcarpeta.glob("publicacion_*.json"))

        if len(snapshots) != 1 or len(informes) != 1:
            continue

        publicaciones.append(
            (snapshots[0], informes[0], subcarpeta.name)
        )

    return publicaciones


def desplegar_publicacion_web_local_menu() -> None:
    """
    Despliega en TuCoach-Web LOCAL un snapshot previamente preparado.
    No publica en Internet ni modifica Supabase.
    """
    cabecera_submenu(
        "DESPLEGAR CONTENIDOS EN TUCOACH-WEB LOCAL",
        "[BACKUP → VALIDAR → SUSTITUIR] Usa únicamente una publicación preparada "
        "y validada. Afecta sólo al proyecto local TuCoach-Web.",
    )

    publicaciones = _listar_publicaciones_web()
    if not publicaciones:
        print(
            "\nNo hay publicaciones preparadas en publicaciones_web/.\n"
            "Ejecuta primero 'Preparar publicación de contenidos TuCoach-Web'."
        )
        pausa()
        return

    snapshot, informe, version = publicaciones[0]
    tamano_mb = snapshot.stat().st_size / (1024 * 1024)

    print("Publicacion mas reciente seleccionada automaticamente")
    print("-" * 78)
    print(f"{version}  {snapshot.name}  [{tamano_mb:.1f} MB]")

    destino = (
        RAIZ.parent
        / "TuCoach-Web"
        / "backend"
        / "data"
        / "oposiciones.sqlite3"
    )

    print("\nRESUMEN")
    print("-" * 78)
    print(f"Versión:  {version}")
    print(f"Snapshot: {snapshot}")
    print(f"Informe:  {informe}")
    print(f"Destino:  {destino}")
    print()
    print("Este proceso:")
    print("- NO modifica TuCoach Streamlit.")
    print("- NO publica en Internet.")
    print("- NO modifica Supabase.")
    print("- crea backup de la SQLite Web local antes de sustituirla.")
    print()
    print(
        "IMPORTANTE: si Windows mantiene la SQLite abierta, detén primero "
        "el backend/Uvicorn de TuCoach-Web."
    )

    if not pedir_si_no("¿Continuar con el despliegue LOCAL de esta versión?"):
        print("Operación cancelada.")
        pausa()
        return

    ejecutar_script(
        "desplegar_contenidos_web_local.py",
        str(snapshot),
        "--informe",
        str(informe),
    )
    pausa()



def actualizar_publicacion_supabase_menu() -> None:
    """
    Publica en Supabase/producción una publicación previamente preparada.
    Sustituye exclusivamente las tablas del esquema contenidos.*.
    """
    cabecera_submenu(
        "ACTUALIZAR CONTENIDOS TUCOACH-WEB EN SUPABASE",
        "[VALIDAR → TRANSACCIÓN → VERIFICAR] Sustituye contenidos.* por una "
        "publicación preparada. No modifica usuarios ni datos personales.",
    )

    publicaciones = _listar_publicaciones_web()
    if not publicaciones:
        print(
            "\nNo hay publicaciones preparadas en publicaciones_web/.\n"
            "Ejecuta primero 'Preparar publicación de contenidos TuCoach-Web'."
        )
        pausa()
        return

    ruta_env = RAIZ.parent / "TuCoach-Web" / "backend" / ".env"
    if not ruta_env.is_file():
        print(f"\nERROR: no existe el fichero de entorno:\n{ruta_env}")
        pausa()
        return

    snapshot, informe, version = publicaciones[0]
    tamano_mb = snapshot.stat().st_size / (1024 * 1024)

    print("Publicacion mas reciente seleccionada automaticamente")
    print("-" * 78)
    print(f"{version}  {snapshot.name}  [{tamano_mb:.1f} MB]")

    informe_salida = (
        RAIZ
        / "publicaciones_web"
        / f"actualizacion_supabase_{version}.json"
    )

    print("\nRESUMEN")
    print("-" * 78)
    print(f"Versión:      {version}")
    print(f"Snapshot:     {snapshot}")
    print(f"Informe:      {informe}")
    print(f"Entorno:      {ruta_env}")
    print("Destino:      Supabase / esquema contenidos.*")
    print()
    print("Este proceso:")
    print("- valida el snapshot y su SHA256.")
    print("- sustituye las 16 tablas publicables en UNA transacción.")
    print("- hace rollback completo si la carga o validación falla.")
    print("- NO modifica auth.*, profiles, subscriptions ni datos de usuario.")
    print("- NO modifica simulacros ni tests guardados.")
    print()
    print(
        "La Web puede permanecer arrancada. Durante la transacción algunas "
        "consultas pueden esperar brevemente, pero no verán una carga parcial."
    )

    if not pedir_si_no(
        "¿Actualizar AHORA los contenidos de TuCoach-Web en Supabase?"
    ):
        print("Operación cancelada.")
        pausa()
        return

    ejecutar_script_supabase(
        "actualizar_contenidos_supabase.py",
        str(snapshot),
        "--env",
        str(ruta_env),
        "--informe-publicacion",
        str(informe),
        "--informe",
        str(informe_salida),
        "--si",
    )
    pausa()

def validacion_completa() -> None:
    print(
        "\nSe ejecutará una validación completa de solo lectura: integridad SQLite, "
        "constructores de banco en modo revisión para todas las convocatorias activas, "
        "auditoría independiente de selección y auditoría general de la base."
    )
    print("\nLa validación no guarda bancos ni modifica ninguna tabla.")

    if pedir_si_no("¿Continuar con la validación completa?"):
        ejecutar_script("validacion_completa.py")

    pausa()


# =============================================================================
# MENÚ CONSOLIDADO: FUNCIONES AUXILIARES
# =============================================================================

def cabecera_submenu(titulo: str, descripcion: str) -> None:
    limpiar_pantalla()
    print("=" * 78)
    print(titulo)
    print("=" * 78)
    print(descripcion)
    print("-" * 78)


def ejecutar_importador_individual(
    script: str,
    fuente: str,
    formatos: str,
) -> None:
    cabecera_submenu(
        f"IMPORTACIÓN DIRECTA: {fuente}",
        f"Procesa {formatos}. Use esta opción para diagnóstico o reimportación "
        "controlada. Para el trabajo periódico normal use el mantenimiento completo.",
    )
    print("1. Procesar toda la carpeta de entrada")
    print("2. Procesar un único fichero")
    print("0. Volver")
    opcion = input("Opción: ").strip()
    if opcion == "0":
        return
    argumentos: list[str] = []
    if opcion == "2":
        fichero = pedir_texto("Nombre o ruta del fichero: ")
        argumentos.extend(["--pdf", fichero])
        if pedir_si_no(
            "¿Forzar la reimportación aunque el fichero figure como completado?"
        ):
            argumentos.append("--forzar")
    elif opcion != "1":
        print("Opción no válida.")
        pausa()
        return
    ejecutar_script(script, *argumentos)
    pausa()


def importar_examenes_directo() -> None:
    cabecera_submenu(
        "IMPORTAR EXÁMENES OFICIALES/APOYO",
        "[ESCRIBE] Importa los PDF de data_examenes/modelo y data_examenes/apoyo. "
        "Las preguntas no extraíbles íntegramente como texto se rechazan individualmente.",
    )
    if pedir_si_no("¿Ejecutar la importación de exámenes?"):
        ejecutar_script("importar_examenes.py")
    pausa()


def depurar_duplicados_directo() -> None:
    cabecera_submenu(
        "DEPURAR DUPLICADOS EXACTOS",
        "[ESCRIBE] Elimina duplicados de lote_preguntas usando el criterio vigente: "
        "coincidencia exacta de enunciado y opciones A/B/C/D.",
    )
    if pedir_si_no("¿Ejecutar la depuración de duplicados?"):
        ejecutar_script("depurar_preguntas.py")
    pausa()


def enriquecer_preguntas_directo() -> None:
    cabecera_submenu(
        "NORMALIZAR Y CLASIFICAR PREGUNTAS",
        "Ejecuta enriquecer_preguntas.py. En revisión no escribe; con aplicar actualiza "
        "normalización y clasificación únicamente de campos pendientes; nunca reclasifica valores existentes.",
    )
    print("1. Vista previa / revisión")
    print("2. Aplicar resultados")
    print("0. Volver")
    op = input("Opción: ").strip()
    if op == "0":
        return
    args: list[str] = []
    if op == "2":
        args.append("--aplicar")
    elif op != "1":
        print("Opción no válida.")
        pausa()
        return
    ejecutar_script("enriquecer_preguntas.py", *args)
    pausa()


def validar_normalizacion_juridicas_menu() -> None:
    cabecera_submenu(
        "VALIDAR NORMALIZACIÓN JURÍDICA",
        "[SOLO LECTURA] Una pregunta jurídica solo es apta para bancos si tiene "
        "tipo/nombre de norma normalizados, norma_id_normalizada y articulo_normalizado.",
    )
    ejecutar_script("validar_normalizacion_juridicas.py")
    pausa()


def depurar_bancos_vigencia_normalizacion_menu() -> None:
    cabecera_submenu(
        "DEPURAR BANCOS POR VIGENCIA Y NORMALIZACIÓN",
        "No toca lote_preguntas. Da de baja de TODOS los bancos únicamente "
        "jurídicas con estado OBSOLETA* o con normalización obligatoria incompleta.",
    )
    print("1. Vista previa / solo lectura")
    print("2. Aplicar bajas [BACKUP]")
    print("0. Volver")
    op=input("Opción: ").strip()
    if op=="0":
        return
    if op=="1":
        ejecutar_script("depurar_bancos_vigencia_normalizacion.py")
    elif op=="2":
        if pedir_si_no(
            "¿Aplicar las bajas? lote_preguntas permanecerá intacto"
        ):
            ejecutar_script(
                "depurar_bancos_vigencia_normalizacion.py",
                "--aplicar",
            )
    else:
        print("Opción no válida.")
    pausa()


def importar_temario_manual_avanzado() -> None:
    cabecera_submenu(
        "IMPORTAR / SINCRONIZAR TEMARIO CSV",
        "[ESCRIBE] Utilidad avanzada para importar directamente un temario.csv en una "
        "convocatoria existente. El alta normal debe hacerse desde 'Alta de convocatoria'.",
    )
    codigo = pedir_texto("Código de convocatoria: ")
    csv = pedir_texto("Ruta del temario.csv: ")
    args = ["--convocatoria", codigo, "--csv", csv]
    nombre = pedir_texto("Nombre del temario [INTRO = predeterminado]: ", obligatorio=False)
    if nombre:
        args.extend(["--nombre", nombre])
    encoding = pedir_texto("Encoding [INTRO = detección automática]: ", obligatorio=False)
    if encoding:
        args.extend(["--encoding", encoding])
    if pedir_si_no(
        "¿Sincronizar eliminaciones? Esto elimina del temario lo que ya no figure en el CSV"
    ):
        args.append("--sincronizar-eliminaciones")
    if pedir_si_no("¿Ejecutar la importación/sincronización?"):
        ejecutar_script("importar_temario.py", *args)
    pausa()


def seleccionar_convocatoria_temario() -> tuple[str, Path] | None:
    """Selecciona una convocatoria activa y resuelve su temario.csv real."""
    db = RAIZ / "db" / "oposiciones.sqlite3"
    if not db.is_file():
        print(f"\nERROR: no existe la base:\n{db}")
        return None
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        columnas = {r[1] for r in con.execute("PRAGMA table_info(convocatorias)")}
        if "codigo" not in columnas:
            print("\nERROR: convocatorias no contiene la columna codigo.")
            return None
        campos = ["id", "codigo"]
        if "puesto" in columnas: campos.append("puesto")
        if "temario_csv" in columnas: campos.append("temario_csv")
        sql = f"SELECT {', '.join(campos)} FROM convocatorias"
        if "activa" in columnas: sql += " WHERE COALESCE(activa,1)=1"
        sql += " ORDER BY id"
        filas = con.execute(sql).fetchall()
    if not filas:
        print("\nNo hay convocatorias activas.")
        return None
    print("\nConvocatorias activas")
    print("-" * 78)
    for i, fila in enumerate(filas, 1):
        puesto = str(fila["puesto"] or "").strip() if "puesto" in fila.keys() else ""
        sufijo = f" | {puesto}" if puesto else ""
        print(f"{i}. {fila['codigo']}{sufijo}")
    print("0. Volver")
    while True:
        op = input("Opción: ").strip()
        if op == "0": return None
        if op.isdigit() and 1 <= int(op) <= len(filas):
            fila = filas[int(op)-1]
            codigo = str(fila["codigo"])
            ruta_guardada = str(fila["temario_csv"] or "").strip() if "temario_csv" in fila.keys() else ""
            ruta = Path(ruta_guardada) if ruta_guardada else Path("data_convocatorias") / f"CONV_{codigo}" / "temario.csv"
            if not ruta.is_absolute(): ruta = RAIZ / ruta
            ruta = ruta.resolve()
            if not ruta.is_file():
                print(f"\nERROR: no existe el temario.csv de {codigo}:\n{ruta}")
                return None
            return codigo, ruta
        print("Opción no válida.")


def mantener_temario_convocatoria_menu() -> None:
    cabecera_submenu("MANTENIMIENTO INTEGRAL DE TEMARIO", "[CSV → BD → CORPUS → NORMALIZACIÓN → BANCO → VALIDACIÓN] Propaga un temario.csv ya aprobado. lote_preguntas nunca se modifica.")
    seleccion = seleccionar_convocatoria_temario()
    if seleccion is None: return
    codigo, ruta_csv = seleccion
    print("\nSelección")
    print("-" * 78)
    print(f"Convocatoria: {codigo}")
    print(f"Temario CSV:  {ruta_csv}")
    if ejecutar_script("orquestar_mantenimiento_temario.py", "--codigo", codigo, "--csv", str(ruta_csv)) != 0:
        pausa(); return
    if not pedir_si_no("¿Aplicar el mantenimiento completo de esta convocatoria?"):
        print("Operación cancelada. La base no ha sido modificada por el orquestador."); pausa(); return
    if ejecutar_script("orquestar_mantenimiento_temario.py", "--codigo", codigo, "--csv", str(ruta_csv), "--aplicar") != 0:
        print("\nEl mantenimiento se ha detenido por una incidencia."); pausa(); return
    print(f"\nRESULTADO: OK - mantenimiento integral de {codigo} completado.")
    pausa()


def importar_temario_manual() -> None:
    while True:
        cabecera_submenu("IMPORTAR / SINCRONIZAR TEMARIO CSV", "El mantenimiento integral propaga un temario.csv aprobado a BD, corpus, normalización y banco. La importación manual avanzada se conserva.")
        print("1. Mantenimiento integral de temario por convocatoria")
        print("2. Importar/sincronizar CSV manualmente [AVANZADO]")
        print("0. Volver")
        op = input("Opción: ").strip()
        if op == "0": return
        if op == "1": mantener_temario_convocatoria_menu()
        elif op == "2": importar_temario_manual_avanzado()
        else: print("Opción no válida.")


def auditar_fidelidad_temario_menu() -> None:
    cabecera_submenu("AUDITAR FIDELIDAD PDF ↔ TEMARIO.CSV", "[IA · CONSULTA OFICIAL · INFORME] Compara el PDF oficial con un temario.csv. Las diferencias confirmadas pueden aplicarse opcionalmente después de crear una copia del temario base. Las dudas nunca se aplican automáticamente.")
    ejecutar_script("auditar_fidelidad_temario.py")
    pausa()


def auditar_simulacros_temario_oficial_menu() -> None:
    cabecera_submenu("AUDITAR SIMULACROS ↔ TEMARIO OFICIAL", "[IA · SOLO LECTURA] Compara directamente las preguntas de uno o varios simulacros PDF con el PDF oficial del temario. No usa temario.csv ni la base de datos. Genera informes JSON y HTML en auditorias/.")
    print("Criterio conservador:")
    print("- OK: encaje razonable en un epígrafe oficial.")
    print("- DUDOSA: existe una duda real de inclusión.")
    print("- FUERA_TEMARIO: exclusión clara.")
    print()
    if pedir_si_no("¿Iniciar la auditoría?"):
        ejecutar_script("auditar_simulacros_temario_oficial.py")
    pausa()


def resolver_referencias_boe_menu() -> None:
    cabecera_submenu(
        "RESOLVER REFERENCIAS DEL TEMARIO MEDIANTE BOE",
        "[AVANZADO · ESCRIBE] Resuelve referencias jurídicas y guarda artículos fuente. "
        "Crea copia de seguridad en las operaciones de reparación.",
    )
    print("1. Procesar pendientes según estado")
    print("2. Procesar una referencia concreta")
    print("3. Reparar textos incompletos")
    print("4. Limpiar artículos fuente huérfanos e incompletos")
    print("5. Reparar UN articulo_fuente por ID                  [CONTROLADO]")
    print("0. Volver")
    op = input("Opción: ").strip()
    if op == "0":
        return
    args: list[str] = []
    if op == "1":
        limite = pedir_texto("Límite [INTRO = sin límite]: ", obligatorio=False)
        if limite:
            if not limite.isdigit() or int(limite) <= 0:
                print("Límite no válido."); pausa(); return
            args.extend(["--limite", limite])
        if pedir_si_no("¿Reintentar también referencias PENDIENTE?"):
            args.append("--reintentar-pendientes")
    elif op == "2":
        ref = pedir_texto("ID de temario_referencias: ")
        if not ref.isdigit() or int(ref) <= 0:
            print("ID no válido."); pausa(); return
        args.extend(["--referencia-id", ref])
    elif op == "3":
        limite = pedir_texto("Límite [INTRO = sin límite]: ", obligatorio=False)
        if limite:
            if not limite.isdigit() or int(limite) <= 0:
                print("Límite no válido."); pausa(); return
            args.extend(["--limite", limite])
        args.append("--reparar-textos-incompletos")
    elif op == "4":
        print(
            "\nSolo se eliminarán artículos a la vez huérfanos e incompletos. "
            "Se crea copia de seguridad."
        )
        if not pedir_si_no("¿Continuar con esta limpieza?"):
            pausa(); return
        args.append("--limpiar-huerfanos-incompletos")
    elif op == "5":
        afid = pedir_texto("ID exacto de articulos_fuente: ")
        if not afid.isdigit() or int(afid) <= 0:
            print("ID no válido."); pausa(); return
        print(
            "\nSe reparará exclusivamente ese registro. El script exige coincidencia "
            "de norma, artículo, BOE y bloque antes de actualizar."
        )
        if not pedir_si_no("¿Continuar con la reparación controlada?"):
            pausa(); return
        args.extend(["--articulo-fuente-id", afid])
    else:
        print("Opción no válida."); pausa(); return

    ejecutar_script("resolver_referencias_boe.py", *args)
    pausa()

def auditar_corpus_temario_menu() -> None:
    cabecera_submenu(
        "AUDITORÍA EXHAUSTIVA DEL TEMARIO/CORPUS",
        "[SOLO LECTURA] Comprueba referencias jurídicas y artículos fuente del temario. "
        "Genera un informe en auditorias/.",
    )
    ejecutar_script("auditar_corpus_temario.py")
    pausa()


def auditar_bd_directo() -> None:
    cabecera_submenu(
        "AUDITORÍA GENERAL DE LA BASE",
        "[SOLO LECTURA] Comprueba integridad y consistencia básica de la base maestra.",
    )
    ejecutar_script("auditar_bd.py")
    pausa()


def auditar_bancos_seleccion_menu() -> None:
    cabecera_submenu(
        "AUDITORÍA INDEPENDIENTE DE SELECCIÓN DE BANCOS",
        "[SOLO LECTURA] Reconstruye virtualmente los bancos con las reglas vigentes y "
        "compara preguntas, temas y partes con lo almacenado.",
    )
    ejecutar_script(
        "auditar_bancos_seleccion.py",
        "--db", "db/oposiciones.sqlite3",
        "--constructor", "scripts/mantener_banco_preguntas.py",
    )
    pausa()


def auditar_consistencia_global_menu() -> None:
    cabecera_submenu(
        "AUDITORÍA GLOBAL LOTE ↔ BANCO",
        "[SOLO LECTURA] Compara el banco real con el esperado y genera informes de diagnóstico.",
    )
    cid = pedir_texto(
        "ID de convocatoria [INTRO = seleccionar/procesar según script]: ",
        obligatorio=False,
    )
    args: list[str] = []
    if cid:
        if not cid.isdigit() or int(cid) <= 0:
            print("ID no válido.")
            pausa(); return
        args.extend(["--convocatoria-id", cid])
    ejecutar_script("auditar_consistencia_global.py", *args)
    pausa()


def auditar_estructura_banco_menu() -> None:
    cabecera_submenu(
        "AUDITAR ESTRUCTURA DEL BANCO",
        "[SOLO LECTURA] Muestra tablas, columnas, índices y claves foráneas relacionadas con el banco.",
    )
    ejecutar_script("auditar_estructura_banco.py")
    pausa()


def reparar_partes_banco_menu() -> None:
    cabecera_submenu(
        "REPARAR PARTES DEL BANCO",
        "[REPARACIÓN] Rellena convocatoria_parte_id usando EXCLUSIVAMENTE las reglas ya "
        "existentes en convocatoria_parte_reglas. Siempre se ejecuta primero en revisión.",
    )
    args = ["--db", "db/oposiciones.sqlite3"]
    if ejecutar_script("reparar_partes_banco.py", *args) != 0:
        pausa(); return
    if pedir_si_no(
        "¿Aplicar únicamente las reparaciones unívocas mostradas? Se creará copia de seguridad"
    ):
        ejecutar_script("reparar_partes_banco.py", *args, "--aplicar")
    pausa()


def inventariar_normas_menu() -> None:
    cabecera_submenu(
        "INVENTARIAR DENOMINACIONES DE NORMAS",
        "[SOLO LECTURA] Genera un inventario de denominaciones jurídicas presentes en lote_preguntas. "
        "Útil para diagnosticar normalización.",
    )
    ejecutar_script("inventariar_denominaciones_normas.py")
    pausa()


def buscar_norma_respuesta_correcta_menu() -> None:
    cabecera_submenu(
        "BUSCAR NORMA/ARTÍCULO DESDE LA RESPUESTA CORRECTA",
        "[IA · COSTE] Componente de diagnóstico de la recuperación de PENDIENTES. "
        "Busca propuestas; no sustituye al orquestador de recuperación y auditoría.",
    )
    pid = pedir_texto("ID de pregunta [INTRO = lote]: ", obligatorio=False)
    args: list[str] = []
    if pid:
        if not pid.isdigit() or int(pid) <= 0:
            print("ID no válido.")
            pausa(); return
        args.extend(["--id", pid])
    else:
        limite = pedir_texto("Límite [20]: ", obligatorio=False) or "20"
        if not limite.isdigit() or int(limite) <= 0:
            print("Límite no válido.")
            pausa(); return
        args.extend(["--limite", limite])
    ejecutar_script("buscar_norma_por_respuesta_correcta.py", *args)
    pausa()


def generar_informatica_menu() -> None:
    cabecera_submenu(
        "GENERAR PREGUNTAS DE INFORMÁTICA MEDIANTE IA",
        "[IA · COSTE · ESCRIBE] Genera, valida y publica una sola vez. "
        "Después sincroniza todos los bancos con el procedimiento común y ejecuta la validación completa.",
    )
    cantidad = pedir_texto("Cantidad total a generar: ")
    if not cantidad.isdigit() or int(cantidad) <= 0:
        print("Cantidad no válida.")
        pausa()
        return
    categoria = pedir_texto(
        "Categoría concreta [INTRO = reparto entre categorías]: ",
        obligatorio=False,
    ).upper()
    origen = (
        pedir_texto(
            "Origen oposición A1/A2/C1/C2 [C1]: ",
            obligatorio=False,
        ).upper()
        or "C1"
    )
    if origen not in {"A1", "A2", "C1", "C2"}:
        print("Origen no válido.")
        pausa()
        return
    args = ["--cantidad", cantidad, "--origen-oposicion", origen, "--guardar"]
    if categoria:
        args.extend(["--categoria", categoria])

    print(
        "\nLa generación se realizará UNA sola vez. Las preguntas que superen "
        "las validaciones se guardarán en lote_preguntas y, a continuación, "
        "se sincronizarán los bancos mediante mantener_banco_preguntas.py."
    )
    if pedir_si_no("¿Generar y guardar ahora?"):
        ejecutar_script("generar_preguntas_informatica.py", *args)
    pausa()

def seleccionar_tema_temario(argumentos_conv: list[str]) -> int | None:
    """
    Permite elegir únicamente el tema del temario.

    La norma y el artículo concretos se seleccionan después automáticamente
    dentro de generar_preguntas_juridicas_ia.py, entre las referencias del tema
    que tienen texto oficial enlazado. Si existen preguntas de ejemplo se usan,
    pero no son requisito para generar la primera pregunta. No modifica la base.
    """
    db = RAIZ / "db" / "oposiciones.sqlite3"
    if not db.is_file():
        print(f"\nERROR: no existe la base de datos:\n{db}")
        return None

    convocatoria_id: int | None = None
    codigo: str | None = None
    for i, arg in enumerate(argumentos_conv):
        if arg == "--convocatoria-id" and i + 1 < len(argumentos_conv):
            convocatoria_id = int(argumentos_conv[i + 1])
        elif arg == "--codigo" and i + 1 < len(argumentos_conv):
            codigo = argumentos_conv[i + 1]

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        if convocatoria_id is None:
            row = con.execute(
                "SELECT id FROM convocatorias WHERE codigo = ?",
                (codigo,),
            ).fetchone()
            if row is None:
                print(f"\nERROR: no existe la convocatoria con código {codigo!r}.")
                return None
            convocatoria_id = int(row["id"])

        temas = con.execute(
            """
            SELECT
                tt.id,
                tt.parte,
                tt.numero_tema,
                tt.titulo,
                COUNT(tr.id) AS referencias
            FROM temarios t
            JOIN temario_temas tt ON tt.temario_id = t.id
            JOIN temario_referencias tr ON tr.tema_id = tt.id
            WHERE t.convocatoria_id = ?
              AND tr.norma_id IS NOT NULL
              AND tr.articulo_fuente_id IS NOT NULL
            GROUP BY tt.id, tt.parte, tt.numero_tema, tt.titulo
            HAVING COUNT(tr.id) > 0
            ORDER BY tt.parte, tt.numero_tema, tt.id
            """,
            (convocatoria_id,),
        ).fetchall()

        if not temas:
            print("\nNo hay temas con referencias norma-artículo utilizables en esta convocatoria.")
            return None

        print("\nSeleccione el tema del temario")
        print("-" * 78)
        for n, tema in enumerate(temas, 1):
            titulo = (tema["titulo"] or "").strip()
            if len(titulo) > 78:
                titulo = titulo[:75] + "..."
            print(
                f"{n:>3}. {tema['parte']} · Tema {tema['numero_tema']} · "
                f"{titulo}  [{tema['referencias']} refs. con texto]"
            )
        print("  0. Cancelar")

        while True:
            valor = input("Tema: ").strip()
            if valor == "0":
                return None
            if valor.isdigit() and 1 <= int(valor) <= len(temas):
                return int(temas[int(valor) - 1]["id"])
            print("Opción no válida.")
    finally:
        con.close()

def convocatoria_admite_practica_menu(args: list[str]) -> bool:
    db=RAIZ/"db"/"oposiciones.sqlite3"; convocatoria_id=None; codigo=None
    for i,valor in enumerate(args):
        if valor=="--convocatoria-id" and i+1<len(args): convocatoria_id=int(args[i+1])
        elif valor=="--codigo" and i+1<len(args): codigo=args[i+1]
    with sqlite3.connect(db) as con:
        conv=con.execute("SELECT id FROM convocatorias WHERE id=?" if convocatoria_id is not None else "SELECT id FROM convocatorias WHERE codigo=?",(convocatoria_id if convocatoria_id is not None else codigo,)).fetchone()
        if conv is None: raise RuntimeError("No existe la convocatoria seleccionada.")
        return con.execute("""SELECT 1 FROM convocatoria_parte_reglas r JOIN convocatoria_partes cp ON cp.id=r.convocatoria_parte_id WHERE cp.convocatoria_id=? AND UPPER(TRIM(COALESCE(r.teorica_practica,'')))='PRACTICA' LIMIT 1""",(int(conv[0]),)).fetchone() is not None


def generar_juridicas_ia_menu() -> None:
    """
    Generación jurídica IA.

    La convocatoria aporta el contexto del temario. Las preguntas aprobadas
    se publican en lote_preguntas; la pertenencia a bancos sigue dependiendo
    de las reglas norma-artículo del mantenimiento de bancos.
    """
    while True:
        cabecera_submenu(
            "GENERAR PREGUNTAS JURÍDICAS MEDIANTE IA",
            "[IA · COSTE] Permite reforzar el banco mediante el modelo de examen, "
            "un tema concreto o todos los temas jurídicos. La generación siempre "
            "parte de referencias norma-artículo del temario.",
        )

        print("1. Generar preguntas IA")
        print("0. Volver")

        op = input("Opción: ").strip()
        if op == "0":
            return
        if op != "1":
            print("Opción no válida.")
            pausa()
            continue

        args = argumentos_convocatoria()

        admite_practica = convocatoria_admite_practica_menu(args)
        print("\nTipo de pregunta")
        print("1. TEORICA")
        if admite_practica:
            print("2. PRACTICA")
            tipo_op=input("Opción [1]: ").strip() or "1"
            if tipo_op not in {"1","2"}:
                print("Opción no válida."); pausa(); continue
            tipo="TEORICA" if tipo_op=="1" else "PRACTICA"
        else:
            print("PRACTICA no disponible: la convocatoria no tiene una parte/regla práctica definida.")
            tipo="TEORICA"

        print("\nÁmbito de generación")
        if tipo == "TEORICA":
            print("1. Reparto automático según el modelo de examen")
            print("2. Tema concreto del temario")
            print("3. Todos los temas jurídicos")
            print("0. Cancelar")
            ambito = input("Opción [1]: ").strip() or "1"
            if ambito not in {"0", "1", "2", "3"}:
                print("Opción no válida.")
                pausa()
                continue
        else:
            # Las prácticas no tienen bloques normativos de examen.
            print("1. Tema concreto del temario")
            print("2. Todos los temas jurídicos")
            print("0. Cancelar")
            ambito = input("Opción [2]: ").strip() or "2"
            if ambito not in {"0", "1", "2"}:
                print("Opción no válida.")
                pausa()
                continue

        if ambito == "0":
            pausa()
            continue

        if tipo == "TEORICA":
            if ambito == "2":
                tema_id = seleccionar_tema_temario(args)
                if tema_id is None:
                    pausa()
                    continue
                args.extend(["--tema-id", str(tema_id)])
            elif ambito == "3":
                args.append("--todos-temas")
        else:
            if ambito == "1":
                tema_id = seleccionar_tema_temario(args)
                if tema_id is None:
                    pausa()
                    continue
                args.extend(["--tema-id", str(tema_id)])
            else:
                args.append("--todos-temas")

        cantidad = (
            pedir_texto(
                "Número de preguntas a generar [5]: ",
                obligatorio=False,
            )
            or "5"
        )
        if not cantidad.isdigit() or int(cantidad) <= 0:
            print("Cantidad no válida.")
            pausa()
            continue

        print(
            "\nSe borrará el registro de la ejecución anterior. "
            "Al terminar quedará únicamente "
            "registros/preguntas_ia_ultima_ejecucion.html."
        )

        if tipo == "TEORICA" and ambito == "1":
            print(
                "El reparto automático se calculará a partir de "
                "convocatoria_modelo_bloques."
            )
        elif (tipo == "TEORICA" and ambito == "2") or (
            tipo == "PRACTICA" and ambito == "1"
        ):
            print(
                "La norma y el artículo se elegirán únicamente entre las "
                "referencias del tema seleccionado."
            )
        else:
            print(
                "La cantidad se repartirá entre los temas jurídicos aptos, "
                "y dentro de cada tema entre sus referencias norma-artículo."
            )

        if not pedir_si_no("¿Continuar?"):
            pausa()
            continue

        ejecutar_script(
            "generar_preguntas_juridicas_ia.py",
            *args,
            "--cantidad",
            cantidad,
            "--tipo",
            tipo,
        )
        pausa()


def localizador_normativa_menu() -> None:
    cabecera_submenu(
        "LOCALIZADOR DE NORMATIVA BOE",
        "[CONSULTA WEB] Localiza una norma, muestra su índice interpretado o resuelve un alcance "
        "como un título/capítulo/sección. No modifica la base.",
    )
    norma = pedir_texto("Norma o ID BOE-A-...: ")
    print("1. Localizar norma")
    print("2. Mostrar índice")
    print("3. Resolver alcance (Título/Capítulo/Sección...)")
    print("0. Volver")
    op = input("Opción: ").strip()
    if op == "0": return
    if op == "1": args=[norma,"--localizar"]
    elif op == "2": args=[norma,"--indice"]
    elif op == "3":
        alcance=pedir_texto("Alcance a resolver: ")
        args=[norma,"--alcance",alcance]
    else:
        print("Opción no válida."); pausa(); return
    if pedir_si_no("¿Refrescar la caché y descargar de nuevo?"):
        args.append("--refrescar")
    ejecutar_script("localizador_normativa.py", *args)
    pausa()

def consultar_articulo_boe_menu() -> None:
    cabecera_submenu(
        "CONSULTAR ARTÍCULO CONSOLIDADO DEL BOE",
        "[CONSULTA WEB] Consulta directamente una norma y un artículo mediante boe_api.py. "
        "No modifica la base.",
    )
    norma = pedir_texto("Norma o ID BOE-A-...: ")
    articulo = pedir_texto("Artículo: ")
    ejecutar_script("boe_api.py", norma, articulo)
    pausa()

def auditar_esquema_menu() -> None:
    cabecera_submenu(
        "AUDITAR POSIBLES OBJETOS OBSOLETOS DEL ESQUEMA",
        "[SOLO LECTURA] Inventaría tablas, columnas y referencias de código. "
        "NO autoriza ni realiza borrados; se conserva como diagnóstico de deuda técnica.",
    )
    ejecutar_script("auditar_esquema_obsoleto.py")
    pausa()

def mostrar_componentes_internos() -> None:
    cabecera_submenu(
        "COMPONENTES INTERNOS / HISTÓRICOS",
        "Estos archivos existen en scripts pero NO deben ejecutarse como procesos normales. "
        "Se muestran aquí para que todo el proyecto quede documentado desde el menú.",
    )
    print("Módulos internos (dependencias, no ejecutables):")
    print("  - importacion_preguntas_comun.py   Publicación transaccional común")
    print("  - normalizador_normas.py           Reglas auxiliares de normalización")
    print("  - normalizar_lote_preguntas_definitivo.py  Reglas deterministas")
    print("  - openai_api.py                    Cliente IA y registro de costes")
    print("  - pdf_normas.py                    Proveedor local de normativa PDF")
    print("\nNo se incluyen scripts históricos ni parches de una sola ejecución en la distribución operativa.")
    pausa()

def sincronizar_todos_bancos_menu() -> None:
    cabecera_submenu(
        "SINCRONIZAR TODOS LOS BANCOS",
        "Usa mantener_banco_preguntas.py como única fuente de reglas. "
        "Primero revisa TODAS las convocatorias activas; solo después permite aplicar.",
    )
    if ejecutar_script("sincronizar_bancos.py") != 0:
        pausa()
        return
    if pedir_si_no("¿Aplicar ahora todas las vinculaciones nuevas y validar?"):
        ejecutar_script("sincronizar_bancos.py", "--aplicar")
    pausa()


def limpiar_temporales_menu() -> None:
    cabecera_submenu(
        "LIMPIAR ARCHIVOS TEMPORALES",
        "Vista previa obligatoria. No toca DB, datos de entrada, cache_boe_v2 ni registros protegidos.",
    )
    if ejecutar_script("limpiar_temporales.py") != 0:
        pausa()
        return
    if pedir_si_no("¿Aplicar exactamente la limpieza mostrada?"):
        ejecutar_script("limpiar_temporales.py", "--aplicar")
    pausa()


def configurar_reglas_partes_menu() -> None:
    cabecera_submenu(
        "CONFIGURAR REGLAS DE PARTES",
        "[ESCRIBE · BACKUP] Define explícitamente cómo se asignan las preguntas a cada parte del examen.",
    )
    ejecutar_script("configurar_reglas_partes.py", "--db", "db/oposiciones.sqlite3")
    pausa()


def configurar_modelo_examen_menu() -> None:
    cabecera_submenu(
        "CONFIGURAR / MODIFICAR MODELO DE EXAMEN",
        "[ESCRIBE · BACKUP] Permite ver, crear, modificar/reemplazar o eliminar los bloques normativos del modelo.",
    )
    ejecutar_script("configurar_modelo_examen.py")
    pausa()


def editar_partes_convocatoria_menu() -> None:
    cabecera_submenu(
        "EDITAR PARTES DE CONVOCATORIA",
        "[ESCRIBE · BACKUP] Modifica nombre, número de preguntas y orden conservando los IDs y sus dependencias.",
    )
    ejecutar_script("editar_partes_convocatoria.py")
    pausa()





def mantener_corpus_chat_menu() -> None:
    cabecera_submenu(
        "MANTENER CORPUS NORMATIVO DEL CHAT",
        "Valida conjuntamente BOE, DOGV y DOUE. "
        "Primero ejecuta todos los planes; sólo después permite aplicar.",
    )

    if ejecutar_script("mantener_corpus_chat.py") != 0:
        print(
            "\nLa validación global no ha terminado correctamente. "
            "No se realizará ninguna aplicación."
        )
        pausa()
        return

    if pedir_si_no(
        "Los tres proveedores han validado. "
        "¿Aplicar ahora las actualizaciones pendientes?"
    ):
        ejecutar_script(
            "mantener_corpus_chat.py",
            "--aplicar",
        )

    pausa()











def auditar_materiales_estudio_menu() -> None:
    cabecera_submenu(
        "AUDITAR MATERIALES DE ESTUDIO",
        "[SOLO LECTURA] Compara los resúmenes preparados con las normas de las "
        "convocatorias activas y con la huella actual del corpus normativo. "
        "No modifica la base, los PDF, Streamlit, Web ni Supabase.",
    )
    ejecutar_script(
        "auditar_materiales_estudio.py",
        "--detalle",
    )
    pausa()




def actualizar_materiales_estudio_menu() -> None:
    cabecera_submenu(
        "ACTUALIZAR MATERIALES DE ESTUDIO",
        "Muestra primero el plan. Solo actúa sobre NUEVA o DESACTUALIZADO. "
        "La completitud del corpus se valida con los proveedores RAG existentes.",
    )

    codigo = ejecutar_script("generar_materiales_estudio.py")
    if codigo != 0:
        pausa()
        return

    print()
    if not pedir_si_no(
        "¿Continuar con la generación/actualización IA de materiales pendientes?"
    ):
        print("Operación cancelada.")
        pausa()
        return

    valor = input(
        "Norma ID concreta (Enter = TODAS las pendientes): "
    ).strip()

    if valor:
        try:
            norma_id = int(valor)
        except ValueError:
            print("ERROR: norma_id debe ser numérico.")
            pausa()
            return
        argumentos = ("--aplicar", "--norma-id", str(norma_id))
    else:
        if not pedir_si_no(
            "¿Confirmas actualizar TODAS las normas pendientes?"
        ):
            print("Operación cancelada.")
            pausa()
            return
        argumentos = ("--aplicar", "--todos-pendientes")

    ejecutar_script(
        "generar_materiales_estudio.py",
        *argumentos,
    )
    pausa()


def construir_materiales_convocatoria_menu() -> None:
    cabecera_submenu(
        "MATERIALES POR CONVOCATORIA",
        "[PLAN → RAG → EXTRACTO → RESUMEN] Revisa las referencias del temario, "
        "comprueba el corpus y crea extractos literales idempotentes. Los resúmenes "
        "sólo se generan después de confirmación y con el presupuesto disponible.",
    )
    codigo = pedir_texto("Código exacto de convocatoria: ")
    try:
        with sqlite3.connect(
            f"file:{(RAIZ / 'db' / 'oposiciones.sqlite3').as_posix()}?mode=ro",
            uri=True,
        ) as con:
            convocatoria = con.execute(
                "SELECT id, puesto FROM convocatorias WHERE codigo=? AND activa=1",
                (codigo,),
            ).fetchone()
            if convocatoria is None:
                print("ERROR: no existe una convocatoria activa con ese código.")
                pausa()
                return
            filas = con.execute(
                """
                SELECT n.id, n.nombre_canonico, COUNT(DISTINCT tr.articulo_fuente_id)
                FROM temarios t
                JOIN temario_temas tt ON tt.temario_id=t.id
                JOIN temario_referencias tr ON tr.tema_id=tt.id
                JOIN normas n ON n.id=tr.norma_id
                WHERE t.convocatoria_id=? AND tr.norma_id IS NOT NULL
                GROUP BY n.id, n.nombre_canonico
                ORDER BY UPPER(n.nombre_canonico), n.id
                """,
                (convocatoria[0],),
            ).fetchall()
    except sqlite3.Error as error:
        print(f"ERROR: no se han podido consultar las normas: {error}")
        pausa()
        return

    if not filas:
        print("ERROR: la convocatoria no tiene normas normalizadas para materiales.")
        pausa()
        return
    print("\nNORMAS DISPONIBLES")
    print("-" * 78)
    for norma_id, nombre, articulos in filas:
        print(f"{norma_id:>5} | {articulos:>4} artículos | {nombre}")
    print("-" * 78)
    ids_disponibles = {int(fila[0]) for fila in filas}
    norma = pedir_texto("ID de norma (Enter = todas las anteriores): ", obligatorio=False)
    argumentos = ("--codigo", codigo)
    if norma:
        if not norma.isdigit():
            print("ERROR: el ID de norma debe ser numérico.")
            pausa()
            return
        if int(norma) not in ids_disponibles:
            print("ERROR: ese ID no pertenece a las normas mostradas para la convocatoria.")
            pausa()
            return
        argumentos += ("--norma-id", norma)

    if ejecutar_script("generar_materiales_convocatoria.py", *argumentos) != 0:
        pausa()
        return
    if not pedir_si_no("¿Validar RAG y crear/actualizar los extractos mostrados?"):
        print("Operación cancelada.")
        pausa()
        return
    if ejecutar_script(
        "generar_materiales_convocatoria.py",
        *argumentos,
        "--aplicar",
        "--validar-rag",
    ) != 0:
        print("No se generarán resúmenes porque el extracto o la validación RAG falló.")
        pausa()
        return
    if pedir_si_no("¿Generar también los resúmenes pendientes con IA?"):
        if ejecutar_script(
            "generar_materiales_convocatoria.py",
            *argumentos,
            "--aplicar",
            "--validar-rag",
            "--generar-resumenes",
        ) != 0:
            print("El extracto se conserva; revise el mensaje anterior para el resumen.")
    pausa()




def publicar_materiales_web_menu() -> None:
    cabecera_submenu(
        "PUBLICAR MATERIALES DE ESTUDIO EN TUCOACH-WEB LOCAL",
        "[PLAN -> VALIDAR -> CONFIRMAR -> BACKUP -> COPIA] Valida todos los "
        "resúmenes preparados y sincroniza los PDF y el catálogo con TuCoach-Web.",
    )

    if ejecutar_script("publicar_materiales_web.py") != 0:
        print("\nPublicación cancelada: la validación previa ha fallado.")
        pausa()
        return

    print()
    if not pedir_si_no(
        "¿Publicar ahora los materiales validados en TuCoach-Web LOCAL?"
    ):
        print("Operación cancelada.")
        pausa()
        return

    ejecutar_script("publicar_materiales_web.py", "--aplicar")
    pausa()


def submenu_convocatorias_tareas() -> None:
    while True:
        cabecera_submenu("1. CONVOCATORIAS Y TEMARIOS", "Alta y mantenimiento de la estructura oficial de cada convocatoria.")
        print("1. Crear nueva convocatoria")
        print("2. Preparar temario desde PDF")
        print("3. Actualizar temario de una convocatoria")
        print("4. Modificar modelo de examen")
        print("5. Modificar partes de una convocatoria")
        print("6. Modificar reglas de partes")
        print("7. Eliminar / gestionar convocatoria")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":alta_convocatoria,"2":extraer_temario_convocatoria,"3":mantener_temario_convocatoria_menu,"4":configurar_modelo_examen_menu,"5":editar_partes_convocatoria_menu,"6":configurar_reglas_partes_menu,"7":gestionar_estado_convocatoria_menu}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_preguntas_tareas() -> None:
    while True:
        cabecera_submenu("2. PREGUNTAS", "Mantenimiento, generación, recuperación, consulta y corrección de preguntas.")
        print("1. Mantenimiento completo de preguntas")
        print("2. Generar preguntas jurídicas con IA")
        print("3. Generar preguntas de informática con IA")
        print("4. Recuperar preguntas pendientes")
        print("5. Buscar preguntas por norma/artículo")
        print("6. Modificar una pregunta manualmente")
        print("7. Revisar importaciones problemáticas")
        print("8. Ver resumen general de preguntas")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":mantenimiento_preguntas,"2":generar_juridicas_ia_menu,"3":generar_informatica_menu,"4":recuperar_pendientes,"5":buscar_preguntas,"6":modificar_pregunta_manual,"7":revisar_importaciones,"8":mostrar_resumen_lote_preguntas}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_bancos_tareas() -> None:
    while True:
        cabecera_submenu("3. BANCOS DE PREGUNTAS", "Sincronización, actualización y consulta de bancos de convocatoria.")
        print("1. Sincronizar todos los bancos")
        print("2. Actualizar un banco")
        print("3. Ver resumen de un banco")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":sincronizar_todos_bancos_menu,"2":actualizar_banco,"3":mostrar_resumen_banco_convocatoria}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_materiales_tareas() -> None:
    while True:
        cabecera_submenu("4. MATERIALES DE ESTUDIO", "Construcción, actualización y auditoría de materiales de estudio.")
        print("1. Actualizar materiales de estudio")
        print("2. Construir materiales de una convocatoria")
        print("3. Auditar materiales de estudio")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":actualizar_materiales_estudio_menu,"2":construir_materiales_convocatoria_menu,"3":auditar_materiales_estudio_menu}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_publicacion_tareas() -> None:
    while True:
        cabecera_submenu("5. PUBLICACIÓN", "Publicación de base de datos, contenidos y materiales en los destinos existentes.")
        print("1. Actualizar BD de TuCoach Streamlit")
        print("2. Preparar publicación TuCoach-Web")
        print("3. Desplegar contenidos en TuCoach-Web local")
        print("4. Actualizar contenidos Web en Supabase")
        print("5. Publicar materiales en TuCoach-Web local")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":actualizar_bd_tucoach,"2":preparar_publicacion_web_menu,"3":desplegar_publicacion_web_local_menu,"4":actualizar_publicacion_supabase_menu,"5":publicar_materiales_web_menu}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_auditar_bancos() -> None:
    while True:
        cabecera_submenu("AUDITAR BANCOS DE PREGUNTAS", "Comprobaciones de selección, funcionamiento, consistencia y estructura de los bancos.")
        print("1. Selección de bancos")
        print("2. Funcionamiento de un banco")
        print("3. Consistencia lote ↔ banco")
        print("4. Estructura del banco")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":auditar_bancos_seleccion_menu,"2":auditar_banco,"3":auditar_consistencia_global_menu,"4":auditar_estructura_banco_menu}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_diagnostico_tareas() -> None:
    while True:
        cabecera_submenu("6. DIAGNÓSTICO Y REPARACIÓN", "Auditorías, verificaciones y reparaciones específicas existentes.")
        print("1. Validación completa")
        print("2. Auditar base de datos")
        print("3. Auditar bancos de preguntas")
        print("4. Auditar corpus / temario")
        print("5. Auditar fidelidad PDF ↔ temario.csv")
        print("6. Auditar vigencia jurídica de preguntas")
        print("7. Auditar posibles objetos obsoletos")
        print("8. Reparar asignación de partes del banco")
        print("9. Depurar obsoletas/incompletas de bancos")
        print("10. Reparar referencias BOE")
        print("11. Buscar norma/artículo desde respuesta correcta")
        print("12. Auditar simulacros ↔ temario oficial")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":validacion_completa,"2":auditar_bd_directo,"3":submenu_auditar_bancos,"4":auditar_corpus_temario_menu,"5":auditar_fidelidad_temario_menu,"6":auditar_vigencia_preguntas,"7":auditar_esquema_menu,"8":reparar_partes_banco_menu,"9":depurar_bancos_vigencia_normalizacion_menu,"10":resolver_referencias_boe_menu,"11":buscar_norma_respuesta_correcta_menu,"12":auditar_simulacros_temario_oficial_menu}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_importaciones_individuales() -> None:
    while True:
        cabecera_submenu("IMPORTACIONES INDIVIDUALES", "Importadores parciales existentes para diagnóstico o trabajo controlado.")
        print("1. Exámenes oficiales/apoyo")
        print("2. Tests visuales PDF/PNG")
        print("3. Academia estructurados")
        print("4. Academia texto")
        print("5. Preguntas de informática")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={
            "1":importar_examenes_directo,
            "2":lambda: ejecutar_importador_individual("importar_tests_imagen.py","tests visuales","PDF y PNG GoFullPage"),
            "3":lambda: ejecutar_importador_individual("importar_tests_academia.py","tests academia estructurados","PDF"),
            "4":lambda: ejecutar_importador_individual("importar_tests_academia_texto.py","tests academia texto","PDF"),
            "5":lambda: ejecutar_importador_individual("importar_preguntas_informatica.py","preguntas informática","PDF"),
        }
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_herramientas_avanzadas() -> None:
    while True:
        cabecera_submenu("7. HERRAMIENTAS AVANZADAS", "Operaciones parciales, consultas técnicas y utilidades de mantenimiento.")
        print("1. Importaciones individuales")
        print("2. Depurar duplicados exactos")
        print("3. Normalizar/clasificar preguntas")
        print("4. Reconstruir catálogo y enlaces")
        print("5. Validar normalización jurídica")
        print("6. Listar preguntas pendientes")
        print("7. Importar/sincronizar temario.csv manualmente")
        print("8. Construir/validar corpus IA + RAG")
        print("9. Localizar norma / índice / alcance BOE")
        print("10. Consultar artículo consolidado BOE")
        print("11. Mantener corpus normativo del Chat")
        print("12. Inventariar denominaciones de normas")
        print("13. Mostrar componentes internos")
        print("14. Limpiar logs/auditorías temporales")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={"1":submenu_importaciones_individuales,"2":depurar_duplicados_directo,"3":enriquecer_preguntas_directo,"4":actualizar_catalogo_y_enlaces,"5":validar_normalizacion_juridicas_menu,"6":listar_pendientes,"7":importar_temario_manual_avanzado,"8":construir_corpus,"9":localizador_normativa_menu,"10":consultar_articulo_boe_menu,"11":mantener_corpus_chat_menu,"12":inventariar_normas_menu,"13":mostrar_componentes_internos,"14":limpiar_temporales_menu}
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def mostrar_menu() -> None:
    limpiar_pantalla()
    print("=" * 78)
    print("TUCOACH — MANTENIMIENTO")
    print("=" * 78)
    print("1. CONVOCATORIAS Y TEMARIOS")
    print("2. PREGUNTAS")
    print("3. BANCOS DE PREGUNTAS")
    print("4. MATERIALES DE ESTUDIO")
    print("5. PUBLICACIÓN")
    print("6. DIAGNÓSTICO Y REPARACIÓN")
    print("7. HERRAMIENTAS AVANZADAS")
    print("0. SALIR")
    print("=" * 78)


def main() -> int:
    if not CARPETA_SCRIPTS.is_dir():
        print(f"No existe la carpeta de scripts: {CARPETA_SCRIPTS}")
        return 1
    acciones={"1":submenu_convocatorias_tareas,"2":submenu_preguntas_tareas,"3":submenu_bancos_tareas,"4":submenu_materiales_tareas,"5":submenu_publicacion_tareas,"6":submenu_diagnostico_tareas,"7":submenu_herramientas_avanzadas}
    while True:
        mostrar_menu()
        opcion=input("Opción: ").strip()
        if opcion=="0":
            print("\nFin del mantenimiento.")
            return 0
        accion=acciones.get(opcion)
        if accion is None:
            print("\nOpción no válida."); pausa(); continue
        try:
            accion()
        except KeyboardInterrupt:
            print("\n\nOperación cancelada por el usuario."); pausa()
        except Exception as error:
            print(f"\nERROR inesperado: {error}"); pausa()


if __name__ == "__main__":
    raise SystemExit(main())
