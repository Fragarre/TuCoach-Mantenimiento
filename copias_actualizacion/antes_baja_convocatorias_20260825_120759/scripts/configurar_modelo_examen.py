"""
OpoCoach-Mantenimiento - Configuración del modelo normativo de simulacro.

OBJETIVO
--------
Mantener una única definición, por convocatoria y parte jurídica teórica, de:
- bloques asignados a una NORMA concreta;
- bloques LIBRE;
- cantidad de preguntas de cada bloque;
- orden de los bloques dentro de la parte.

NO INTERVIENE EN:
- lote_preguntas;
- construcción de bancos;
- selección de preguntas prácticas;
- selección de preguntas no jurídicas;
- tests.

REGLAS
------
1. convocatoria_partes sigue siendo la fuente del total de preguntas de la parte.
2. Solo se configura un modelo para partes con contenido jurídico y no marcadas
   como PRACTICA por convocatoria_parte_reglas.
3. Una norma de un bloque NORMA debe aparecer en el temario de esa misma parte.
4. La suma de todos los bloques debe coincidir exactamente con
   convocatoria_partes.numero_preguntas.
5. Una misma norma puede aparecer en varios bloques, para reproducir su orden.
6. Los bloques LIBRE solo pueden usar, en el simulacro, normas de la misma parte
   que NO estén preasignadas en ningún bloque NORMA.
7. El límite acordado para LIBRE es de 2 preguntas por norma en el conjunto de
   todos los bloques LIBRE de una misma parte.

El script crea la tabla convocatoria_modelo_bloques solo con confirmación expresa
(o mediante --inicializar). Toda escritura crea antes una copia de seguridad.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
CARPETA_COPIAS = RAIZ / "db" / "copias_seguridad"
TABLA = "convocatoria_modelo_bloques"


@dataclass(frozen=True)
class Parte:
    id: int
    convocatoria_id: int
    nombre: str
    numero_preguntas: int
    orden: int


@dataclass(frozen=True)
class Bloque:
    orden: int
    tipo_bloque: str
    norma_id: int | None
    cantidad: int


def conectar(ruta_db: Path, solo_lectura: bool = False) -> sqlite3.Connection:
    if solo_lectura:
        con = sqlite3.connect(f"file:{ruta_db.as_posix()}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(ruta_db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def tabla_existe(con: sqlite3.Connection) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLA,),
    ).fetchone() is not None


def crear_backup(ruta_db: Path, motivo: str) -> Path:
    CARPETA_COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = CARPETA_COPIAS / f"{ruta_db.stem}_antes_{motivo}_{marca}{ruta_db.suffix}"
    shutil.copy2(ruta_db, destino)
    return destino


def crear_tabla(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLA} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            convocatoria_parte_id INTEGER NOT NULL,
            orden INTEGER NOT NULL CHECK (orden > 0),
            tipo_bloque TEXT NOT NULL
                CHECK (tipo_bloque IN ('NORMA', 'LIBRE')),
            norma_id INTEGER,
            cantidad INTEGER NOT NULL CHECK (cantidad > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (convocatoria_parte_id)
                REFERENCES convocatoria_partes(id)
                ON UPDATE CASCADE
                ON DELETE CASCADE,
            FOREIGN KEY (norma_id)
                REFERENCES normas(id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,

            UNIQUE (convocatoria_parte_id, orden),

            CHECK (
                (tipo_bloque = 'NORMA' AND norma_id IS NOT NULL)
                OR
                (tipo_bloque = 'LIBRE' AND norma_id IS NULL)
            )
        )
        """
    )
    con.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLA}_parte "
        f"ON {TABLA}(convocatoria_parte_id, orden)"
    )
    con.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLA}_norma "
        f"ON {TABLA}(norma_id)"
    )


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


def pedir_entero(mensaje: str, minimo: int = 1, maximo: int | None = None) -> int:
    while True:
        valor = input(mensaje).strip()
        if valor.isdigit():
            numero = int(valor)
            if numero >= minimo and (maximo is None or numero <= maximo):
                return numero
        if maximo is None:
            print(f"Debe ser un entero igual o mayor que {minimo}.")
        else:
            print(f"Debe ser un entero entre {minimo} y {maximo}.")


