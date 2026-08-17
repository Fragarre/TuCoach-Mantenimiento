"""
OpoCoach-Mantenimiento - Generación experimental de preguntas jurídicas con IA.

OBJETIVO
--------
Generar candidatos de dificultad ALTA/MUY ALTA usando exclusivamente:
- una referencia norma-artículo del temario de una convocatoria;
- el texto oficial ya almacenado en articulos_fuente;
- preguntas ya pertenecientes al banco de esa convocatoria para la misma
  norma y artículo principal.

SEGURIDAD
---------
- La generación aplica DOBLE VALIDACIÓN IA independiente.
- Solo las candidatas que superan ambas validaciones se publican automáticamente.
- Los candidatos y sus validaciones se guardan en una base auxiliar:
      db/generacion_preguntas_ia.sqlite3
- Las rechazadas por IA no se publican; pueden incorporarse después mediante revisión humana.
- Toda publicación IA puede retirarse posteriormente por ID de generación.
- Al aprobar se conserva:
      tipo_fuente = 'ia_generada'
      origen_oposicion = nivel de la convocatoria (A1/A2/C1/C2)
- Las preguntas aprobadas se publican primero en lote_preguntas.
- Al terminar la ejecución se invoca el sincronizador común de todos los bancos,
  que reutiliza mantener_banco_preguntas.py como única fuente de reglas.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import re
import sqlite3
import shutil
import tempfile
import time
import gc
import sys
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mantener_banco_preguntas import (
    CLASIFICACION_JURIDICA,
    normalizar_articulo,
)
from sincronizar_bancos import sincronizar_todos_bancos

ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
DB_AUX = Path(tempfile.gettempdir()) / "opocoach_generacion_preguntas_ia.sqlite3"
REGISTROS = ROOT / "registros"

TIPO_FUENTE = "ia_generada"
DIFICULTAD_OBJETIVO = "ALTA_MUY_ALTA"
PROMPT_VERSION = "juridicas-v3-practicas-limpio"
MODELO_DEFECTO = "gpt-5.4-nano"
MAX_EJEMPLOS_DEFECTO = 25


@dataclass
class ContextoReferencia:
    convocatoria_id: int
    convocatoria_codigo: str
    origen_oposicion: str
    temario_id: int
    referencia_id: int
    tema_id: int
    parte: str
    numero_tema: int
    titulo_tema: str
    norma_id: int
    nombre_norma_csv: str
    nombre_norma_normalizada: str
    articulo_solicitado: str
    articulo_fuente_id: int
    articulo_boe: str
    texto_articulo: str


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def iniciar_ejecucion_generacion() -> None:
    """
    Empieza una ejecución de generación desde cero sin borrar registros ajenos.

    Solo sustituye el informe único de la generación jurídica anterior:
        registros/preguntas_ia_ultima_ejecucion.html

    La base auxiliar temporal se elimina también al comenzar.
    """
    REGISTROS.mkdir(parents=True, exist_ok=True)
    (REGISTROS / "preguntas_ia_ultima_ejecucion.html").unlink(missing_ok=True)
    _borrar_auxiliar_temporal()

def _borrar_auxiliar_temporal() -> None:
    """
    Elimina la SQLite temporal y sus ficheros WAL/SHM si existen.

    En Windows un antivirus/indexador puede mantener el fichero abierto unas
    décimas de segundo después de cerrar SQLite. Se reintenta la eliminación
    para que ese bloqueo transitorio no convierta una ejecución correcta en
    un error final.
    """
    gc.collect()

    rutas = [
        DB_AUX,
        Path(str(DB_AUX) + "-wal"),
        Path(str(DB_AUX) + "-shm"),
        Path(str(DB_AUX) + "-journal"),
    ]

    for intento in range(10):
        pendientes = []

        for ruta in rutas:
            if not ruta.exists():
                continue
            try:
                ruta.unlink()
            except PermissionError:
                pendientes.append(ruta)

        if not pendientes:
            return

        time.sleep(0.20 * (intento + 1))
        gc.collect()

    # Es un fichero TEMPORAL fuera de la carpeta del proyecto. Si un proceso
    # externo de Windows mantiene el bloqueo más tiempo, no se falsea el
    # resultado de la generación. Se eliminará al inicio de la siguiente.
    return


def finalizar_ejecucion_generacion() -> None:
    """
    Cierra la ejecución y elimina la base auxiliar temporal.

    En el proyecto permanece exclusivamente:
        registros/preguntas_ia_ultima_ejecucion.html
    """
    _borrar_auxiliar_temporal()


def fuente_oficial_suficiente(
    texto: str,
    tipo_pregunta: str,
) -> bool:
    """
    Evita generar desde registros de articulos_fuente que contienen únicamente
    el título del artículo o una remisión.

    Las prácticas requieren más contexto normativo para construir un supuesto
    de dificultad alta con seguridad.
    """
    limpio = re.sub(r"\s+", " ", str(texto or "")).strip()

    minimo = 250 if str(tipo_pregunta).upper() == "PRACTICA" else 100

    if len(limpio) < minimo:
        return False

    # Debe haber contenido normativo real, no solo encabezado/remisión.
    palabras_normativas = (
        " deberá ", " deberán ", " podrá ", " podrán ", " será ", " serán ",
        " corresponde ", " tendrá ", " tendrán ", " tiene derecho ",
        " no podrá ", " no podrán ", " se garantizará ", " se aplicará ",
        " se acordará ", " se realizará ", " se instrumentará ",
    )
    bajo = f" {limpio.lower()} "

    return any(marca in bajo for marca in palabras_normativas) or len(limpio) >= 500


def filtrar_contextos_por_fuente(
    contextos: list[ContextoReferencia],
    tipo_pregunta: str,
) -> list[ContextoReferencia]:
    return [
        ctx
        for ctx in contextos
        if fuente_oficial_suficiente(ctx.texto_articulo, tipo_pregunta)
    ]


def nivel_desde_codigo(codigo: str) -> str:
    m = re.match(r"^(A1|A2|C1|C2)(?:\b|[-_])", (codigo or "").strip().upper())
    if not m:
        raise RuntimeError(
            "No se puede obtener A1/A2/C1/C2 del código de convocatoria: "
            f"{codigo!r}."
        )
    return m.group(1)




def extraer_referencia_articulo_precisa(valor: Any) -> str:
    """
    Extrae únicamente la referencia precisa del artículo devuelta por la IA.

    Formatos aceptados, entre otros:
        18              -> 18
        18.2            -> 18.2
        82.2 bis        -> 82.2 bis
        24.1.b          -> 24.1.b
        Artículo 18.2   -> 18.2
        art. 18.2       -> 18.2
        Ley 40/2015, art. 18.2 -> 18.2

    La función NO toma el primer número arbitrario de una cadena. Si hay texto
    previo, exige un marcador explícito "artículo"/"art." para evitar
    confundir, por ejemplo, el 40 de "Ley 40/2015" con el artículo.
    """
    texto = "" if valor is None else str(valor).strip()
    if not texto:
        raise RuntimeError("La candidata no contiene una referencia de artículo.")

    # Referencia permitida: número, apartados numéricos, sufijo bis/ter/...
    # y, cuando proceda, subapartado alfabético final.
    patron_ref = (
        r"(\d+(?:\.\d+)*(?:\s*(?:bis|ter|quater|quinquies))?"
        r"(?:\.[A-Za-z])?)"
    )

    # Si la cadena comienza directamente por la referencia, se acepta.
    m = re.match(r"^\s*" + patron_ref, texto, flags=re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())

    # Si existe texto previo, solo se acepta una referencia precedida por
    # 'artículo' o 'art.'; así no se interpreta el número de la propia ley.
    m = re.search(
        r"(?:art[ií]culo|art\.)\s*" + patron_ref,
        texto,
        flags=re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())

    raise RuntimeError(
        "No se puede extraer con seguridad la referencia precisa del artículo "
        f"de la candidata: {texto!r}."
    )


def conectar_maestra(ruta: Path) -> sqlite3.Connection:
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def conectar_auxiliar() -> sqlite3.Connection:
    DB_AUX.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_AUX)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS generaciones_preguntas_ia (
            id INTEGER PRIMARY KEY,
            fecha_generacion TEXT NOT NULL,
            convocatoria_contexto_id INTEGER NOT NULL,
            convocatoria_codigo TEXT NOT NULL,
            origen_oposicion TEXT NOT NULL,
            temario_referencia_id INTEGER NOT NULL,
            tema_id INTEGER NOT NULL,
            norma_id INTEGER NOT NULL,
            nombre_norma TEXT NOT NULL,
            articulo_solicitado TEXT NOT NULL,
            articulo_fuente_id INTEGER NOT NULL,
            tipo_pregunta TEXT NOT NULL,
            dificultad_objetivo TEXT NOT NULL,
            modelo_generacion TEXT NOT NULL,
            modelo_validacion TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            ejemplos_ids_json TEXT NOT NULL,
            analisis_json TEXT NOT NULL,
            pregunta_json TEXT NOT NULL,
            validacion_json TEXT NOT NULL,
            estado TEXT NOT NULL,
            fecha_revision TEXT,
            lote_pregunta_id INTEGER,
            observaciones TEXT
        )
        """
    )
    existentes = {r[1] for r in con.execute("PRAGMA table_info(generaciones_preguntas_ia)")}
    nuevas = {
        "validacion2_json": "TEXT",
        "validacion3_json": "TEXT",
        "dictamen_ia": "TEXT",
        "tipo_publicacion": "TEXT",
        "fecha_retirada": "TEXT",
        "auditoria_json": "TEXT",
        "similitud_maxima": "REAL",
        "similitud_pregunta_id": "INTEGER",
        "dictamen_auditoria": "TEXT",
    }
    for columna, tipo in nuevas.items():
        if columna not in existentes:
            con.execute(f"ALTER TABLE generaciones_preguntas_ia ADD COLUMN {columna} {tipo}")
    con.commit()
    return con

def obtener_convocatoria(con: sqlite3.Connection, convocatoria_id: int | None, codigo: str | None) -> sqlite3.Row:
    if convocatoria_id is not None:
        fila = con.execute("SELECT * FROM convocatorias WHERE id = ?", (convocatoria_id,)).fetchone()
    else:
        fila = con.execute("SELECT * FROM convocatorias WHERE codigo = ?", ((codigo or "").strip(),)).fetchone()
    if fila is None:
        raise RuntimeError("La convocatoria indicada no existe.")
    return fila


def listar_referencias(con: sqlite3.Connection, convocatoria_id: int) -> None:
    filas = con.execute(
        """
        SELECT tr.id AS referencia_id, tt.numero_tema, tt.parte, tt.titulo,
               tr.norma_id, tr.nombre_norma_csv, tr.articulo_solicitado,
               tr.articulo_fuente_id
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id = t.id
        JOIN temario_referencias tr ON tr.tema_id = tt.id
        WHERE t.convocatoria_id = ?
          AND tr.norma_id IS NOT NULL
          AND tr.articulo_fuente_id IS NOT NULL
        ORDER BY tt.parte, tt.numero_tema, tr.nombre_norma_csv, tr.articulo_solicitado
        """,
        (convocatoria_id,),
    ).fetchall()
    print("\nREFERENCIAS DISPONIBLES")
    print("=" * 100)
    for f in filas:
        print(
            f"ID {f['referencia_id']:>5} | Tema {f['numero_tema']:>2} | "
            f"{f['nombre_norma_csv']} | art. {f['articulo_solicitado']}"
        )
    print(f"\nTotal: {len(filas)}")


def cargar_contexto(con: sqlite3.Connection, convocatoria_id: int, referencia_id: int) -> ContextoReferencia:
    fila = con.execute(
        """
        SELECT c.id AS convocatoria_id, c.codigo AS convocatoria_codigo,
               t.id AS temario_id,
               tr.id AS referencia_id, tr.tema_id, tt.parte, tt.numero_tema,
               tt.titulo AS titulo_tema,
               tr.norma_id, tr.nombre_norma_csv, tr.nombre_norma_normalizada,
               tr.articulo_solicitado, tr.articulo_fuente_id,
               af.articulo_boe, af.texto AS texto_articulo
        FROM convocatorias c
        JOIN temarios t ON t.convocatoria_id = c.id
        JOIN temario_temas tt ON tt.temario_id = t.id
        JOIN temario_referencias tr ON tr.tema_id = tt.id
        JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
        WHERE c.id = ? AND tr.id = ?
        """,
        (convocatoria_id, referencia_id),
    ).fetchone()
    if fila is None:
        raise RuntimeError(
            "La referencia no pertenece al temario de la convocatoria o no tiene texto oficial enlazado."
        )
    d = dict(fila)
    d["origen_oposicion"] = nivel_desde_codigo(d["convocatoria_codigo"])
    return ContextoReferencia(**d)



