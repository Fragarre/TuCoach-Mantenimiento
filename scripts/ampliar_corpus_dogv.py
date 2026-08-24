"""
OpoCoach-Mantenimiento
Ampliación controlada del corpus DOGV/Generalitat.

Por defecto: SOLO PLAN.
Para aplicar:
    python scripts/ampliar_corpus_dogv.py --aplicar

Requisitos:
- auditar_ampliacion_corpus_dogv_v7.py en scripts/
- fuentes normativas ya preparadas en fuentes_normativas/

Garantías:
- reutiliza exactamente la lógica V7 ya validada;
- sólo amplía una fuente canónica por norma;
- no rellena identificadores duplicados no canónicos;
- repara únicamente filas cuya incompatibilidad ha sido demostrada por V7;
- copia de seguridad antes de escribir;
- una única transacción;
- INSERT puro para ausentes, UPDATE exacto para reparaciones;
- snapshot de todas las filas preexistentes;
- verificación posterior e idempotencia.
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

import auditar_ampliacion_corpus_dogv_v7 as v7


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


@dataclass(frozen=True)
class Reparacion:
    fila_id: int
    norma: str
    id_boe: str
    articulo: str
    titulo: str
    departamento: str
    texto: str
    hash_texto: str


def limpiar(valor: object | None) -> str:
    return " ".join(str(valor or "").split()).strip()


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def validar_estructura(con: sqlite3.Connection) -> None:
    existe = con.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='articulos_fuente'
        """
    ).fetchone()
    if existe is None:
        raise RuntimeError("No existe articulos_fuente.")

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


def id_bloque_para(fuente: v7.Fuente, numero: int) -> str:
    """
    Convenciones ya existentes del proyecto.

    pdf_normas.py:
        LOCAL-DOGV-* -> ART-N

    Importadores DOGV específicos:
        DOGV-2026-13821 -> decreto-76-2026-art-N
        DOGV-CONSOLIDADO-D-2019-077 -> decreto-77-2019-art-N
        DOGV-2025-3226 -> decreto-30-2025-art-N
    """
    if fuente.id_fuente.startswith("LOCAL-DOGV-"):
        return f"ART-{numero}"

    if fuente.id_fuente == "DOGV-2026-13821":
        return f"decreto-76-2026-art-{numero}"

    if fuente.id_fuente == "DOGV-CONSOLIDADO-D-2019-077":
        return f"decreto-77-2019-art-{numero}"

    if fuente.id_fuente == "DOGV-2025-3226":
        return f"decreto-30-2025-art-{numero}"

    raise RuntimeError(
        f"No hay convención id_bloque verificada para {fuente.id_fuente}."
    )


def departamento_para(fuente: v7.Fuente) -> str:
    return "Generalitat Valenciana"


def titulo_para(fuente: v7.Fuente, numero: int, bloque: dict) -> str:
    # LOCAL-DOGV se comporta como pdf_normas.py: encabezado completo.
    if fuente.id_fuente.startswith("LOCAL-DOGV-"):
        return limpiar(bloque["titulo"])

    # El importador histórico del D. 77/2019 guarda "Article N".
    if fuente.id_fuente == "DOGV-CONSOLIDADO-D-2019-077":
        return f"Article {numero}"

    # Otros importadores DOGV específicos usan "Artículo N".
    return f"Artículo {numero}"


def cargar_filas(con: sqlite3.Connection, id_boe: str):
    return con.execute(
        """
        SELECT id, id_boe, id_bloque, articulo_boe, titulo_bloque,
               departamento, texto, hash_texto
        FROM articulos_fuente
        WHERE id_boe=?
        ORDER BY id
        """,
        (id_boe,),
    ).fetchall()


