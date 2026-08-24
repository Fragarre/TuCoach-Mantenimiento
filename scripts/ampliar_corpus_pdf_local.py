"""Fallback local para completar una norma entera desde fuentes_normativas/.

Idempotente:
- no duplica artículos ya cubiertos;
- inserta sólo artículos inexistentes;
- repara únicamente filas cuyo texto actual es manifiestamente insuficiente;
- una segunda ejecución sin cambios produce 0 escrituras.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from boe_api import texto_articulo_suficiente
from pdf_normas import BOEError, buscar_norma_por_id, obtener_todos_articulos_por_id

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def limpiar(v: object | None) -> str:
    return " ".join(str(v or "").split()).strip()


def art_norm(v: object | None) -> str:
    return limpiar(v).replace(",", ".").lower()


def copia_seguridad(db: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = db.with_name(f"{db.stem}_antes_corpus_pdf_local_{marca}{db.suffix}")
    shutil.copy2(db, destino)
    return destino


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--id-fuente", required=True)
    p.add_argument("--aplicar", action="store_true")
    args = p.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1

    try:
        pdf = buscar_norma_por_id(args.id_fuente)
        articulos = obtener_todos_articulos_por_id(args.id_fuente)
    except BOEError as exc:
        print(f"PDF_LOCAL_NO_ENCONTRADO: {exc}")
        return 4

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        existentes = con.execute(
            """
            SELECT id, id_bloque, articulo_boe, titulo_bloque, texto
            FROM articulos_fuente
            WHERE UPPER(id_boe)=UPPER(?)
            ORDER BY id
            """,
            (args.id_fuente,),
        ).fetchall()

    por_art: dict[str, list[sqlite3.Row]] = {}
    for fila in existentes:
        por_art.setdefault(art_norm(fila["articulo_boe"]), []).append(fila)

    insertar = []
    reparar: list[tuple[sqlite3.Row, object]] = []
    ambiguos: list[str] = []
    ya_ok = 0

    for articulo in articulos:
        clave = art_norm(articulo.articulo)
        filas = por_art.get(clave, [])
        if not filas:
            insertar.append(articulo)
            continue
        texto_local = limpiar(articulo.texto)
        hash_local = hashlib.sha256(texto_local.encode("utf-8")).hexdigest()
        iguales = 0
        for fila in filas:
            texto_actual = limpiar(fila["texto"])
            hash_actual = hashlib.sha256(texto_actual.encode("utf-8")).hexdigest()
            if hash_actual == hash_local:
                iguales += 1
            else:
                # El PDF local se usa como fuente consolidada de respaldo. Se
                # actualiza el contenido conservando el id y el id_bloque para
                # no romper ninguna referencia existente.
                reparar.append((fila, articulo))
        if iguales == len(filas):
            ya_ok += 1

    print("=" * 78)
    print("FALLBACK PDF LOCAL - NORMA COMPLETA")
    print("=" * 78)
    print(f"Fuente:                {args.id_fuente}")
    print(f"PDF:                   {pdf.ruta}")
    print(f"Artículos PDF:         {len(articulos)}")
    print(f"Ya correctos:          {ya_ok}")
    print(f"Nuevos:                {len(insertar)}")
    print(f"A sincronizar desde PDF: {len(reparar)}")
    print(f"Ambiguos:              {len(ambiguos)}")

    if ambiguos:
        print("No se escribe: existen artículos ambiguos en la BD: " + ", ".join(ambiguos))
        return 3

    if not args.aplicar:
        print("Modo SOLO VALIDAR: no se modifica la base.")
        return 0

    if not insertar and not reparar:
        print("APLICACIÓN: OK - 0 cambios (idempotente)")
        return 0

    backup = copia_seguridad(db)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            con.execute("BEGIN IMMEDIATE")
            for fila, articulo in reparar:
                texto = limpiar(articulo.texto)
                con.execute(
                    """
                    UPDATE articulos_fuente
                    SET titulo_bloque=?, departamento=?, texto=?, hash_texto=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        articulo.titulo_bloque,
                        articulo.departamento,
                        texto,
                        hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                        int(fila["id"]),
                    ),
                )
            for articulo in insertar:
                texto = limpiar(articulo.texto)
                con.execute(
                    """
                    INSERT INTO articulos_fuente(
                        id_boe,id_bloque,articulo_boe,titulo_bloque,departamento,texto,hash_texto
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        articulo.id_boe,
                        articulo.id_bloque,
                        articulo.articulo,
                        articulo.titulo_bloque,
                        articulo.departamento,
                        texto,
                        hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                    ),
                )
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(f"foreign_key_check: {len(fk)} incidencias")
            con.commit()
        except Exception:
            con.rollback()
            raise

    print(f"Backup:                 {backup}")
    print(f"Insertados:             {len(insertar)}")
    print(f"Reparados:              {len(reparar)}")
    print("APLICACIÓN: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