def obtener_referencias_aptas_del_tema(
    con: sqlite3.Connection,
    convocatoria_id: int,
    tema_id: int,
) -> list[ContextoReferencia]:
    """
    Devuelve todas las referencias jurídicas aptas del tema, sin modificar datos.

    Criterio idéntico al usado hasta ahora por elegir_referencia_del_tema():
    - referencia perteneciente al tema/convocatoria;
    - norma_id y texto oficial enlazado;
    - al menos una pregunta jurídica INCLUIDA en el banco de la convocatoria
      para la misma norma y artículo principal.
    """
    tema = con.execute(
        """
        SELECT tt.id, tt.parte, tt.numero_tema, tt.titulo
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id = t.id
        WHERE t.convocatoria_id = ? AND tt.id = ?
        """,
        (convocatoria_id, tema_id),
    ).fetchone()
    if tema is None:
        raise RuntimeError(
            "El tema indicado no pertenece al temario de la convocatoria."
        )

    referencias = con.execute(
        """
        SELECT tr.id, tr.norma_id, tr.articulo_solicitado
        FROM temario_referencias tr
        WHERE tr.tema_id = ?
          AND tr.norma_id IS NOT NULL
          AND tr.articulo_fuente_id IS NOT NULL
        ORDER BY tr.id
        """,
        (tema_id,),
    ).fetchall()
    if not referencias:
        raise RuntimeError(
            "El tema seleccionado no contiene referencias jurídicas con texto oficial enlazado."
        )

    normas = sorted({int(r["norma_id"]) for r in referencias})
    marcadores = ",".join("?" for _ in normas)
    preguntas = con.execute(
        f"""
        SELECT DISTINCT lp.norma_id_normalizada, lp.articulo_normalizado
        FROM banco_preguntas bp
        JOIN lote_preguntas lp ON lp.id = bp.pregunta_id
        WHERE bp.convocatoria_id = ?
          AND bp.estado = 'INCLUIDA'
          AND lp.tipo_clasificacion = 'JURIDICA'
          AND lp.norma_id_normalizada IN ({marcadores})
        """,
        (convocatoria_id, *normas),
    ).fetchall()

    articulos_con_ejemplos: set[tuple[int, str]] = set()
    for p in preguntas:
        art = normalizar_articulo(p["articulo_normalizado"])
        if art is not None:
            articulos_con_ejemplos.add((int(p["norma_id_normalizada"]), art))

    aptas_ids: list[int] = []
    for r in referencias:
        art = normalizar_articulo(r["articulo_solicitado"])
        if art is not None and (int(r["norma_id"]), art) in articulos_con_ejemplos:
            aptas_ids.append(int(r["id"]))

    if not aptas_ids:
        raise RuntimeError(
            "El tema seleccionado no tiene ninguna referencia norma-artículo con "
            "preguntas de ejemplo ya incluidas en el banco de esta convocatoria."
        )

    return [
        cargar_contexto(con, convocatoria_id, referencia_id)
        for referencia_id in aptas_ids
    ]


def obtener_referencias_aptas_todos_temas(
    con: sqlite3.Connection,
    convocatoria_id: int,
) -> tuple[list[ContextoReferencia], int]:
    """
    Devuelve todas las referencias jurídicas aptas de todos los temas de la
    convocatoria. El reparto posterior se hace entre referencias; por ello,
    cada tema recibe candidatas proporcionalmente al número de referencias
    aptas que contiene.
    """
    temas = con.execute(
        """
        SELECT DISTINCT tt.id
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id = t.id
        JOIN temario_referencias tr ON tr.tema_id = tt.id
        WHERE t.convocatoria_id = ?
          AND tr.norma_id IS NOT NULL
          AND tr.articulo_fuente_id IS NOT NULL
        ORDER BY tt.parte, tt.numero_tema, tt.id
        """,
        (convocatoria_id,),
    ).fetchall()

    contextos: list[ContextoReferencia] = []
    temas_aptos = 0
    referencias_vistas: set[int] = set()

    for tema in temas:
        try:
            aptas = obtener_referencias_aptas_del_tema(
                con, convocatoria_id, int(tema["id"])
            )
        except RuntimeError:
            continue
        if not aptas:
            continue
        temas_aptos += 1
        for ctx in aptas:
            if ctx.referencia_id in referencias_vistas:
                continue
            referencias_vistas.add(ctx.referencia_id)
            contextos.append(ctx)

    if not contextos:
        raise RuntimeError(
            "La convocatoria no tiene referencias jurídicas aptas con texto "
            "oficial y preguntas de ejemplo incluidas en el banco."
        )

    return contextos, temas_aptos



def cargar_modelo_generacion_ia(
    con: sqlite3.Connection,
    convocatoria_id: int,
) -> list[dict[str, Any]]:
    """
    Deriva el reparto de generación IA de convocatoria_modelo_bloques.

    convocatoria_modelo_bloques es la fuente prevalente del patrón normativo.
    Para generación se agregan bloques equivalentes de una misma parte:
    - NORMA: misma norma_id;
    - LIBRE: conjunto de normas no preasignadas de esa parte.

    El peso de cada grupo es la suma de cantidades configuradas en el modelo.
    """
    existe = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table'
          AND name='convocatoria_modelo_bloques'
        """
    ).fetchone()

    if existe is None:
        raise RuntimeError(
            "La base no contiene convocatoria_modelo_bloques. "
            "Configure primero el modelo de examen de la convocatoria."
        )

    filas = con.execute(
        """
        SELECT
            b.id,
            b.convocatoria_parte_id,
            cp.nombre AS parte_modelo,
            cp.orden AS parte_orden,
            b.orden AS bloque_orden,
            b.tipo_bloque,
            b.norma_id,
            n.nombre_canonico AS norma,
            b.cantidad
        FROM convocatoria_modelo_bloques b
        JOIN convocatoria_partes cp
          ON cp.id = b.convocatoria_parte_id
        LEFT JOIN normas n
          ON n.id = b.norma_id
        WHERE cp.convocatoria_id = ?
        ORDER BY cp.orden, b.orden, b.id
        """,
        (convocatoria_id,),
    ).fetchall()

    if not filas:
        raise RuntimeError(
            "La convocatoria no tiene bloques configurados en "
            "convocatoria_modelo_bloques."
        )

    partes_temario: dict[int, tuple[str, ...]] = {}
    for parte_id in sorted({int(f["convocatoria_parte_id"]) for f in filas}):
        reglas = con.execute(
            """
            SELECT temario_parte, tipo_contenido, teorica_practica, tema_no_juridico
            FROM convocatoria_parte_reglas
            WHERE convocatoria_parte_id = ?
            ORDER BY prioridad, id
            """,
            (parte_id,),
        ).fetchall()

        valores: list[str] = []
        for regla in reglas:
            if str(regla["teorica_practica"] or "").strip().upper() == "PRACTICA":
                continue
            if str(regla["tipo_contenido"] or "").strip().upper() in {
                "NO_JURIDICO",
                "INFORMATICA",
            }:
                continue
            if str(regla["tema_no_juridico"] or "").strip():
                continue
            parte_temario = str(regla["temario_parte"] or "").strip().upper()
            if parte_temario and parte_temario not in valores:
                valores.append(parte_temario)

        if not valores:
            nombre = next(
                str(f["parte_modelo"])
                for f in filas
                if int(f["convocatoria_parte_id"]) == parte_id
            )
            raise RuntimeError(
                "La parte configurada del modelo no tiene una regla jurídica "
                f"con temario_parte utilizable: {nombre!r}."
            )

        partes_temario[parte_id] = tuple(valores)

    agregados: dict[tuple[int, str, int | None], dict[str, Any]] = {}
    orden_claves: list[tuple[int, str, int | None]] = []

    for fila in filas:
        parte_id = int(fila["convocatoria_parte_id"])
        tipo_bloque = str(fila["tipo_bloque"] or "").strip().upper()
        norma_id = int(fila["norma_id"]) if fila["norma_id"] is not None else None

        if tipo_bloque not in {"NORMA", "LIBRE"}:
            raise RuntimeError(
                f"Tipo de bloque no reconocido en convocatoria_modelo_bloques: {tipo_bloque!r}."
            )
        if tipo_bloque == "NORMA" and norma_id is None:
            raise RuntimeError(
                f"Bloque NORMA sin norma_id en convocatoria_modelo_bloques (id={fila['id']})."
            )
        if tipo_bloque == "LIBRE":
            norma_id = None

        clave = (parte_id, tipo_bloque, norma_id)
        if clave not in agregados:
            orden_claves.append(clave)
            agregados[clave] = {
                "clave": len(orden_claves),
                "convocatoria_parte_id": parte_id,
                "parte_modelo": str(fila["parte_modelo"] or "").strip(),
                "temario_partes": partes_temario[parte_id],
                "tipo_bloque": tipo_bloque,
                "norma_id": norma_id,
                "norma": str(fila["norma"] or "").strip() or None,
                "peso_objetivo": 0.0,
            }
        agregados[clave]["peso_objetivo"] += float(fila["cantidad"])

    return [agregados[clave] for clave in orden_claves]


def _repartir_cantidad_por_pesos(
    elementos: list[dict[str, Any]],
    cantidad: int,
) -> dict[int, int]:
    """
    Reparto proporcional por restos mayores, con desempate aleatorio.

    La suma devuelta es EXACTAMENTE cantidad.
    """
    if cantidad <= 0:
        raise ValueError("cantidad debe ser positiva")
    if not elementos:
        raise ValueError("No hay elementos ponderables.")

    total_peso = sum(float(x["peso"]) for x in elementos)
    if total_peso <= 0:
        raise ValueError("El modelo no contiene pesos positivos.")

    rng = random.SystemRandom()
    calculos: list[dict[str, Any]] = []
    asignadas = 0

    for elemento in elementos:
        cuota = cantidad * float(elemento["peso"]) / total_peso
        base = int(cuota)
        asignadas += base
        calculos.append(
            {
                "clave": int(elemento["clave"]),
                "base": base,
                "resto": cuota - base,
                "azar": rng.random(),
            }
        )

    faltan = cantidad - asignadas
    calculos.sort(
        key=lambda x: (x["resto"], x["azar"]),
        reverse=True,
    )
    for fila in calculos[:faltan]:
        fila["base"] += 1

    return {
        int(fila["clave"]): int(fila["base"])
        for fila in calculos
    }


def _seleccionar_contextos_libres(
    candidatos: list[ContextoReferencia],
    cantidad: int,
    rng: random.SystemRandom,
) -> list[ContextoReferencia]:
    """
    Reparte un cupo LIBRE entre normas antes de repetir norma.

    Así se conserva la diversidad del modelo: una segunda pregunta de una norma
    solo aparece después de haber recorrido las demás normas libres disponibles.
    """
    por_norma: dict[int, list[ContextoReferencia]] = {}
    for ctx in candidatos:
        por_norma.setdefault(int(ctx.norma_id), []).append(ctx)

    if not por_norma:
        return []

    resultado: list[ContextoReferencia] = []
    norma_ids = list(por_norma)

    while len(resultado) < cantidad:
        ciclo_normas = list(norma_ids)
        rng.shuffle(ciclo_normas)
        for norma_id in ciclo_normas:
            if len(resultado) >= cantidad:
                break
            refs = list(por_norma[norma_id])
            rng.shuffle(refs)
            resultado.append(refs[0])

    return resultado


def secuencia_contextos_segun_modelo(
    contextos: list[ContextoReferencia],
    modelo: list[dict[str, Any]],
    cantidad: int,
) -> tuple[list[ContextoReferencia], list[str]]:
    """
    Construye el lote teórico a partir de convocatoria_modelo_bloques.

    Los bloques NORMA usan exclusivamente esa norma dentro de la parte del
    temario asociada por convocatoria_parte_reglas. Los bloques LIBRE usan
    únicamente normas de la misma parte que no estén preasignadas en ningún
    bloque NORMA de esa convocatoria_parte.

    Si un grupo del modelo no tiene referencias utilizables, su peso se
    redistribuye entre los grupos disponibles y se deja constancia en avisos.
    """
    if cantidad <= 0:
        raise ValueError("cantidad debe ser positiva")
    if not modelo:
        raise RuntimeError(
            "La convocatoria no tiene modelo de examen configurado para la generación."
        )

    rng = random.SystemRandom()
    normas_fijas_por_parte: dict[int, set[int]] = {}
    for grupo in modelo:
        if grupo["tipo_bloque"] == "NORMA" and grupo["norma_id"] is not None:
            normas_fijas_por_parte.setdefault(
                int(grupo["convocatoria_parte_id"]), set()
            ).add(int(grupo["norma_id"]))

    candidatos_por_clave: dict[int, list[ContextoReferencia]] = {}
    elementos_disponibles: list[dict[str, Any]] = []
    avisos: list[str] = []

    for grupo in modelo:
        clave = int(grupo["clave"])
        parte_id = int(grupo["convocatoria_parte_id"])
        temario_partes = {str(x).strip().upper() for x in grupo["temario_partes"]}

        candidatos = [
            ctx
            for ctx in contextos
            if str(ctx.parte or "").strip().upper() in temario_partes
        ]

        if grupo["tipo_bloque"] == "NORMA":
            norma_id = int(grupo["norma_id"])
            candidatos = [ctx for ctx in candidatos if int(ctx.norma_id) == norma_id]
            etiqueta = grupo["norma"] or f"norma_id={norma_id}"
        else:
            fijas = normas_fijas_por_parte.get(parte_id, set())
            candidatos = [ctx for ctx in candidatos if int(ctx.norma_id) not in fijas]
            etiqueta = "LIBRE"

        if not candidatos:
            avisos.append(
                "Sin referencias utilizables para "
                f"{grupo['parte_modelo'] or 'SIN PARTE'} · {etiqueta} "
                f"(peso modelo {grupo['peso_objetivo']:g}). Su cuota se redistribuye."
            )
            continue

        candidatos_por_clave[clave] = candidatos
        elementos_disponibles.append(
            {
                "clave": clave,
                "peso": float(grupo["peso_objetivo"]),
            }
        )

    if not elementos_disponibles:
        raise RuntimeError(
            "Ningún bloque de convocatoria_modelo_bloques tiene referencias "
            "utilizables con texto oficial completo y ejemplos en el banco."
        )

    cupos = _repartir_cantidad_por_pesos(elementos_disponibles, cantidad)
    resultado: list[ContextoReferencia] = []

    for grupo in modelo:
        clave = int(grupo["clave"])
        cupo = int(cupos.get(clave, 0))
        if cupo <= 0:
            continue

        candidatos = list(candidatos_por_clave[clave])
        if grupo["tipo_bloque"] == "LIBRE":
            resultado.extend(_seleccionar_contextos_libres(candidatos, cupo, rng))
            continue

        seleccionados: list[ContextoReferencia] = []
        while len(seleccionados) < cupo:
            ciclo = list(candidatos)
            rng.shuffle(ciclo)
            faltan = cupo - len(seleccionados)
            seleccionados.extend(ciclo[:faltan])
        resultado.extend(seleccionados)

    rng.shuffle(resultado)

    if len(resultado) != cantidad:
        raise RuntimeError(
            "Error interno al repartir convocatoria_modelo_bloques para generación IA."
        )

    return resultado, avisos


def resumen_reparto_secuencia(
    secuencia: list[ContextoReferencia],
) -> list[tuple[str, str, int]]:
    conteo: dict[tuple[str, str], int] = {}

    for ctx in secuencia:
        clave = (
            str(ctx.parte or "").strip().upper(),
            str(ctx.nombre_norma_csv or "").strip(),
        )
        conteo[clave] = conteo.get(clave, 0) + 1

    return [
        (parte, norma, cantidad)
        for (parte, norma), cantidad in sorted(
            conteo.items(),
            key=lambda x: (x[0][0], x[0][1]),
        )
    ]


def elegir_referencia_del_tema(
    con: sqlite3.Connection,
    convocatoria_id: int,
    tema_id: int,
) -> tuple[ContextoReferencia, int]:
    """Compatibilidad con el modo de una sola candidata."""
    aptas = obtener_referencias_aptas_del_tema(con, convocatoria_id, tema_id)
    return random.SystemRandom().choice(aptas), len(aptas)


def secuencia_contextos_lote(
    contextos: list[ContextoReferencia],
    cantidad: int,
) -> list[ContextoReferencia]:
    """
    Reparte el lote entre referencias aptas. No repite una referencia hasta
    haber recorrido todas las disponibles en un ciclo. Cada ciclo se baraja
    de forma independiente.
    """
    if cantidad <= 0:
        raise ValueError("cantidad debe ser positiva")
    if not contextos:
        raise ValueError("No hay referencias aptas.")

    rng = random.SystemRandom()
    resultado: list[ContextoReferencia] = []
    while len(resultado) < cantidad:
        ciclo = list(contextos)
        rng.shuffle(ciclo)
        faltan = cantidad - len(resultado)
        resultado.extend(ciclo[:faltan])
    return resultado

def cargar_ejemplos(
    con: sqlite3.Connection,
    ctx: ContextoReferencia,
    max_ejemplos: int,
) -> list[dict[str, Any]]:
    articulo_base = normalizar_articulo(ctx.articulo_solicitado)
    if articulo_base is None:
        raise RuntimeError("El artículo del temario no puede normalizarse con la regla vigente del banco.")

    filas = con.execute(
        """
        SELECT DISTINCT lp.id, lp.enunciado, lp.opcion_a, lp.opcion_b,
               lp.opcion_c, lp.opcion_d, lp.respuesta_correcta,
               lp.articulo, lp.articulo_normalizado, lp.teorica_practica,
               lp.origen_oposicion, lp.tipo_fuente,
               lp.tipo_norma, lp.nombre_norma,
               lp.tipo_norma_normalizado, lp.nombre_norma_normalizado
        FROM banco_preguntas bp
        JOIN lote_preguntas lp ON lp.id = bp.pregunta_id
        WHERE bp.convocatoria_id = ?
          AND bp.estado = 'INCLUIDA'
          AND lp.tipo_clasificacion = 'JURIDICA'
          AND lp.norma_id_normalizada = ?
        ORDER BY lp.id
        """,
        (ctx.convocatoria_id, ctx.norma_id),
    ).fetchall()

    ejemplos = [
        dict(f)
        for f in filas
        if normalizar_articulo(f["articulo_normalizado"]) == articulo_base
    ]

    if not ejemplos:
        raise RuntimeError(
            "No existen preguntas del banco de esta convocatoria para la norma-artículo seleccionada. "
            "El experimento exige ejemplos reales/ya existentes."
        )

    return ejemplos[:max_ejemplos]


def pregunta_para_prompt(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"],
        "enunciado": p["enunciado"],
        "A": p["opcion_a"],
        "B": p["opcion_b"],
        "C": p["opcion_c"],
        "D": p["opcion_d"],
        "correcta": p["respuesta_correcta"],
        "articulo": p["articulo_normalizado"] or p["articulo"],
        "tipo": p["teorica_practica"],
    }


def construir_prompt_generacion(
    ctx: ContextoReferencia,
    ejemplos: list[dict[str, Any]],
    tipo_pregunta: str,
) -> str:
    ejemplos_json = json.dumps(
        [pregunta_para_prompt(p) for p in ejemplos],
        ensure_ascii=False,
        indent=2,
    )

    tipo = (tipo_pregunta or "").strip().upper()

    if tipo == "PRACTICA":
        instrucciones_tipo = """
