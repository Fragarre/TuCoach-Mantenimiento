"""
==============================================================================
Proyecto : OpoCoach-Mantenimiento
Archivo  : scripts/generar_preguntas_informatica.py

Objetivo:
    Generar mediante IA preguntas de informática con cuatro opciones y una
    respuesta correcta, validarlas e incorporarlas a lote_preguntas.

Flujo:
    1. Lee preguntas reales de lote_preguntas como referencia de estilo.
    2. Genera lotes JSON por categoría mediante scripts/openai_api.py.
    3. Valida estructura, respuestas y duplicados.
    4. Guarda una copia CSV de auditoría.
    5. En modo --guardar registra una importación e inserta en lote_preguntas.

No modifica:
    - Las reglas de banco no se implementan aquí: tras guardar se invoca
      el sincronizador común, que reutiliza mantener_banco_preguntas.py.
    - Las tablas ni su estructura.

Uso:
    Vista previa:
        python scripts/generar_preguntas_informatica.py

    Guardado:
        python scripts/generar_preguntas_informatica.py --guardar

    Generar solo una categoría:
        python scripts/generar_preguntas_informatica.py \
            --categoria SEGURIDAD --cantidad 100 --guardar
==============================================================================
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sincronizar_bancos import sincronizar_todos_bancos

from openai_api import seleccionar_fragmento_json


ROOT = Path(__file__).resolve().parents[1]
DB_PREDETERMINADA = ROOT / "db" / "oposiciones.sqlite3"
AUDITORIAS = ROOT / "auditorias"
MODELO_PREDETERMINADO = "gpt-5.4-nano"
TIPO_FUENTE = "ia_generada"
ORIGEN_OPOSICION_PREDETERMINADO = "C2"
TAMANO_LOTE_PREDETERMINADO = 5
EJEMPLOS_POR_CATEGORIA = 12

# Distribución inicial. Puede modificarse aquí o limitarse con los argumentos
# --categoria y --cantidad.
PLAN_GENERACION = {
    "SISTEMA_OPERATIVO": {
        "cantidad": 100,
        "subtemas": [
            ("Fundamentos y entorno gráfico de Windows 11", 12),
            ("Ventanas, iconos, menús contextuales y cuadros de diálogo", 14),
            ("Escritorio y sus elementos", 10),
            ("Menú Inicio y barra de tareas", 12),
            ("Calculadora, Bloc de notas y Herramienta Recortes", 12),
            ("Explorador de archivos de Windows 11", 12),
            ("Gestión de carpetas y archivos", 12),
            ("Búsqueda de archivos y carpetas", 8),
            ("Carpetas locales, carpetas de red, accesos directos y Descargas", 8),
        ],
    },
    "NAVEGADORES": {
        "cantidad": 60,
        "subtemas": [
            ("Conceptos básicos y definición de navegador web", 8),
            ("Navegación por pestañas", 8),
            ("Marcadores o favoritos", 8),
            ("Historial de navegación", 8),
            ("Ajustes de privacidad y seguridad", 10),
            ("Extensiones y complementos", 8),
            ("Navegación segura e identificación de phishing y malware", 10),
        ],
    },
    "MICROSOFT_365": {
        "cantidad": 60,
        "subtemas": [
            ("Conceptos básicos de Microsoft OneDrive", 8),
            ("Almacenamiento y sincronización de archivos", 10),
            ("Organización de archivos y carpetas en OneDrive", 8),
            ("Acceso a documentos desde distintos dispositivos", 8),
            ("Compartición de archivos y carpetas", 10),
            ("Permisos básicos de compartición", 8),
            ("Recuperación y gestión básica de documentos", 8),
        ],
    },
    "OUTLOOK": {
        "cantidad": 80,
        "subtemas": [
            ("Conceptos elementales y entorno de trabajo de Outlook", 8),
            ("Redacción y envío de mensajes", 10),
            ("Recepción, respuesta y respuesta a todos", 8),
            ("Reenvío de mensajes", 6),
            ("Archivos adjuntos", 8),
            ("Búsqueda de mensajes", 8),
            ("Reglas de recepción de mensajes", 8),
            ("Libretas de direcciones y contactos", 8),
            ("Carpetas de trabajo y organización del correo", 8),
            ("Calendario de trabajo, citas y reuniones", 8),
        ],
    },
    "TEAMS": {
        "cantidad": 80,
        "subtemas": [
            ("Entorno general de Microsoft Teams", 6),
            ("Chat", 8),
            ("Llamadas", 8),
            ("Estados de presencia", 6),
            ("Equipos", 8),
            ("Canales", 8),
            ("Conversaciones y publicaciones", 8),
            ("Compartición de información y archivos", 8),
            ("Menciones", 6),
            ("Convocatorias y reuniones por videoconferencia", 14),
        ],
    },
    "OFIMATICA_WORD": {
        "cantidad": 120,
        "subtemas": [
            ("Principales funciones y utilidades de Word", 8),
            ("Creación de documentos", 8),
            ("Edición y selección de texto", 10),
            ("Formato de caracteres", 10),
            ("Formato de párrafos", 10),
            ("Estructuración de documentos", 10),
            ("Maquetación y configuración de página", 10),
            ("Tablas e imágenes", 10),
            ("Grabación y recuperación de documentos", 10),
            ("Impresión de documentos", 8),
            ("Importación y exportación de formatos", 8),
            ("Dictado", 6),
            ("Revisión de documentos", 12),
        ],
    },
    "OFIMATICA_EXCEL": {
        "cantidad": 120,
        "subtemas": [
            ("Principales funciones y utilidades de Excel", 6),
            ("Libros, hojas, filas, columnas y celdas", 10),
            ("Configuración de hojas y libros", 8),
            ("Introducción de datos", 8),
            ("Edición y formato de datos", 10),
            ("Fórmulas y operadores", 12),
            ("Referencias relativas, absolutas y mixtas", 10),
            ("Funciones básicas", 14),
            ("Gráficos", 10),
            ("Ordenación y filtrado", 10),
            ("Gestión de listas y datos", 12),
            ("Importación de datos", 10),
        ],
    },
    "INTELIGENCIA_ARTIFICIAL": {
        "cantidad": 60,
        "subtemas": [
            ("Conceptos básicos de herramientas de inteligencia artificial", 8),
            ("Usos administrativos de la IA", 8),
            ("Elaboración de borradores y resúmenes", 8),
            ("Fiabilidad y necesidad de verificar resultados", 10),
            ("Riesgos en el puesto de trabajo", 8),
            ("Privacidad y protección de la información", 8),
            ("Prompting básico", 10),
        ],
    },
    "SEGURIDAD": {
        "cantidad": 80,
        "subtemas": [
            ("Contraseñas seguras", 8),
            ("Autenticación multifactor", 8),
            ("Phishing", 10),
            ("Malware", 8),
            ("Actualizaciones de seguridad", 8),
            ("Copias de seguridad", 8),
            ("Permisos y control de acceso", 8),
            ("Protección de datos en el puesto de trabajo", 10),
            ("Navegación y descarga segura", 12),
        ],
    },
}


@dataclass(frozen=True)
class Pregunta:
    enunciado: str
    opcion_a: str
    opcion_b: str
    opcion_c: str
    opcion_d: str
    respuesta_correcta: str
    tema_no_juridico: str


class ErrorProceso(RuntimeError):
    pass


def ahora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalizar_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.casefold()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return " ".join(texto.split())


def limpiar_opcion(texto: Any) -> str:
    valor = str(texto or "").strip()
    valor = re.sub(r"^[a-dA-D][\)\.\-:]\s*", "", valor)
    return " ".join(valor.split())


def hash_importacion(contenido: str) -> str:
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def comprobar_esquema(con: sqlite3.Connection) -> None:
    requeridas = {
        "lote_preguntas": {
            "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d",
            "respuesta_correcta", "tipo_clasificacion", "tema_no_juridico",
            "origen_oposicion", "tipo_fuente", "importacion_fichero_id",
            "pagina_origen",
        },
        "importaciones_ficheros": {
            "ruta_relativa", "nombre_fichero", "hash_sha256", "tipo_fuente",
            "estado", "paginas_totales", "paginas_insertadas", "paginas_omitidas",
            "paginas_error", "fecha_inicio", "fecha_fin", "reimportar",
            "ultimo_error",
        },
    }
    for tabla, columnas in requeridas.items():
        existentes = {
            fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")
        }
        faltan = sorted(columnas - existentes)
        if faltan:
            raise ErrorProceso(
                f"La tabla {tabla} no tiene las columnas esperadas: {', '.join(faltan)}"
            )


def cargar_enunciados_existentes(con: sqlite3.Connection) -> set[str]:
    return {
        normalizar_texto(fila[0])
        for fila in con.execute("SELECT enunciado FROM lote_preguntas")
        if fila[0]
    }


def cargar_ejemplos(
    con: sqlite3.Connection,
    categoria: str,
    limite: int = EJEMPLOS_POR_CATEGORIA,
) -> list[dict[str, str]]:
    filas = con.execute(
        """
        SELECT enunciado, opcion_a, opcion_b, opcion_c, opcion_d, respuesta_correcta
        FROM lote_preguntas
        WHERE tipo_clasificacion = 'INFORMATICA'
          AND tema_no_juridico = ?
        ORDER BY id
        LIMIT ?
        """,
        (categoria, limite),
    ).fetchall()

    if len(filas) < min(5, limite):
        restantes = limite - len(filas)
        filas_generales = con.execute(
            """
            SELECT enunciado, opcion_a, opcion_b, opcion_c, opcion_d, respuesta_correcta
            FROM lote_preguntas
            WHERE tipo_clasificacion = 'INFORMATICA'
              AND tema_no_juridico <> ?
            ORDER BY id
            LIMIT ?
            """,
            (categoria, restantes),
        ).fetchall()
        filas.extend(filas_generales)

    return [
        {
            "enunciado": f[0],
            "opcion_a": f[1],
            "opcion_b": f[2],
            "opcion_c": f[3],
            "opcion_d": f[4],
            "respuesta_correcta": f[5],
        }
        for f in filas
    ]


def construir_prompt(
    categoria: str,
    subtema: str,
    cantidad: int,
    ejemplos: list[dict[str, str]],
    ya_generadas: list[str],
) -> str:
    return f"""