def obtener_convocatorias(con: sqlite3.Connection) -> list[sqlite3.Row]:
    columnas = {
        str(r["name"])
        for r in con.execute("PRAGMA table_info(convocatorias)")
    }
    if "activa" in columnas:
        return con.execute(
            "SELECT id, codigo, puesto, numero_preguntas "
            "FROM convocatorias WHERE activa = 1 ORDER BY id"
        ).fetchall()
    return con.execute(
        "SELECT id, codigo, puesto, numero_preguntas FROM convocatorias ORDER BY id"
    ).fetchall()


def resolver_convocatoria(
    con: sqlite3.Connection,
    convocatoria_id: int | None,
    codigo: str | None,
) -> sqlite3.Row | None:
    if convocatoria_id is not None:
        return con.execute(
            "SELECT id, codigo, puesto, numero_preguntas FROM convocatorias WHERE id=?",
            (convocatoria_id,),
        ).fetchone()
    if codigo:
        return con.execute(
            "SELECT id, codigo, puesto, numero_preguntas FROM convocatorias WHERE codigo=?",
            (codigo,),
        ).fetchone()
    return None


def seleccionar_convocatoria(con: sqlite3.Connection) -> sqlite3.Row | None:
    filas = obtener_convocatorias(con)
    if not filas:
        print("No existen convocatorias.")
        return None
    print("\nCONVOCATORIAS")
    print("-" * 78)
    for i, fila in enumerate(filas, 1):
        print(
            f"{i:>3}. {fila['codigo']:<20} | "
            f"{fila['puesto']:<35} | {fila['numero_preguntas']} preguntas"
        )
    print("  0. Cancelar")
    opcion = pedir_entero("Convocatoria: ", 0, len(filas))
    if opcion == 0:
        return None
    return filas[opcion - 1]


def obtener_partes(con: sqlite3.Connection, convocatoria_id: int) -> list[Parte]:
    filas = con.execute(
        """
        SELECT id, convocatoria_id, nombre, numero_preguntas, orden
        FROM convocatoria_partes
        WHERE convocatoria_id=?
        ORDER BY orden, id
        """,
        (convocatoria_id,),
    ).fetchall()
    return [
        Parte(
            id=int(f["id"]),
            convocatoria_id=int(f["convocatoria_id"]),
            nombre=str(f["nombre"]),
            numero_preguntas=int(f["numero_preguntas"]),
            orden=int(f["orden"]),
        )
        for f in filas
    ]


