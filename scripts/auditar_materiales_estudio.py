from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
CATALOGO_DEFECTO = RAIZ / "materiales_estudio" / "catalogo_materiales.json"
RESUMENES_DEFECTO = RAIZ / "materiales_estudio" / "resumenes"


def limpiar(valor: object | None) -> str:
    return " ".join(str(valor or "").split()).strip()


def clave_articulo(valor: object | None) -> tuple:
    s = limpiar(valor).replace(",", ".").lower()
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\s+(.*))?$", s)
    if m:
        return (0, int(m.group(1)), int(m.group(2) or 0), m.group(3) or "")
    return (1, 10**9, 0, s)


def validar_estructura(con: sqlite3.Connection) -> None:
    necesarias = {
        "convocatorias", "temarios", "temario_temas",
        "temario_referencias", "normas", "articulos_fuente",
    }
    existentes = {
        str(x[0])
        for x in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    faltan = sorted(necesarias - existentes)
    if faltan:
        raise RuntimeError(
            "Faltan tablas necesarias: " + ", ".join(faltan)
        )


def normas_activas(con: sqlite3.Connection) -> list[dict]:
    filas = con.execute(
        """
        SELECT
            n.id AS norma_id,
            n.nombre_canonico,
            GROUP_CONCAT(DISTINCT c.codigo) AS convocatorias
        FROM convocatorias c
        JOIN temarios t ON t.convocatoria_id = c.id
        JOIN temario_temas tt ON tt.temario_id = t.id
        JOIN temario_referencias tr ON tr.tema_id = tt.id
        JOIN normas n ON n.id = tr.norma_id
        WHERE c.activa = 1
          AND tr.norma_id IS NOT NULL
        GROUP BY n.id, n.nombre_canonico
        ORDER BY UPPER(n.nombre_canonico), n.id
        """
    ).fetchall()
    return [dict(x) for x in filas]


def fuentes_norma_activa(
    con: sqlite3.Connection,
    norma_id: int,
) -> list[str]:
    filas = con.execute(
        """
        SELECT DISTINCT af.id_boe
        FROM convocatorias c
        JOIN temarios t ON t.convocatoria_id = c.id
        JOIN temario_temas tt ON tt.temario_id = t.id
        JOIN temario_referencias tr ON tr.tema_id = tt.id
        JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
        WHERE c.activa = 1
          AND tr.norma_id = ?
          AND tr.estado = 'COMPLETADO'
          AND tr.articulo_fuente_id IS NOT NULL
          AND TRIM(COALESCE(af.id_boe, '')) <> ''
        ORDER BY af.id_boe
        """,
        (norma_id,),
    ).fetchall()
    return [limpiar(x[0]) for x in filas if limpiar(x[0])]


def contenido_fuente(
    con: sqlite3.Connection,
    id_fuente: str,
) -> list[tuple[str, str, str, str]]:
    filas = con.execute(
        """
        SELECT id_bloque, articulo_boe, titulo_bloque, texto
        FROM articulos_fuente
        WHERE UPPER(id_boe) = UPPER(?)
          AND TRIM(COALESCE(texto, '')) <> ''
        ORDER BY id
        """,
        (id_fuente,),
    ).fetchall()

    contenido = [
        (
            limpiar(f["articulo_boe"]).replace(",", ".").lower(),
            limpiar(f["id_bloque"]),
            limpiar(f["titulo_bloque"]),
            limpiar(f["texto"]),
        )
        for f in filas
    ]

    contenido.sort(
        key=lambda x: (
            clave_articulo(x[0]),
            x[1].casefold(),
            x[2].casefold(),
            x[3],
        )
    )
    return contenido


def hash_contenido(
    contenido: list[tuple[str, str, str, str]],
) -> str:
    h = hashlib.sha256()
    for articulo, id_bloque, titulo, texto in contenido:
        h.update(
            (
                f"{articulo}\x1f{id_bloque}\x1f"
                f"{titulo}\x1f{texto}\n"
            ).encode("utf-8")
        )
    return h.hexdigest()


def seleccionar_fuente_canonica(
    con: sqlite3.Connection,
    norma_id: int,
) -> tuple[str, list[tuple[str, str, str, str]]]:
    """
    Este auditor NO decide si la norma está jurídicamente completa.
    Esa función sigue correspondiendo a los validadores RAG actuales.

    Entre los IDs físicos ya enlazados a la norma selecciona la fuente con
    mayor cobertura almacenada. Si varias empatan y su contenido difiere,
    se detiene: no elige silenciosamente.
    """
    fuentes = fuentes_norma_activa(con, norma_id)
    if not fuentes:
        raise RuntimeError(
            "No hay fuente enlazada desde referencias COMPLETADAS."
        )

    candidatas = [
        (fuente, contenido_fuente(con, fuente))
        for fuente in fuentes
    ]

    max_cobertura = max(len(c) for _, c in candidatas)
    maximas = [
        (fuente, contenido)
        for fuente, contenido in candidatas
        if len(contenido) == max_cobertura
    ]

    if len(maximas) == 1:
        return maximas[0]

    hashes = {hash_contenido(c) for _, c in maximas}
    if len(hashes) != 1:
        detalle = ", ".join(
            f"{f}({len(c)})" for f, c in maximas
        )
        raise RuntimeError(
            "Varias fuentes con igual cobertura y contenido diferente: "
            + detalle
        )

    return sorted(maximas, key=lambda x: x[0].upper())[0]


def hash_corpus_norma(
    con: sqlite3.Connection,
    norma_id: int,
) -> tuple[str, str, int]:
    fuente, contenido = seleccionar_fuente_canonica(con, norma_id)
    if not contenido:
        raise RuntimeError("La fuente seleccionada no contiene texto.")
    return hash_contenido(contenido), fuente, len(contenido)


def cargar_catalogo(ruta: Path) -> dict[int, dict]:
    if not ruta.is_file():
        return {}
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, list):
        raise RuntimeError("El catálogo debe contener una lista JSON.")
    salida = {}
    for item in datos:
        norma_id = int(item["norma_id"])
        if norma_id in salida:
            raise RuntimeError(
                f"norma_id duplicado en catálogo: {norma_id}"
            )
        salida[norma_id] = dict(item)
    return salida


