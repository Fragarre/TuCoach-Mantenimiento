"""
OpoCoach-Mantenimiento
Ampliación controlada del corpus DOUE: TUE y TFUE.

Por defecto: SOLO PLAN.
Para aplicar:
    python scripts/ampliar_corpus_doue.py --aplicar

Garantías:
- reutiliza la lógica de auditar_ampliacion_corpus_doue.py;
- conserva las convenciones históricas de id_bloque:
      TUE  -> tue-art-N
      TFUE -> tfue-art-N
- sólo inserta artículos ausentes;
- no modifica filas preexistentes;
- copia de seguridad;
- una única transacción;
- snapshot de filas existentes;
- PRAGMA foreign_key_check;
- idempotencia posterior.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import auditar_ampliacion_corpus_doue as auditor


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
FUENTES = RAIZ / "fuentes_normativas"


@dataclass(frozen=True)
class Insercion:
    norma: str
    id_boe: str
    id_bloque: str
    articulo: str
    titulo: str
    departamento: str
    texto: str
    hash_texto: str


def limpiar(v) -> str:
    return " ".join(str(v or "").split()).strip()


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def validar_estructura(con: sqlite3.Connection) -> None:
    existe = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name='articulos_fuente'
        """
    ).fetchone()
    if existe is None:
        raise RuntimeError("No existe la tabla articulos_fuente.")

    columnas = {
        str(f[1])
        for f in con.execute("PRAGMA table_info(articulos_fuente)")
    }
    requeridas = {
        "id", "id_boe", "id_bloque", "articulo_boe",
        "titulo_bloque", "departamento", "texto", "hash_texto",
    }
    faltan = requeridas - columnas
    if faltan:
        raise RuntimeError(
            f"Faltan columnas en articulos_fuente: {sorted(faltan)}"
        )


def id_bloque(fuente: auditor.Fuente, numero: int) -> str:
    if fuente.id_fuente == "DOUE-C-2010-083-TUE":
        return f"tue-art-{numero}"
    if fuente.id_fuente == "DOUE-C-2010-083-TFUE":
        return f"tfue-art-{numero}"
    raise RuntimeError(
        f"Fuente DOUE no configurada: {fuente.id_fuente}"
    )


def evaluar_fuente(
    fuente: auditor.Fuente,
    con: sqlite3.Connection,
) -> dict:
    ruta = FUENTES / fuente.archivo
    if not ruta.exists():
        raise RuntimeError(f"Falta la fuente: {ruta}")

    texto = auditor.leer_pdf(ruta)
    aps = auditor.apariciones(texto)
    nums = auditor.inventario(aps)
    grupos = auditor.agrupar(aps, nums)
    ind, dup, conc = auditor.modo_indice(grupos, nums)
    seleccion, errores1 = auditor.seleccionar(
        grupos, nums, ind
    )
    bloques, errores2 = auditor.segmentar(
        texto, seleccion, nums
    )
    errores = errores1 + errores2

    if errores:
        raise RuntimeError(
            f"{fuente.norma}: extracción no válida: "
            + "; ".join(errores)
        )

    filas = con.execute(
        """
        SELECT id, id_boe, id_bloque, articulo_boe,
               titulo_bloque, departamento, texto, hash_texto
        FROM articulos_fuente
        WHERE id_boe=?
        ORDER BY id
        """,
        (fuente.id_fuente,),
    ).fetchall()

    bd_nums = {
        int(limpiar(x["articulo_boe"]))
        for x in filas
        if limpiar(x["articulo_boe"]).isdigit()
    }

    oficiales = set(nums)
    ausentes = oficiales - bd_nums
    extras = bd_nums - oficiales

    if extras:
        raise RuntimeError(
            f"{fuente.norma}: hay artículos guardados fuera del "
            f"inventario: {sorted(extras)}"
        )

    return {
        "fuente": fuente,
        "nums": nums,
        "bloques": bloques,
        "filas": filas,
        "ausentes": ausentes,
        "indice": ind,
        "duplicados": dup,
        "concordantes": conc,
    }


def construir_plan(
    con: sqlite3.Connection,
    ids_fuente: list[str] | None = None,
):
    inserciones: list[Insercion] = []
    resumen = []

    fuentes = list(auditor.FUENTES_DOUE)
    if ids_fuente:
        solicitados = {limpiar(x).upper() for x in ids_fuente}
        por_id = {limpiar(f.id_fuente).upper(): f for f in fuentes}
        desconocidos = sorted(solicitados - set(por_id))
        if desconocidos:
            raise RuntimeError(
                "Fuentes DOUE no configuradas: " + ", ".join(desconocidos)
            )
        fuentes = [f for f in fuentes if limpiar(f.id_fuente).upper() in solicitados]

    for fuente in fuentes:
        d = evaluar_fuente(fuente, con)

        for numero in sorted(d["ausentes"]):
            bloque = d["bloques"].get(numero)
            if bloque is None:
                raise RuntimeError(
                    f"{fuente.norma} art. {numero}: "
                    "no existe cuerpo validado."
                )

            texto = str(bloque["texto"]).strip()
            titulo = f"Artículo {numero}"

            inserciones.append(
                Insercion(
                    norma=fuente.norma,
                    id_boe=fuente.id_fuente,
                    id_bloque=id_bloque(fuente, numero),
                    articulo=str(numero),
                    titulo=titulo,
                    departamento="Unión Europea",
                    texto=texto,
                    hash_texto=sha(texto),
                )
            )

        resumen.append(
            (
                fuente.norma,
                fuente.id_fuente,
                len(d["nums"]),
                len(d["nums"]) - len(d["ausentes"]),
                len(d["ausentes"]),
            )
        )

    return inserciones, resumen