def obtener_reglas_parte(con: sqlite3.Connection, parte_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT id, prioridad, temario_parte, tipo_contenido,
               teorica_practica, tema_no_juridico
        FROM convocatoria_parte_reglas
        WHERE convocatoria_parte_id=?
        ORDER BY prioridad, id
        """,
        (parte_id,),
    ).fetchall()


def parte_marcada_practica(con: sqlite3.Connection, parte_id: int) -> bool:
    return con.execute(
        """
        SELECT 1
        FROM convocatoria_parte_reglas
        WHERE convocatoria_parte_id=?
          AND UPPER(TRIM(COALESCE(teorica_practica,'')))='PRACTICA'
        LIMIT 1
        """,
        (parte_id,),
    ).fetchone() is not None


def normas_juridicas_de_parte(
    con: sqlite3.Connection,
    parte: Parte,
) -> list[sqlite3.Row]:
    """
    Devuelve exclusivamente normas del temario que pueden justificar preguntas
    jurídicas en esta convocatoria_parte según convocatoria_parte_reglas.

    No usa nombres de parte como heurística: aplica las reglas almacenadas.
    """
    reglas = obtener_reglas_parte(con, parte.id)
    if not reglas:
        return []

    normas: dict[int, sqlite3.Row] = {}

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

        condiciones = [
            "t.convocatoria_id = ?",
            "UPPER(TRIM(COALESCE(tt.tipo_contenido,''))) = 'JURIDICO'",
            "tr.norma_id IS NOT NULL",
        ]
        params: list[object] = [parte.convocatoria_id]

        temario_parte = str(regla["temario_parte"] or "").strip()
        if temario_parte:
            condiciones.append("UPPER(TRIM(tt.parte)) = UPPER(TRIM(?))")
            params.append(temario_parte)

        tipo_contenido = str(regla["tipo_contenido"] or "").strip()
        if tipo_contenido:
            condiciones.append("UPPER(TRIM(tt.tipo_contenido)) = UPPER(TRIM(?))")
            params.append(tipo_contenido)

        consulta = f"""
            SELECT DISTINCT n.id, n.nombre_canonico
            FROM temarios t
            JOIN temario_temas tt ON tt.temario_id=t.id
            JOIN temario_referencias tr ON tr.tema_id=tt.id
            JOIN normas n ON n.id=tr.norma_id
            WHERE {' AND '.join(condiciones)}
            ORDER BY n.nombre_canonico, n.id
        """
        for fila in con.execute(consulta, params).fetchall():
            normas[int(fila["id"])] = fila

    return sorted(
        normas.values(),
        key=lambda f: (str(f["nombre_canonico"]).casefold(), int(f["id"])),
    )


def parte_admite_modelo(con: sqlite3.Connection, parte: Parte) -> tuple[bool, str]:
    if parte_marcada_practica(con, parte.id):
        return False, "parte jurídica PRACTICA: la selección será aleatoria"
    normas = normas_juridicas_de_parte(con, parte)
    if not normas:
        return False, "no contiene referencias jurídicas teóricas según las reglas actuales"
    return True, "parte jurídica teórica configurable"


def seleccionar_parte(
    con: sqlite3.Connection,
    convocatoria_id: int,
    solo_configurables: bool = False,
) -> Parte | None:
    partes = obtener_partes(con, convocatoria_id)
    if not partes:
        print("La convocatoria no tiene partes.")
        return None

    visibles: list[Parte] = []
    print("\nPARTES")
    print("-" * 78)
    for parte in partes:
        admite, motivo = parte_admite_modelo(con, parte)
        if solo_configurables and not admite:
            continue
        visibles.append(parte)
        estado = "CONFIGURABLE" if admite else "SIN MODELO"
        print(
            f"{len(visibles):>3}. {parte.nombre:<35} | "
            f"{parte.numero_preguntas:>3} preguntas | {estado} | {motivo}"
        )

    if not visibles:
        print("No hay partes configurables.")
        return None

    print("  0. Cancelar")
    opcion = pedir_entero("Parte: ", 0, len(visibles))
    if opcion == 0:
        return None
    return visibles[opcion - 1]


def obtener_bloques(con: sqlite3.Connection, parte_id: int) -> list[Bloque]:
    if not tabla_existe(con):
        return []
    filas = con.execute(
        f"""
        SELECT orden, tipo_bloque, norma_id, cantidad
        FROM {TABLA}
        WHERE convocatoria_parte_id=?
        ORDER BY orden, id
        """,
        (parte_id,),
    ).fetchall()
    return [
        Bloque(
            orden=int(f["orden"]),
            tipo_bloque=str(f["tipo_bloque"]),
            norma_id=int(f["norma_id"]) if f["norma_id"] is not None else None,
            cantidad=int(f["cantidad"]),
        )
        for f in filas
    ]


def validar_bloques_parte(
    con: sqlite3.Connection,
    parte: Parte,
    bloques: Iterable[Bloque] | None = None,
) -> tuple[list[str], list[str]]:
    errores: list[str] = []
    avisos: list[str] = []
    bloques_lista = list(bloques if bloques is not None else obtener_bloques(con, parte.id))

    if not bloques_lista:
        avisos.append("Modelo no configurado para esta parte.")
        return errores, avisos

    admite, motivo = parte_admite_modelo(con, parte)
    if not admite:
        errores.append(f"La parte no debe tener modelo de bloques: {motivo}.")
        return errores, avisos

    ordenes = [b.orden for b in bloques_lista]
    esperados = list(range(1, len(bloques_lista) + 1))
    if ordenes != esperados:
        errores.append(
            f"Los órdenes deben ser consecutivos 1..{len(bloques_lista)}; "
            f"se encontraron {ordenes}."
        )

    suma = sum(b.cantidad for b in bloques_lista)
    if suma != parte.numero_preguntas:
        errores.append(
            f"La suma de bloques es {suma}, pero la parte exige "
            f"{parte.numero_preguntas}."
        )

    normas_validas_filas = normas_juridicas_de_parte(con, parte)
    normas_validas = {int(f["id"]): str(f["nombre_canonico"]) for f in normas_validas_filas}
    normas_fijas: set[int] = set()
    total_libre = 0

    for bloque in bloques_lista:
        if bloque.cantidad <= 0:
            errores.append(f"Bloque {bloque.orden}: cantidad no positiva.")
        if bloque.tipo_bloque == "NORMA":
            if bloque.norma_id is None:
                errores.append(f"Bloque {bloque.orden}: NORMA sin norma_id.")
            elif bloque.norma_id not in normas_validas:
                errores.append(
                    f"Bloque {bloque.orden}: norma_id={bloque.norma_id} "
                    "no pertenece al temario jurídico de esta parte."
                )
            else:
                normas_fijas.add(bloque.norma_id)
        elif bloque.tipo_bloque == "LIBRE":
            if bloque.norma_id is not None:
                errores.append(f"Bloque {bloque.orden}: LIBRE no puede llevar norma_id.")
            total_libre += bloque.cantidad
        else:
            errores.append(
                f"Bloque {bloque.orden}: tipo desconocido {bloque.tipo_bloque!r}."
            )

    if total_libre:
        candidatas_libres = set(normas_validas) - normas_fijas
        capacidad = len(candidatas_libres) * 2
        if total_libre > capacidad:
            errores.append(
                f"Los bloques LIBRE suman {total_libre}, pero solo hay "
                f"{len(candidatas_libres)} normas no preasignadas en la parte. "
                f"Con máximo 2 por norma, la capacidad teórica es {capacidad}."
            )
        if not candidatas_libres:
            errores.append(
                "Hay bloques LIBRE, pero todas las normas de la parte están "
                "preasignadas en bloques NORMA."
            )

    return errores, avisos


def nombre_norma(con: sqlite3.Connection, norma_id: int | None) -> str:
    if norma_id is None:
        return "—"
    fila = con.execute(
        "SELECT nombre_canonico FROM normas WHERE id=?", (norma_id,)
    ).fetchone()
    return str(fila[0]) if fila else f"norma_id={norma_id}"


def mostrar_bloques(con: sqlite3.Connection, parte: Parte, bloques: list[Bloque]) -> None:
    print("\n" + "=" * 78)
    print(f"MODELO | {parte.nombre} | total parte: {parte.numero_preguntas}")
    print("=" * 78)
    if not bloques:
        print("Sin bloques configurados.")
        return

    posicion = 1
    for b in bloques:
        fin = posicion + b.cantidad - 1
        etiqueta = "LIBRE" if b.tipo_bloque == "LIBRE" else nombre_norma(con, b.norma_id)
        rango = str(posicion) if posicion == fin else f"{posicion}-{fin}"
        print(
            f"{b.orden:>3}. posiciones {rango:<9} | "
            f"{b.tipo_bloque:<5} | {etiqueta:<48} | {b.cantidad:>3}"
        )
        posicion = fin + 1

    print("-" * 78)
    print(f"Suma de bloques: {sum(b.cantidad for b in bloques)}")
    errores, avisos = validar_bloques_parte(con, parte, bloques)
    if errores:
        print("ESTADO: ERROR")
        for e in errores:
            print(f"  - {e}")
    elif avisos:
        print("ESTADO: AVISO")
        for a in avisos:
            print(f"  - {a}")
    else:
        print("ESTADO: CORRECTO")


def construir_bloques_interactivo(con: sqlite3.Connection, parte: Parte) -> list[Bloque] | None:
    normas = normas_juridicas_de_parte(con, parte)
    if not normas:
        print("No hay normas jurídicas disponibles para esta parte.")
        return None

    print("\nNORMAS DEL TEMARIO DISPONIBLES EN ESTA PARTE")
    print("-" * 78)
    for i, norma in enumerate(normas, 1):
        print(f"{i:>3}. [{norma['id']}] {norma['nombre_canonico']}")

    bloques: list[Bloque] = []
    acumulado = 0
    print("\nINTRODUCCIÓN DE BLOQUES")
    print("-" * 78)
    print("N = norma concreta | L = bloque libre | C = cancelar")
    print(
        "La suma debe alcanzar exactamente "
        f"{parte.numero_preguntas} preguntas."
    )

    while acumulado < parte.numero_preguntas:
        restante = parte.numero_preguntas - acumulado
        print(
            f"\nBloque {len(bloques)+1} | acumulado {acumulado} | "
            f"faltan {restante}"
        )
        while True:
            tipo = input("Tipo [N/L/C]: ").strip().upper()
            if tipo in {"N", "L", "C"}:
                break
            print("Opción no válida.")
        if tipo == "C":
            print("Configuración cancelada; no se ha escrito nada.")
            return None

        norma_id: int | None = None
        tipo_bloque = "LIBRE"
        if tipo == "N":
            tipo_bloque = "NORMA"
            indice = pedir_entero("Número de la norma de la lista: ", 1, len(normas))
            norma_id = int(normas[indice - 1]["id"])
            print(f"Norma: {normas[indice - 1]['nombre_canonico']}")

        cantidad = pedir_entero(
            f"Cantidad de preguntas del bloque [1-{restante}]: ",
            1,
            restante,
        )
        bloques.append(
            Bloque(
                orden=len(bloques) + 1,
                tipo_bloque=tipo_bloque,
                norma_id=norma_id,
                cantidad=cantidad,
            )
        )
        acumulado += cantidad

    mostrar_bloques(con, parte, bloques)
    errores, _ = validar_bloques_parte(con, parte, bloques)
    if errores:
        print("\nEl modelo NO puede guardarse mientras existan estos errores.")
        return None
    return bloques


def guardar_bloques(
    con: sqlite3.Connection,
    parte: Parte,
    bloques: list[Bloque],
) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            f"DELETE FROM {TABLA} WHERE convocatoria_parte_id=?",
            (parte.id,),
        )
        con.executemany(
            f"""
            INSERT INTO {TABLA} (
                convocatoria_parte_id, orden, tipo_bloque, norma_id, cantidad
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (parte.id, b.orden, b.tipo_bloque, b.norma_id, b.cantidad)
                for b in bloques
            ],
        )
        con.commit()
    except Exception:
        con.rollback()
        raise


