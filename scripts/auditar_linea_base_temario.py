"""
Auditor de línea base del Constructor de Temarios V2.

Objetivo:
    Inventariar, en SOLO LECTURA, el estado consolidado de una convocatoria
    existente antes de introducir cualquier inferencia automática de normas
    o artículos.

No modifica la base de datos. Sólo lee SQLite y genera un informe JSON de
trazabilidad en auditorias/constructor_temario_v2/.

Uso:
    python scripts/auditar_linea_base_temario.py --convocatoria-id 1
    python scripts/auditar_linea_base_temario.py --codigo C1-01_58_26
    python scripts/auditar_linea_base_temario.py --codigo C1-01_58_26 --db db/oposiciones.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import comun


RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
SALIDA_BASE = RAIZ / "auditorias" / "constructor_temario_v2"

TABLAS_REQUERIDAS = {
    "convocatorias",
    "temarios",
    "temario_temas",
    "temario_referencias",
    "normas",
    "norma_fuentes",
    "articulos_fuente",
    "lote_preguntas",
    "banco_preguntas",
    "documentos_corpus",
    "convocatoria_documentos_corpus",
}

COLUMNAS_REQUERIDAS: dict[str, set[str]] = {
    "convocatorias": {"id", "codigo"},
    "temarios": {"id", "convocatoria_id", "nombre", "fichero_origen"},
    "temario_temas": {"id", "temario_id", "parte", "numero_tema", "titulo", "tipo_contenido"},
    "temario_referencias": {
        "id",
        "tema_id",
        "nombre_norma_csv",
        "nombre_norma_normalizada",
        "articulo_solicitado",
        "estado",
        "articulo_fuente_id",
        "norma_id",
    },
    "normas": {"id", "nombre_canonico", "clave_normalizada"},
    "norma_fuentes": {"id", "norma_id", "id_fuente", "titulo_fuente"},
    "articulos_fuente": {"id", "articulo_boe", "texto"},
    "lote_preguntas": {
        "id",
        "norma_id_normalizada",
        "articulo_normalizado",
        "tipo_clasificacion",
        "estado_vigencia",
    },
    "banco_preguntas": {"id", "convocatoria_id", "pregunta_id", "estado"},
    "documentos_corpus": {"id", "ruta_relativa", "nombre"},
    "convocatoria_documentos_corpus": {"convocatoria_id", "documento_id"},
}


def tablas(con: sqlite3.Connection) -> set[str]:
    return {
        str(fila[0])
        for fila in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def columnas(con: sqlite3.Connection, tabla: str) -> set[str]:
    return {str(fila[1]) for fila in con.execute(f"PRAGMA table_info({tabla})").fetchall()}


def validar_esquema(con: sqlite3.Connection) -> list[str]:
    errores: list[str] = []
    existentes = tablas(con)

    faltan_tablas = sorted(TABLAS_REQUERIDAS - existentes)
    if faltan_tablas:
        errores.append("Faltan tablas requeridas: " + ", ".join(faltan_tablas))

    for tabla, requeridas in COLUMNAS_REQUERIDAS.items():
        if tabla not in existentes:
            continue
        reales = columnas(con, tabla)
        faltan = sorted(requeridas - reales)
        if faltan:
            errores.append(
                f"Tabla {tabla}: faltan columnas esperadas: {', '.join(faltan)}"
            )

    return errores


def resolver_convocatoria(
    con: sqlite3.Connection,
    convocatoria_id: int | None,
    codigo: str | None,
) -> sqlite3.Row:
    if convocatoria_id is not None:
        fila = con.execute(
            "SELECT * FROM convocatorias WHERE id=?",
            (convocatoria_id,),
        ).fetchone()
    else:
        fila = con.execute(
            "SELECT * FROM convocatorias WHERE codigo=?",
            (codigo,),
        ).fetchone()

    if fila is None:
        identificador = convocatoria_id if convocatoria_id is not None else codigo
        raise RuntimeError(f"No existe la convocatoria: {identificador}")
    return fila


def entero(valor: Any) -> int:
    return int(valor or 0)


def resumen_global(con: sqlite3.Connection, convocatoria_id: int) -> dict[str, Any]:
    temarios = entero(
        con.execute(
            "SELECT COUNT(*) FROM temarios WHERE convocatoria_id=?",
            (convocatoria_id,),
        ).fetchone()[0]
    )

    temas = entero(
        con.execute(
            """
            SELECT COUNT(*)
            FROM temario_temas tt
            JOIN temarios t ON t.id=tt.temario_id
            WHERE t.convocatoria_id=?
            """,
            (convocatoria_id,),
        ).fetchone()[0]
    )

    refs = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN tr.norma_id IS NOT NULL THEN 1 ELSE 0 END) AS con_norma,
            SUM(CASE WHEN tr.articulo_fuente_id IS NOT NULL THEN 1 ELSE 0 END) AS con_articulo_fuente,
            COUNT(DISTINCT tr.norma_id) AS normas_distintas,
            COUNT(DISTINCT CASE
                WHEN tr.norma_id IS NOT NULL AND TRIM(COALESCE(tr.articulo_solicitado,''))<>''
                THEN CAST(tr.norma_id AS TEXT) || '|' || TRIM(tr.articulo_solicitado)
            END) AS pares_norma_articulo,
            SUM(CASE WHEN UPPER(TRIM(COALESCE(tr.estado,'')))='COMPLETADO' THEN 1 ELSE 0 END) AS completadas,
            SUM(CASE WHEN UPPER(TRIM(COALESCE(tr.estado,'')))<>'COMPLETADO' THEN 1 ELSE 0 END) AS no_completadas
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        JOIN temarios t ON t.id=tt.temario_id
        WHERE t.convocatoria_id=?
        """,
        (convocatoria_id,),
    ).fetchone()

    lote = con.execute(
        """
        WITH pares AS (
            SELECT DISTINCT tr.norma_id, TRIM(tr.articulo_solicitado) AS articulo
            FROM temario_referencias tr
            JOIN temario_temas tt ON tt.id=tr.tema_id
            JOIN temarios t ON t.id=tt.temario_id
            WHERE t.convocatoria_id=?
              AND tr.norma_id IS NOT NULL
              AND TRIM(COALESCE(tr.articulo_solicitado,''))<>''
        )
        SELECT
            COUNT(DISTINCT lp.id) AS preguntas_disponibles,
            COUNT(DISTINCT CASE
                WHEN UPPER(TRIM(COALESCE(lp.estado_vigencia,''))) LIKE 'OBSOLETA%'
                THEN lp.id END
            ) AS obsoletas
        FROM lote_preguntas lp
        JOIN pares p
          ON p.norma_id=lp.norma_id_normalizada
         AND p.articulo=TRIM(COALESCE(lp.articulo_normalizado,''))
        """,
        (convocatoria_id,),
    ).fetchone()

    banco = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN UPPER(TRIM(COALESCE(estado,'')))='INCLUIDA' THEN 1 ELSE 0 END) AS incluidas,
            SUM(CASE WHEN UPPER(TRIM(COALESCE(estado,'')))='REVISION' THEN 1 ELSE 0 END) AS revision
        FROM banco_preguntas
        WHERE convocatoria_id=?
        """,
        (convocatoria_id,),
    ).fetchone()

    corpus = con.execute(
        """
        SELECT COUNT(*)
        FROM convocatoria_documentos_corpus
        WHERE convocatoria_id=?
        """,
        (convocatoria_id,),
    ).fetchone()[0]

    return {
        "temarios": temarios,
        "temas": temas,
        "referencias": {
            "total": entero(refs["total"]),
            "con_norma": entero(refs["con_norma"]),
            "con_articulo_fuente": entero(refs["con_articulo_fuente"]),
            "normas_distintas": entero(refs["normas_distintas"]),
            "pares_norma_articulo_distintos": entero(refs["pares_norma_articulo"]),
            "completadas": entero(refs["completadas"]),
            "no_completadas": entero(refs["no_completadas"]),
        },
        "lote": {
            "preguntas_disponibles_para_referencias": entero(lote["preguntas_disponibles"]),
            "obsoletas_entre_ellas": entero(lote["obsoletas"]),
        },
        "banco": {
            "total": entero(banco["total"]),
            "incluidas": entero(banco["incluidas"]),
            "revision": entero(banco["revision"]),
        },
        "documentos_corpus_asociados": entero(corpus),
    }


def temas_detalle(con: sqlite3.Connection, convocatoria_id: int) -> list[dict[str, Any]]:
    filas = con.execute(
        """
        SELECT
            tt.id AS tema_id,
            tt.parte,
            tt.numero_tema,
            tt.titulo,
            tt.tipo_contenido,
            COUNT(DISTINCT tr.id) AS referencias,
            COUNT(DISTINCT tr.norma_id) AS normas,
            COUNT(DISTINCT tr.articulo_fuente_id) AS articulos_fuente,
            COUNT(DISTINCT lp.id) AS preguntas_lote,
            COUNT(DISTINCT CASE
                WHEN UPPER(TRIM(COALESCE(bp.estado,'')))='INCLUIDA' THEN bp.pregunta_id
            END) AS preguntas_banco
        FROM temario_temas tt
        JOIN temarios t ON t.id=tt.temario_id
        LEFT JOIN temario_referencias tr ON tr.tema_id=tt.id
        LEFT JOIN lote_preguntas lp
          ON lp.norma_id_normalizada=tr.norma_id
         AND TRIM(COALESCE(lp.articulo_normalizado,''))=TRIM(COALESCE(tr.articulo_solicitado,''))
        LEFT JOIN banco_preguntas bp
          ON bp.convocatoria_id=t.convocatoria_id
         AND bp.pregunta_id=lp.id
        WHERE t.convocatoria_id=?
        GROUP BY tt.id, tt.parte, tt.numero_tema, tt.titulo, tt.tipo_contenido
        ORDER BY tt.parte, tt.numero_tema, tt.id
        """,
        (convocatoria_id,),
    ).fetchall()

    return [
        {
            "tema_id": entero(f["tema_id"]),
            "parte": f["parte"],
            "numero_tema": f["numero_tema"],
            "titulo": f["titulo"],
            "tipo_contenido": f["tipo_contenido"],
            "referencias": entero(f["referencias"]),
            "normas": entero(f["normas"]),
            "articulos_fuente": entero(f["articulos_fuente"]),
            "preguntas_lote": entero(f["preguntas_lote"]),
            "preguntas_banco": entero(f["preguntas_banco"]),
        }
        for f in filas
    ]


def normas_detalle(con: sqlite3.Connection, convocatoria_id: int) -> list[dict[str, Any]]:
    filas = con.execute(
        """
        SELECT
            n.id AS norma_id,
            n.nombre_canonico,
            n.clave_normalizada,
            COUNT(DISTINCT tr.id) AS referencias,
            COUNT(DISTINCT tr.tema_id) AS temas,
            COUNT(DISTINCT tr.articulo_fuente_id) AS articulos,
            COUNT(DISTINCT nf.id) AS fuentes_oficiales
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        JOIN temarios t ON t.id=tt.temario_id
        JOIN normas n ON n.id=tr.norma_id
        LEFT JOIN norma_fuentes nf ON nf.norma_id=n.id
        WHERE t.convocatoria_id=?
        GROUP BY n.id, n.nombre_canonico, n.clave_normalizada
        ORDER BY n.nombre_canonico, n.id
        """,
        (convocatoria_id,),
    ).fetchall()

    return [
        {
            "norma_id": entero(f["norma_id"]),
            "nombre_canonico": f["nombre_canonico"],
            "clave_normalizada": f["clave_normalizada"],
            "referencias": entero(f["referencias"]),
            "temas": entero(f["temas"]),
            "articulos": entero(f["articulos"]),
            "fuentes_oficiales": entero(f["fuentes_oficiales"]),
        }
        for f in filas
    ]


def documentos_corpus(con: sqlite3.Connection, convocatoria_id: int) -> list[dict[str, Any]]:
    filas = con.execute(
        """
        SELECT dc.id, dc.nombre, dc.ruta_relativa
        FROM convocatoria_documentos_corpus cdc
        JOIN documentos_corpus dc ON dc.id=cdc.documento_id
        WHERE cdc.convocatoria_id=?
        ORDER BY dc.nombre, dc.id
        """,
        (convocatoria_id,),
    ).fetchall()
    return [dict(f) for f in filas]


def imprimir_resumen(codigo: str, resumen: dict[str, Any]) -> None:
    print("=" * 78)
    print("AUDITORÍA LÍNEA BASE TEMARIO V2 - SOLO LECTURA")
    print("=" * 78)
    print(f"Convocatoria..................... {codigo}")
    print(f"Temarios......................... {resumen['temarios']}")
    print(f"Temas............................ {resumen['temas']}")
    print(f"Referencias...................... {resumen['referencias']['total']}")
    print(f"  Con norma canónica............. {resumen['referencias']['con_norma']}")
    print(f"  Con artículo fuente............ {resumen['referencias']['con_articulo_fuente']}")
    print(f"Normas distintas................. {resumen['referencias']['normas_distintas']}")
    print(f"Pares norma+artículo............. {resumen['referencias']['pares_norma_articulo_distintos']}")
    print(f"Referencias COMPLETADO........... {resumen['referencias']['completadas']}")
    print(f"Referencias no completadas....... {resumen['referencias']['no_completadas']}")
    print(f"Preguntas lote coincidentes...... {resumen['lote']['preguntas_disponibles_para_referencias']}")
    print(f"  Obsoletas...................... {resumen['lote']['obsoletas_entre_ellas']}")
    print(f"Banco total...................... {resumen['banco']['total']}")
    print(f"  INCLUIDA....................... {resumen['banco']['incluidas']}")
    print(f"  REVISION....................... {resumen['banco']['revision']}")
    print(f"Documentos corpus asociados...... {resumen['documentos_corpus_asociados']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditor de línea base del Constructor de Temarios V2 (solo lectura)."
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--convocatoria-id", type=int)
    grupo.add_argument("--codigo")
    parser.add_argument("--db", type=Path, default=DB_PREDETERMINADA)
    parser.add_argument(
        "--sin-json",
        action="store_true",
        help="No generar el informe JSON; sólo mostrar el resumen en consola.",
    )
    args = parser.parse_args()

    db = args.db
    if not db.is_absolute():
        db = (RAIZ / db).resolve()
    else:
        db = db.resolve()

    if not db.is_file():
        print(f"ERROR: no existe la base de datos: {db}")
        return 2

    con = comun.conectar_sqlite_solo_lectura(db)
    try:
        errores = validar_esquema(con)
        if errores:
            print("ERROR: el esquema de la BD no coincide con el contrato esperado.")
            for error in errores:
                print(f"- {error}")
            return 3

        convocatoria = resolver_convocatoria(
            con,
            args.convocatoria_id,
            args.codigo,
        )
        convocatoria_id = int(convocatoria["id"])
        codigo = str(convocatoria["codigo"])

        resumen = resumen_global(con, convocatoria_id)
        informe = {
            "tipo": "linea_base_temario_v2",
            "modo": "SOLO_LECTURA",
            "generado_en": datetime.now().isoformat(timespec="seconds"),
            "db": str(db),
            "convocatoria": {
                "id": convocatoria_id,
                "codigo": codigo,
            },
            "resumen": resumen,
            "temas": temas_detalle(con, convocatoria_id),
            "normas": normas_detalle(con, convocatoria_id),
            "documentos_corpus": documentos_corpus(con, convocatoria_id),
        }

        imprimir_resumen(codigo, resumen)

        if not args.sin_json:
            SALIDA_BASE.mkdir(parents=True, exist_ok=True)
            salida = SALIDA_BASE / f"linea_base_{codigo}.json"
            salida.write_text(
                json.dumps(informe, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"\nInforme JSON..................... {salida}")

        print("\nLa base de datos NO ha sido modificada.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