TIPO OBLIGATORIO: PRACTICA.

Crea un SUPUESTO PRÁCTICO real de oposición, no una pregunta teórica adornada.

Debe cumplir TODOS estos criterios:
1. Presenta una situación concreta con hechos jurídicamente relevantes.
2. Para contestar hay que aplicar el artículo a esos hechos.
3. Incluye, cuando el texto lo permita, AL MENOS DOS datos o condiciones
   jurídicamente relevantes del supuesto (por ejemplo: sujeto + plazo,
   porcentaje + consecuencia, requisito + excepción, órgano + actuación).
4. La dificultad debe venir de decidir qué consecuencia produce la combinación
   de hechos, no de una redacción confusa.
5. Los distractores deben modificar de forma sutil un dato decisivo:
   plazo, sujeto, porcentaje, órgano, requisito, excepción o efecto.
6. NO se exige usar varias leyes: toda la solución debe salir de la fuente
   oficial suministrada.
7. Una aplicación directa de una regla a hechos concretos SÍ es práctica,
   pero para dificultad ALTA/MUY ALTA evita casos triviales de un solo dato.
8. Si el artículo no ofrece contenido suficiente para construir un supuesto
   práctico de dificultad ALTA/MUY ALTA sin inventar normativa, devuelve
   no_apta_practica=true.

No uses conocimientos jurídicos externos aunque los conozcas.
""".strip()
    else:
        instrucciones_tipo = """
TIPO OBLIGATORIO: TEORICA.

Evalúa conocimiento jurídico directo del texto oficial, con dificultad
ALTA/MUY ALTA, distractores plausibles y sin normativa externa.
""".strip()

    return f"""
Actúas como redactor experto de preguntas de oposición.

FUENTE OFICIAL ÚNICA
Norma: {ctx.nombre_norma_csv}
Artículo: {ctx.articulo_solicitado}
---
{ctx.texto_articulo}
---

EJEMPLOS EXISTENTES DEL BANCO
{ejemplos_json}

{instrucciones_tipo}

REGLAS COMUNES
- Una sola pregunta nueva.
- Cuatro opciones A/B/C/D y exactamente una correcta.
- La correcta y la falsedad de los distractores deben poder comprobarse
  EXCLUSIVAMENTE con la fuente oficial anterior.
- No copies ni reformules de cerca los ejemplos.
- No inventes plazos, órganos, excepciones, requisitos ni consecuencias.
- Devuelve la referencia más precisa dentro del artículo.

