"""
OpoCoach - Menú de mantenimiento

Este menú no modifica la estructura del proyecto ni las rutas de los scripts.
Se limita a ejecutar los scripts existentes desde la raíz del proyecto.

Ubicación prevista:
    menu_mantenimiento.py

Ejecución:
    python menu_mantenimiento.py
"""

from __future__ import annotations

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


def construir_corpus() -> None:
    argumentos = argumentos_convocatoria()

    print("\nModo")
    print("1. Construir o completar el corpus")
    print("2. Solo validar el estado actual")
    opcion = input("Opción: ").strip()

    if opcion == "2":
        argumentos.append("--solo-validar")
    elif opcion != "1":
        print("Opción no válida.")
        pausa()
        return

    if opcion == "1":
        if pedir_si_no("¿Reintentar referencias pendientes?"):
            argumentos.append("--reintentar-pendientes")

        if pedir_si_no(
            "¿Reparar también artículos COMPLETADO cuyo texto sea "
            "manifiestamente incompleto?"
        ):
            argumentos.append("--reparar-textos-incompletos")

        if pedir_si_no("¿Detener el proceso al primer error?"):
            argumentos.append("--detener-en-error")

    ejecutar_script(
        "construir_corpus_convocatoria.py",
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

def actualizar_bd_opocoach() -> None:
    """
    Copia la base de datos de OpoCoach-Mantenimiento a OpoCoach.

    Antes de sustituir la base de destino, crea una copia de seguridad en:
        OpoCoach/db/copias_seguridad/
    """
    origen = RAIZ / "db" / "oposiciones.sqlite3"
    carpeta_opocoach = RAIZ.parent / "OpoCoach"
    destino = carpeta_opocoach / "db" / "oposiciones.sqlite3"
    carpeta_copias = carpeta_opocoach / "db" / "copias_seguridad"

    print("\nACTUALIZAR BASE DE DATOS DE OPOCOACH")
    print("-" * 78)
    print(f"Origen:  {origen}")
    print(f"Destino: {destino}")

    print("\nValidación obligatoria previa al despliegue...")
    if ejecutar_script("validacion_completa.py") != 0:
        print(
            "\nERROR: la base de mantenimiento no supera la validación completa. "
            "No se copia a OpoCoach."
        )
        pausa()
        return

    if not origen.is_file():
        print(f"\nERROR: no existe la base de datos de origen:\n{origen}")
        pausa()
        return

    if not carpeta_opocoach.is_dir():
        print(f"\nERROR: no existe la carpeta del proyecto OpoCoach:\n{carpeta_opocoach}")
        pausa()
        return

    if not destino.parent.is_dir():
        print(f"\nERROR: no existe la carpeta de destino:\n{destino.parent}")
        pausa()
        return

    print(
        "\nSe sustituirá la base de datos de OpoCoach por la versión "
        "actual de OpoCoach-Mantenimiento."
    )

    if not pedir_si_no("¿Continuar?"):
        print("Operación cancelada.")
        pausa()
        return

    try:
        if destino.is_file():
            from datetime import datetime

            carpeta_copias.mkdir(parents=True, exist_ok=True)
            marca = datetime.now().strftime("%Y%m%d_%H%M%S")
            copia = (
                carpeta_copias
                / f"oposiciones_antes_actualizacion_{marca}.sqlite3"
            )
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

        print("\nBase de datos actualizada correctamente.")
        print(f"Archivo actualizado:\n{destino}")

    except Exception as error:
        print(f"\nERROR al actualizar la base de datos: {error}")

    pausa()



def validacion_completa() -> None:
    print(
        "\nSe ejecutará una validación completa de solo lectura: integridad SQLite, "
        "constructores de banco en modo revisión para todas las convocatorias, "
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


def importar_temario_manual() -> None:
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
    que tienen texto oficial enlazado y preguntas de ejemplo en el banco de la
    convocatoria. No modifica la base de datos.
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
            print("\nNo hay temas con referencias jurídicas utilizables en esta convocatoria.")
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

        print("\nTipo de pregunta")
        print("1. TEORICA")
        print("2. PRACTICA")
        tipo_op = input("Opción [1]: ").strip() or "1"
        if tipo_op not in {"1", "2"}:
            print("Opción no válida.")
            pausa()
            continue
        tipo = "TEORICA" if tipo_op == "1" else "PRACTICA"

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
        "Primero revisa TODAS las convocatorias; solo después permite aplicar.",
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


def configurar_modelo_examen_menu() -> None:
    cabecera_submenu(
        "CONFIGURAR MODELO DE EXAMEN",
        "[ESCRIBE · BACKUP] Define por convocatoria los bloques normativos del modelo.",
    )
    ejecutar_script("configurar_modelo_examen.py")
    pausa()


def submenu_flujo_habitual() -> None:
    while True:
        cabecera_submenu(
            "1. FLUJO HABITUAL",
            "Operaciones completas. Cada proceso de escritura termina dejando los bancos sincronizados "
            "y la aplicación validada, salvo que se indique expresamente lo contrario.",
        )
        print("1. Mantenimiento completo de preguntas                [IMPORTA → BANCOS → VALIDA]")
        print("2. Recuperar preguntas PENDIENTE                      [IA → BANCOS → VALIDA]")
        print("3. Generar preguntas jurídicas IA                     [IA → BANCOS → VALIDA]")
        print("4. Generar preguntas de informática IA                [IA → BANCOS → VALIDA]")
        print("5. Sincronizar todos los bancos                       [REVISIÓN → APLICAR → VALIDA]")
        print("6. Actualizar BD de OpoCoach                          [VALIDA → BACKUP → COPIA]")
        print("7. Ver resumen general del lote                       [SOLO LECTURA]")
        print("8. Ver resumen de banco de convocatoria               [SOLO LECTURA]")
        print("0. Volver")
        op = input("Opción: ").strip()
        if op == "0": return
        acciones = {
            "1": mantenimiento_preguntas,
            "2": recuperar_pendientes,
            "3": generar_juridicas_ia_menu,
            "4": generar_informatica_menu,
            "5": sincronizar_todos_bancos_menu,
            "6": actualizar_bd_opocoach,
            "7": mostrar_resumen_lote_preguntas,
            "8": mostrar_resumen_banco_convocatoria,
        }
        fn = acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_convocatorias() -> None:
    while True:
        cabecera_submenu(
            "2. CONVOCATORIAS, TEMARIOS Y CORPUS",
            "Alta y mantenimiento de la estructura oficial. Las reparaciones BOE son herramientas avanzadas.",
        )
        print("1. Extraer temario desde PDF                          [CREA CSV]")
        print("2. Alta de convocatoria y temario                     [VALIDA → ESCRIBE]")
        print("3. Importar/sincronizar temario.csv existente         [AVANZADO]")
        print("4. Construir o validar corpus jurídico                [BOE]")
        print("5. Resolver/reparar referencias BOE                   [AVANZADO]")
        print("6. Auditar corpus jurídico                            [SOLO LECTURA]")
        print("7. Configurar modelo de examen                        [ESCRIBE · BACKUP]")
        print("8. Localizar norma / índice / alcance BOE             [CONSULTA WEB]")
        print("9. Consultar artículo consolidado BOE                 [CONSULTA WEB]")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={
            "1":extraer_temario_convocatoria, "2":alta_convocatoria,
            "3":importar_temario_manual, "4":construir_corpus,
            "5":resolver_referencias_boe_menu, "6":auditar_corpus_temario_menu,
            "7":configurar_modelo_examen_menu, "8":localizador_normativa_menu,
            "9":consultar_articulo_boe_menu,
        }
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_preguntas() -> None:
    while True:
        cabecera_submenu(
            "3. PREGUNTAS E IMPORTACIONES AVANZADAS",
            "Componentes individuales para diagnóstico o corrección. "
            "No sustituyen al mantenimiento completo del Flujo habitual.",
        )
        print("1. Importar exámenes oficiales/apoyo                  [PARCIAL]")
        print("2. Importar tests visuales PDF/PNG                    [PARCIAL · IA]")
        print("3. Importar tests academia estructurados              [PARCIAL]")
        print("4. Importar tests academia texto                      [PARCIAL]")
        print("5. Importar preguntas de informática                  [PARCIAL · IA]")
        print("6. Revisar importaciones problemáticas                [SOLO LECTURA]")
        print("7. Depurar duplicados exactos                         [ESCRIBE]")
        print("8. Normalizar/clasificar preguntas                    [REVISIÓN/APLICAR]")
        print("9. Reconstruir catálogo y enlaces                     [ESCRIBE]")
        print("10. Validar normalización jurídica                    [SOLO LECTURA]")
        print("11. Listar preguntas PENDIENTE                        [SOLO LECTURA]")
        print("12. Buscar pregunta por norma/artículo                [SOLO LECTURA]")
        print("13. Modificar una pregunta manualmente                [ESCRIBE]")
        print("14. Auditar vigencia jurídica                         [REVISIÓN/APLICAR]")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={
            "1":importar_examenes_directo,
            "2":lambda: ejecutar_importador_individual("importar_tests_imagen.py","tests visuales","PDF y PNG GoFullPage"),
            "3":lambda: ejecutar_importador_individual("importar_tests_academia.py","tests academia estructurados","PDF"),
            "4":lambda: ejecutar_importador_individual("importar_tests_academia_texto.py","tests academia texto","PDF"),
            "5":lambda: ejecutar_importador_individual("importar_preguntas_informatica.py","preguntas informática","PDF"),
            "6":revisar_importaciones, "7":depurar_duplicados_directo,
            "8":enriquecer_preguntas_directo, "9":actualizar_catalogo_y_enlaces,
            "10":validar_normalizacion_juridicas_menu, "11":listar_pendientes,
            "12":buscar_preguntas, "13":modificar_pregunta_manual,
            "14":auditar_vigencia_preguntas,
        }
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_bancos() -> None:
    while True:
        cabecera_submenu(
            "4. BANCOS DE PREGUNTAS · AVANZADO",
            "El flujo normal sincroniza todos los bancos automáticamente. "
            "Estas opciones son para diagnóstico o intervención concreta.",
        )
        print("1. Actualizar UN banco                                [VISTA PREVIA → GUARDAR]")
        print("2. Auditar UN banco                                   [SOLO LECTURA]")
        print("3. Auditar selección de TODOS los bancos              [SOLO LECTURA]")
        print("4. Auditoría global lote ↔ banco                      [SOLO LECTURA]")
        print("5. Reparar asignación de partes                       [REVISIÓN → BACKUP/APLICAR]")
        print("6. Depurar obsoletas/incompletas de TODOS los bancos [REVISIÓN → BACKUP/APLICAR]")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={
            "1":actualizar_banco, "2":auditar_banco,
            "3":auditar_bancos_seleccion_menu, "4":auditar_consistencia_global_menu,
            "5":reparar_partes_banco_menu, "6":depurar_bancos_vigencia_normalizacion_menu,
        }
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_auditorias() -> None:
    while True:
        cabecera_submenu(
            "5. AUDITORÍAS Y DIAGNÓSTICO",
            "Herramientas de verificación. La validación completa es la prueba de regresión principal.",
        )
        print("1. Validación completa                                [SOLO LECTURA]")
        print("2. Auditoría general de la base                       [SOLO LECTURA]")
        print("3. Auditoría de selección de bancos                   [SOLO LECTURA]")
        print("4. Auditoría funcional de un banco                    [SOLO LECTURA]")
        print("5. Auditoría global lote ↔ banco                      [SOLO LECTURA]")
        print("6. Auditoría de estructura del banco                  [SOLO LECTURA]")
        print("7. Auditoría del corpus/temario                       [SOLO LECTURA]")
        print("8. Auditar posibles objetos obsoletos                 [SOLO LECTURA]")
        print("9. Inventariar denominaciones de normas               [SOLO LECTURA]")
        print("10. Buscar norma por respuesta correcta               [IA · DIAGNÓSTICO]")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={
            "1":validacion_completa, "2":auditar_bd_directo,
            "3":auditar_bancos_seleccion_menu, "4":auditar_banco,
            "5":auditar_consistencia_global_menu, "6":auditar_estructura_banco_menu,
            "7":auditar_corpus_temario_menu, "8":auditar_esquema_menu,
            "9":inventariar_normas_menu, "10":buscar_norma_respuesta_correcta_menu,
        }
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def submenu_administracion() -> None:
    while True:
        cabecera_submenu(
            "6. ADMINISTRACIÓN",
            "Despliegue, limpieza conservadora e información técnica.",
        )
        print("1. Actualizar BD de OpoCoach                           [VALIDA → BACKUP → COPIA]")
        print("2. Limpiar logs/auditorías temporales                 [VISTA PREVIA → APLICAR]")
        print("3. Mostrar componentes internos                       [INFORMATIVO]")
        print("0. Volver")
        op=input("Opción: ").strip()
        if op=="0": return
        acciones={
            "1":actualizar_bd_opocoach,
            "2":limpiar_temporales_menu,
            "3":mostrar_componentes_internos,
        }
        fn=acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


def mostrar_menu() -> None:
    limpiar_pantalla()
    print("=" * 78)
    print("OPOCOACH - MANTENIMIENTO ESTABLE")
    print("=" * 78)
    print("Las operaciones normales completas están en 'Flujo habitual'.")
    print("Las operaciones parciales o de reparación están separadas como avanzadas.")
    print("-" * 78)
    print("1. Flujo habitual                    Operaciones completas")
    print("2. Convocatorias, temarios y corpus  Estructura oficial y normativa")
    print("3. Preguntas e importaciones          Herramientas parciales/avanzadas")
    print("4. Bancos de preguntas                Diagnóstico/intervención avanzada")
    print("5. Auditorías y diagnóstico           Verificación y regresión")
    print("6. Administración                     Despliegue y limpieza")
    print("0. Salir")
    print("=" * 78)

def main() -> int:
    if not CARPETA_SCRIPTS.is_dir():
        print(f"No existe la carpeta de scripts: {CARPETA_SCRIPTS}")
        return 1

    acciones = {
        "1": submenu_flujo_habitual,
        "2": submenu_convocatorias,
        "3": submenu_preguntas,
        "4": submenu_bancos,
        "5": submenu_auditorias,
        "6": submenu_administracion,
    }

    while True:
        mostrar_menu()
        opcion = input("Opción: ").strip()
        if opcion == "0":
            print("\nFin del mantenimiento.")
            return 0
        accion = acciones.get(opcion)
        if accion is None:
            print("\nOpción no válida.")
            pausa()
            continue
        try:
            accion()
        except KeyboardInterrupt:
            print("\n\nOperación cancelada por el usuario.")
            pausa()
        except Exception as error:
            print(f"\nERROR inesperado: {error}")
            pausa()

