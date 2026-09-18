"""
Validación completa de solo lectura de OpoCoach-Mantenimiento.

Comprueba:
1. Integridad SQLite y claves foráneas.
2. Estado básico de banco_preguntas (duplicados y partes nulas).
3. Duplicados/casi duplicados en lote_preguntas: tras normalizar únicamente
   mayúsculas/minúsculas y espacios, exige 0 duplicados exactos y 0 pares con
   al menos 4 de 5 campos idénticos y distancia de edición <= 5 en el quinto.
4. Calidad mínima de las preguntas INCLUIDAS en bancos:
   - respuesta_correcta válida (A/B/C/D);
   - la opción marcada como correcta no puede estar duplicada en otra opción.
   Las duplicaciones sólo entre distractores se muestran como aviso, pero no bloquean.
5. Las preguntas en REVISION se contabilizan como cuarentena y no invalidan por sí mismas.
   Ninguna exclusión global puede conservar un vínculo INCLUIDA.
6. Normalización jurídica: las incompletas pueden permanecer en lote_preguntas,
   pero nunca entre las preguntas INCLUIDAS del banco.
7. Vigencia: ningún estado OBSOLETA* puede permanecer entre las preguntas INCLUIDAS.
8. mantener_banco_preguntas.py en modo SOLO REVISIÓN para cada convocatoria.
9. auditar_bancos_seleccion.py contra el constructor vigente.
10. Modelos de examen configurados (si existe la nueva tabla).
11. auditar_bd.py.

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



def normalizar_texto_duplicados(texto: str | None) -> str:
    """Normaliza solo espacios y mayúsculas/minúsculas; no altera contenido."""
    return re.sub(r"\s+", " ", (texto or "").replace("\xa0", " ")).strip().lower()


def distancia_edicion_hasta(a: str, b: str, limite: int = 5) -> int | None:
    """Levenshtein acotada. Devuelve None si la distancia supera el límite."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limite:
        return None

    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        minimo_j = max(1, i - limite)
        maximo_j = min(len(b), i + limite)
        actual = [limite + 1] * (len(b) + 1)
        actual[0] = i
        minimo_fila = limite + 1

        for j in range(minimo_j, maximo_j + 1):
            coste = 0 if ca == b[j - 1] else 1
            actual[j] = min(
                anterior[j] + 1,
                actual[j - 1] + 1,
                anterior[j - 1] + coste,
            )
            minimo_fila = min(minimo_fila, actual[j])

        if minimo_fila > limite:
            return None
        anterior = actual

    distancia = anterior[len(b)]
    return distancia if distancia <= limite else None