def validar_convocatoria(
    con: sqlite3.Connection,
    convocatoria_id: int,
    mostrar: bool = True,
) -> tuple[int, int, int]:
    partes = obtener_partes(con, convocatoria_id)
    configuradas = 0
    errores_total = 0
    avisos_total = 0

    for parte in partes:
        bloques = obtener_bloques(con, parte.id)
        if not bloques:
            continue
        configuradas += 1
        errores, avisos = validar_bloques_parte(con, parte, bloques)
        errores_total += len(errores)
        avisos_total += len(avisos)
        if mostrar:
            mostrar_bloques(con, parte, bloques)

    return configuradas, errores_total, avisos_total


def validar_todos(ruta_db: Path) -> int:
    with conectar(ruta_db, solo_lectura=True) as con:
        if not tabla_existe(con):
            print("Modelo de examen: tabla todavía no inicializada.")
            return 0
        filas = obtener_convocatorias(con)
        total_errores = 0
        print("=" * 78)
        print("VALIDACIÓN DE MODELOS DE EXAMEN CONFIGURADOS")
        print("=" * 78)
        for conv in filas:
            configuradas, errores, avisos = validar_convocatoria(
                con, int(conv["id"]), mostrar=False
            )
            total_errores += errores
            print(
                f"{conv['codigo']:<25} partes configuradas={configuradas:<2} "
                f"errores={errores:<2} avisos={avisos:<2}"
            )
        print("=" * 78)
        print("RESULTADO:", "CORRECTO" if total_errores == 0 else "ERROR")
        return 0 if total_errores == 0 else 1


