"""
OpoCoach - Ampliación del corpus del Chat.
FASE 1: SOLO VALIDACIÓN (V4).

Principios:
- PRAGMA query_only = ON.
- No crea, inserta, actualiza ni elimina nada.
- Inventaría documentos BOE-A-* ya presentes en articulos_fuente.
- La existencia jurídica se compara por número de artículo, no por id_bloque.
- El id_bloque se conserva como identidad técnica de la fuente.
- Si el índice BOE ofrece varios bloques para un mismo artículo, se comprueba
  el contenido real de esos bloques y se aplica la misma regla temporal que
  boe_api.py: versión/bloque actual inequívoco por fecha_actualizacion.
- Admite encabezados "Artículo 32", "Art 32", "Artículo treinta y dos", etc.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from boe_api import (
    BOEError,
    DATOS_IDS_VERIFICADOS,
    _datos_bloque_indice,
    _seleccionar_version_actualizada,
    _texto_version,
    extraer_articulo_desde_html,
    texto_articulo_suficiente,
    encabezado_articulo,
    normalizar,
    normalizar_numero_articulo,
    obtener_bloque_texto,
    obtener_indice_texto,
)


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
DB_POR_DEFECTO = RAIZ_PROYECTO / "db" / "oposiciones.sqlite3"
PATRON_BOE = re.compile(r"^BOE-A-\d{4}-\d+$", re.I)


@dataclass(frozen=True)
class BloqueArticulo:
    id_bloque: str
    articulo: str
    titulo: str
    fecha_actualizacion: str
    origen_numero: str = "indice"


def limpiar(valor: object | None) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).split()).strip()


def normalizar_articulo(valor: object | None) -> str:
    return normalizar_numero_articulo(
        limpiar(valor).replace(",", ".")
    )


UNIDADES = {
    1: "uno", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco",
    6: "seis", 7: "siete", 8: "ocho", 9: "nueve",
}

VARIANTES_LETRAS: dict[str, str] = {}

def registrar(numero: int, *variantes: str) -> None:
    for variante in variantes:
        clave = normalizar(variante)
        anterior = VARIANTES_LETRAS.get(clave)
        if anterior is not None and anterior != str(numero):
            raise RuntimeError(
                f"Variante numérica ambigua: {variante!r}"
            )
        VARIANTES_LETRAS[clave] = str(numero)


registrar(1, "primero", "uno")
registrar(2, "segundo", "dos")
registrar(3, "tercero", "tres")
registrar(4, "cuarto", "cuatro")
registrar(5, "quinto", "cinco")
registrar(6, "sexto", "seis")
registrar(7, "septimo", "siete")
registrar(8, "octavo", "ocho")
registrar(9, "noveno", "nueve")
registrar(10, "diez", "decimo")
registrar(11, "once", "undecimo")
registrar(12, "doce", "duodecimo")

for numero, palabra in (
    (13, "trece"), (14, "catorce"), (15, "quince"),
    (16, "dieciseis"), (17, "diecisiete"), (18, "dieciocho"),
    (19, "diecinueve"), (20, "veinte"), (21, "veintiuno"),
    (22, "veintidos"), (23, "veintitres"), (24, "veinticuatro"),
    (25, "veinticinco"), (26, "veintiseis"), (27, "veintisiete"),
    (28, "veintiocho"), (29, "veintinueve"),
):
    registrar(numero, palabra)

for decena, palabra in (
    (30, "treinta"), (40, "cuarenta"), (50, "cincuenta"),
    (60, "sesenta"), (70, "setenta"), (80, "ochenta"),
    (90, "noventa"),
):
    registrar(decena, palabra)
    for unidad, palabra_unidad in UNIDADES.items():
        registrar(
            decena + unidad,
            f"{palabra} y {palabra_unidad}",
        )

registrar(100, "cien")


def extraer_numero_encabezado(texto: str) -> str:
    """
    Extrae SOLO un encabezado de artículo situado al inicio del texto.

    Acepta:
      Artículo 32
      Art. 32
      Art 32
      Artículo treinta y dos
      Art treinta y dos
    """
    texto_limpio = limpiar(texto)
    texto_n = normalizar(texto_limpio)

    m = re.match(
        r"^(?:articulo|art\.?)\s*"
        r"(\d+(?:\.\d+)*(?:\s+(?:bis|ter|quater|quinquies|"
        r"sexies|septies|octies|nonies|decies))?|unico)"
        r"(?=\.|\s|$)",
        texto_n,
        flags=re.I | re.U,
    )
    if m:
        return normalizar_articulo(m.group(1))

    m = re.match(
        r"^(?:articulo|art\.?)\s+(.+?)(?:\.(?:\s|$)|$)",
        texto_n,
        flags=re.I | re.U,
    )
    if not m:
        return ""

    candidato = normalizar(limpiar(m.group(1))).strip()
    return VARIANTES_LETRAS.get(candidato, "")


def validar_estructura_bd(conexion: sqlite3.Connection) -> None:
    existe = conexion.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='articulos_fuente'
        """
    ).fetchone()
    if existe is None:
        raise RuntimeError("No existe la tabla articulos_fuente.")

    columnas = {
        limpiar(f[1])
        for f in conexion.execute("PRAGMA table_info(articulos_fuente)")
    }
    requeridas = {
        "id", "id_boe", "id_bloque",
        "articulo_boe", "titulo_bloque", "texto",
    }
    faltan = requeridas - columnas
    if faltan:
        raise RuntimeError(
            f"Faltan columnas en articulos_fuente: {sorted(faltan)}"
        )


