"""
OpoCoach — orquestador seguro de enriquecimiento de preguntas.

RESPONSABILIDAD
---------------
Este script NO importa preguntas, NO elimina duplicados y NO sustituye al
orquestador de mantenimiento existente.

Actúa únicamente sobre lote_preguntas y, por defecto, solo completa campos
vacíos de preguntas jurídicas:

1. tipo_norma_normalizado y nombre_norma_normalizado
2. articulo_normalizado
3. teorica_practica

SEGURIDAD
---------
- Vista previa por defecto: no modifica la base sin --aplicar.
- No modifica tipo_norma, nombre_norma ni articulo.
- No sobrescribe normalizaciones existentes.
- No sobrescribe clasificaciones TEORICA/PRACTICA existentes.
- Crea una única copia de seguridad antes de escribir.
- Ejecuta todos los cambios en una única transacción.
- Las normas o artículos no automatizables se informan y quedan pendientes; no bloquean los cambios resolubles.
- No elimina preguntas.

REQUISITO
---------
Debe existir en scripts/:

    normalizar_lote_preguntas_definitivo.py

Se reutiliza únicamente su función determinista normalizar(); no se ejecuta su
proceso de borrado ni su escritura masiva.

USO
---
Vista previa:

    python scripts/enriquecer_preguntas.py

Aplicar solo a campos pendientes:

    python scripts/enriquecer_preguntas.py --aplicar

Otra base:

    python scripts/enriquecer_preguntas.py --db ruta/base.sqlite3
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DB_PREDETERMINADA = ROOT / "db" / "oposiciones.sqlite3"
CARPETA_COPIAS = ROOT / "db" / "copias_seguridad"
NORMALIZADOR_NORMAS = SCRIPTS / "normalizar_lote_preguntas_definitivo.py"

SUFIJOS_ARTICULO = (
    "bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies"
)
TOKEN_ARTICULO = (
    rf"\d+(?:(?:\.\d+)+)?"
    rf"(?:\s+(?:{SUFIJOS_ARTICULO}))?"
    rf"(?:\.[A-Za-z])?"
    rf"|"
    rf"\d+\s+(?:{SUFIJOS_ARTICULO})(?:(?:\.\d+)+)?(?:\.[A-Za-z])?"
)
RX_ARTICULO_EXPLICITO = re.compile(
    rf"(?i)\b(?:art[ií]culo|art\.)\s*({TOKEN_ARTICULO})"
)
RX_ARTICULO_INICIAL = re.compile(
    rf"(?i)^\s*({TOKEN_ARTICULO})(?=\s*(?:$|\)|,|;|de\b|del\b|apartado\b|\(|t[ií]tulo\b|cap[ií]tulo\b|libro\b|parte\b))"
)
RX_APARTADO_ARTICULO = re.compile(r"(?i)\bapartado\s+(\d+(?:\.\d+)*)\b")
RX_LETRA_ARTICULO = re.compile(r"(?i)\bletra\s+([A-Za-z])\b")


def extraer_articulo_normalizado(valor: object) -> str | None:
    """Extrae una referencia de artículo solo cuando es inequívoca."""
    texto = " ".join(str(valor or "").split()).strip()
    if not texto:
        return None

    # No colapsamos varias referencias en una sola ni reducimos subpuntos
    # ordinales de forma potencialmente destructiva.
    if re.search(r"(?i)^\s*\d+(?:\.\w+)*\s+y\s+\d+", texto):
        return None

    coincidencia = RX_ARTICULO_EXPLICITO.search(texto)
    if coincidencia is None:
        coincidencia = RX_ARTICULO_INICIAL.search(texto)
    if coincidencia is None:
        return None

    resto = texto[coincidencia.end():]
    if re.match(r"^\)\s*\d+\s*\.?[ºª]", resto):
        return None

    articulo = re.sub(r"\s*\.\s*", ".", coincidencia.group(1).strip())
    articulo = re.sub(r"\s+", " ", articulo)
    if re.search(r"[A-Za-z]", articulo):
        articulo = articulo.lower()

    apartado = RX_APARTADO_ARTICULO.search(resto)
    if apartado and "." not in articulo.split(" ", 1)[0]:
        articulo += "." + apartado.group(1)

    letra = RX_LETRA_ARTICULO.search(resto)
    if letra and not re.search(r"\.[A-Za-z]$", articulo):
        articulo += "." + letra.group(1).lower()

    return articulo



# ---------------------------------------------------------------------------
# Clasificación TEORICA / PRACTICA
# ---------------------------------------------------------------------------

PATRONES_SUJETO = [
    r"\b(?:don|dona|señor|señora|sr\.?|sra\.?)\s+[a-záéíóúñ]",
    r"\buna? ciudadan[oa]\b",
    r"\buna? interesad[oa]\b",
    r"\buna? funcionari[oa]\b",
    r"\buna? emplead[oa]\s+public[oa]\b",
    r"\buna? trabajador(?:a)?\b",
    r"\buna? aspirante\b",
    r"\buna? solicitante\b",
    r"\buna? contratista\b",
    r"\buna? licitador(?:a)?\b",
    r"\buna? empresa\b",
    r"\buna? sociedad\b",
    r"\buna? mercantil\b",
    r"\buna? asociacion\b",
    r"\buna? ayuntamiento\b",
    r"\bla conselleria\s+[a-zx]\b",
]

PATRONES_CASO = [
    r"\bse presenta personalmente\b",
    r"\bha presentado\b",
    r"\bpresenta una solicitud\b",
    r"\binterpone (?:un|una)\b",
    r"\bsolicita (?:un|una|el|la)\b",
    r"\brecibe (?:un|una|el|la)\b",
    r"\bse le notifica\b",
    r"\bse le comunica\b",
    r"\besta tramitando\b",
    r"\bse encuentra tramitando\b",
    r"\bante la siguiente situacion\b",
    r"\bsupuesto practico\b",
    r"\bsupuesto de hecho\b",
    r"\ben el siguiente supuesto\b",
    r"\ben el caso planteado\b",
]

PATRONES_HIPOTESIS = [
    r"\ben caso de que\b",
    r"\ben el supuesto de que\b",
    r"\bsuponiendo que\b",
    r"\bsuponga que\b",
    r"\bimaginemos que\b",
    r"\bsi un(?:a)?\b",
    r"\bsi la persona\b",
    r"\bsi el interesado\b",
    r"\bsi la interesada\b",
    r"\bsi una persona\b",
    r"\bsi se presenta\b",
    r"\bsi se solicita\b",
    r"\bsi se interpone\b",
    r"\bsi se produce\b",
    r"\bque ocurriria si\b",
    r"\bque deberia hacer\b",
    r"\bcomo debera actuar\b",
    r"\bque procederia\b",
]

PATRONES_DETALLE = [
    r"\b\d{1,2}:\d{2}\s*(?:horas?)?\b",
    r"\b\d{1,2}\s+de\s+[a-z]+\s+de\s+\d{4}\b",
    r"\b\d+(?:[.,]\d+)?\s*(?:euros?|€)\b",
    r"\bdurante\s+\d+\s+(?:dias?|meses?|años?|horas?)\b",
]

PATRON_ACTUACION = (
    r"\b("
    r"presenta|presentado|solicita|solicitado|interpone|interpuesto|"
    r"notifica|notificado|tramita|tramitando|recibe|recibido|comparece|"
    r"fallece|incumple|comete|celebra|contrata|adjudica|reclama"
    r")\b"
)

RX_SUJETO = [re.compile(p, re.IGNORECASE) for p in PATRONES_SUJETO]
RX_CASO = [re.compile(p, re.IGNORECASE) for p in PATRONES_CASO]
RX_HIPOTESIS = [re.compile(p, re.IGNORECASE) for p in PATRONES_HIPOTESIS]
RX_DETALLE = [re.compile(p, re.IGNORECASE) for p in PATRONES_DETALLE]
RX_ACTUACION = re.compile(PATRON_ACTUACION, re.IGNORECASE)


def normalizar_texto(texto: object) -> str:
    valor = str(texto or "").lower()
    valor = unicodedata.normalize("NFKD", valor)
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", valor).strip()


def coincide_alguno(texto: str, patrones: list[re.Pattern]) -> bool:
    return any(p.search(texto) for p in patrones)


def clasificar_teorica_practica(enunciado: object, opciones: list[object]) -> str:
    texto_enunciado = normalizar_texto(enunciado)
    texto_completo = normalizar_texto(
        " ".join([str(enunciado or "")] + [str(o or "") for o in opciones])
    )

    if coincide_alguno(texto_enunciado, RX_SUJETO):
        return "PRACTICA"
    if coincide_alguno(texto_completo, RX_CASO):
        return "PRACTICA"
    if coincide_alguno(texto_completo, RX_HIPOTESIS):
        return "PRACTICA"

    contiene_detalle = coincide_alguno(texto_completo, RX_DETALLE)
    contiene_actuacion = bool(RX_ACTUACION.search(texto_completo))

    return "PRACTICA" if contiene_detalle and contiene_actuacion else "TEORICA"


# ---------------------------------------------------------------------------
# Infraestructura
# ---------------------------------------------------------------------------

def cargar_normalizador() -> ModuleType:
    if not NORMALIZADOR_NORMAS.is_file():
        raise FileNotFoundError(
            "No existe el normalizador requerido: "
            f"{NORMALIZADOR_NORMAS}"
        )

    spec = importlib.util.spec_from_file_location(
        "normalizador_normas_opocoach",
        NORMALIZADOR_NORMAS,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se puede cargar {NORMALIZADOR_NORMAS}")

    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)

    if not hasattr(modulo, "normalizar"):
        raise RuntimeError(
            "El normalizador no contiene la función normalizar(tipo, nombre)."
        )
    return modulo


def columnas_tabla(con: sqlite3.Connection) -> set[str]:
    return {fila[1] for fila in con.execute("PRAGMA table_info(lote_preguntas)")}


def validar_esquema(con: sqlite3.Connection) -> None:
    necesarias = {
        "id",
        "tipo_clasificacion",
        "tipo_norma",
        "nombre_norma",
        "articulo",
        "articulo_normalizado",
        "tipo_norma_normalizado",
        "nombre_norma_normalizado",
        "enunciado",
        "opcion_a",
        "opcion_b",
        "opcion_c",
        "opcion_d",
    }
    faltan = sorted(necesarias - columnas_tabla(con))
    if faltan:
        raise RuntimeError("Faltan columnas: " + ", ".join(faltan))


def asegurar_columna_teorica_practica(con: sqlite3.Connection) -> None:
    if "teorica_practica" not in columnas_tabla(con):
        con.execute(
            "ALTER TABLE lote_preguntas ADD COLUMN teorica_practica TEXT"
        )


def crear_copia_seguridad(ruta_bd: Path) -> Path:
    CARPETA_COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = (
        CARPETA_COPIAS
        / f"{ruta_bd.stem}_antes_enriquecimiento_{marca}{ruta_bd.suffix}"
    )
    shutil.copy2(ruta_bd, destino)
    return destino


def vacio(valor: object) -> bool:
    return valor is None or str(valor).strip() == ""


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------

def analizar(
    con: sqlite3.Connection,
    normalizador: ModuleType,
) -> dict:
    filas = con.execute(
        """
        SELECT
            id,
            tipo_clasificacion,
            tipo_norma,
            nombre_norma,
            articulo,
            articulo_normalizado,
            tipo_norma_normalizado,
            nombre_norma_normalizado,
            teorica_practica,
            enunciado,
            opcion_a,
            opcion_b,
            opcion_c,
            opcion_d
        FROM lote_preguntas
        ORDER BY id
        """
    ).fetchall()

    resultado = {
        "normas": [],
        "articulos": [],
        "clasificaciones": [],
        "normas_pendientes": [],
        "articulos_pendientes": [],
        "no_juridicas_con_clasificacion": [],
        "ya_completas": 0,
    }

    for fila in filas:
        es_juridica = fila["tipo_clasificacion"] == "JURIDICA"

        if not es_juridica:
            if not vacio(fila["teorica_practica"]):
                resultado["no_juridicas_con_clasificacion"].append(fila)
            continue

        necesita_norma = (
            vacio(fila["tipo_norma_normalizado"])
            or vacio(fila["nombre_norma_normalizado"])
        )
        if necesita_norma:
            propuesta = normalizador.normalizar(
                fila["tipo_norma"],
                fila["nombre_norma"],
            )
            if propuesta.estado == "PROPUESTO":
                resultado["normas"].append((fila, propuesta))
            else:
                resultado["normas_pendientes"].append(fila)

        if vacio(fila["articulo_normalizado"]):
            articulo = extraer_articulo_normalizado(fila["articulo"])
            if articulo is not None:
                resultado["articulos"].append((fila, articulo))
            else:
                resultado["articulos_pendientes"].append(fila)

        necesita_clasificacion = vacio(fila["teorica_practica"])
        if necesita_clasificacion:
            clasificacion = clasificar_teorica_practica(
                fila["enunciado"],
                [
                    fila["opcion_a"],
                    fila["opcion_b"],
                    fila["opcion_c"],
                    fila["opcion_d"],
                ],
            )
            resultado["clasificaciones"].append((fila, clasificacion))

        if (
            not necesita_norma
            and not vacio(fila["articulo_normalizado"])
            and not necesita_clasificacion
        ):
            resultado["ya_completas"] += 1

    return resultado


def mostrar_analisis(datos: dict) -> None:
    clasificaciones = Counter(valor for _, valor in datos["clasificaciones"])

    print()
    print("=" * 78)
    print("ENRIQUECIMIENTO DE PREGUNTAS — VISTA PREVIA")
    print("=" * 78)
    print(f"Normas que se completarían........... {len(datos['normas'])}")
    print(f"Artículos que se completarían........ {len(datos['articulos'])}")
    print(f"Clasificaciones que se escribirían... {len(datos['clasificaciones'])}")
    print(f"  TEORICA............................. {clasificaciones['TEORICA']}")
    print(f"  PRACTICA............................ {clasificaciones['PRACTICA']}")
    print(f"Jurídicas ya completas y conservadas. {datos['ya_completas']}")
    print()
    print(f"Normas no automatizables............. {len(datos['normas_pendientes'])}")
    print(f"Artículos no automatizables.......... {len(datos['articulos_pendientes'])}")
    print(
        "No jurídicas con TEORICA/PRACTICA... "
        f"{len(datos['no_juridicas_con_clasificacion'])}"
    )
    print("Modo clasificación.................. SOLO PENDIENTES")

    if datos["normas_pendientes"]:
        print()
        print("NORMAS PENDIENTES")
        for fila in datos["normas_pendientes"]:
            print(
                f"- id={fila['id']} | tipo={fila['tipo_norma']!r} | "
                f"nombre={fila['nombre_norma']!r} | articulo={fila['articulo']!r}"
            )

    if datos["articulos_pendientes"]:
        print()
        print("ARTÍCULOS PENDIENTES")
        for fila in datos["articulos_pendientes"]:
            print(
                f"- id={fila['id']} | norma={fila['nombre_norma']!r} | "
                f"articulo={fila['articulo']!r}"
            )


# ---------------------------------------------------------------------------
# Escritura y validación
# ---------------------------------------------------------------------------

def aplicar_cambios(con: sqlite3.Connection, datos: dict) -> dict[str, int]:
    cambios = {
        "normas": 0,
        "articulos": 0,
        "clasificaciones": 0,
    }

    for fila, propuesta in datos["normas"]:
        cur = con.execute(
            """
            UPDATE lote_preguntas
            SET tipo_norma_normalizado = ?,
                nombre_norma_normalizado = ?
            WHERE id = ?
              AND (
                    tipo_norma_normalizado IS NULL
                 OR TRIM(tipo_norma_normalizado) = ''
                 OR nombre_norma_normalizado IS NULL
                 OR TRIM(nombre_norma_normalizado) = ''
              )
            """,
            (
                propuesta.tipo_normalizado,
                propuesta.nombre_normalizado,
                fila["id"],
            ),
        )
        cambios["normas"] += cur.rowcount

    for fila, articulo in datos["articulos"]:
        cur = con.execute(
            """
            UPDATE lote_preguntas
            SET articulo_normalizado = ?
            WHERE id = ?
              AND (
                    articulo_normalizado IS NULL
                 OR TRIM(articulo_normalizado) = ''
              )
            """,
            (articulo, fila["id"]),
        )
        cambios["articulos"] += cur.rowcount

    for fila, clasificacion in datos["clasificaciones"]:
        cur = con.execute(
            """
            UPDATE lote_preguntas
            SET teorica_practica = ?
            WHERE id = ?
            """,
            (clasificacion, fila["id"]),
        )
        cambios["clasificaciones"] += cur.rowcount

    return cambios


def validar_final(con: sqlite3.Connection) -> dict[str, int]:
    fila = con.execute(
        """
        SELECT
            SUM(
                CASE WHEN tipo_clasificacion = 'JURIDICA'
                  AND (
                       tipo_norma_normalizado IS NULL
                    OR TRIM(tipo_norma_normalizado) = ''
                    OR nombre_norma_normalizado IS NULL
                    OR TRIM(nombre_norma_normalizado) = ''
                  )
                THEN 1 ELSE 0 END
            ) AS normas_pendientes,
            SUM(
                CASE WHEN tipo_clasificacion = 'JURIDICA'
                  AND (
                       articulo_normalizado IS NULL
                    OR TRIM(articulo_normalizado) = ''
                  )
                THEN 1 ELSE 0 END
            ) AS articulos_pendientes,
            SUM(
                CASE WHEN tipo_clasificacion = 'JURIDICA'
                  AND teorica_practica NOT IN ('TEORICA', 'PRACTICA')
                THEN 1 ELSE 0 END
            ) AS clasificacion_invalida,
            SUM(
                CASE WHEN tipo_clasificacion <> 'JURIDICA'
                  AND teorica_practica IS NOT NULL
                THEN 1 ELSE 0 END
            ) AS no_juridicas_clasificadas
        FROM lote_preguntas
        """
    ).fetchone()

    return {
        clave: int(fila[clave] or 0)
        for clave in fila.keys()
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Completa normalización y clasificación sin importar preguntas."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PREDETERMINADA,
        help=f"Base SQLite. Predeterminada: {DB_PREDETERMINADA}",
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Crea copia y escribe los cambios.",
    )
    args = parser.parse_args()

    ruta_bd = args.db.expanduser().resolve()
    if not ruta_bd.is_file():
        print(f"No existe la base de datos: {ruta_bd}")
        return 1

    normalizador = cargar_normalizador()

    con = sqlite3.connect(ruta_bd)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    try:
        # En vista previa no alteramos ni siquiera el esquema.
        if args.aplicar:
            asegurar_columna_teorica_practica(con)

        validar_esquema(con)
        datos = analizar(con, normalizador)

        print(f"Base de datos: {ruta_bd}")
        print("La base NO se modifica sin --aplicar.")
        mostrar_analisis(datos)

        if not args.aplicar:
            return 0

        copia = crear_copia_seguridad(ruta_bd)
        print()
        print(f"Copia de seguridad creada: {copia}")

        con.execute("BEGIN IMMEDIATE")
        cambios = aplicar_cambios(con, datos)
        validacion = validar_final(con)

        con.commit()

        print()
        print("=" * 78)
        print("RESUMEN FINAL")
        print("=" * 78)
        print(f"Normas completadas................... {cambios['normas']}")
        print(f"Artículos completados................ {cambios['articulos']}")
        print(f"Clasificaciones escritas............. {cambios['clasificaciones']}")
        print(f"Normas que siguen pendientes......... {validacion['normas_pendientes']}")
        print(f"Artículos que siguen pendientes...... {validacion['articulos_pendientes']}")
        print("Proceso completado correctamente.")
        return 0

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())