def menu_convocatoria(con: sqlite3.Connection, ruta_db: Path, conv: sqlite3.Row) -> None:
    while True:
        print("\n" + "=" * 78)
        print(f"MODELO DE EXAMEN | {conv['codigo']} | {conv['puesto']}")
        print("=" * 78)
        print("1. Ver modelo configurado")
        print("2. Configurar / reemplazar modelo de una parte jurídica teórica")
        print("3. Validar modelo configurado")
        print("4. Eliminar modelo de una parte")
        print("0. Volver")
        op = input("Opción: ").strip()

        if op == "0":
            return

        if op == "1":
            partes = obtener_partes(con, int(conv["id"]))
            alguno = False
            for parte in partes:
                bloques = obtener_bloques(con, parte.id)
                if bloques:
                    alguno = True
                    mostrar_bloques(con, parte, bloques)
            if not alguno:
                print("\nNo hay ningún modelo de bloques configurado para esta convocatoria.")
            continue

        if op == "2":
            parte = seleccionar_parte(con, int(conv["id"]), solo_configurables=True)
            if parte is None:
                continue
            actuales = obtener_bloques(con, parte.id)
            if actuales:
                print("\nMODELO ACTUAL")
                mostrar_bloques(con, parte, actuales)
                if not pedir_si_no("¿Reemplazar completamente este modelo?"):
                    continue
            nuevos = construir_bloques_interactivo(con, parte)
            if nuevos is None:
                continue
            if not pedir_si_no("¿Guardar exactamente este modelo?"):
                print("No se ha modificado la base.")
                continue
            copia = crear_backup(ruta_db, "modelo_examen")
            guardar_bloques(con, parte, nuevos)
            print(f"\nCopia de seguridad: {copia}")
            print("Modelo guardado correctamente.")
            continue

        if op == "3":
            configuradas, errores, avisos = validar_convocatoria(
                con, int(conv["id"]), mostrar=True
            )
            print("\nRESUMEN")
            print(f"Partes con modelo: {configuradas}")
            print(f"Errores:           {errores}")
            print(f"Avisos:            {avisos}")
            continue

        if op == "4":
            partes = [
                p
                for p in obtener_partes(con, int(conv["id"]))
                if obtener_bloques(con, p.id)
            ]
            if not partes:
                print("No existen modelos configurados para eliminar.")
                continue
            print("\nPARTES CON MODELO")
            for i, p in enumerate(partes, 1):
                print(f"{i:>3}. {p.nombre}")
            print("  0. Cancelar")
            idx = pedir_entero("Parte: ", 0, len(partes))
            if idx == 0:
                continue
            parte = partes[idx - 1]
            mostrar_bloques(con, parte, obtener_bloques(con, parte.id))
            if not pedir_si_no("¿Eliminar el modelo de esta parte?"):
                continue
            copia = crear_backup(ruta_db, "eliminar_modelo_examen")
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute(
                    f"DELETE FROM {TABLA} WHERE convocatoria_parte_id=?",
                    (parte.id,),
                )
                con.commit()
            except Exception:
                con.rollback()
                raise
            print(f"Copia de seguridad: {copia}")
            print("Modelo eliminado.")
            continue

        print("Opción no válida.")