def evaluar_fuente(
    fuente: v7.Fuente,
    con: sqlite3.Connection,
) -> dict:
    ruta = FUENTES / fuente.archivo
    if not ruta.exists():
        raise RuntimeError(f"Falta la fuente: {ruta}")

    resultado, intentos = v7.obtener_extraccion_valida(ruta)

    if resultado["errores"]:
        detalle = "; ".join(resultado["errores"])
        raise RuntimeError(
            f"{fuente.norma}/{fuente.id_fuente}: extracción no válida: "
            f"{detalle}"
        )

    filas = cargar_filas(con, fuente.id_fuente)
    sospechosas = v7.auditar_filas(
        fuente,
        filas,
        resultado["bloques"],
    )

    return {
        "fuente": fuente,
        "resultado": resultado,
        "filas": filas,
        "sospechosas": sospechosas,
        "intentos": intentos,
    }


def seleccionar_fuente_canonica(grupo: list[dict]) -> dict:
    completas = [
        d for d in grupo
        if not d["resultado"]["parcial"]
    ]

    preferidas = [
        d for d in completas
        if d["fuente"].canonica
    ]
    candidatas = preferidas or completas

    if not candidatas:
        # Sólo fuente parcial: se puede mantener lo cubierto por esa fuente,
        # pero nunca inferir artículos fuera de su inventario.
        preferidas = [
            d for d in grupo
            if d["fuente"].canonica
        ]
        candidatas = preferidas or grupo

    if len(candidatas) != 1:
        ids = ", ".join(d["fuente"].id_fuente for d in candidatas)
        raise RuntimeError(
            f"No hay fuente canónica inequívoca para "
            f"{grupo[0]['fuente'].norma}: {ids}"
        )

    return candidatas[0]


def construir_plan(
    con: sqlite3.Connection,
    ids_fuente: list[str] | None = None,
):
    fuentes = list(v7.FUENTES_PDF)
    if ids_fuente:
        solicitados = {limpiar(x).upper() for x in ids_fuente}
        por_id = {limpiar(f.id_fuente).upper(): f for f in fuentes}
        desconocidos = sorted(solicitados - set(por_id))
        if desconocidos:
            raise RuntimeError(
                "Fuentes DOGV no configuradas: " + ", ".join(desconocidos)
            )
        # La cobertura histórica de una misma norma puede estar repartida entre
        # varios IDs. Si se selecciona uno, se procesan todas las fuentes de esa
        # misma norma para conservar la lógica V7 existente.
        normas = {por_id[x].norma for x in solicitados}
        fuentes = [f for f in fuentes if f.norma in normas]

    evaluadas = [
        evaluar_fuente(fuente, con)
        for fuente in fuentes
    ]

    por_norma: dict[str, list[dict]] = {}
    for d in evaluadas:
        por_norma.setdefault(d["fuente"].norma, []).append(d)

    inserciones: list[Insercion] = []
    reparaciones: list[Reparacion] = []
    resumen_normas = []

    for norma in sorted(por_norma):
        grupo = por_norma[norma]
        canonica = seleccionar_fuente_canonica(grupo)
        fuente = canonica["fuente"]
        resultado = canonica["resultado"]
        bloques = resultado["bloques"]
        oficiales = set(resultado["nums"])

        # Cobertura REAL de la norma: puede estar repartida entre IDs
        # duplicados, como Decreto 30/2025.
        cubiertos = set()
        for d in grupo:
            for fila in d["filas"]:
                art = limpiar(fila["articulo_boe"])
                if art.isdigit():
                    cubiertos.add(int(art))

        ausentes = oficiales - cubiertos

        # Sólo las sospechosas de la fuente canónica se reparan.
        sospechosas = canonica["sospechosas"]

        for numero in sorted(ausentes):
            bloque = bloques.get(numero)
            if bloque is None:
                raise RuntimeError(
                    f"{norma} art. {numero}: falta cuerpo validado."
                )

            texto = str(bloque["texto"]).strip()
            titulo = titulo_para(fuente, numero, bloque)

            inserciones.append(
                Insercion(
                    norma=norma,
                    id_boe=fuente.id_fuente,
                    id_bloque=id_bloque_para(fuente, numero),
                    articulo=str(numero),
                    titulo=titulo,
                    departamento=departamento_para(fuente),
                    texto=texto,
                    hash_texto=sha(texto),
                )
            )

        for sospechosa in sospechosas:
            fila_id = int(sospechosa[0])
            numero = int(sospechosa[1])
            bloque = bloques.get(numero)
            if bloque is None:
                raise RuntimeError(
                    f"{norma} art. {numero}: reparación sin cuerpo validado."
                )

            texto = str(bloque["texto"]).strip()
            titulo = titulo_para(fuente, numero, bloque)

            reparaciones.append(
                Reparacion(
                    fila_id=fila_id,
                    norma=norma,
                    id_boe=fuente.id_fuente,
                    articulo=str(numero),
                    titulo=titulo,
                    departamento=departamento_para(fuente),
                    texto=texto,
                    hash_texto=sha(texto),
                )
            )

        resumen_normas.append(
            (
                norma,
                fuente.id_fuente,
                len(oficiales),
                len(oficiales & cubiertos),
                len(ausentes),
                len(sospechosas),
                resultado["parcial"],
            )
        )

    return inserciones, reparaciones, resumen_normas