def cargar_documentos(
    conexion: sqlite3.Connection,
    id_boe: str | None,
    limite: int | None,
) -> list[sqlite3.Row]:
    condiciones = ["UPPER(id_boe) LIKE 'BOE-A-%'"]
    parametros: list[object] = []

    if id_boe:
        condiciones.append("UPPER(id_boe)=UPPER(?)")
        parametros.append(id_boe)

    filas = conexion.execute(
        f"""
        SELECT UPPER(id_boe) AS id_boe, COUNT(*) AS filas
        FROM articulos_fuente
        WHERE {' AND '.join(condiciones)}
        GROUP BY UPPER(id_boe)
        ORDER BY UPPER(id_boe)
        """,
        parametros,
    ).fetchall()

    return filas[:limite] if limite is not None else filas


def cargar_guardados(
    conexion: sqlite3.Connection,
    id_boe: str,
) -> list[sqlite3.Row]:
    return conexion.execute(
        """
        SELECT id, id_bloque, articulo_boe, titulo_bloque, texto
        FROM articulos_fuente
        WHERE UPPER(id_boe)=UPPER(?)
        ORDER BY id
        """,
        (id_boe,),
    ).fetchall()


def candidatos_indice(id_boe: str) -> list[BloqueArticulo]:
    raiz = obtener_indice_texto(id_boe)
    resultado: list[BloqueArticulo] = []

    for elemento in raiz.iter():
        datos = _datos_bloque_indice(elemento)
        if not datos:
            continue

        id_bloque, titulo, fecha = datos
        numero = extraer_numero_encabezado(titulo)
        if not numero:
            continue

        resultado.append(
            BloqueArticulo(
                id_bloque=limpiar(id_bloque),
                articulo=numero,
                titulo=limpiar(titulo),
                fecha_actualizacion=re.sub(r"\D", "", limpiar(fecha)),
            )
        )

    # id_bloque no puede representar dos cosas distintas.
    por_id: dict[str, BloqueArticulo] = {}
    for bloque in resultado:
        anterior = por_id.get(bloque.id_bloque)
        if anterior is not None and anterior != bloque:
            raise BOEError(
                f"{id_boe}: datos incompatibles para bloque {bloque.id_bloque}."
            )
        por_id[bloque.id_bloque] = bloque

    return list(por_id.values())


def numero_real_desde_bloque(
    id_boe: str,
    bloque: BloqueArticulo,
) -> str:
    """
    Verifica el número usando el CONTENIDO de la versión actual del bloque.

    Se usa en casos ambiguos porque el índice puede contener una rúbrica
    histórica errónea o dos bloques de épocas distintas.
    """
    raiz = obtener_bloque_texto(id_boe, bloque.id_bloque)
    version = _seleccionar_version_actualizada(
        raiz,
        bloque.fecha_actualizacion,
    )
    contenido = _texto_version(version)

    numero = extraer_numero_encabezado(contenido)
    if numero:
        return numero

    # Si el cuerpo XML no repite el encabezado, no inventamos.
    raise BOEError(
        f"{id_boe}/{bloque.id_bloque}: no se pudo confirmar el número "
        "de artículo desde el contenido actual del bloque."
    )