def auditar(
    db: Path,
    catalogo_path: Path,
    resumenes_dir: Path,
) -> tuple[list[dict], dict[str, int]]:
    catalogo = cargar_catalogo(catalogo_path)

    with sqlite3.connect(
        f"file:{db.as_posix()}?mode=ro",
        uri=True,
    ) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        validar_estructura(con)

        activas = normas_activas(con)
        activas_ids = {int(x["norma_id"]) for x in activas}
        resultado = []

        for norma in activas:
            norma_id = int(norma["norma_id"])
            item = catalogo.get(norma_id)

            try:
                hash_actual, fuente, articulos = hash_corpus_norma(
                    con, norma_id
                )
                error_corpus = ""
            except Exception as exc:
                hash_actual = ""
                fuente = ""
                articulos = 0
                error_corpus = str(exc)

            if item is None:
                estado = "NUEVA"
                archivo = ""
                hash_guardado = ""
            else:
                archivo = limpiar(item.get("archivo"))
                hash_guardado = limpiar(item.get("hash_corpus"))

                if error_corpus:
                    estado = "ERROR_CORPUS"
                elif not archivo or not (resumenes_dir / archivo).is_file():
                    estado = "ERROR_MATERIAL"
                elif not hash_guardado:
                    estado = "SIN_HUELLA"
                elif hash_guardado != hash_actual:
                    estado = "DESACTUALIZADO"
                else:
                    estado = "OK"

            resultado.append(
                {
                    "norma_id": norma_id,
                    "norma": limpiar(norma["nombre_canonico"]),
                    "convocatorias": limpiar(norma["convocatorias"]),
                    "estado": estado,
                    "fuente_canonica": fuente,
                    "articulos_corpus": articulos,
                    "hash_actual": hash_actual,
                    "hash_guardado": hash_guardado,
                    "archivo": archivo,
                    "error": error_corpus,
                }
            )

        for norma_id, item in sorted(catalogo.items()):
            if norma_id in activas_ids:
                continue
            resultado.append(
                {
                    "norma_id": norma_id,
                    "norma": limpiar(item.get("norma")),
                    "convocatorias": "",
                    "estado": "NO_USADA",
                    "fuente_canonica": "",
                    "articulos_corpus": 0,
                    "hash_actual": "",
                    "hash_guardado": limpiar(item.get("hash_corpus")),
                    "archivo": limpiar(item.get("archivo")),
                    "error": "",
                }
            )

    recuento = {}
    for fila in resultado:
        recuento[fila["estado"]] = recuento.get(fila["estado"], 0) + 1
    return resultado, recuento


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita en SOLO LECTURA los resúmenes frente al corpus actual."
        )
    )
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument("--catalogo", default=str(CATALOGO_DEFECTO))
    parser.add_argument("--resumenes", default=str(RESUMENES_DEFECTO))
    parser.add_argument("--detalle", action="store_true")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    catalogo = Path(args.catalogo).resolve()
    resumenes = Path(args.resumenes).resolve()

    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 2

    print("=" * 78)
    print("AUDITORÍA DE MATERIALES DE ESTUDIO - SOLO LECTURA")
    print("=" * 78)
    print(f"Base:       {db}")
    print(f"Catálogo:   {catalogo}")
    print(f"Resúmenes:  {resumenes}")
    print("Escrituras: 0")
    print()

    try:
        filas, recuento = auditar(db, catalogo, resumenes)
    except Exception as exc:
        print(f"ERROR FATAL: {exc.__class__.__name__}: {exc}")
        return 2

    if args.detalle:
        print("DETALLE")
        print("-" * 78)
        for x in filas:
            print(
                f"{x['norma_id']:>5} | {x['estado']:<15} | "
                f"{x['articulos_corpus']:>4} | {x['norma']}"
            )
            if x["fuente_canonica"]:
                print(f"      fuente: {x['fuente_canonica']}")
            if x["error"]:
                print(f"      ERROR: {x['error']}")
        print()

    print("RESUMEN")
    print("-" * 78)
    for estado in (
        "OK", "NUEVA", "DESACTUALIZADO", "NO_USADA",
        "SIN_HUELLA", "ERROR_CORPUS", "ERROR_MATERIAL",
    ):
        print(f"{estado:<18} {recuento.get(estado, 0):>4}")

    problemas = sum(
        recuento.get(x, 0)
        for x in (
            "NUEVA", "DESACTUALIZADO", "SIN_HUELLA",
            "ERROR_CORPUS", "ERROR_MATERIAL",
        )
    )
    print()
    if problemas:
        print("RESULTADO: REQUIERE ACTUALIZACIÓN / REVISIÓN")
        return 1

    print("RESULTADO: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
