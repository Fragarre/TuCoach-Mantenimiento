from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from auditar_materiales_estudio import (
    DB_DEFECTO,
    CATALOGO_DEFECTO,
    RESUMENES_DEFECTO,
    cargar_catalogo,
    hash_corpus_norma,
    normas_activas,
)


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Migra una sola vez las huellas del catálogo de Materiales "
            "al criterio canónico por artículo."
        )
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--catalogo", default=str(CATALOGO_DEFECTO))
    p.add_argument("--resumenes", default=str(RESUMENES_DEFECTO))
    p.add_argument("--aplicar", action="store_true")
    args = p.parse_args()

    db = Path(args.db).resolve()
    catalogo_path = Path(args.catalogo).resolve()
    resumenes = Path(args.resumenes).resolve()

    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 2
    if not catalogo_path.is_file():
        print(f"ERROR: no existe el catálogo: {catalogo_path}")
        return 2

    catalogo = cargar_catalogo(catalogo_path)

    with sqlite3.connect(
        f"file:{db.as_posix()}?mode=ro",
        uri=True,
    ) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        activas = normas_activas(con)

        cambios = []
        for norma in activas:
            norma_id = int(norma["norma_id"])
            item = catalogo.get(norma_id)
            if item is None:
                print(
                    f"ERROR: norma activa {norma_id} no existe en catálogo."
                )
                return 2

            archivo = str(item.get("archivo") or "").strip()
            if not archivo or not (resumenes / archivo).is_file():
                print(
                    f"ERROR: falta PDF de norma_id={norma_id}: {archivo}"
                )
                return 2

            h, fuente, articulos, descartadas = hash_corpus_norma(
                con,
                norma_id,
            )
            cambios.append(
                {
                    "norma_id": norma_id,
                    "norma": str(norma["nombre_canonico"]),
                    "hash_nuevo": h,
                    "fuente": fuente,
                    "articulos": articulos,
                    "descartadas": descartadas,
                }
            )

    print("=" * 78)
    print("MIGRACIÓN DE HUELLAS DE MATERIALES - CRITERIO CANÓNICO")
    print("=" * 78)
    print(f"Normas activas: {len(cambios)}")
    print("PDF modificados: 0")
    print("Base de datos modificada: 0")
    print()

    for x in cambios:
        print(
            f"{x['norma_id']:>5} | {x['articulos']:>4} artículos | "
            f"descartadas={x['descartadas']:>2} | {x['norma']}"
        )

    if not args.aplicar:
        print()
        print("SOLO PLAN: el catálogo no ha sido modificado.")
        print("Para aplicar: vuelva a ejecutar con --aplicar.")
        return 0

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = catalogo_path.with_name(
        f"{catalogo_path.stem}_backup_{marca}{catalogo_path.suffix}"
    )
    shutil.copy2(catalogo_path, backup)

    datos = json.loads(catalogo_path.read_text(encoding="utf-8"))
    por_id = {int(x["norma_id"]): x for x in datos}

    for x in cambios:
        item = por_id[x["norma_id"]]
        item["hash_corpus"] = x["hash_nuevo"]
        item["fuente_canonica_actual"] = x["fuente"]
        item["articulos_canonicos_actual"] = x["articulos"]
        item["filas_no_canonicas_ignoradas"] = x["descartadas"]
        item["algoritmo_huella"] = "materiales-canonico-v2"
        item["fecha_migracion_huella"] = datetime.now().isoformat(
            timespec="seconds"
        )

    temporal = catalogo_path.with_suffix(".tmp")
    temporal.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporal.replace(catalogo_path)

    print()
    print(f"Backup catálogo: {backup}")
    print("MIGRACIÓN: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