Eres un redactor especializado en preguntas tipo test para oposiciones del grupo C2
administrativo en España.

Genera exactamente {cantidad} preguntas NUEVAS.

CATEGORÍA INTERNA:
{categoria}

EPÍGRAFE EXCLUSIVO DE ESTE LOTE:
{subtema}

No generes preguntas sobre otros epígrafes de la categoría.

REGLAS OBLIGATORIAS:
1. Cada pregunta tendrá un enunciado claro y cuatro opciones plausibles.
2. Habrá una sola respuesta correcta, indicada exclusivamente con A, B, C o D.
3. Nivel básico o intermedio de usuario, apropiado para una oposición C2.
4. Alterna preguntas conceptuales, funcionales y pequeños supuestos de uso.
5. No preguntes por datos efímeros, precios, licencias ni cifras cambiantes.
6. No inventes funciones, menús, atajos ni capacidades.
7. Evita ambigüedades, dobles negaciones y preguntas discutibles.
8. No repitas ni reformules las preguntas de ejemplo ni las ya generadas.
9. Distribuye equilibradamente la respuesta correcta entre A, B, C y D.
10. Usa español de España.
11. Devuelve únicamente JSON válido, sin Markdown ni comentarios.
12. No utilices comillas dobles dentro del contenido de ningún campo JSON.
    Si necesitas mencionar el nombre de una opción, botón, comando o menú,
    escríbelo sin comillas. Por ejemplo: Guardar como, Copiar, Pegar o Vista.