def corregir_ambiguedades(
    id_boe: str,
    candidatos: list[BloqueArticulo],
) -> tuple[list[BloqueArticulo], list[str]]:
    """
    Sólo consulta el contenido de bloques cuando el índice asigna el mismo
    número de artículo a más de un id_bloque.
    """
    grupos: dict[str, list[BloqueArticulo]] = {}
    for b in candidatos:
        grupos.setdefault(b.articulo, []).append(b)

    corregidos: list[BloqueArticulo] = []
    incidencias: list[str] = []

    for articulo, bloques in grupos.items():
        if len(bloques) == 1:
            corregidos.append(bloques[0])
            continue

        for bloque in bloques:
            try:
                real = numero_real_desde_bloque(id_boe, bloque)
            except BOEError as exc:
                incidencias.append(str(exc))
                corregidos.append(bloque)
                continue

            corregidos.append(
                BloqueArticulo(
                    id_bloque=bloque.id_bloque,
                    articulo=real,
                    titulo=bloque.titulo,
                    fecha_actualizacion=bloque.fecha_actualizacion,
                    origen_numero="contenido_bloque",
                )
            )

    return corregidos, incidencias


def seleccionar_bloques_actuales(
    candidatos: list[BloqueArticulo],
) -> tuple[dict[str, BloqueArticulo], list[str]]:
    """
    Para cada número de artículo conserva el bloque inequívocamente más reciente,
    igual que hace obtener_articulo() cuando hay varios bloques válidos.
    """
    grupos: dict[str, list[BloqueArticulo]] = {}
    for b in candidatos:
        grupos.setdefault(b.articulo, []).append(b)

    elegidos: dict[str, BloqueArticulo] = {}
    incidencias: list[str] = []

    for articulo, bloques in grupos.items():
        if len(bloques) == 1:
            elegidos[articulo] = bloques[0]
            continue

        fechas = [b.fecha_actualizacion for b in bloques if b.fecha_actualizacion]
        if not fechas:
            incidencias.append(
                f"art. {articulo}: varios bloques sin fecha_actualizacion: "
                + ", ".join(b.id_bloque for b in bloques)
            )
            continue

        fecha_max = max(fechas)
        recientes = [
            b for b in bloques
            if b.fecha_actualizacion == fecha_max
        ]

        if len(recientes) != 1:
            incidencias.append(
                f"art. {articulo}: varios bloques igualmente recientes: "
                + ", ".join(
                    f"{b.id_bloque}({b.fecha_actualizacion})"
                    for b in recientes
                )
            )
            continue

        elegidos[articulo] = recientes[0]

    return elegidos, incidencias



@dataclass(frozen=True)
class ArticuloPlan:
    id_boe: str
    id_bloque: str
    articulo_boe: str
    titulo_bloque: str
    departamento: str
    texto: str
    hash_texto: str