def auditar_duplicados_lote(conexion: sqlite3.Connection) -> tuple[int, int]:
    """
    Devuelve:
      - número de grupos duplicados exactos;
      - número de pares casi duplicados.

    Regla de casi duplicado:
      * enunciado + A + B + C + D se comparan por posición;
      * al menos 4 de los 5 campos son idénticos tras normalización;
      * el único campo distinto tiene distancia de edición <= 5.
    """
    campos = ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d")
    filas = conexion.execute(
        """
        SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d
        FROM lote_preguntas
        ORDER BY id
        """
    ).fetchall()

    normalizadas: dict[int, tuple[str, str, str, str, str]] = {}
    grupos_exactos: dict[tuple[str, str, str, str, str], list[int]] = {}

    for fila in filas:
        pregunta_id = int(fila[0])
        valores = tuple(normalizar_texto_duplicados(fila[i]) for i in range(1, 6))
        normalizadas[pregunta_id] = valores
        grupos_exactos.setdefault(valores, []).append(pregunta_id)

    duplicados_exactos = sum(1 for ids in grupos_exactos.values() if len(ids) > 1)

    # Índices por los otros cuatro campos para no comparar todas contra todas.
    indices: list[dict[tuple[str, ...], list[tuple[int, str]]]] = [
        {} for _ in range(5)
    ]
    for pregunta_id, valores in normalizadas.items():
        for indice_distinto in range(5):
            firma = valores[:indice_distinto] + valores[indice_distinto + 1:]
            indices[indice_distinto].setdefault(firma, []).append(
                (pregunta_id, valores[indice_distinto])
            )

    pares_casi: set[tuple[int, int]] = set()
    for indice in indices:
        for candidatos in indice.values():
            if len(candidatos) < 2:
                continue
            for i in range(len(candidatos)):
                id_a, texto_a = candidatos[i]
                for j in range(i + 1, len(candidatos)):
                    id_b, texto_b = candidatos[j]

                    # Los exactos se contabilizan aparte.
                    if texto_a == texto_b:
                        continue

                    if distancia_edicion_hasta(texto_a, texto_b, 5) is not None:
                        pares_casi.add((min(id_a, id_b), max(id_a, id_b)))

    return duplicados_exactos, len(pares_casi)


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
            duplicados_lote_exactos, duplicados_lote_casi = auditar_duplicados_lote(conexion)

            # Calidad mínima de preguntas actualmente seleccionables.
            # REVISION es cuarentena válida y se excluye de estos errores.
            preguntas_incluidas = conexion.execute(
                """
                SELECT DISTINCT
                    lp.id, lp.respuesta_correcta,
                    lp.opcion_a, lp.opcion_b, lp.opcion_c, lp.opcion_d
                FROM banco_preguntas bp
                JOIN lote_preguntas lp ON lp.id = bp.pregunta_id
                WHERE UPPER(TRIM(COALESCE(bp.estado, ''))) = 'INCLUIDA'
                """
            ).fetchall()

            respuestas_invalidas_incluidas = 0
            correcta_duplicada_incluida = 0
            distractores_duplicados_incluidos = 0

            for fila in preguntas_incluidas:
                respuesta = str(fila[1] or '').strip().upper()
                opciones = {
                    'A': normalizar_texto_duplicados(fila[2]),
                    'B': normalizar_texto_duplicados(fila[3]),
                    'C': normalizar_texto_duplicados(fila[4]),
                    'D': normalizar_texto_duplicados(fila[5]),
                }

                if respuesta not in {'A', 'B', 'C', 'D'}:
                    respuestas_invalidas_incluidas += 1
                    continue

                texto_correcta = opciones[respuesta]
                if any(
                    letra != respuesta and texto == texto_correcta
                    for letra, texto in opciones.items()
                ):
                    correcta_duplicada_incluida += 1
                    continue

                valores_distractores = [
                    texto for letra, texto in opciones.items() if letra != respuesta
                ]
                if len(set(valores_distractores)) < len(valores_distractores):
                    distractores_duplicados_incluidos += 1

            revisiones_banco = conexion.execute(
                "SELECT COUNT(*) FROM banco_preguntas WHERE UPPER(TRIM(COALESCE(estado, '')))='REVISION'"
            ).fetchone()[0]
            exclusiones_total = conexion.execute(
                """
                SELECT COUNT(*)
                FROM preguntas_exclusiones
                WHERE estado IN ('CUARENTENA', 'RETIRADA')
                """
            ).fetchone()[0]
            exclusiones_incluidas = conexion.execute(
                """
                SELECT COUNT(*)
                FROM banco_preguntas AS bp
                JOIN preguntas_exclusiones AS pe
                  ON pe.pregunta_id = bp.pregunta_id
                WHERE pe.estado IN ('CUARENTENA', 'RETIRADA')
                  AND UPPER(TRIM(COALESCE(bp.estado, '')))='INCLUIDA'
                """
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
                  AND UPPER(TRIM(COALESCE(bp.estado, '')))='INCLUIDA'
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
    datos["duplicados_lote_exactos"] = int(duplicados_lote_exactos)
    datos["duplicados_lote_casi"] = int(duplicados_lote_casi)
    datos["partes_nulas"] = int(partes_nulas)
    datos["respuestas_invalidas_incluidas"] = int(respuestas_invalidas_incluidas)
    datos["correcta_duplicada_incluida"] = int(correcta_duplicada_incluida)
    datos["distractores_duplicados_incluidos"] = int(distractores_duplicados_incluidos)
    datos["revisiones_banco"] = int(revisiones_banco)
    datos["exclusiones_total"] = int(exclusiones_total)
    datos["exclusiones_incluidas"] = int(exclusiones_incluidas)
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
        and int(duplicados_lote_exactos) == 0
        and int(duplicados_lote_casi) == 0
        and int(partes_nulas) == 0
        and int(respuestas_invalidas_incluidas) == 0
        and int(correcta_duplicada_incluida) == 0
        and int(exclusiones_incluidas) == 0
        and int(ia[4] or 0) == 0
        and int(prohibidas_banco[0] or 0) == 0
        and int(prohibidas_banco[1] or 0) == 0
    )
    return ok, datos


def obtener_convocatorias() -> list[tuple[int, str]]:
    conexion = sqlite3.connect(f"file:{RUTA_DB.as_posix()}?mode=ro", uri=True)
    try:
        columnas = {
            str(r[1])
            for r in conexion.execute("PRAGMA table_info(convocatorias)").fetchall()
        }
        if "activa" in columnas:
            filas = conexion.execute(
                "SELECT id, codigo FROM convocatorias WHERE activa = 1 ORDER BY id"
            ).fetchall()
        else:
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
    print(f"Duplicados exactos en lote............ {datos['duplicados_lote_exactos']}")
    print(f"Casi duplicados en lote............... {datos['duplicados_lote_casi']}")
    print(f"Partes de convocatoria NULAS......... {datos['partes_nulas']}")
    print("\nCalidad mínima de preguntas INCLUIDAS:")
    print(f"  Respuesta correcta inválida......... {datos['respuestas_invalidas_incluidas']}")
    print(f"  Opción correcta duplicada............ {datos['correcta_duplicada_incluida']}")
    print(f"  Distractores duplicados (AVISO)...... {datos['distractores_duplicados_incluidos']}")
    print(f"  Vinculaciones en REVISION............ {datos['revisiones_banco']}")
    print(f"  Exclusiones globales................. {datos['exclusiones_total']}")
    print(f"  Exclusiones todavía INCLUIDA......... {datos['exclusiones_incluidas']}")
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