def crear_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Configura el modelo normativo y orden de los simulacros."
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--convocatoria-id", type=int)
    g.add_argument("--codigo")
    p.add_argument("--inicializar", action="store_true")
    p.add_argument("--validar-todos", action="store_true")
    return p


def main() -> int:
    args = crear_parser().parse_args()
    ruta_db = Path(args.db).resolve()
    if not ruta_db.is_file():
        print(f"ERROR: no existe la base: {ruta_db}")
        return 1

    if args.validar_todos:
        return validar_todos(ruta_db)

    with conectar(ruta_db) as con:
        if not tabla_existe(con):
            if args.inicializar:
                confirmar = True
            else:
                print(
                    f"La tabla {TABLA} todavía no existe.\n"
                    "Es la nueva fuente única del patrón normativo/orden de las "
                    "partes jurídicas teóricas."
                )
                confirmar = pedir_si_no("¿Crear ahora esta estructura?")
            if not confirmar:
                print("No se ha modificado la base.")
                return 0
            copia = crear_backup(ruta_db, "crear_modelo_examen")
            try:
                crear_tabla(con)
                con.commit()
            except Exception:
                con.rollback()
                raise
            print(f"Copia de seguridad: {copia}")
            print(f"Tabla {TABLA} creada correctamente.")

        conv = resolver_convocatoria(con, args.convocatoria_id, args.codigo)
        if (args.convocatoria_id is not None or args.codigo) and conv is None:
            print("ERROR: convocatoria no encontrada.")
            return 1
        if conv is None:
            conv = seleccionar_convocatoria(con)
        if conv is None:
            return 0

        menu_convocatoria(con, ruta_db, conv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