def analizar_documento(
    conexion: sqlite3.Connection,
    id_boe: str,
    mostrar_faltantes: bool,
) -> tuple[str, dict[str, int], dict[str, BloqueArticulo], list[str]]:
    guardados = cargar_guardados(conexion, id_boe)

    por_articulo_guardado: dict[str, list[sqlite3.Row]] = {}
    for fila in guardados:
        art = normalizar_articulo(fila["articulo_boe"])
        if art:
            por_articulo_guardado.setdefault(art, []).append(fila)

    candidatos = candidatos_indice(id_boe)
    candidatos, incidencias_contenido = corregir_ambiguedades(
        id_boe,
        candidatos,
    )
    actuales, incidencias_seleccion = seleccionar_bloques_actuales(candidatos)

    incidencias = incidencias_contenido + incidencias_seleccion

    articulos_actuales = set(actuales)
    articulos_guardados = set(por_articulo_guardado)

    faltantes = sorted(
        articulos_actuales - articulos_guardados,
        key=lambda x: (
            int(re.match(r"\d+", x).group()) if re.match(r"\d+", x) else 10**9,
            x,
        ),
    )
    guardados_sin_actual = sorted(
        articulos_guardados - articulos_actuales,
        key=lambda x: (
            int(re.match(r"\d+", x).group()) if re.match(r"\d+", x) else 10**9,
            x,
        ),
    )

    distinto_bloque = 0
    for articulo in articulos_actuales & articulos_guardados:
        actual = actuales[articulo]
        ids_guardados = {
            limpiar(f["id_bloque"])
            for f in por_articulo_guardado[articulo]
        }
        if actual.id_bloque not in ids_guardados:
            distinto_bloque += 1

    if incidencias:
        estado = "NO_AMPLIABLE"
    elif guardados_sin_actual:
        estado = "REVISAR"
    else:
        estado = "AMPLIABLE"

    print(f"  Estado: {estado}")
    print(f"  Artículos guardados en BD: {len(guardados)}")
    print(f"  Artículos actuales identificados: {len(actuales)}")
    print(f"  Artículos realmente ausentes: {len(faltantes)}")
    print(f"  Artículos cubiertos con otro id_bloque: {distinto_bloque}")
    print(f"  Guardados sin artículo actual equivalente: {len(guardados_sin_actual)}")
    print(f"  Incidencias de identidad no resueltas: {len(incidencias)}")

    if mostrar_faltantes and faltantes:
        print("  Faltantes reales:")
        for articulo in faltantes:
            b = actuales[articulo]
            print(
                f"    art. {articulo} | bloque {b.id_bloque} | {b.titulo}"
            )

    if guardados_sin_actual:
        print("  Guardados sin artículo actual equivalente:")
        for articulo in guardados_sin_actual:
            filas = por_articulo_guardado[articulo]
            detalle = ", ".join(
                f"BD {f['id']} / {f['id_bloque']}"
                for f in filas
            )
            print(f"    art. {articulo} | {detalle}")

    if incidencias:
        print("  Incidencias:")
        for incidencia in incidencias:
            print(f"    {incidencia}")

    faltantes_map = {articulo: actuales[articulo] for articulo in faltantes}

    return estado, {
        "guardados": len(guardados),
        "actuales": len(actuales),
        "faltantes": len(faltantes),
        "distinto_bloque": distinto_bloque,
        "sin_actual": len(guardados_sin_actual),
        "incidencias": len(incidencias),
    }, faltantes_map, incidencias


def departamento_documento(
    conexion: sqlite3.Connection,
    id_boe: str,
) -> str:
    filas = conexion.execute(
        """
        SELECT DISTINCT COALESCE(departamento, '') AS departamento
        FROM articulos_fuente
        WHERE UPPER(id_boe)=UPPER(?)
        """,
        (id_boe,),
    ).fetchall()

    departamentos = {limpiar(f["departamento"]) for f in filas}
    if len(departamentos) != 1:
        raise RuntimeError(
            f"{id_boe}: no existe un único departamento en articulos_fuente: "
            f"{sorted(departamentos)}"
        )
    return next(iter(departamentos))


def recuperar_articulo_plan(
    id_boe: str,
    departamento: str,
    bloque: BloqueArticulo,
) -> ArticuloPlan:
    """
    Recupera el texto vigente del bloque ya validado.

    Primero usa bloque+fecha del índice. Sólo si ese bloque no proporciona un
    cuerpo normativo suficiente se usa el respaldo HTML oficial que ya emplea
    boe_api.py. Nunca se acepta texto vacío o mera rúbrica.
    """
    id_bloque = bloque.id_bloque
    titulo = bloque.titulo
    texto = ""

    try:
        raiz = obtener_bloque_texto(id_boe, bloque.id_bloque)
        version = _seleccionar_version_actualizada(
            raiz,
            bloque.fecha_actualizacion,
        )
        texto = _texto_version(version)
    except BOEError:
        texto = ""

    if not texto_articulo_suficiente(texto, titulo):
        respaldo = extraer_articulo_desde_html(id_boe, bloque.articulo)
        if respaldo is None:
            raise BOEError(
                f"{id_boe} art. {bloque.articulo}: el bloque actual no aporta "
                "texto suficiente y el respaldo HTML no pudo recuperarlo."
            )
        id_html, titulo_html, texto_html = respaldo
        if not texto_articulo_suficiente(texto_html, titulo_html):
            raise BOEError(
                f"{id_boe} art. {bloque.articulo}: el respaldo HTML tampoco "
                "contiene cuerpo normativo suficiente."
            )
        id_bloque = limpiar(id_html)
        titulo = limpiar(titulo_html)
        texto = limpiar(texto_html)

    numero_titulo = extraer_numero_encabezado(titulo)
    if numero_titulo and numero_titulo != bloque.articulo:
        raise BOEError(
            f"{id_boe}: el título recuperado identifica art. {numero_titulo}, "
            f"pero se esperaba art. {bloque.articulo}."
        )

    texto = limpiar(texto)
    return ArticuloPlan(
        id_boe=id_boe,
        id_bloque=id_bloque,
        articulo_boe=bloque.articulo,
        titulo_bloque=titulo,
        departamento=departamento,
        texto=texto,
        hash_texto=hashlib.sha256(texto.encode("utf-8")).hexdigest(),
    )