13. No incluyas el carácter de comillas dobles (\") dentro de enunciados ni
    opciones. Las únicas comillas dobles permitidas son las necesarias para
    delimitar las claves y valores del propio JSON.
14. Antes de responder, verifica que el JSON puede analizarse correctamente y
    que todas las cadenas están cerradas.

FORMATO EXACTO:
{{
  "preguntas": [
    {{
      "enunciado": "...",
      "opcion_a": "...",
      "opcion_b": "...",
      "opcion_c": "...",
      "opcion_d": "...",
      "respuesta_correcta": "A"
    }}
  ]
}}

EJEMPLOS DE ESTILO DEL BANCO ACTUAL:
{json.dumps(ejemplos, ensure_ascii=False, indent=2)}

TODOS LOS ENUNCIADOS YA GENERADOS EN ESTA CATEGORÍA:
{json.dumps(ya_generadas, ensure_ascii=False, indent=2)}
""".strip()



def validar_respuesta_json(
    datos: Any,
    categoria: str,
    cantidad_esperada: int,
    existentes: set[str],
    generados: set[str],
) -> tuple[list[Pregunta], list[str]]:
    errores: list[str] = []
    if not isinstance(datos, dict) or not isinstance(datos.get("preguntas"), list):
        return [], ["La respuesta no contiene una lista 'preguntas'."]

    elementos = datos["preguntas"]
    if len(elementos) != cantidad_esperada:
        errores.append(
            f"Se esperaban {cantidad_esperada} preguntas y se recibieron {len(elementos)}."
        )

    resultado: list[Pregunta] = []
    for indice, item in enumerate(elementos, start=1):
        if not isinstance(item, dict):
            errores.append(f"Elemento {indice}: no es un objeto JSON.")
            continue

        enunciado = " ".join(str(item.get("enunciado") or "").split())
        opciones = {
            "A": limpiar_opcion(item.get("opcion_a")),
            "B": limpiar_opcion(item.get("opcion_b")),
            "C": limpiar_opcion(item.get("opcion_c")),
            "D": limpiar_opcion(item.get("opcion_d")),
        }
        correcta = str(item.get("respuesta_correcta") or "").strip().upper()

        if len(enunciado) < 20:
            errores.append(f"Elemento {indice}: enunciado vacío o demasiado corto.")
            continue
        if any(len(v) < 1 for v in opciones.values()):
            errores.append(f"Elemento {indice}: falta alguna opción.")
            continue
        if len({normalizar_texto(v) for v in opciones.values()}) != 4:
            errores.append(f"Elemento {indice}: contiene opciones repetidas.")
            continue
        if correcta not in {"A", "B", "C", "D"}:
            errores.append(f"Elemento {indice}: respuesta correcta inválida: {correcta!r}.")
            continue

        clave = normalizar_texto(enunciado)
        if clave in existentes:
            errores.append(f"Elemento {indice}: duplicada en lote_preguntas: {enunciado}")
            continue
        if clave in generados:
            errores.append(f"Elemento {indice}: duplicada en esta ejecución: {enunciado}")
            continue

        generados.add(clave)
        resultado.append(
            Pregunta(
                enunciado=enunciado,
                opcion_a=opciones["A"],
                opcion_b=opciones["B"],
                opcion_c=opciones["C"],
                opcion_d=opciones["D"],
                respuesta_correcta=correcta,
                tema_no_juridico=categoria,
            )
        )

    return resultado, errores


def generar_categoria(
    con: sqlite3.Connection,
    categoria: str,
    subtemas: list[tuple[str, int]],
    cantidad: int,
    tamano_lote: int,
    modelo: str,
    existentes: set[str],
    generados_globales: set[str],
) -> list[Pregunta]:
    ejemplos = cargar_ejemplos(con, categoria)
    acumuladas: list[Pregunta] = []

    total_subtemas = sum(cantidad_subtema for _, cantidad_subtema in subtemas)
    if total_subtemas != cantidad:
        raise ErrorProceso(
            f"El plan de {categoria} suma {total_subtemas} preguntas, "
            f"pero la categoría declara {cantidad}."
        )

    for numero_subtema, (subtema, objetivo_subtema) in enumerate(subtemas, start=1):
        acumuladas_subtema = 0
        lotes_sin_validas = 0
        max_lotes_sin_validas = 4

        print(
            f"  Epígrafe {numero_subtema}/{len(subtemas)}: "
            f"{subtema} ({objetivo_subtema})"
        )

        while acumuladas_subtema < objetivo_subtema:
            pendientes = objetivo_subtema - acumuladas_subtema
            bloque = min(tamano_lote, 5, pendientes)

            prompt = construir_prompt(
                categoria=categoria,
                subtema=subtema,
                cantidad=bloque,
                ejemplos=ejemplos,
                ya_generadas=[p.enunciado for p in acumuladas],
            )

            datos = seleccionar_fragmento_json(
                prompt=prompt,
                modelo=modelo,
                operacion=(
                    f"generar_preguntas_informatica_"
                    f"{categoria.lower()}_epigrafe_{numero_subtema}"
                ),
            )
            preguntas, errores = validar_respuesta_json(
                datos=datos,
                categoria=categoria,
                cantidad_esperada=bloque,
                existentes=existentes,
                generados=generados_globales,
            )

            if errores:
                print(f"    Se descartaron {len(errores)} incidencias:")
                for error in errores[:10]:
                    print(f"      - {error}")
                if len(errores) > 10:
                    print(f"      - ... y {len(errores) - 10} incidencias más.")

            if preguntas:
                espacio = objetivo_subtema - acumuladas_subtema
                aceptadas = preguntas[:espacio]
                acumuladas.extend(aceptadas)
                acumuladas_subtema += len(aceptadas)
                lotes_sin_validas = 0
                print(
                    f"    Epígrafe: {acumuladas_subtema}/{objetivo_subtema} | "
                    f"Categoría: {len(acumuladas)}/{cantidad}"
                )
            else:
                lotes_sin_validas += 1
                print(
                    f"    Lote sin preguntas válidas "
                    f"({lotes_sin_validas}/{max_lotes_sin_validas})."
                )
                if lotes_sin_validas >= max_lotes_sin_validas:
                    raise ErrorProceso(
                        f"No se han obtenido preguntas válidas para el epígrafe "
                        f"'{subtema}' de {categoria} en "
                        f"{max_lotes_sin_validas} lotes consecutivos.\n"
                        "No se insertó ninguna pregunta."
                    )

    return acumuladas

def guardar_csv(preguntas: list[Pregunta], carpeta: Path) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "preguntas_generadas.csv"
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d",
                "respuesta_correcta", "tipo_clasificacion", "tema_no_juridico",
                "origen_oposicion", "tipo_fuente",
            ],
            delimiter=";",
        )
        writer.writeheader()
        for p in preguntas:
            writer.writerow(
                {
                    "enunciado": p.enunciado,
                    "opcion_a": p.opcion_a,
                    "opcion_b": p.opcion_b,
                    "opcion_c": p.opcion_c,
                    "opcion_d": p.opcion_d,
                    "respuesta_correcta": p.respuesta_correcta,
                    "tipo_clasificacion": "INFORMATICA",
                    "tema_no_juridico": p.tema_no_juridico,
                    "origen_oposicion": ORIGEN_OPOSICION_PREDETERMINADO,
                    "tipo_fuente": TIPO_FUENTE,
                }
            )
    return ruta


def insertar_preguntas(
    con: sqlite3.Connection,
    preguntas: list[Pregunta],
    ruta_csv: Path,
    origen_oposicion: str,
) -> int:
    inicio = ahora_iso()
    contenido_hash = hash_importacion(ruta_csv.read_text(encoding="utf-8-sig"))
    ruta_relativa = str(ruta_csv.relative_to(ROOT)).replace("\\", "/")

    existente = con.execute(
        "SELECT id FROM importaciones_ficheros WHERE hash_sha256 = ?",
        (contenido_hash,),
    ).fetchone()
    if existente:
        raise ErrorProceso(
            f"Esta generación ya fue importada (importacion_fichero_id={existente[0]})."
        )

    cur = con.execute(
        """
        INSERT INTO importaciones_ficheros (
            ruta_relativa, nombre_fichero, hash_sha256, tipo_fuente, estado,
            paginas_totales, paginas_insertadas, paginas_omitidas, paginas_error,
            fecha_inicio, fecha_fin, reimportar, ultimo_error
        ) VALUES (?, ?, ?, ?, 'EN_PROCESO', ?, 0, 0, 0, ?, NULL, 0, NULL)
        """,
        (
            ruta_relativa,
            ruta_csv.name,
            contenido_hash,
            TIPO_FUENTE,
            len(preguntas),
            inicio,
        ),
    )
    importacion_id = int(cur.lastrowid)

    for numero, p in enumerate(preguntas, start=1):
        con.execute(
            """
            INSERT INTO lote_preguntas (
                enunciado, opcion_a, opcion_b, opcion_c, opcion_d,
                respuesta_correcta, tipo_clasificacion, tipo_norma, nombre_norma,
                articulo, tema_no_juridico, origen_oposicion, tipo_fuente,
                importacion_fichero_id, pagina_origen, norma_id_normalizada,
                articulo_normalizado, teorica_practica, tipo_norma_normalizado,
                nombre_norma_normalizado
            ) VALUES (
                ?, ?, ?, ?, ?, ?, 'INFORMATICA', NULL, NULL, NULL, ?, ?, ?, ?, ?,
                NULL, NULL, NULL, NULL, NULL
            )
            """,
            (
                p.enunciado,
                p.opcion_a,
                p.opcion_b,
                p.opcion_c,
                p.opcion_d,
                p.respuesta_correcta,
                p.tema_no_juridico,
                origen_oposicion,
                TIPO_FUENTE,
                importacion_id,
                numero,
            ),
        )

    con.execute(
        """
        UPDATE importaciones_ficheros
        SET estado = 'COMPLETADO',
            paginas_insertadas = ?,
            fecha_fin = ?
        WHERE id = ?
        """,
        (len(preguntas), ahora_iso(), importacion_id),
    )
    return importacion_id


def _repartir_proporcional(
    elementos: list[tuple[str, int]],
    total_nuevo: int,
) -> list[tuple[str, int]]:
    """
    Reparte un total entero proporcionalmente a los pesos originales,
    conservando exactamente la suma solicitada.
    """
    if total_nuevo < 1:
        raise ErrorProceso("La cantidad debe ser mayor que cero.")

    peso_total = sum(peso for _, peso in elementos)
    if peso_total <= 0:
        raise ErrorProceso("El plan de generación no contiene pesos válidos.")

    calculos: list[tuple[str, int, float]] = []
    asignadas = 0

    for nombre, peso in elementos:
        cuota = total_nuevo * peso / peso_total
        base = int(cuota)
        resto = cuota - base
        calculos.append((nombre, base, resto))
        asignadas += base

    faltan = total_nuevo - asignadas
    orden = sorted(
        range(len(calculos)),
        key=lambda indice: calculos[indice][2],
        reverse=True,
    )

    cantidades = [base for _, base, _ in calculos]
    for indice in orden[:faltan]:
        cantidades[indice] += 1

    return [
        (calculos[indice][0], cantidades[indice])
        for indice in range(len(calculos))
        if cantidades[indice] > 0
    ]


def _configuracion_categoria(
    categoria: str,
    cantidad: int,
) -> dict[str, Any]:
    original = PLAN_GENERACION[categoria]
    subtemas = _repartir_proporcional(
        list(original["subtemas"]),
        cantidad,
    )
    return {
        "cantidad": cantidad,
        "subtemas": subtemas,
    }


def construir_plan(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    categoria = (
        args.categoria.strip().upper()
        if args.categoria
        else None
    )

    if categoria is not None and categoria not in PLAN_GENERACION:
        permitidas = ", ".join(PLAN_GENERACION)
        raise ErrorProceso(
            f"Categoría no contemplada: {categoria}. "
            f"Permitidas: {permitidas}"
        )

    if args.cantidad is not None and args.cantidad < 1:
        raise ErrorProceso("--cantidad debe ser mayor que cero.")

    if categoria is None and args.cantidad is None:
        return PLAN_GENERACION

    if categoria is not None:
        if args.cantidad is None:
            raise ErrorProceso(
                "Con --categoria debe indicar también --cantidad."
            )
        return {
            categoria: _configuracion_categoria(
                categoria,
                args.cantidad,
            )
        }

    categorias_repartidas = _repartir_proporcional(
        [
            (nombre, int(configuracion["cantidad"]))
            for nombre, configuracion in PLAN_GENERACION.items()
        ],
        args.cantidad,
    )

    return {
        nombre: _configuracion_categoria(nombre, cantidad)
        for nombre, cantidad in categorias_repartidas
    }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera preguntas de informática y las incorpora a lote_preguntas."
    )
    parser.add_argument("--db", type=Path, default=DB_PREDETERMINADA)
    parser.add_argument("--guardar", action="store_true")
    parser.add_argument("--categoria")
    parser.add_argument("--cantidad", type=int)
    parser.add_argument("--tamano-lote", type=int, default=TAMANO_LOTE_PREDETERMINADO)
    parser.add_argument("--modelo", default=MODELO_PREDETERMINADO)
    parser.add_argument(
        "--origen-oposicion",
        default=ORIGEN_OPOSICION_PREDETERMINADO,
        choices=["A1", "A2", "C1", "C2"],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.tamano_lote < 1 or args.tamano_lote > 50:
        raise ErrorProceso("--tamano-lote debe estar entre 1 y 50.")
    if not args.db.exists():
        raise ErrorProceso(f"No existe la base de datos: {args.db}")

    plan = construir_plan(args)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta_auditoria = AUDITORIAS / f"generar_informatica_ia_{sello}"

    print("=" * 78)
    print("GENERACIÓN DE PREGUNTAS DE INFORMÁTICA MEDIANTE IA")
    print(f"Base de datos: {args.db}")
    print(f"Modo: {'GUARDAR' if args.guardar else 'VISTA PREVIA'}")
    print(f"Modelo: {args.modelo}")
    print(f"Origen oposición: {args.origen_oposicion}")
    print(f"Total solicitado: {sum(v['cantidad'] for v in plan.values())}")
    print("=" * 78)

    con = sqlite3.connect(args.db)
    try:
        comprobar_esquema(con)
        existentes = cargar_enunciados_existentes(con)
        generados_globales: set[str] = set()
        todas: list[Pregunta] = []

        for categoria, cfg in plan.items():
            print(f"\nGenerando {categoria}: {cfg['cantidad']}")
            todas.extend(
                generar_categoria(
                    con=con,
                    categoria=categoria,
                    subtemas=cfg["subtemas"],
                    cantidad=int(cfg["cantidad"]),
                    tamano_lote=args.tamano_lote,
                    modelo=args.modelo,
                    existentes=existentes,
                    generados_globales=generados_globales,
                )
            )

        ruta_csv = guardar_csv(todas, carpeta_auditoria)
        print(f"\nCSV generado: {ruta_csv}")
        print(f"Preguntas válidas: {len(todas)}")

        if not args.guardar:
            print("\nVISTA PREVIA: no se ha modificado la base de datos.")
            print("Repita el comando con --guardar para insertar las preguntas.")
            return 0

        try:
            con.execute("BEGIN IMMEDIATE")
            importacion_id = insertar_preguntas(
                con=con,
                preguntas=todas,
                ruta_csv=ruta_csv,
                origen_oposicion=args.origen_oposicion,
            )
            con.commit()
        except Exception:
            con.rollback()
            raise

        print("\nINSERCIÓN COMPLETADA")
        print(f"Importación ID: {importacion_id}")
        print(f"Preguntas insertadas en lote_preguntas: {len(todas)}")

        # Flujo común: el generador no implementa reglas propias de banco.
        con.close()
        con = None
        sincronizar_todos_bancos(args.db, aplicar=True, validar_final=True)
        print("Preguntas y bancos sincronizados correctamente.")
        return 0

    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ErrorProceso as exc:
        print(f"\nPROCESO DETENIDO: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nProceso cancelado por el usuario.", file=sys.stderr)
        raise SystemExit(130)