def snapshot(con: sqlite3.Connection):
    return {
        int(f["id"]): tuple(f)
        for f in con.execute(
            """
            SELECT id, id_boe, id_bloque, articulo_boe, titulo_bloque,
                   departamento, texto, hash_texto
            FROM articulos_fuente
            ORDER BY id
            """
        ).fetchall()
    }


def verificar_no_modificadas(
    antes: dict[int, tuple],
    despues: dict[int, tuple],
    ids_reparados: set[int],
) -> int:
    modificadas = 0
    for fila_id, valores in antes.items():
        if fila_id in ids_reparados:
            continue
        if despues.get(fila_id) != valores:
            modificadas += 1
    return modificadas


def aplicar(
    ruta_db: Path,
    inserciones: list[Insercion],
    reparaciones: list[Reparacion],
) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    copia = ruta_db.with_name(
        f"{ruta_db.stem}_antes_ampliar_corpus_dogv_{marca}"
        f"{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, copia)

    with sqlite3.connect(ruta_db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        validar_estructura(con)

        antes = snapshot(con)
        total_antes = len(antes)
        ids_reparados = {r.fila_id for r in reparaciones}

        try:
            con.execute("BEGIN IMMEDIATE")

            insertados = 0
            for x in inserciones:
                existente = con.execute(
                    """
                    SELECT id
                    FROM articulos_fuente
                    WHERE id_boe=? AND id_bloque=?
                    """,
                    (x.id_boe, x.id_bloque),
                ).fetchone()

                if existente is not None:
                    raise RuntimeError(
                        f"Conflicto: ya existe {x.id_boe}/{x.id_bloque}."
                    )

                # También prohibimos duplicar el mismo artículo dentro de
                # la misma fuente bajo otro bloque.
                existente_art = con.execute(
                    """
                    SELECT id, id_bloque
                    FROM articulos_fuente
                    WHERE id_boe=? AND articulo_boe=?
                    """,
                    (x.id_boe, x.articulo),
                ).fetchall()

                if existente_art:
                    raise RuntimeError(
                        f"Conflicto: {x.id_boe} art. {x.articulo} "
                        "ya existe bajo otro id_bloque."
                    )

                con.execute(
                    """
                    INSERT INTO articulos_fuente (
                        id_boe, id_bloque, articulo_boe, titulo_bloque,
                        departamento, texto, hash_texto
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        x.id_boe, x.id_bloque, x.articulo, x.titulo,
                        x.departamento, x.texto, x.hash_texto,
                    ),
                )
                insertados += 1

            reparados = 0
            for r in reparaciones:
                actual = con.execute(
                    """
                    SELECT id_boe, articulo_boe
                    FROM articulos_fuente
                    WHERE id=?
                    """,
                    (r.fila_id,),
                ).fetchone()

                if actual is None:
                    raise RuntimeError(
                        f"No existe la fila a reparar: {r.fila_id}."
                    )

                if (
                    limpiar(actual["id_boe"]) != r.id_boe
                    or limpiar(actual["articulo_boe"]) != r.articulo
                ):
                    raise RuntimeError(
                        f"La fila {r.fila_id} cambió desde la auditoría."
                    )

                con.execute(
                    """
                    UPDATE articulos_fuente
                    SET titulo_bloque=?,
                        departamento=?,
                        texto=?,
                        hash_texto=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        r.titulo,
                        r.departamento,
                        r.texto,
                        r.hash_texto,
                        r.fila_id,
                    ),
                )
                reparados += 1

            despues = snapshot(con)

            esperado = total_antes + len(inserciones)
            if len(despues) != esperado:
                raise RuntimeError(
                    f"Recuento inesperado: {len(despues)}; "
                    f"esperado {esperado}."
                )

            no_previstas = verificar_no_modificadas(
                antes,
                despues,
                ids_reparados,
            )
            if no_previstas:
                raise RuntimeError(
                    f"Se modificaron {no_previstas} filas preexistentes "
                    "fuera del plan."
                )

            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(
                    f"foreign_key_check detectó {len(fk)} incidencia(s)."
                )

            con.commit()

        except Exception:
            con.rollback()
            raise

    print(f"Copia de seguridad: {copia}")
    print(f"Artículos insertados: {insertados}")
    print(f"Filas reparadas: {reparados}")
    print("Filas preexistentes modificadas fuera del plan: 0")
    print("PRAGMA foreign_key_check: OK")
    print("APLICACIÓN DOGV: OK")

    return copia


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument(
        "--id-fuente",
        action="append",
        help=(
            "Limita el plan a la norma correspondiente a esta fuente DOGV. "
            "Puede repetirse. Sin esta opción conserva el comportamiento global."
        ),
    )
    args = parser.parse_args()

    ruta_db = Path(args.db).resolve()
    if not ruta_db.exists():
        raise FileNotFoundError(ruta_db)

    print("=" * 78)
    print("AMPLIAR CORPUS DOGV")
    print("=" * 78)
    print(f"Base de datos: {ruta_db}")
    print(
        "Modo: "
        + ("APLICACIÓN" if args.aplicar else "SOLO PLAN / SIN ESCRITURAS")
    )
    print()

    with sqlite3.connect(ruta_db) as con:
        con.row_factory = sqlite3.Row
        if not args.aplicar:
            con.execute("PRAGMA query_only = ON")
        validar_estructura(con)
        inserciones, reparaciones, resumen = construir_plan(con, args.id_fuente)

    print("=" * 78)
    print("PLAN POR NORMA")
    print("=" * 78)

    for (
        norma, id_canonico, oficiales, cubiertos,
        ausentes, reparar, parcial
    ) in resumen:
        print(
            f"{norma} | fuente={id_canonico} | "
            f"oficiales={oficiales} | cubiertos={cubiertos} | "
            f"insertar={ausentes} | reparar={reparar} | "
            f"{'PARCIAL' if parcial else 'COMPLETA'}"
        )

    print()
    print("=" * 78)
    print("RESUMEN DE PLAN")
    print("=" * 78)
    print(f"Artículos a insertar: {len(inserciones)}")
    print(f"Filas a reparar:      {len(reparaciones)}")
    print(f"Acciones totales:     {len(inserciones) + len(reparaciones)}")

    # La presencia de 0 acciones es válida: demuestra idempotencia.
    print("PLAN DOGV: OK")

    if not args.aplicar:
        print()
        print("La base de datos no ha sido modificada.")
        print("Para aplicar el plan validado, use --aplicar.")
        return 0

    print()
    print("=" * 78)
    print("APLICACIÓN")
    print("=" * 78)

    aplicar(
        ruta_db,
        inserciones,
        reparaciones,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"ERROR FATAL: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        raise