def crear_copia_seguridad(ruta_db: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = ruta_db.with_name(
        f"{ruta_db.stem}_antes_ampliar_corpus_chat_{marca}{ruta_db.suffix}"
    )
    shutil.copy2(ruta_db, destino)
    return destino


def snapshot_existentes(conexion: sqlite3.Connection) -> dict[int, tuple]:
    filas = conexion.execute(
        """
        SELECT
            id, id_boe, id_bloque, articulo_boe, titulo_bloque,
            COALESCE(departamento,''), texto, hash_texto,
            created_at, updated_at
        FROM articulos_fuente
        ORDER BY id
        """
    ).fetchall()
    return {
        int(f[0]): tuple(f[1:])
        for f in filas
    }


def validar_plan_contra_bd(
    conexion: sqlite3.Connection,
    plan: list[ArticuloPlan],
) -> None:
    claves_plan: set[tuple[str, str]] = set()
    articulos_plan: set[tuple[str, str]] = set()

    for item in plan:
        clave = (item.id_boe.upper(), item.id_bloque)
        if clave in claves_plan:
            raise RuntimeError(
                f"Plan duplicado para {item.id_boe}/{item.id_bloque}."
            )
        claves_plan.add(clave)

        clave_articulo = (item.id_boe.upper(), normalizar_articulo(item.articulo_boe))
        if clave_articulo in articulos_plan:
            raise RuntimeError(
                f"Plan contiene dos inserciones para {item.id_boe} "
                f"art. {item.articulo_boe}."
            )
        articulos_plan.add(clave_articulo)

        existente_bloque = conexion.execute(
            """
            SELECT id, articulo_boe
            FROM articulos_fuente
            WHERE UPPER(id_boe)=UPPER(?) AND id_bloque=?
            """,
            (item.id_boe, item.id_bloque),
        ).fetchone()
        if existente_bloque is not None:
            raise RuntimeError(
                f"{item.id_boe}/{item.id_bloque} ya existe como "
                f"articulos_fuente.id={existente_bloque['id']}."
            )

        existentes_articulo = conexion.execute(
            """
            SELECT id, articulo_boe
            FROM articulos_fuente
            WHERE UPPER(id_boe)=UPPER(?)
            """,
            (item.id_boe,),
        ).fetchall()
        for fila in existentes_articulo:
            if normalizar_articulo(fila["articulo_boe"]) == normalizar_articulo(
                item.articulo_boe
            ):
                raise RuntimeError(
                    f"{item.id_boe} art. {item.articulo_boe} ya está cubierto "
                    f"por articulos_fuente.id={fila['id']}."
                )


def aplicar_plan(
    ruta_db: Path,
    plan: list[ArticuloPlan],
) -> tuple[Path, int]:
    """
    Inserta sólo filas nuevas. Nunca hace UPSERT ni UPDATE.

    Todas las inserciones se ejecutan en una única transacción y se verifican
    antes del COMMIT. Ante cualquier incidencia se hace rollback.
    """
    copia = crear_copia_seguridad(ruta_db)

    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")
        validar_estructura_bd(conexion)

        antes = snapshot_existentes(conexion)
        total_antes = len(antes)
        validar_plan_contra_bd(conexion, plan)

        try:
            conexion.execute("BEGIN IMMEDIATE")

            for item in plan:
                conexion.execute(
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
                        item.id_boe,
                        item.id_bloque,
                        item.articulo_boe,
                        item.titulo_bloque,
                        item.departamento,
                        item.texto,
                        item.hash_texto,
                    ),
                )

            despues_existentes = snapshot_existentes(conexion)
            for id_original, datos_originales in antes.items():
                datos_despues = despues_existentes.get(id_original)
                if datos_despues != datos_originales:
                    raise RuntimeError(
                        f"La fila preexistente articulos_fuente.id={id_original} "
                        "ha cambiado durante la ampliación."
                    )

            total_despues = conexion.execute(
                "SELECT COUNT(*) FROM articulos_fuente"
            ).fetchone()[0]
            esperado = total_antes + len(plan)
            if total_despues != esperado:
                raise RuntimeError(
                    f"Recuento final inesperado: {total_despues}; "
                    f"esperado {esperado}."
                )

            for item in plan:
                fila = conexion.execute(
                    """
                    SELECT articulo_boe, titulo_bloque, COALESCE(departamento,''),
                           texto, hash_texto
                    FROM articulos_fuente
                    WHERE UPPER(id_boe)=UPPER(?) AND id_bloque=?
                    """,
                    (item.id_boe, item.id_bloque),
                ).fetchone()
                if fila is None:
                    raise RuntimeError(
                        f"No se encuentra tras INSERT {item.id_boe}/{item.id_bloque}."
                    )

                esperado_fila = (
                    item.articulo_boe,
                    item.titulo_bloque,
                    item.departamento,
                    item.texto,
                    item.hash_texto,
                )
                if tuple(fila) != esperado_fila:
                    raise RuntimeError(
                        f"Verificación de contenido fallida para "
                        f"{item.id_boe}/{item.id_bloque}."
                    )

            fk = conexion.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(
                    f"PRAGMA foreign_key_check detectó {len(fk)} incidencias."
                )

            conexion.commit()
            return copia, len(plan)

        except Exception:
            conexion.rollback()
            raise