Devuelve SOLO JSON:
{{
  "analisis": {{
    "aspectos_ya_preguntados": ["..."],
    "aspecto_elegido": "...",
    "justificacion_dificultad": "...",
    "justificacion_tipo": "..."
  }},
  "pregunta": {{
    "tipo_pregunta": "{tipo}",
    "no_apta_practica": false,
    "enunciado": "...",
    "opcion_a": "...",
    "opcion_b": "...",
    "opcion_c": "...",
    "opcion_d": "...",
    "respuesta_correcta": "A|B|C|D",
    "articulo_referencia": "...",
    "explicacion_literal": "..."
  }}
}}
""".strip()
def construir_prompt_validacion(
    ctx: ContextoReferencia,
    ejemplos: list[dict[str, Any]],
    pregunta: dict[str, Any],
    numero_revision: int = 1,
) -> str:
    ejemplos_json = json.dumps(
        [pregunta_para_prompt(p) for p in ejemplos],
        ensure_ascii=False,
        indent=2,
    )
    pregunta_json = json.dumps(
        pregunta,
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Actúas como revisor jurídico independiente.
Revisión nº {numero_revision}.

FUENTE OFICIAL ÚNICA
Norma: {ctx.nombre_norma_csv}
Artículo: {ctx.articulo_solicitado}
---
{ctx.texto_articulo}
---

EJEMPLOS EXISTENTES PARA DETECTAR CLONACIÓN
{ejemplos_json}

CANDIDATA
{pregunta_json}

Comprueba EXCLUSIVAMENTE:
1. La respuesta marcada es jurídicamente correcta según la fuente.
2. Los otros tres distractores son inequívocamente falsos según la fuente.
3. No se usa información jurídica externa.
4. La dificultad es ALTA/MUY ALTA por la proximidad de los distractores,
   condiciones, excepciones, sujetos, plazos, porcentajes o efectos.
5. No es clon ni paráfrasis cercana de los ejemplos.
6. articulo_referencia identifica con precisión el artículo/apartado aplicable.
7. El tipo real de la pregunta coincide con CANDIDATA.tipo_pregunta. Si declara
   PRACTICA, debe existir un supuesto concreto y ser necesario aplicar la norma
   a esos hechos; una pregunta puramente literal no cumple este criterio. Si
   declara TEORICA, no debe depender de resolver un supuesto práctico.

IMPORTANTE PARA CANDIDATAS PRACTICAS:
- NO las rechaces por ser una aplicación directa del artículo a hechos.
- Una regla literal aplicada a un supuesto concreto sigue siendo práctica.
- El tipo PRACTICA no exige varias leyes ni una inferencia compleja.
- Si consideras que el caso es demasiado fácil, refleja eso SOLO en
  "dificultad"; no lo conviertas artificialmente en un error de tipo.

No corrijas silenciosamente la pregunta.

Devuelve SOLO JSON:
{{
  "aprobable": true,
  "correccion_juridica": "OK|ERROR",
  "distractores": "OK|ERROR",
  "dificultad": "ALTA|MUY_ALTA|INSUFICIENTE",
  "no_clon": true,
  "referencia_precisa": true,
  "tipo_correcto": true,
  "observaciones": ["..."]
}}
""".strip()
def construir_prompt_desempate(
    ctx: ContextoReferencia,
    ejemplos: list[dict[str, Any]],
    pregunta: dict[str, Any],
    validacion1: dict[str, Any],
    validacion2: dict[str, Any],
) -> str:
    ejemplos_json = json.dumps(
        [pregunta_para_prompt(p) for p in ejemplos],
        ensure_ascii=False,
        indent=2,
    )
    pregunta_json = json.dumps(
        pregunta,
        ensure_ascii=False,
        indent=2,
    )
    v1_json = json.dumps(
        validacion1,
        ensure_ascii=False,
        indent=2,
    )
    v2_json = json.dumps(
        validacion2,
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Actúas como TERCER REVISOR DE DESEMPATE de una pregunta de oposición.

Tu misión NO es volver a redactar la pregunta ni ser más exigente que los
revisores anteriores. Debes resolver una discrepancia entre dos validaciones.

FUENTE OFICIAL ÚNICA
Norma: {ctx.nombre_norma_csv}
Artículo: {ctx.articulo_solicitado}
---
{ctx.texto_articulo}
---

PREGUNTAS EXISTENTES PARA DETECTAR CLONACIÓN
{ejemplos_json}

CANDIDATA
{pregunta_json}

VALIDACIÓN 1
{v1_json}

VALIDACIÓN 2
{v2_json}

REGLAS DE DESEMPATE
1. Comprueba por ti mismo la pregunta usando exclusivamente la fuente oficial.
2. No des por correcta ninguna observación de los validadores por el mero hecho
   de que aparezca escrita.
3. Si un validador marca "distractores=ERROR" pero en sus propias observaciones
   explica que los tres distractores son falsos, considera esa validación
   internamente incongruente.
4. Si un validador marca correccion_juridica=ERROR pero su explicación afirma
   que la respuesta correcta coincide con la norma, considera esa validación
   internamente incongruente.
5. No rechaces por cuestiones meramente estilísticas, por preferencia de
   redacción ni por una interpretación más elegante.
6. Rechaza solo por DEFECTO MATERIAL:
   - respuesta marcada incorrecta;
   - al menos un distractor también correcto o realmente ambiguo;
   - información jurídica necesaria no contenida en la fuente;
   - dificultad claramente insuficiente;
   - clonación real o paráfrasis demasiado cercana;
   - referencia jurídica materialmente incorrecta;
   - tipo real de pregunta distinto del declarado en CANDIDATA.tipo_pregunta.
7. En preguntas PRACTICAS, una aplicación directa de una regla a hechos
   concretos es válida. No exijas varias leyes ni razonamiento complejo.
8. Si ambos revisores discrepan pero uno de ellos es internamente contradictorio
   y el otro es coherente con la fuente, prevalece el coherente.
9. Tu decisión debe ser binaria: VALIDAR o RECHAZAR.

Devuelve SOLO JSON:
{{
  "aprobable": true,
  "correccion_juridica": "OK|ERROR",
  "distractores": "OK|ERROR",
  "dificultad": "ALTA|MUY_ALTA|INSUFICIENTE",
  "no_clon": true,
  "referencia_precisa": true,
  "tipo_correcto": true,
  "incongruencia_validacion_1": false,
  "incongruencia_validacion_2": false,
  "decision_desempate": "VALIDAR|RECHAZAR",
  "observaciones": ["..."]
}}
""".strip()


def validacion_internamente_incongruente(validacion: dict[str, Any]) -> bool:
    """
    Detecta contradicciones evidentes entre los campos estructurados y las
    propias observaciones del revisor.

    No pretende sustituir al tercer check; únicamente mejora la trazabilidad.
    """
    observaciones = _texto_observaciones(validacion).lower()

    if str(validacion.get("distractores") or "").upper() == "ERROR":
        marcas_falsedad = (
            "distractor b es falso",
            "distractor c es falso",
            "distractor d es falso",
            "opción b es falsa",
            "opción c es falsa",
            "opción d es falsa",
            "opcion b es falsa",
            "opcion c es falsa",
            "opcion d es falsa",
        )
        if sum(marca in observaciones for marca in marcas_falsedad) >= 3:
            return True

    if str(validacion.get("correccion_juridica") or "").upper() == "ERROR":
        marcas_correcta = (
            "la opción a es jurídicamente correcta",
            "la opcion a es juridicamente correcta",
            "la respuesta correcta es correcta",
            "coincide con el artículo",
            "coincide con el articulo",
        )
        if any(marca in observaciones for marca in marcas_correcta):
            return True

    return False


def resolver_dictamen_validaciones(
    ctx: ContextoReferencia,
    ejemplos: list[dict[str, Any]],
    pregunta: dict[str, Any],
    validacion1: dict[str, Any],
    validacion2: dict[str, Any],
    modelo_validacion: str,
) -> tuple[bool, dict[str, Any] | None]:
    """
    Regla:
      - 2 OK -> validada
      - 2 NO -> rechazada
      - discrepancia -> tercer check de desempate
    """
    from openai_api import seleccionar_fragmento_json

    ok1 = validacion_superada(validacion1)
    ok2 = validacion_superada(validacion2)

    if ok1 and ok2:
        return True, None

    if not ok1 and not ok2:
        return False, None

    validacion3 = seleccionar_fragmento_json(
        prompt=construir_prompt_desempate(
            ctx,
            ejemplos,
            pregunta,
            validacion1,
            validacion2,
        ),
        modelo=modelo_validacion,
        operacion="validar_pregunta_juridica_ia_desempate",
    )

    decision = str(
        validacion3.get("decision_desempate") or ""
    ).strip().upper()

    if decision == "VALIDAR":
        return True, validacion3

    if decision == "RECHAZAR":
        return False, validacion3

    # Si el tercer revisor no respeta el esquema, se aplica validacion_superada
    # como criterio de seguridad.
    return validacion_superada(validacion3), validacion3



def _normalizar_similitud(valor: Any) -> str:
    texto = re.sub(r"\s+", " ", str(valor or "").strip().casefold())
    return re.sub(r"[^a-záéíóúüñ0-9%]+", " ", texto).strip()


def comprobar_originalidad_masiva(
    con: sqlite3.Connection,
    ctx: ContextoReferencia,
    pregunta: dict[str, Any],
) -> tuple[bool, float, int | None]:
    """Control conservador de clonación. No penaliza compartir contenido jurídico.

    Solo rechaza automáticamente una similitud textual extraordinariamente alta
    (>= 0.965) con otra pregunta de la misma norma y artículo principal.
    """
    articulo_base = normalizar_articulo(ctx.articulo_solicitado)
    objetivo = _normalizar_similitud(
        " | ".join(str(pregunta.get(k) or "") for k in (
            "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d"
        ))
    )
    if not objetivo:
        return False, 1.0, None

    filas = con.execute(
        """
        SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d, articulo_normalizado
        FROM lote_preguntas
        WHERE tipo_clasificacion='JURIDICA' AND norma_id_normalizada=?
        """,
        (int(ctx.norma_id),),
    ).fetchall()

    mejor = 0.0
    mejor_id: int | None = None
    for f in filas:
        if normalizar_articulo(f["articulo_normalizado"]) != articulo_base:
            continue
        candidato = _normalizar_similitud(
            " | ".join(str(f[k] or "") for k in (
                "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d"
            ))
        )
        ratio = SequenceMatcher(None, objetivo, candidato, autojunk=False).ratio()
        if ratio > mejor:
            mejor = ratio
            mejor_id = int(f["id"])

    return mejor < 0.965, mejor, mejor_id


def construir_prompt_auditoria_ciega(
    ctx: ContextoReferencia,
    pregunta: dict[str, Any],
) -> str:
    """Auditor posterior. No recibe la respuesta marcada por el generador."""
    candidata = {
        "enunciado": pregunta.get("enunciado"),
        "A": pregunta.get("opcion_a"),
        "B": pregunta.get("opcion_b"),
        "C": pregunta.get("opcion_c"),
        "D": pregunta.get("opcion_d"),
        "tipo_pregunta": pregunta.get("tipo_pregunta"),
        "articulo_referencia": pregunta.get("articulo_referencia"),
    }
    return f"""
Actúas como AUDITOR JURÍDICO CIEGO de una pregunta de oposición.
No conoces la respuesta que marcó el generador. Debes resolverla desde cero.

FUENTE OFICIAL ÚNICA
Norma: {ctx.nombre_norma_csv}
Artículo: {ctx.articulo_solicitado}
---
{ctx.texto_articulo}
---

CANDIDATA
{json.dumps(candidata, ensure_ascii=False, indent=2)}

AUDITORÍA OBLIGATORIA
1. Elige por ti mismo la única respuesta correcta A/B/C/D.
2. Dictamina cada opción A/B/C/D como CORRECTA o FALSA según la fuente.
3. Indica si existe exactamente una respuesta correcta.
4. Indica si para resolverla hace falta información jurídica externa a la fuente.
5. Comprueba si la referencia propuesta corresponde al artículo suministrado.
6. Valora la dificultad como ALTA, MUY_ALTA o INSUFICIENTE.
7. No rechaces una PRACTICA por aplicar directamente una regla a hechos concretos.
8. No corrijas ni reescribas la candidata.

Devuelve SOLO JSON:
{{
  "respuesta_elegida": "A|B|C|D",
  "opcion_a": "CORRECTA|FALSA",
  "opcion_b": "CORRECTA|FALSA",
  "opcion_c": "CORRECTA|FALSA",
  "opcion_d": "CORRECTA|FALSA",
  "respuesta_unica": true,
  "usa_informacion_externa": false,
  "referencia_correcta": true,
  "dificultad": "ALTA|MUY_ALTA|INSUFICIENTE",
  "observaciones": ["..."]
}}
""".strip()


def auditoria_ciega_superada(
    auditoria: dict[str, Any],
    respuesta_generador: str,
) -> bool:
    """
    Control MATERIAL de la auditoría ciega.

    La dificultad se evalúa aparte por consenso; un único auditor no puede
    rechazar por sí solo una pregunta jurídicamente correcta únicamente por
    considerarla de dificultad insuficiente.
    """
    elegida = str(auditoria.get("respuesta_elegida") or "").strip().upper()
    correcta = str(respuesta_generador or "").strip().upper()
    if elegida != correcta or correcta not in {"A", "B", "C", "D"}:
        return False
    if not bool(auditoria.get("respuesta_unica")):
        return False
    if bool(auditoria.get("usa_informacion_externa")):
        return False
    if not bool(auditoria.get("referencia_correcta")):
        return False
    for letra in "ABCD":
        valor = str(auditoria.get(f"opcion_{letra.lower()}") or "").upper()
        esperado = "CORRECTA" if letra == correcta else "FALSA"
        if valor != esperado:
            return False
    return True


def evaluar_dificultad_por_consenso(
    validacion1: dict[str, Any],
    validacion2: dict[str, Any],
    auditoria: dict[str, Any],
) -> tuple[bool, bool, list[str]]:
    """
    La dificultad se decide por mayoría entre Check 1, Check 2 y auditoría.

    Devuelve:
      - dificultad_ok: al menos 2 de 3 dicen ALTA/MUY_ALTA;
      - discutida: no hay unanimidad;
      - votos: detalle de los tres dictámenes.
    """
    votos = [
        str(validacion1.get("dificultad") or "").strip().upper(),
        str(validacion2.get("dificultad") or "").strip().upper(),
        str(auditoria.get("dificultad") or "").strip().upper(),
    ]
    positivos = sum(v in {"ALTA", "MUY_ALTA"} for v in votos)
    dificultad_ok = positivos >= 2
    discutida = len(set(votos)) > 1
    return dificultad_ok, discutida, votos


def validar_estructura_pregunta(p: dict[str, Any]) -> None:
    obligatorios = [
        "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d",
        "respuesta_correcta", "articulo_referencia", "explicacion_literal",
    ]
    faltantes = [k for k in obligatorios if not str(p.get(k, "")).strip()]
    if faltantes:
        raise RuntimeError("La IA devolvió una pregunta incompleta: " + ", ".join(faltantes))
    correcta = str(p["respuesta_correcta"]).strip().upper()
    if correcta not in {"A", "B", "C", "D"}:
        raise RuntimeError("respuesta_correcta no es A/B/C/D.")
    p["respuesta_correcta"] = correcta


def clave_exacta(p: dict[str, Any]) -> tuple[str, str, str, str, str]:
    def n(v: Any) -> str:
        return re.sub(r"\s+", " ", str(v or "").strip().casefold())
    return (n(p["enunciado"]), n(p["opcion_a"]), n(p["opcion_b"]), n(p["opcion_c"]), n(p["opcion_d"]))


def comprobar_no_duplicada(con: sqlite3.Connection, pregunta: dict[str, Any]) -> None:
    objetivo = clave_exacta(pregunta)
    for f in con.execute("SELECT id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d FROM lote_preguntas"):
        d = dict(f)
        if clave_exacta(d) == objetivo:
            raise RuntimeError(f"La candidata es duplicado exacto de lote_preguntas.id={f['id']}.")


def validacion_superada(validacion: dict[str, Any]) -> bool:
    return (
        bool(validacion.get("aprobable"))
        and str(validacion.get("correccion_juridica") or "").upper() == "OK"
        and str(validacion.get("distractores") or "").upper() == "OK"
        and str(validacion.get("dificultad") or "").upper() in {"ALTA", "MUY_ALTA"}
        and bool(validacion.get("no_clon"))
        and bool(validacion.get("referencia_precisa"))
        and bool(validacion.get("tipo_correcto"))
    )


def _dictamen_legacy(validacion: dict[str, Any]) -> str:
    return "VALIDADA" if validacion_superada(validacion) else "RECHAZADA"


def _texto_observaciones(validacion: dict[str, Any]) -> str:
    obs = validacion.get("observaciones") or []
    if isinstance(obs, str):
        obs = [obs]
    return " | ".join(str(x).replace("\r", " ").replace("\n", " ").strip() for x in obs if str(x).strip())


def _motivo_validacion(validacion: dict[str, Any]) -> str:
    motivos: list[str] = []
    if str(validacion.get("correccion_juridica") or "").upper() != "OK":
        motivos.append("CORRECCION_JURIDICA")
    if str(validacion.get("distractores") or "").upper() != "OK":
        motivos.append("DISTRACTORES")
    if str(validacion.get("dificultad") or "").upper() not in {"ALTA", "MUY_ALTA"}:
        motivos.append("DIFICULTAD")
    if not bool(validacion.get("no_clon")):
        motivos.append("CLON")
    if not bool(validacion.get("referencia_precisa")):
        motivos.append("REFERENCIA")
    if not bool(validacion.get("tipo_correcto")):
        motivos.append("TIPO_PREGUNTA")
    if not bool(validacion.get("aprobable")) and not motivos:
        motivos.append("NO_APROBABLE")
    return ",".join(motivos) if motivos else "OK"


def _html_texto(valor: Any) -> str:
    return html.escape("" if valor is None else str(valor)).replace("\n", "<br>")


def _clase_dictamen(fila: dict[str, Any]) -> str:
    d = str(fila.get("dictamen_ia") or "").upper()
    estado = str(fila.get("estado_actual") or "").upper()
    if d.startswith("VALIDADA") or estado in {"APROBADA", "VALIDADA_IA"}:
        return "ok"
    if "RECHAZ" in d or "RECHAZ" in estado:
        return "ko"
    return "neutral"


def _generar_html(ruta: Path, titulo: str, filas: list[dict[str, Any]], subtitulo: str) -> None:
    tarjetas: list[str] = []
    for f in filas:
        clase = _clase_dictamen(f)
        gid = _html_texto(f.get("generacion_id"))
        dictamen = _html_texto(f.get("dictamen_ia"))
        estado = _html_texto(f.get("estado_actual"))
        lote = _html_texto(f.get("lote_pregunta_id") or "—")
        correcta = str(f.get("respuesta_correcta") or "").strip().upper()
        opciones: list[str] = []
        for letra in ("A", "B", "C", "D"):
            texto = _html_texto(f.get(f"opcion_{letra.lower()}") or "")
            marca = " correcta" if correcta == letra else ""
            opciones.append(f'<div class="opcion{marca}"><span class="letra">{letra})</span> {texto}</div>')

        v1_ok = str(f.get("validacion1_resultado") or "") == "OK"
        v2_ok = str(f.get("validacion2_resultado") or "") == "OK"
        busqueda = " ".join(str(f.get(k) or "") for k in (
            "generacion_id", "dictamen_ia", "estado_actual", "convocatoria",
            "norma", "articulo_temario", "tipo_pregunta", "enunciado"
        )).lower()
        clase_v1 = "texto-ok" if v1_ok else "texto-ko"
        clase_v2 = "texto-ok" if v2_ok else "texto-ko"
        tarjetas.append(f"""
<article class="pregunta {clase}" data-busqueda="{_html_texto(busqueda)}">
  <header>
    <div><strong>Generación #{gid}</strong> <span class="badge {clase}">{dictamen}</span> <span class="badge neutral">{estado}</span></div>
    <div class="meta">{_html_texto(f.get('convocatoria'))} · {_html_texto(f.get('origen_oposicion'))} · {_html_texto(f.get('tipo_pregunta'))} · lote: {lote}</div>
  </header>
  <div class="referencia"><strong>{_html_texto(f.get('norma'))}</strong> · art. {_html_texto(f.get('articulo_temario'))} · referencia propuesta: {_html_texto(f.get('articulo_referencia'))}</div>
  <div class="enunciado">{_html_texto(f.get('enunciado'))}</div>
  <div class="opciones">{''.join(opciones)}</div>
  <div class="respuesta">Respuesta correcta: <strong>{_html_texto(correcta)}</strong></div>
  <details>
    <summary>Validación IA 1 — <span class="{clase_v1}">{_html_texto(f.get('validacion1_resultado'))}</span> · dificultad {_html_texto(f.get('validacion1_dificultad'))}</summary>
    <div class="validacion"><strong>Motivo:</strong> {_html_texto(f.get('validacion1_motivo'))}<br><strong>Observaciones:</strong><br>{_html_texto(f.get('validacion1_observaciones')) or '—'}</div>
  </details>
  <details>
    <summary>Validación IA 2 — <span class="{clase_v2}">{_html_texto(f.get('validacion2_resultado'))}</span> · dificultad {_html_texto(f.get('validacion2_dificultad'))}</summary>
    <div class="validacion"><strong>Motivo:</strong> {_html_texto(f.get('validacion2_motivo'))}<br><strong>Observaciones:</strong><br>{_html_texto(f.get('validacion2_observaciones')) or '—'}</div>
  </details>
  <footer>Generada: {_html_texto(f.get('fecha_generacion'))} · modelo: {_html_texto(f.get('modelo_generacion'))} · validador: {_html_texto(f.get('modelo_validacion'))}</footer>
</article>""")

    contenido = "".join(tarjetas) if tarjetas else "<p>No hay preguntas en este grupo.</p>"
    documento = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titulo)}</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin:0; background:#f5f6f8; color:#202124; }}
main {{ max-width:1100px; margin:auto; padding:24px; }}
h1 {{ margin-bottom:4px; }}
.subtitulo {{ color:#5f6368; margin:0 0 18px; }}
.controles {{ position:sticky; top:0; background:#f5f6f8; padding:10px 0 14px; z-index:2; }}
input {{ width:100%; box-sizing:border-box; padding:11px 12px; font-size:16px; border:1px solid #c8ccd0; border-radius:8px; }}
.resumen {{ margin:10px 0 18px; font-weight:600; }}
.pregunta {{ background:white; border:1px solid #ddd; border-left:5px solid #9aa0a6; border-radius:9px; padding:18px; margin:16px 0; box-shadow:0 1px 2px rgba(0,0,0,.04); }}
.pregunta.ok {{ border-left-color:#188038; }} .pregunta.ko {{ border-left-color:#d93025; }}
header {{ display:flex; justify-content:space-between; gap:15px; flex-wrap:wrap; }}
.meta, footer {{ color:#666; font-size:13px; }}
.badge {{ display:inline-block; padding:3px 7px; border-radius:10px; font-size:12px; margin-left:5px; }}
.badge.ok {{ background:#e6f4ea; color:#137333; }} .badge.ko {{ background:#fce8e6; color:#c5221f; }} .badge.neutral {{ background:#eef0f2; color:#4a4d50; }}
.referencia {{ margin:13px 0; padding:9px 11px; background:#f8f9fa; border-radius:6px; }}
.enunciado {{ font-weight:650; font-size:17px; line-height:1.45; margin:15px 0; }}
.opcion {{ padding:8px 10px; margin:5px 0; border-radius:6px; background:#fafafa; line-height:1.4; }}
.opcion.correcta {{ background:#e6f4ea; border:1px solid #b7dfc2; }}
.letra {{ font-weight:700; }} .respuesta {{ margin:12px 0; }}
details {{ margin:9px 0; border-top:1px solid #eee; padding-top:8px; }} summary {{ cursor:pointer; font-weight:600; }}
.validacion {{ padding:9px 5px 5px; line-height:1.45; }} .texto-ok {{ color:#137333; }} .texto-ko {{ color:#c5221f; }}
footer {{ margin-top:14px; border-top:1px solid #eee; padding-top:8px; }}
.oculta {{ display:none; }}
</style>
</head>
<body><main>
<h1>{html.escape(titulo)}</h1>
<p class="subtitulo">{html.escape(subtitulo)}</p>
<div class="controles"><input id="buscar" type="search" placeholder="Buscar por ID, norma, artículo, convocatoria, estado o texto..."></div>
<div class="resumen">Mostrando <span id="visibles">{len(filas)}</span> de {len(filas)} preguntas</div>
{contenido}
</main>
<script>
const caja=document.getElementById('buscar');
const tarjetas=[...document.querySelectorAll('.pregunta')];
const visibles=document.getElementById('visibles');
function filtrar() {{
 const q=caja.value.trim().toLowerCase(); let n=0;
 tarjetas.forEach(t=>{{ const ok=!q || t.dataset.busqueda.includes(q); t.classList.toggle('oculta',!ok); if(ok)n++; }});
 visibles.textContent=n;
}}
caja.addEventListener('input',filtrar);
</script></body></html>"""
    ruta.write_text(documento, encoding="utf-8")


def _generar_indice_html(rutas: list[tuple[str, Path, int]]) -> Path:
    ruta = REGISTROS / "preguntas_ia_indice.html"
    enlaces = "".join(
        f'<li><a href="{html.escape(p.name)}">{html.escape(nombre)}</a> <span>({cantidad})</span></li>'
        for nombre, p, cantidad in rutas
    )
    ruta.write_text(
        f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Preguntas IA</title><style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:800px;margin:40px auto;padding:0 20px;color:#202124}}li{{margin:14px 0;font-size:18px}}a{{color:#1967d2}}span{{color:#666}}</style></head><body><h1>Preguntas jurídicas generadas por IA</h1><p>Informes de revisión generados automáticamente.</p><ul>{enlaces}</ul></body></html>""",
        encoding="utf-8",
    )
    return ruta


def exportar_informe_ultima_ejecucion(
    generacion_ids: list[int],
    errores: list[str] | None = None,
) -> Path:
    """
    ÚNICO registro persistente de una ejecución:
        registros/preguntas_ia_ultima_ejecucion.html

    No consulta generaciones anteriores.
    """
    REGISTROS.mkdir(parents=True, exist_ok=True)
    ruta = REGISTROS / "preguntas_ia_ultima_ejecucion.html"

    filas: list[sqlite3.Row] = []

    if generacion_ids and DB_AUX.exists():
        marcadores = ",".join("?" for _ in generacion_ids)
        with conectar_auxiliar() as aux:
            filas = aux.execute(
                f"""
                SELECT *
                FROM generaciones_preguntas_ia
                WHERE id IN ({marcadores})
                ORDER BY id
                """,
                tuple(generacion_ids),
            ).fetchall()

    tarjetas: list[str] = []
    n_validadas = 0
    n_rechazadas = 0

    for f in filas:
        try:
            pregunta = json.loads(f["pregunta_json"] or "{}")
        except Exception:
            pregunta = {}

        try:
            v1 = json.loads(f["validacion_json"] or "{}")
        except Exception:
            v1 = {}

        try:
            v2 = json.loads(f["validacion2_json"] or "{}")
        except Exception:
            v2 = {}

        try:
            v3 = json.loads(
                (f["validacion3_json"] if "validacion3_json" in f.keys() else None)
                or "{}"
            )
        except Exception:
            v3 = {}

        try:
            auditoria = json.loads(
                (f["auditoria_json"] if "auditoria_json" in f.keys() else None) or "{}"
            )
        except Exception:
            auditoria = {}

        ok1 = validacion_superada(v1)
        ok2 = validacion_superada(v2)

        # El resultado final es el dictamen ya resuelto por 2 checks o,
        # cuando hubo discrepancia, por el tercer check de desempate.
        dictamen_final = str(f["dictamen_ia"] or "").strip().upper()
        validada = dictamen_final.startswith("VALIDADA")

        if validada:
            n_validadas += 1
        else:
            n_rechazadas += 1

        estado = "VALIDADA" if validada else "RECHAZADA"
        clase = "ok" if validada else "ko"

        opciones = "".join(
            f"<li><b>{letra.upper()})</b> "
            f"{html.escape(str(pregunta.get('opcion_'+letra, '')))}</li>"
            for letra in "abcd"
        )

        obs1 = _texto_observaciones(v1)
        obs2 = _texto_observaciones(v2)
        obs3 = _texto_observaciones(v3)

        incong1 = validacion_internamente_incongruente(v1)
        incong2 = validacion_internamente_incongruente(v2)

        bloque_desempate = ""
        if v3:
            decision3 = str(v3.get("decision_desempate") or "")
            bloque_desempate = f"""
  <div class="check desempate">
    <h3>Check 3 · DESEMPATE · {html.escape(decision3)}</h3>
    <p><b>Corrección:</b> {html.escape(str(v3.get('correccion_juridica', '')))}
       · <b>Distractores:</b> {html.escape(str(v3.get('distractores', '')))}
       · <b>Dificultad:</b> {html.escape(str(v3.get('dificultad', '')))}
       · <b>No clon:</b> {html.escape(str(v3.get('no_clon', '')))}
       · <b>Referencia:</b> {html.escape(str(v3.get('referencia_precisa', '')))}</p>
    <p>{html.escape(obs3)}</p>
  </div>
"""

        bloque_auditoria = ""
        if auditoria:
            audit_ok = auditoria_ciega_superada(
                auditoria,
                pregunta.get("respuesta_correcta"),
            )
            dificultad_ok_html, dificultad_discutida_html, votos_html = (
                evaluar_dificultad_por_consenso(v1, v2, auditoria)
            )
            estado_auditoria = "VALIDA" if audit_ok else "RECHAZA"
            if audit_ok and dificultad_ok_html and dificultad_discutida_html:
                estado_auditoria += " · DIFICULTAD DISCUTIDA"
            elif audit_ok and not dificultad_ok_html:
                estado_auditoria += " · DIFICULTAD INSUFICIENTE POR MAYORÍA"
            clase_auditoria = (
                "okbox"
                if audit_ok and dificultad_ok_html
                else "kobox"
            )
            bloque_auditoria = f"""
  <div class="check {clase_auditoria}">
    <h3>Auditoría ciega · {estado_auditoria}</h3>
    <p><b>Respuesta independiente:</b> {html.escape(str(auditoria.get('respuesta_elegida', '')))}
       · <b>Única:</b> {html.escape(str(auditoria.get('respuesta_unica', '')))}
       · <b>Información externa:</b> {html.escape(str(auditoria.get('usa_informacion_externa', '')))}
       · <b>Referencia:</b> {html.escape(str(auditoria.get('referencia_correcta', '')))}
       · <b>Dificultad auditor:</b> {html.escape(str(auditoria.get('dificultad', '')))}</p>
    <p><b>Votos de dificultad:</b> Check 1 = {html.escape(votos_html[0])} · Check 2 = {html.escape(votos_html[1])} · Auditor = {html.escape(votos_html[2])} · <b>Resultado:</b> {'ALTA/MUY ALTA por mayoría' if dificultad_ok_html else 'INSUFICIENTE por mayoría'}</p>
    <p>{html.escape(_texto_observaciones(auditoria))}</p>
  </div>
"""

        similitud = float(f["similitud_maxima"] or 0.0) if "similitud_maxima" in f.keys() else 0.0
        clon_id = f["similitud_pregunta_id"] if "similitud_pregunta_id" in f.keys() else None
        bloque_originalidad = f"""
  <div class="check {'okbox' if similitud < 0.965 else 'kobox'}">
    <h3>Control de originalidad</h3>
    <p><b>Similitud máxima:</b> {similitud:.1%}
       · <b>Pregunta más próxima:</b> {html.escape(str(clon_id or '—'))}
       · <b>Umbral de rechazo:</b> 96,5%</p>
  </div>
"""

        tarjetas.append(
            f"""
<article class="pregunta {clase}">
  <h2>Generación {int(f['id'])} · {estado}</h2>
  <p><b>Tipo:</b> {html.escape(str(f['tipo_pregunta']))}
     · <b>Norma:</b> {html.escape(str(f['nombre_norma']))}
     · <b>Artículo:</b> {html.escape(str(f['articulo_solicitado']))}</p>
  {
      (
          '<p><b>Integración:</b> '
          + html.escape(str(f['observaciones']))
          + '</p>'
      )
      if (
          'observaciones' in f.keys()
          and f['observaciones']
      )
      else ''
  }

  <p class="enunciado">{html.escape(str(pregunta.get('enunciado', '')))}</p>
  <ol>{opciones}</ol>
  <p><b>Correcta:</b> {html.escape(str(pregunta.get('respuesta_correcta', '')))}
     · <b>Referencia:</b>
     {html.escape(str(pregunta.get('articulo_referencia', '')))}</p>

  <div class="check {'okbox' if ok1 else 'kobox'}">
    <h3>Check 1 · {'VALIDA' if ok1 else 'RECHAZA'}{' · INCONGRUENTE' if incong1 else ''}</h3>
    <p><b>Corrección:</b> {html.escape(str(v1.get('correccion_juridica', '')))}
       · <b>Distractores:</b> {html.escape(str(v1.get('distractores', '')))}
       · <b>Dificultad:</b> {html.escape(str(v1.get('dificultad', '')))}
       · <b>No clon:</b> {html.escape(str(v1.get('no_clon', '')))}
       · <b>Referencia:</b> {html.escape(str(v1.get('referencia_precisa', '')))}</p>
    <p>{html.escape(obs1)}</p>
  </div>

  <div class="check {'okbox' if ok2 else 'kobox'}">
    <h3>Check 2 · {'VALIDA' if ok2 else 'RECHAZA'}{' · INCONGRUENTE' if incong2 else ''}</h3>
    <p><b>Corrección:</b> {html.escape(str(v2.get('correccion_juridica', '')))}
       · <b>Distractores:</b> {html.escape(str(v2.get('distractores', '')))}
       · <b>Dificultad:</b> {html.escape(str(v2.get('dificultad', '')))}
       · <b>No clon:</b> {html.escape(str(v2.get('no_clon', '')))}
       · <b>Referencia:</b> {html.escape(str(v2.get('referencia_precisa', '')))}</p>
    <p>{html.escape(obs2)}</p>
  </div>
  {bloque_desempate}
  {bloque_originalidad}
  {bloque_auditoria}
</article>
"""
        )

    errores = errores or []
    html_errores = "".join(
        f"<li>{html.escape(str(error))}</li>"
        for error in errores
    )

    documento = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preguntas IA · última ejecución</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f5f6f8;
color:#202124;margin:0}}
main{{max-width:1100px;margin:auto;padding:24px}}
.resumen{{background:white;border:1px solid #ddd;border-radius:10px;padding:16px}}
.pregunta{{background:white;border:1px solid #ddd;border-left:6px solid #777;
border-radius:10px;padding:18px;margin:18px 0}}
.pregunta.ok{{border-left-color:#188038}}
.pregunta.ko{{border-left-color:#d93025}}
.enunciado{{font-size:17px;font-weight:650;line-height:1.45}}
li{{margin:6px 0}}
.check{{padding:10px 12px;border-radius:8px;margin-top:12px}}
.okbox{{background:#e6f4ea}}
.kobox{{background:#fce8e6}}
.desempate{{background:#e8f0fe;border:1px solid #aecbfa}}
h3{{margin:0 0 7px}}
</style>
</head>
<body><main>
<h1>Preguntas jurídicas IA · última ejecución</h1>
<div class="resumen">
<p><b>Generadas:</b> {len(filas)}
 · <b>Validadas:</b> {n_validadas}
 · <b>Rechazadas:</b> {n_rechazadas}
 · <b>Errores:</b> {len(errores)}</p>
<p>Este es el ÚNICO registro conservado. Al iniciar otra generación se borra.</p>
{('<h3>Errores de generación</h3><ul>'+html_errores+'</ul>') if errores else ''}
</div>
{''.join(tarjetas) if tarjetas else '<p>No se generó ninguna candidata.</p>'}
</main></body></html>
"""

    ruta.write_text(documento, encoding="utf-8")
    return ruta


def generar(
    con: sqlite3.Connection, ctx: ContextoReferencia, tipo_pregunta: str,
    modelo_generacion: str, modelo_validacion: str, max_ejemplos: int,
    mostrar_detalle: bool = True,
) -> int:
    from openai_api import seleccionar_fragmento_json

    if not fuente_oficial_suficiente(ctx.texto_articulo, tipo_pregunta):
        raise RuntimeError(
            "Fuente oficial insuficiente: articulos_fuente contiene solo "
            "un encabezado/remisión o un texto demasiado corto para generar "
            f"una pregunta {tipo_pregunta} con seguridad."
        )

    ejemplos=cargar_ejemplos(con,ctx,max_ejemplos)
    if mostrar_detalle: print(f"Ejemplos utilizados.................. {len(ejemplos)}")
    datos=seleccionar_fragmento_json(prompt=construir_prompt_generacion(ctx,ejemplos,tipo_pregunta),modelo=modelo_generacion,operacion="generar_pregunta_juridica_ia")
    analisis=datos.get("analisis") or {}; pregunta=datos.get("pregunta") or {}

    tipo_solicitado=(tipo_pregunta or "").strip().upper()
    tipo_devuelto=str(pregunta.get("tipo_pregunta") or "").strip().upper()

    if tipo_solicitado=="PRACTICA":
        if pregunta.get("no_apta_practica") is True:
            raise RuntimeError(
                "La IA indica que esta referencia no permite construir una "
                "práctica ALTA/MUY ALTA sin inventar normativa."
            )
        if tipo_devuelto!="PRACTICA":
            raise RuntimeError(
                "La generación no devolvió una pregunta PRACTICA."
            )

    validar_estructura_pregunta(pregunta); comprobar_no_duplicada(con,pregunta)
    validacion1=seleccionar_fragmento_json(
        prompt=construir_prompt_validacion(ctx,ejemplos,pregunta,1),
        modelo=modelo_validacion,
        operacion="validar_pregunta_juridica_ia_1",
    )
    validacion2=seleccionar_fragmento_json(
        prompt=construir_prompt_validacion(ctx,ejemplos,pregunta,2),
        modelo=modelo_validacion,
        operacion="validar_pregunta_juridica_ia_2",
    )

    doble_ok, validacion3 = resolver_dictamen_validaciones(
        ctx=ctx,
        ejemplos=ejemplos,
        pregunta=pregunta,
        validacion1=validacion1,
        validacion2=validacion2,
        modelo_validacion=modelo_validacion,
    )

    # Capa posterior: no modifica el generador ni los checks actuales.
    auditoria: dict[str, Any] = {}
    originalidad_ok = True
    similitud_maxima = 0.0
    similitud_pregunta_id: int | None = None
    auditoria_ok = False
    dificultad_ok = False
    dificultad_discutida = False
    votos_dificultad: list[str] = []

    if doble_ok:
        originalidad_ok, similitud_maxima, similitud_pregunta_id = comprobar_originalidad_masiva(
            con, ctx, pregunta
        )
        if originalidad_ok:
            auditoria = seleccionar_fragmento_json(
                prompt=construir_prompt_auditoria_ciega(ctx, pregunta),
                modelo=modelo_validacion,
                operacion="auditar_pregunta_juridica_ia_ciega",
            )
            auditoria_ok = auditoria_ciega_superada(
                auditoria, pregunta["respuesta_correcta"]
            )
            dificultad_ok, dificultad_discutida, votos_dificultad = (
                evaluar_dificultad_por_consenso(
                    validacion1,
                    validacion2,
                    auditoria,
                )
            )

    aceptada_final = (
        doble_ok
        and originalidad_ok
        and auditoria_ok
        and dificultad_ok
    )
    if aceptada_final:
        dictamen = "VALIDADA_AUDITADA"
        estado = "VALIDADA_IA"
    elif doble_ok and not originalidad_ok:
        dictamen = "RECHAZADA_SIMILITUD"
        estado = "RECHAZADA_IA"
    elif doble_ok:
        dictamen = "RECHAZADA_AUDITORIA"
        estado = "RECHAZADA_IA"
    else:
        dictamen = "RECHAZADA"
        estado = "RECHAZADA_IA"

    with conectar_auxiliar() as aux:
        cur=aux.execute(
            """INSERT INTO generaciones_preguntas_ia (
                fecha_generacion,
                convocatoria_contexto_id,
                convocatoria_codigo,
                origen_oposicion,
                temario_referencia_id,
                tema_id,
                norma_id,
                nombre_norma,
                articulo_solicitado,
                articulo_fuente_id,
                tipo_pregunta,
                dificultad_objetivo,
                modelo_generacion,
                modelo_validacion,
                prompt_version,
                ejemplos_ids_json,
                analisis_json,
                pregunta_json,
                validacion_json,
                validacion2_json,
                validacion3_json,
                dictamen_ia,
                estado,
                auditoria_json,
                similitud_maxima,
                similitud_pregunta_id,
                dictamen_auditoria
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ahora_iso(),
                ctx.convocatoria_id,
                ctx.convocatoria_codigo,
                ctx.origen_oposicion,
                ctx.referencia_id,
                ctx.tema_id,
                ctx.norma_id,
                ctx.nombre_norma_csv,
                ctx.articulo_solicitado,
                ctx.articulo_fuente_id,
                tipo_pregunta,
                DIFICULTAD_OBJETIVO,
                modelo_generacion,
                modelo_validacion,
                PROMPT_VERSION,
                json.dumps([p["id"] for p in ejemplos]),
                json.dumps(analisis,ensure_ascii=False),
                json.dumps(pregunta,ensure_ascii=False),
                json.dumps(validacion1,ensure_ascii=False),
                json.dumps(validacion2,ensure_ascii=False),
                json.dumps(validacion3,ensure_ascii=False) if validacion3 else None,
                dictamen,
                estado,
                json.dumps(auditoria, ensure_ascii=False) if auditoria else None,
                float(similitud_maxima),
                similitud_pregunta_id,
                (
                    "OK_DIFICULTAD_DISCUTIDA"
                    if aceptada_final and dificultad_discutida
                    else "OK"
                    if aceptada_final
                    else "SIMILITUD"
                    if doble_ok and not originalidad_ok
                    else "DIFICULTAD"
                    if doble_ok and originalidad_ok and auditoria_ok and not dificultad_ok
                    else "NO"
                ),
            ),
        )
        generacion_id=int(cur.lastrowid)
        aux.commit()

    if aceptada_final:
        try: aprobar(con,generacion_id,modo_publicacion="IA_DOBLE_CHECK_AUDITADA")
        except Exception as exc:
            with conectar_auxiliar() as aux:
                aux.execute("UPDATE generaciones_preguntas_ia SET estado='ERROR_PUBLICACION', observaciones=? WHERE id=?",(f"Validada por doble check, pero no publicada: {exc}",generacion_id)); aux.commit()
            if mostrar_detalle: print(f"ERROR al publicar automáticamente: {exc}")
    if mostrar_detalle:
        r=resumen_generacion(generacion_id)
        print("\n"+"="*78); print(f"GENERACIÓN {generacion_id} | {r['estado']}"); print("="*78)
        print(pregunta["enunciado"])
        for letra in "abcd": print(f"{letra.upper()}) {pregunta['opcion_'+letra]}")
        print(f"Correcta: {pregunta['respuesta_correcta']}"); print(f"Referencia: {pregunta['articulo_referencia']}")
        print(f"Validación 1: {validacion1.get('dificultad')} | {'OK' if validacion_superada(validacion1) else 'NO'}")
        print(f"Validación 2: {validacion2.get('dificultad')} | {'OK' if validacion_superada(validacion2) else 'NO'}")
        if validacion3 is not None:
            print(
                "Desempate: "
                f"{validacion3.get('decision_desempate')} | "
                f"{validacion3.get('dificultad')}"
            )
        print(f"Dictamen IA: {dictamen}")
        print(
            "Publicación: "
            + ("automática" if doble_ok and r['estado']=='APROBADA' else "no publicada")
        )
    return generacion_id

def resumen_generacion(generacion_id: int) -> dict[str, Any]:
    with conectar_auxiliar() as aux:
        fila=aux.execute("SELECT * FROM generaciones_preguntas_ia WHERE id=?",(generacion_id,)).fetchone()
    if fila is None: raise RuntimeError(f"No existe la generación {generacion_id}.")
    try: v1=json.loads(fila["validacion_json"] or "{}")
    except Exception: v1={}
    dictamen=fila["dictamen_ia"] if "dictamen_ia" in fila.keys() and fila["dictamen_ia"] else _dictamen_legacy(v1)
    return {"id":int(fila["id"]),"nombre_norma":fila["nombre_norma"],"articulo":fila["articulo_solicitado"],"estado":fila["estado"],"dificultad":str(v1.get("dificultad") or "").upper(),"dictamen":dictamen,"lote_pregunta_id":fila["lote_pregunta_id"]}

def generar_lote(
    con: sqlite3.Connection,
    contextos: list[ContextoReferencia],
    cantidad: int,
    tipo_pregunta: str,
    modelo_generacion: str,
    modelo_validacion: str,
    max_ejemplos: int,
) -> list[dict[str, Any]]:
    iniciar_ejecucion_generacion()

    contextos_validos = filtrar_contextos_por_fuente(
        contextos,
        tipo_pregunta,
    )

    if not contextos_validos:
        ruta = exportar_informe_ultima_ejecucion(
            [],
            [
                "No existen referencias con texto oficial suficientemente "
                f"completo para generar preguntas {tipo_pregunta}."
            ],
        )
        finalizar_ejecucion_generacion()
        raise RuntimeError(
            "No existen referencias con texto oficial suficientemente "
            f"completo para generar preguntas {tipo_pregunta}. "
            f"Informe: {ruta}"
        )

    avisos_perfil: list[str] = []

    if str(tipo_pregunta).strip().upper() == "TEORICA":
        convocatoria_ids = {
            int(ctx.convocatoria_id)
            for ctx in contextos_validos
        }
        if len(convocatoria_ids) != 1:
            raise RuntimeError(
                "Los contextos del lote no pertenecen a una única convocatoria."
            )

        convocatoria_id = next(iter(convocatoria_ids))
        modelo = cargar_modelo_generacion_ia(
            con,
            convocatoria_id,
        )

        secuencia, avisos_perfil = secuencia_contextos_segun_modelo(
            contextos_validos,
            modelo,
            cantidad,
        )
    else:
        # PRACTICA: de momento utiliza todas las referencias prácticas aptas
        # de la convocatoria. El perfil práctico podrá incorporarse a la misma
        # tabla cuando se cierre su patrón de examen.
        secuencia = secuencia_contextos_lote(
            contextos_validos,
            cantidad,
        )

    resultados: list[dict[str, Any]] = []
    ids_ejecucion: list[int] = []
    errores_texto: list[str] = []

    print("\n" + "=" * 78)
    print("GENERACIÓN POR LOTE · DOBLE CHECK IA")
    print("=" * 78)
    print(f"Candidatas solicitadas............... {cantidad}")
    print(f"Referencias aptas originales......... {len(contextos)}")
    print(f"Referencias con fuente completa...... {len(contextos_validos)}")

    if str(tipo_pregunta).strip().upper() == "TEORICA":
        print("Reparto............................... convocatoria_modelo_bloques")
        print("\nDISTRIBUCIÓN DEL LOTE")
        for parte, norma, n in resumen_reparto_secuencia(secuencia):
            print(f"  {parte:<10} | {norma:<45} | {n:>3}")
        if avisos_perfil:
            print("\nAVISOS DEL MODELO")
            for aviso in avisos_perfil:
                print(f"  - {aviso}")
    else:
        print("Reparto............................... referencias prácticas aptas")

    print("Registro.............................. UN SOLO HTML")

    try:
        for indice, ctx in enumerate(secuencia, 1):
            print(
                f"\n[{indice}/{cantidad}] "
                f"{ctx.nombre_norma_csv} · art. {ctx.articulo_solicitado}"
            )
            try:
                gid = generar(
                    con,
                    ctx,
                    tipo_pregunta,
                    modelo_generacion,
                    modelo_validacion,
                    max_ejemplos,
                    mostrar_detalle=False,
                )
                ids_ejecucion.append(gid)
                r = resumen_generacion(gid)
                resultados.append(r)
                print(
                    f"  Generación {gid} | {r['dictamen']} | "
                    f"{r['estado']} | lote={r['lote_pregunta_id'] or '-'}"
                )
            except Exception as exc:
                mensaje = (
                    f"{ctx.nombre_norma_csv} · art. "
                    f"{ctx.articulo_solicitado}: {exc}"
                )
                errores_texto.append(mensaje)
                resultados.append(
                    {
                        "id": None,
                        "estado": "ERROR",
                        "dictamen": "ERROR",
                        "error": str(exc),
                    }
                )
                print(f"  ERROR: {exc}")

        validadas = [
            r for r in resultados
            if str(r.get("dictamen", "")).startswith("VALIDADA")
        ]
        rechazadas = [
            r for r in resultados
            if str(r.get("dictamen", "")).startswith("RECHAZADA")
        ]

        ruta = exportar_informe_ultima_ejecucion(
            ids_ejecucion,
            errores_texto,
        )

        print("\n" + "=" * 78)
        print("RESUMEN DEL LOTE")
        print("=" * 78)
        print(f"Solicitadas.......................... {cantidad}")
        print(f"Validadas y auditadas................. {len(validadas)}")
        print(f"No validadas/auditadas............... {len(rechazadas)}")
        print(f"Errores............................... {len(errores_texto)}")
        print(f"ÚNICO informe........................ {ruta}")

        return resultados

    finally:
        # La base auxiliar era solo de trabajo. No se conserva historial.
        finalizar_ejecucion_generacion()


def metadatos_norma_desde_ejemplos(ejemplos: list[dict[str, Any]]) -> dict[str, Any]:
    # Se reutiliza la forma ya existente en las preguntas del mismo banco/norma.
    for p in ejemplos:
        if p.get("tipo_norma_normalizado") and p.get("nombre_norma_normalizado"):
            return {
                "tipo_norma": p.get("tipo_norma"),
                "nombre_norma": p.get("nombre_norma"),
                "tipo_norma_normalizado": p.get("tipo_norma_normalizado"),
                "nombre_norma_normalizado": p.get("nombre_norma_normalizado"),
            }
    raise RuntimeError("No hay un ejemplo con metadatos normalizados de norma para reutilizar.")


def aprobar(
    con: sqlite3.Connection,
    generacion_id: int,
    modo_publicacion: str = "HUMANA",
) -> int:
    """
    Publica una candidata validada.

    ORDEN ESTRUCTURAL OBLIGATORIO:
        1. validar;
        2. insertar COMPLETA en lote_preguntas;
        3. COMMIT del repositorio maestro;
        4. dejar la sincronización de bancos al sincronizador común al final
           de la ejecución.

    El generador no contiene reglas propias de selección de bancos.
    """
    with conectar_auxiliar() as aux:
        g = aux.execute(
            "SELECT * FROM generaciones_preguntas_ia WHERE id = ?",
            (generacion_id,),
        ).fetchone()

        if g is None:
            raise RuntimeError("No existe esa generación.")

        estados_aprobables = {
            "PENDIENTE_REVISION",
            "RECHAZADA_IA",
            "VALIDADA_IA",
            "ERROR_PUBLICACION",
        }

        if g["estado"] not in estados_aprobables:
            raise RuntimeError(
                "La candidata no está en un estado publicable; "
                f"estado actual: {g['estado']}."
            )

        ctx = cargar_contexto(
            con,
            int(g["convocatoria_contexto_id"]),
            int(g["temario_referencia_id"]),
        )

        # La convocatoria es contexto de generación, NO propietaria
        # de la pregunta.
        ejemplos = cargar_ejemplos(
            con,
            ctx,
            MAX_EJEMPLOS_DEFECTO,
        )
        meta = metadatos_norma_desde_ejemplos(ejemplos)

        pregunta = json.loads(g["pregunta_json"])
        validar_estructura_pregunta(pregunta)
        comprobar_no_duplicada(con, pregunta)

        referencia_cruda = str(
            pregunta["articulo_referencia"]
        ).strip()
        referencia = extraer_referencia_articulo_precisa(
            referencia_cruda
        )

        articulo_base_ref = normalizar_articulo(referencia)
        articulo_base_temario = normalizar_articulo(
            ctx.articulo_solicitado
        )

        if (
            articulo_base_ref is None
            or articulo_base_temario is None
        ):
            raise RuntimeError(
                "No se puede normalizar con seguridad el artículo."
            )

        if articulo_base_ref != articulo_base_temario:
            raise RuntimeError(
                "La referencia de la candidata no pertenece al artículo "
                "principal utilizado para generarla."
            )

        # La norma debe existir ya en el catálogo porque procede de
        # temario_referencias.norma_id.
        norma_catalogo = con.execute(
            "SELECT id, nombre_canonico FROM normas WHERE id=?",
            (int(ctx.norma_id),),
        ).fetchone()

        if norma_catalogo is None:
            raise RuntimeError(
                "La norma del contexto no existe en el catálogo central."
            )

        contenido = json.dumps(
            pregunta,
            ensure_ascii=False,
            sort_keys=True,
        )
        h = hashlib.sha256(
            contenido.encode("utf-8")
        ).hexdigest()

        REGISTROS.mkdir(
            parents=True,
            exist_ok=True,
        )

        ruta_rel = (
            "registros/preguntas_ia_ultima_ejecucion.html"
        )
        nombre_archivo = (
            "preguntas_ia_ultima_ejecucion.html"
        )
        inicio = ahora_iso()

        # ---------------------------------------------------------------
        # FASE 1: PERSISTENCIA MAESTRA
        # ---------------------------------------------------------------
        con.execute("BEGIN IMMEDIATE")
        try:
            cur_imp = con.execute(
                """
                INSERT INTO importaciones_ficheros (
                    ruta_relativa,
                    nombre_fichero,
                    hash_sha256,
                    tipo_fuente,
                    estado,
                    paginas_totales,
                    paginas_insertadas,
                    paginas_omitidas,
                    paginas_error,
                    fecha_inicio,
                    fecha_fin,
                    reimportar,
                    ultimo_error
                )
                VALUES (
                    ?, ?, ?, ?,
                    'COMPLETADO',
                    1, 1, 0, 0,
                    ?, ?, 0, NULL
                )
                """,
                (
                    ruta_rel,
                    nombre_archivo,
                    h,
                    TIPO_FUENTE,
                    inicio,
                    inicio,
                ),
            )
            importacion_id = int(
                cur_imp.lastrowid
            )

            cur = con.execute(
                """
                INSERT INTO lote_preguntas (
                    enunciado,
                    opcion_a,
                    opcion_b,
                    opcion_c,
                    opcion_d,
                    respuesta_correcta,
                    tipo_clasificacion,
                    tipo_norma,
                    nombre_norma,
                    articulo,
                    tema_no_juridico,
                    origen_oposicion,
                    tipo_fuente,
                    importacion_fichero_id,
                    pagina_origen,
                    norma_id_normalizada,
                    articulo_normalizado,
                    teorica_practica,
                    tipo_norma_normalizado,
                    nombre_norma_normalizado,
                    estado_vigencia,
                    vigencia_revision_clave,
                    criterio_inclusion_banco
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    NULL, NULL, ?
                )
                """,
                (
                    pregunta["enunciado"],
                    pregunta["opcion_a"],
                    pregunta["opcion_b"],
                    pregunta["opcion_c"],
                    pregunta["opcion_d"],
                    pregunta["respuesta_correcta"],
                    CLASIFICACION_JURIDICA,
                    meta["tipo_norma"],
                    meta["nombre_norma"],
                    referencia,
                    None,
                    # Procedencia/contexto; NO determina banco.
                    ctx.origen_oposicion,
                    TIPO_FUENTE,
                    importacion_id,
                    1,
                    int(ctx.norma_id),
                    referencia,
                    str(g["tipo_pregunta"]).strip().upper(),
                    meta["tipo_norma_normalizado"],
                    meta["nombre_norma_normalizado"],
                    "NORMA_ARTICULO",
                ),
            )

            pregunta_id = int(
                cur.lastrowid
            )

            if con.execute(
                "PRAGMA foreign_key_check"
            ).fetchall():
                raise RuntimeError(
                    "La inserción maestra produciría errores "
                    "de claves foráneas."
                )

            if (
                con.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                != "ok"
            ):
                raise RuntimeError(
                    "La inserción maestra no supera "
                    "integrity_check."
                )

            con.commit()

        except Exception:
            con.rollback()
            raise

        # ---------------------------------------------------------------
        # FASE 2: SINCRONIZACIÓN DIFERIDA
        # ---------------------------------------------------------------
        # No se aplican aquí reglas de banco. Todas las preguntas aprobadas
        # se sincronizan al final de la ejecución mediante sincronizar_bancos.py,
        # cuya única fuente de reglas es mantener_banco_preguntas.py.
        observaciones_publicacion = (
            f"lote_preguntas.id={pregunta_id}; "
            "bancos=PENDIENTE_SINCRONIZACION_COMUN"
        )

        aux.execute(
            """
            UPDATE generaciones_preguntas_ia
            SET estado='APROBADA',
                fecha_revision=?,
                lote_pregunta_id=?,
                tipo_publicacion=?,
                observaciones=?
            WHERE id=?
            """,
            (
                ahora_iso(),
                pregunta_id,
                modo_publicacion,
                observaciones_publicacion,
                generacion_id,
            ),
        )
        aux.commit()

    print(
        f"Generación {generacion_id} aprobada."
    )
    print(
        f"lote_preguntas.id.................... "
        f"{pregunta_id}"
    )
    print(
        "Persistencia maestra.................. OK"
    )
    print(
        f"tipo_fuente.......................... "
        f"{TIPO_FUENTE}"
    )
    print(
        f"origen_oposicion..................... "
        f"{ctx.origen_oposicion} "
        "(solo procedencia/contexto)"
    )

    return pregunta_id


def retirar_publicada(
    con: sqlite3.Connection,
    generacion_id: int,
    observaciones: str | None = None,
) -> None:
    """
    Retira una pregunta IA de los BANCOS, pero conserva siempre
    lote_preguntas.

    El borrado de una pregunta del repositorio maestro requiere un proceso
    distinto, explícito y excepcional.
    """
    with conectar_auxiliar() as aux:
        g = aux.execute(
            "SELECT * FROM generaciones_preguntas_ia WHERE id=?",
            (generacion_id,),
        ).fetchone()

    if g is None:
        raise RuntimeError("No existe esa generación.")

    if not g["lote_pregunta_id"]:
        raise RuntimeError(
            "La generación no tiene pregunta publicada "
            "en lote_preguntas."
        )

    pregunta_id = int(
        g["lote_pregunta_id"]
    )

    lp = con.execute(
        """
        SELECT id, tipo_fuente
        FROM lote_preguntas
        WHERE id=?
        """,
        (pregunta_id,),
    ).fetchone()

    if lp is None:
        raise RuntimeError(
            "La pregunta no existe en lote_preguntas."
        )

    if (
        str(lp["tipo_fuente"]).strip().lower()
        != TIPO_FUENTE
    ):
        raise RuntimeError(
            "Protección: la pregunta no tiene "
            "tipo_fuente=ia_generada."
        )

    # Solo se eliminan las MATERIALIZACIONES de banco.
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            """
            DELETE FROM banco_preguntas
            WHERE pregunta_id=?
            """,
            (pregunta_id,),
        )

        if con.execute(
            "PRAGMA foreign_key_check"
        ).fetchall():
            raise RuntimeError(
                "La desvinculación produciría errores "
                "de claves foráneas."
            )

        con.commit()

    except Exception:
        con.rollback()
        raise

    with conectar_auxiliar() as aux:
        aux.execute(
            """
            UPDATE generaciones_preguntas_ia
            SET estado='RETIRADA_BANCOS_HUMANA',
                fecha_retirada=?,
                observaciones=?
            WHERE id=?
            """,
            (
                ahora_iso(),
                observaciones,
                generacion_id,
            ),
        )
        aux.commit()

    print(
        f"Generación {generacion_id}: retirada de bancos."
    )
    print(
        f"lote_preguntas.id conservado......... {pregunta_id}"
    )


def rechazar(generacion_id: int, observaciones: str | None) -> None:
    with conectar_auxiliar() as aux:
        g = aux.execute("SELECT estado FROM generaciones_preguntas_ia WHERE id=?", (generacion_id,)).fetchone()
        if g is None:
            raise RuntimeError("No existe esa generación.")
        if g["estado"] == "APROBADA":
            raise RuntimeError("Una generación ya aprobada no puede rechazarse desde este script.")
        aux.execute(
            "UPDATE generaciones_preguntas_ia SET estado='RECHAZADA_HUMANA', fecha_revision=?, observaciones=? WHERE id=?",
            (ahora_iso(), observaciones, generacion_id),
        )
        aux.commit()
    print(f"Generación {generacion_id} marcada RECHAZADA_HUMANA.")


def causa_resumida(validacion: dict[str, Any], estado: str) -> str:
    """Resume el motivo principal sin modificar el dictamen almacenado."""
    if estado == "RECHAZADA_HUMANA":
        return "HUMANA"
    if estado == "APROBADA":
        return str(validacion.get("dificultad") or "APROBADA").upper()
    if estado == "PENDIENTE_REVISION":
        return str(validacion.get("dificultad") or "PENDIENTE").upper()
    if estado != "RECHAZADA_IA":
        return estado

    if str(validacion.get("correccion_juridica") or "").upper() == "ERROR":
        return "JURIDICA"
    if not bool(validacion.get("referencia_precisa", True)):
        return "REFERENCIA"
    if str(validacion.get("distractores") or "").upper() == "ERROR":
        return "DISTRACTORES"
    if not bool(validacion.get("no_clon", True)):
        return "CLON"
    if str(validacion.get("dificultad") or "").upper() == "INSUFICIENTE":
        return "INSUFICIENTE"
    return "RECHAZADA_IA"


def listar_generaciones() -> None:
    with conectar_auxiliar() as aux: filas=aux.execute("SELECT * FROM generaciones_preguntas_ia ORDER BY id DESC LIMIT 100").fetchall()
    if not filas: print("No hay generaciones registradas."); return
    print("ID    | Fecha               | Convocatoria    | Artículo | Dictamen IA | Estado            | Lote"); print("-"*115)
    for f in filas:
        try: v1=json.loads(f["validacion_json"] or "{}")
        except Exception: v1={}
        dictamen=(f["dictamen_ia"] if "dictamen_ia" in f.keys() else None) or _dictamen_legacy(v1)
        print(f"{f['id']:>5} | {f['fecha_generacion']:<19} | {f['convocatoria_codigo']:<15} | {str(f['articulo_solicitado']):<8} | {dictamen:<11} | {f['estado']:<17} | {f['lote_pregunta_id'] or '-'}")

def detalle_generacion(generacion_id: int) -> None:
    with conectar_auxiliar() as aux: f=aux.execute("SELECT * FROM generaciones_preguntas_ia WHERE id=?",(generacion_id,)).fetchone()
    if f is None: raise RuntimeError(f"No existe la generación {generacion_id}.")
    try: pregunta=json.loads(f["pregunta_json"] or "{}")
    except Exception: pregunta={}
    try: v1=json.loads(f["validacion_json"] or "{}")
    except Exception: v1={}
    try: v2=json.loads((f["validacion2_json"] if "validacion2_json" in f.keys() else None) or "{}")
    except Exception: v2={}
    dictamen=(f["dictamen_ia"] if "dictamen_ia" in f.keys() else None) or _dictamen_legacy(v1)
    print("\n"+"="*78); print(f"DETALLE GENERACIÓN IA Nº {generacion_id}"); print("="*78); print(f"Dictamen IA.......................... {dictamen}"); print(f"Estado actual........................ {f['estado']}"); print(f"Convocatoria......................... {f['convocatoria_codigo']}"); print(f"Norma................................ {f['nombre_norma']}"); print(f"Artículo............................. {f['articulo_solicitado']}"); print(f"ID lote.............................. {f['lote_pregunta_id'] or '-'}")
    print("\nPREGUNTA\n"+"-"*78); print(pregunta.get("enunciado",""));
    for letra in "abcd": print(f"{letra.upper()}) {pregunta.get('opcion_'+letra,'')}")
    print(f"Correcta: {pregunta.get('respuesta_correcta','-')}"); print(f"Referencia: {pregunta.get('articulo_referencia','-')}")
    for num,v in ((1,v1),(2,v2)):
        print(f"\nVALIDACIÓN IA {num}\n"+"-"*78)
        if not v: print("[no disponible: generación anterior al doble check]"); continue
        print(f"Aprobable............................ {'SÍ' if bool(v.get('aprobable')) else 'NO'}"); print(f"Corrección jurídica.................. {v.get('correccion_juridica','-')}"); print(f"Distractores......................... {v.get('distractores','-')}"); print(f"Dificultad........................... {v.get('dificultad','-')}"); print(f"No clon.............................. {v.get('no_clon','-')}"); print(f"Referencia precisa................... {v.get('referencia_precisa','-')}")
        for obs in v.get("observaciones") or []: print(f"  - {obs}")
    if f["observaciones"]: print(f"\nObservaciones posteriores:\n  {f['observaciones']}")
    print("="*78)

def crear_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="Generación experimental de preguntas jurídicas con IA."); p.add_argument("--db",default=str(DB_DEFECTO)); g=p.add_mutually_exclusive_group(); g.add_argument("--convocatoria-id",type=int); g.add_argument("--codigo"); p.add_argument("--listar-referencias",action="store_true"); seleccion=p.add_mutually_exclusive_group(); seleccion.add_argument("--referencia-id",type=int); seleccion.add_argument("--tema-id",type=int); seleccion.add_argument("--todos-temas",action="store_true"); p.add_argument("--tipo",choices=["TEORICA","PRACTICA"],default="TEORICA"); p.add_argument("--modelo-generacion",default=MODELO_DEFECTO); p.add_argument("--modelo-validacion",default=MODELO_DEFECTO); p.add_argument("--max-ejemplos",type=int,default=MAX_EJEMPLOS_DEFECTO); p.add_argument("--cantidad",type=int,default=1); p.add_argument("--listar-generaciones",action="store_true"); p.add_argument("--detalle",type=int); p.add_argument("--aprobar",type=int); p.add_argument("--retirar",type=int); p.add_argument("--exportar-csv",action="store_true"); p.add_argument("--rechazar",type=int); p.add_argument("--observaciones"); return p

def main() -> int:
    args=crear_parser().parse_args(); ruta_db=Path(args.db).resolve()
    if not ruta_db.is_file(): print(f"ERROR: no existe la base: {ruta_db}"); return 1
    if (
        args.listar_generaciones
        or args.detalle is not None
        or args.exportar_csv
        or args.aprobar is not None
        or args.retirar is not None
        or args.rechazar is not None
    ):
        print(
            "Esta versión no conserva historial de generaciones. "
            "Solo existe el HTML de la última ejecución."
        )
        return 1
    with conectar_maestra(ruta_db) as con:
        if args.convocatoria_id is None and not args.codigo: print("ERROR: indique --convocatoria-id o --codigo."); return 1
        convocatoria=obtener_convocatoria(con,args.convocatoria_id,args.codigo); convocatoria_id=int(convocatoria["id"])
        if args.listar_referencias:
            listar_referencias(con,convocatoria_id)
            return 0

        if args.max_ejemplos<=0 or args.cantidad<=0:
            print("ERROR: cantidades deben ser positivas.")
            return 1

        contextos_aptos, temas_aptos = obtener_referencias_aptas_todos_temas(
            con,
            convocatoria_id,
        )

        print(f"Temas jurídicos aptos................. {temas_aptos}")
        print(f"Referencias aptas totales............. {len(contextos_aptos)}")

        generar_lote(
            con,
            contextos_aptos,
            args.cantidad,
            args.tipo,
            args.modelo_generacion,
            args.modelo_validacion,
            args.max_ejemplos,
        )

    # La conexión maestra ya está cerrada. Una única operación común
    # sincroniza todos los bancos y ejecuta la validación completa.
    sincronizar_todos_bancos(ruta_db, aplicar=True, validar_final=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)