def snapshot(con: sqlite3.Connection):
    return {
        int(f["id"]): tuple(f)
        for f in con.execute(
            """
            SELECT id, id_boe, id_bloque, articulo_boe,
                   titulo_bloque, departamento, texto, hash_texto
            FROM articulos_fuente
            ORDER BY id
            """
        ).fetchall()
    }


def aplicar(
    ruta_db: Path,
    inserciones: list[Insercion],
) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    copia = ruta_db.with_name(
        f"{ruta_db.stem}_antes_ampliar_corpus_doue_"
        f"{marca}{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, copia)

    with sqlite3.connect(ruta_db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        validar_estructura(con)

        antes = snapshot(con)
        total_antes = len(antes)

        try:
            con.execute("BEGIN IMMEDIATE")

            insertados = 0

            for x in inserciones:
                por_bloque = con.execute(
                    """
                    SELECT id
                    FROM articulos_fuente
                    WHERE id_boe=? AND id_bloque=?
                    """,
                    (x.id_boe, x.id_bloque),
                ).fetchone()

                if por_bloque is not None:
                    raise RuntimeError(
                        f"Conflicto: ya existe "
                        f"{x.id_boe}/{x.id_bloque}."
                    )

                por_articulo = con.execute(
                    """
                    SELECT id, id_bloque
                    FROM articulos_fuente
                    WHERE id_boe=? AND articulo_boe=?
                    """,
                    (x.id_boe, x.articulo),
                ).fetchall()

                if por_articulo:
                    raise RuntimeError(
                        f"Conflicto: {x.id_boe} art. {x.articulo} "
                        "ya existe bajo otro id_bloque."
                    )

                con.execute(
                    """
                    INSERT INTO articulos_fuente (
                        id_boe,
                        id_bloque,
                        articulo_boe,
                        titulo_bloque,
                        departamento,
                        texto,
                        hash_texto
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        x.id_boe,
                        x.id_bloque,
                        x.articulo,
                        x.titulo,
                        x.departamento,
                        x.texto,
                        x.hash_texto,
                    ),
                )
                insertados += 1

            despues = snapshot(con)

            esperado = total_antes + len(inserciones)
            if len(despues) != esperado:
                raise RuntimeError(
                    f"Recuento inesperado: {len(despues)}; "
                    f"esperado {esperado}."
                )

            modificadas = sum(
                1
                for fila_id, valores in antes.items()
                if despues.get(fila_id) != valores
            )

            if modificadas:
                raise RuntimeError(
                    f"Se modificaron {modificadas} filas preexistentes."
                )

            fk = con.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

            if fk:
                raise RuntimeError(
                    f"foreign_key_check detectó "
                    f"{len(fk)} incidencia(s)."
                )

            con.commit()

        except Exception:
            con.rollback()
            raise

    print(f"Copia de seguridad: {copia}")
    print(f"Artículos insertados: {insertados}")
    print("Filas preexistentes modificadas: 0")
    print("PRAGMA foreign_key_check: OK")
    print("APLICACIÓN DOUE: OK")

    return copia


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=str(DB_DEFECTO),
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
    )
    parser.add_argument(
        "--id-fuente",
        action="append",
        help=(
            "Limita el plan a esta fuente DOUE. Puede repetirse. "
            "Sin esta opción conserva el comportamiento global."
        ),
    )
    args = parser.parse_args()

    ruta_db = Path(args.db).resolve()
    if not ruta_db.exists():
        raise FileNotFoundError(ruta_db)

    print("=" * 78)
    print("AMPLIAR CORPUS DOUE")
    print("=" * 78)
    print(f"Base de datos: {ruta_db}")
    print(
        "Modo: "
        + (
            "APLICACIÓN"
            if args.aplicar
            else "SOLO PLAN / SIN ESCRITURAS"
        )
    )
    print()

    with sqlite3.connect(ruta_db) as con:
        con.row_factory = sqlite3.Row
        if not args.aplicar:
            con.execute("PRAGMA query_only = ON")

        validar_estructura(con)
        inserciones, resumen = construir_plan(con, args.id_fuente)

    print("=" * 78)
    print("PLAN POR TRATADO")
    print("=" * 78)

    for norma, id_fuente, oficiales, cubiertos, ausentes in resumen:
        print(
            f"{norma} | fuente={id_fuente} | "
            f"oficiales={oficiales} | "
            f"cubiertos={cubiertos} | "
            f"insertar={ausentes}"
        )

    print()
    print("=" * 78)
    print("RESUMEN DE PLAN")
    print("=" * 78)
    print(f"Artículos a insertar: {len(inserciones)}")
    print("Filas a reparar:      0")
    print(f"Acciones totales:     {len(inserciones)}")
    print("PLAN DOUE: OK")

    if not args.aplicar:
        print()
        print("La base de datos no ha sido modificada.")
        print(
            "Para aplicar el plan validado, "
            "vuelva a ejecutar con --aplicar."
        )
        return 0

    print()
    print("=" * 78)
    print("APLICACIÓN")
    print("=" * 78)

    aplicar(ruta_db, inserciones)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"ERROR FATAL: "
            f"{exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        raise