def ejecutar(args: argparse.Namespace) -> int:
    ruta_db = Path(args.db).resolve()
    if not ruta_db.exists():
        raise FileNotFoundError(f"No existe la base de datos: {ruta_db}")

    if args.id_boe and not PATRON_BOE.fullmatch(args.id_boe):
        raise ValueError("--id-boe debe tener formato BOE-A-AAAA-NNNNN.")

    print("=" * 78)
    print("AMPLIAR CORPUS CHAT - FASE 2 - PLANIFICACIÓN E INCORPORACIÓN")
    print("=" * 78)
    print(f"Base de datos: {ruta_db}")
    print("Ámbito: documentos BOE-A-* ya presentes en articulos_fuente")
    print(f"Modo: {'APLICAR' if args.aplicar else 'SOLO PLAN / SIN ESCRITURAS'}")
    print()

    resumen = {
        "documentos": 0,
        "ampliables": 0,
        "revisar": 0,
        "no_ampliables": 0,
        "sin_indice": 0,
        "errores": 0,
        "guardados": 0,
        "actuales": 0,
        "faltantes": 0,
        "distinto_bloque": 0,
        "sin_actual": 0,
        "incidencias": 0,
    }
    plan: list[ArticuloPlan] = []
    errores_plan: list[str] = []

    # Toda la fase de descubrimiento se hace sobre conexión de sólo lectura.
    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA query_only = ON")
        validar_estructura_bd(conexion)

        documentos = cargar_documentos(
            conexion,
            args.id_boe,
            args.limite_documentos,
        )
        resumen["documentos"] = len(documentos)

        for posicion, fila in enumerate(documentos, 1):
            id_boe = limpiar(fila["id_boe"])
            print("-" * 78)
            print(
                f"[{posicion}/{len(documentos)}] {id_boe} | "
                f"filas actuales: {fila['filas']}"
            )

            if id_boe in DATOS_IDS_VERIFICADOS:
                resumen["sin_indice"] += 1
                resumen["guardados"] += int(fila["filas"])
                print("  Estado: EXCLUIDO_SIN_INDICE_CONSOLIDADO")
                continue

            try:
                estado, datos, faltantes, incidencias = analizar_documento(
                    conexion,
                    id_boe,
                    args.mostrar_faltantes,
                )
            except BOEError as exc:
                resumen["errores"] += 1
                print(f"  ERROR BOE: {limpiar(exc)}")
                continue
            except Exception as exc:
                resumen["errores"] += 1
                print(
                    f"  ERROR: {exc.__class__.__name__}: {limpiar(exc)}"
                )
                continue

            if estado == "AMPLIABLE":
                resumen["ampliables"] += 1
            elif estado == "REVISAR":
                resumen["revisar"] += 1
            else:
                resumen["no_ampliables"] += 1

            for clave in (
                "guardados", "actuales", "faltantes",
                "distinto_bloque", "sin_actual", "incidencias",
            ):
                resumen[clave] += datos[clave]

            if estado != "AMPLIABLE":
                continue

            departamento = departamento_documento(conexion, id_boe)

            if faltantes:
                print(f"  Recuperando y validando {len(faltantes)} textos faltantes...")

            for articulo, bloque in faltantes.items():
                try:
                    item = recuperar_articulo_plan(
                        id_boe,
                        departamento,
                        bloque,
                    )
                    plan.append(item)
                except Exception as exc:
                    mensaje = (
                        f"{id_boe} art. {articulo}: "
                        f"{exc.__class__.__name__}: {limpiar(exc)}"
                    )
                    errores_plan.append(mensaje)
                    print(f"    ERROR PLAN: {mensaje}")

    print()
    print("=" * 78)
    print("RESUMEN DE PLAN")
    print("=" * 78)
    print(f"Documentos analizados:                  {resumen['documentos']}")
    print(f"AMPLIABLE:                              {resumen['ampliables']}")
    print(f"REVISAR:                                {resumen['revisar']}")
    print(f"NO_AMPLIABLE:                           {resumen['no_ampliables']}")
    print(f"EXCLUIDO_SIN_INDICE_CONSOLIDADO:        {resumen['sin_indice']}")
    print(f"Errores de consulta:                    {resumen['errores']}")
    print(f"Artículos realmente faltantes:          {resumen['faltantes']}")
    print(f"Textos recuperados y validados:         {len(plan)}")
    print(f"Errores al construir el plan:           {len(errores_plan)}")

    global_limpio = (
        resumen["revisar"] == 0
        and resumen["no_ampliables"] == 0
        and resumen["errores"] == 0
        and resumen["incidencias"] == 0
        and not errores_plan
        and len(plan) == resumen["faltantes"]
    )

    if not global_limpio:
        print()
        print("PLAN GLOBAL: REQUIERE REVISIÓN")
        print("No se realizará ninguna escritura.")
        return 1

    # Revalidación estructural final contra la BD real antes de cualquier write.
    with sqlite3.connect(ruta_db) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA query_only = ON")
        validar_plan_contra_bd(conexion, plan)

    print("PLAN GLOBAL: OK")

    if not args.aplicar:
        print()
        print("La base de datos no ha sido modificada.")
        print(
            "Para incorporar el plan validado, vuelva a ejecutar con --aplicar."
        )
        return 0

    if not plan:
        print()
        print("No hay artículos nuevos que incorporar.")
        print("APLICACIÓN: OK - 0 inserciones")
        return 0

    print()
    print("=" * 78)
    print("APLICACIÓN")
    print("=" * 78)

    copia, insertados = aplicar_plan(ruta_db, plan)

    print(f"Copia de seguridad: {copia}")
    print(f"Artículos insertados: {insertados}")
    print("Filas preexistentes modificadas: 0")
    print("PRAGMA foreign_key_check: OK")
    print("APLICACIÓN: OK")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Planifica e incorpora artículos BOE-A-* faltantes al corpus del "
            "Chat. Por defecto no escribe; --aplicar habilita la inserción."
        )
    )
    p.add_argument("--db", default=str(DB_POR_DEFECTO))
    p.add_argument("--id-boe")
    p.add_argument("--limite-documentos", type=int)
    p.add_argument("--mostrar-faltantes", action="store_true")
    p.add_argument(
        "--aplicar",
        action="store_true",
        help=(
            "Inserta el plan sólo si la validación global y todos los textos "
            "son correctos. Sin esta opción no se escribe nada."
        ),
    )
    return p


def main() -> None:
    parser = construir_parser()
    args = parser.parse_args()

    if args.limite_documentos is not None and args.limite_documentos <= 0:
        parser.error("--limite-documentos debe ser mayor que cero.")

    try:
        codigo = ejecutar(args)
    except Exception as exc:
        print(
            f"ERROR FATAL: {exc.__class__.__name__}: {limpiar(exc)}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    raise SystemExit(codigo)


if __name__ == "__main__":
    main()
