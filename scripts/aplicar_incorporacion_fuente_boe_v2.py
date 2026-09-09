"""
Constructor Temario V2 - aplicador atómico de una fuente BOE preparada.

Por defecto funciona en SIMULACIÓN: valida el JSON y el estado actual de la
base de datos, calcula exactamente qué haría y NO escribe nada.

Con --aplicar:
- abre una transacción IMMEDIATE;
- revalida norma, enlace y artículos dentro de la misma transacción;
- inserta sólo lo que falte;
- si detecta conflicto o divergencia, hace ROLLBACK completo;
- es idempotente: repetir una incorporación ya aplicada no duplica datos.

No modifica temarios, temario_referencias, lote_preguntas, bancos ni C1/C2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import comun


def limpiar(valor) -> str:
    return " ".join(str(valor or "").split()).strip()


def normalizar_articulo(valor) -> str:
    texto = limpiar(valor).replace(",", ".").lower()
    texto = re.sub(r"^(?:art[ií]culo|art\.?)\s*", "", texto, flags=re.I)
    return texto.strip(" .ºª")


def hash_texto(texto: str) -> str:
    return hashlib.sha256(limpiar(texto).encode("utf-8")).hexdigest()


def validar_preparacion(data: dict) -> None:
    if data.get("naturaleza") != "PREPARACION_INCORPORACION_FUENTE_BOE":
        raise RuntimeError("El JSON no es una preparación de fuente BOE V2.")
    if data.get("estado") != "LISTO_PARA_REVISION":
        raise RuntimeError(
            f"La preparación no está lista: estado={data.get('estado')!r}."
        )
    if data.get("errores"):
        raise RuntimeError("La preparación contiene errores y no puede aplicarse.")
    if not limpiar(data.get("id_boe")):
        raise RuntimeError("Falta id_boe en la preparación.")
    if not data.get("norma_id"):
        raise RuntimeError("Falta norma_id en la preparación.")

    articulos = data.get("articulos_preparados") or []
    if not articulos:
        raise RuntimeError("La preparación no contiene artículos.")

    vistos = set()
    for item in articulos:
        art = normalizar_articulo(item.get("articulo_boe"))
        if not art:
            raise RuntimeError("Existe un artículo preparado sin número válido.")
        if art in vistos:
            raise RuntimeError(f"Artículo duplicado en la preparación: {art}")
        vistos.add(art)
        texto = limpiar(item.get("texto"))
        if not texto:
            raise RuntimeError(f"Artículo {art}: texto vacío.")
        hash_guardado = limpiar(item.get("hash_texto"))
        hash_calculado = hash_texto(texto)
        if hash_guardado and hash_guardado != hash_calculado:
            raise RuntimeError(
                f"Artículo {art}: hash_texto no coincide con el contenido preparado."
            )


def comprobar_esquema(con: sqlite3.Connection) -> None:
    requeridas = {
        "normas": {"id", "nombre_canonico"},
        "norma_fuentes": {
            "norma_id", "id_fuente", "titulo_fuente", "departamento"
        },
        "articulos_fuente": {
            "id_boe", "id_bloque", "articulo_boe", "titulo_bloque",
            "departamento", "texto", "hash_texto"
        },
    }
    for tabla, columnas in requeridas.items():
        existe = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (tabla,),
        ).fetchone()
        if existe is None:
            raise RuntimeError(f"No existe la tabla requerida: {tabla}")
        reales = {
            str(f[1]) for f in con.execute(f"PRAGMA table_info({tabla})").fetchall()
        }
        faltan = columnas - reales
        if faltan:
            raise RuntimeError(
                f"{tabla}: faltan columnas requeridas: {sorted(faltan)}"
            )


def analizar_estado(con: sqlite3.Connection, prep: dict) -> dict:
    comprobar_esquema(con)

    id_boe = limpiar(prep["id_boe"]).upper()
    norma_id = int(prep["norma_id"])

    norma = con.execute(
        "SELECT id, nombre_canonico FROM normas WHERE id=?",
        (norma_id,),
    ).fetchone()
    if norma is None:
        raise RuntimeError(f"No existe norma canónica id={norma_id}.")

    enlaces = con.execute(
        """
        SELECT norma_id, id_fuente, titulo_fuente, departamento
        FROM norma_fuentes
        WHERE UPPER(id_fuente)=UPPER(?)
        ORDER BY norma_id
        """,
        (id_boe,),
    ).fetchall()

    ids_enlace = {int(r["norma_id"]) for r in enlaces}
    if ids_enlace and ids_enlace != {norma_id}:
        raise RuntimeError(
            f"Conflicto: {id_boe} ya está enlazado a norma(s) {sorted(ids_enlace)}, "
            f"no a {norma_id}."
        )

    insertar_enlace = len(enlaces) == 0

    existentes = con.execute(
        """
        SELECT id, id_boe, id_bloque, articulo_boe, titulo_bloque,
               departamento, texto, hash_texto
        FROM articulos_fuente
        WHERE UPPER(id_boe)=UPPER(?)
        ORDER BY id
        """,
        (id_boe,),
    ).fetchall()

    por_articulo: dict[str, list[sqlite3.Row]] = {}
    for fila in existentes:
        por_articulo.setdefault(
            normalizar_articulo(fila["articulo_boe"]), []
        ).append(fila)

    insertar_articulos = []
    ya_presentes = []
    conflictos = []

    for item in prep.get("articulos_preparados") or []:
        art = normalizar_articulo(item.get("articulo_boe"))
        texto_nuevo = limpiar(item.get("texto"))
        hash_nuevo = limpiar(item.get("hash_texto")) or hash_texto(texto_nuevo)
        filas = por_articulo.get(art, [])

        if not filas:
            insertar_articulos.append(item)
            continue

        compatibles = []
        incompatibles = []
        for fila in filas:
            texto_actual = limpiar(fila["texto"])
            hash_actual = limpiar(fila["hash_texto"]) or hash_texto(texto_actual)
            if hash_actual == hash_nuevo and texto_actual == texto_nuevo:
                compatibles.append(fila)
            else:
                incompatibles.append(fila)

        if compatibles and not incompatibles:
            ya_presentes.append({
                "articulo": art,
                "ids": [int(f["id"]) for f in compatibles],
            })
        else:
            conflictos.append({
                "articulo": art,
                "motivo": "EXISTENTE_DISTINTO_O_AMBIGUO",
                "ids_existentes": [int(f["id"]) for f in filas],
            })

    if conflictos:
        raise RuntimeError(
            "Hay artículos ya existentes con contenido distinto o ambiguo: "
            + ", ".join(x["articulo"] for x in conflictos)
        )

    return {
        "id_boe": id_boe,
        "norma_id": norma_id,
        "nombre_canonico": limpiar(norma["nombre_canonico"]),
        "insertar_enlace": insertar_enlace,
        "insertar_articulos": insertar_articulos,
        "articulos_ya_presentes": ya_presentes,
        "enlaces_existentes": [dict(r) for r in enlaces],
    }


def imprimir_plan(estado: dict, aplicar: bool) -> None:
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - APLICADOR FUENTE BOE")
    print("=" * 78)
    print(f"Modo.............................. {'APLICAR' if aplicar else 'SIMULACION'}")
    print(f"Fuente BOE....................... {estado['id_boe']}")
    print(
        f"Norma canónica................... {estado['norma_id']} | "
        f"{estado['nombre_canonico']}"
    )
    print(
        f"Crear enlace norma_fuentes....... "
        f"{'SI' if estado['insertar_enlace'] else 'NO'}"
    )
    print(f"Artículos nuevos................. {len(estado['insertar_articulos'])}")
    print(f"Artículos ya presentes........... {len(estado['articulos_ya_presentes'])}")
    for item in estado["insertar_articulos"]:
        print(
            f"  NUEVO | art. {normalizar_articulo(item.get('articulo_boe'))} "
            f"| bloque={limpiar(item.get('id_bloque'))}"
        )
    for item in estado["articulos_ya_presentes"]:
        print(f"  OK    | art. {item['articulo']} | ya presente")


def aplicar_operacion(con: sqlite3.Connection, prep: dict, estado: dict) -> dict:
    id_boe = estado["id_boe"]
    norma_id = estado["norma_id"]
    insertados_enlace = 0
    insertados_articulos = 0

    if estado["insertar_enlace"]:
        # Sólo usamos datos ya presentes en la preparación; no inventamos
        # departamento ni un título jurídico distinto del nombre preparado.
        con.execute(
            """
            INSERT INTO norma_fuentes (
                norma_id, id_fuente, titulo_fuente, departamento
            ) VALUES (?, ?, ?, ?)
            """,
            (
                norma_id,
                id_boe,
                limpiar(prep.get("nombre_esperado")),
                "",
            ),
        )
        insertados_enlace = 1

    for item in estado["insertar_articulos"]:
        con.execute(
            """
            INSERT INTO articulos_fuente (
                id_boe, id_bloque, articulo_boe, titulo_bloque,
                departamento, texto, hash_texto
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id_boe,
                limpiar(item.get("id_bloque")),
                limpiar(item.get("articulo_boe")),
                limpiar(item.get("titulo_bloque")),
                limpiar(item.get("departamento")),
                limpiar(item.get("texto")),
                limpiar(item.get("hash_texto"))
                or hash_texto(limpiar(item.get("texto"))),
            ),
        )
        insertados_articulos += 1

    return {
        "enlaces_insertados": insertados_enlace,
        "articulos_insertados": insertados_articulos,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Simula o aplica atómicamente una incorporación BOE preparada. "
            "Sin --aplicar no modifica la BD."
        )
    )
    ap.add_argument("--preparacion-json", required=True)
    ap.add_argument("--db")
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    prep_path = Path(args.preparacion_json).expanduser().resolve()
    prep = json.loads(prep_path.read_text(encoding="utf-8"))
    validar_preparacion(prep)

    db = Path(args.db or prep.get("db") or "").expanduser().resolve()
    if not db.is_file():
        raise SystemExit(f"No existe la base de datos: {db}")

    if not args.aplicar:
        with comun.conectar_sqlite_solo_lectura(db) as con:
            estado = analizar_estado(con, prep)
        imprimir_plan(estado, aplicar=False)
        print()
        print("SIMULACION OK.")
        print("La base de datos NO ha sido modificada.")
        return 0

    # Escritura explícita: toda la revalidación ocurre dentro de la misma
    # transacción para evitar aplicar un plan obsoleto.
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("BEGIN IMMEDIATE")
        estado = analizar_estado(con, prep)
        imprimir_plan(estado, aplicar=True)
        resultado = aplicar_operacion(con, prep, estado)

        # Revalidación posterior antes del COMMIT.
        final = analizar_estado(con, prep)
        if final["insertar_enlace"] or final["insertar_articulos"]:
            raise RuntimeError(
                "La verificación posterior indica que la incorporación no quedó completa."
            )

        con.commit()
        print()
        print("APLICACION COMPLETADA.")
        print(f"Enlaces insertados................ {resultado['enlaces_insertados']}")
        print(f"Artículos insertados.............. {resultado['articulos_insertados']}")
        return 0
    except Exception:
        con.rollback()
        print()
        print("ERROR: operación revertida completamente (ROLLBACK).")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
