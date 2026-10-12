from __future__ import annotations

import argparse
import csv
import importlib.util
import sqlite3
from collections import Counter
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
CONSTRUCTOR_DEFECTO = RAIZ / "scripts" / "mantener_banco_preguntas.py"


def cargar_constructor(ruta: Path):
    spec = importlib.util.spec_from_file_location("mantener_banco_preguntas_diag", ruta)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se puede cargar el constructor: {ruta}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def escribir_csv(ruta: Path, filas: list[dict]) -> None:
    if not filas:
        ruta.write_text("", encoding="utf-8-sig")
        return
    columnas = []
    vistos = set()
    for fila in filas:
        for clave in fila:
            if clave not in vistos:
                vistos.add(clave)
                columnas.append(clave)
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        w.writerows(filas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico SOLO LECTURA de nuevas preguntas del banco sin regla de parte."
    )
    parser.add_argument("--convocatoria-id", type=int, required=True)
    parser.add_argument("--db", default=str(DB_DEFECTO))
    parser.add_argument("--constructor", default=str(CONSTRUCTOR_DEFECTO))
    args = parser.parse_args()

    db = Path(args.db).resolve()
    constructor_path = Path(args.constructor).resolve()

    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")
    if not constructor_path.is_file():
        raise FileNotFoundError(f"No existe el constructor: {constructor_path}")

    constructor = cargar_constructor(constructor_path)

    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        con.execute("PRAGMA foreign_keys = ON")

        conv = con.execute(
            "SELECT id, codigo FROM convocatorias WHERE id = ?",
            (args.convocatoria_id,),
        ).fetchone()
        if conv is None:
            raise RuntimeError(f"No existe la convocatoria {args.convocatoria_id}.")

        temarios = con.execute(
            "SELECT id FROM temarios WHERE convocatoria_id = ? ORDER BY id",
            (args.convocatoria_id,),
        ).fetchall()
        if len(temarios) != 1:
            raise RuntimeError(
                f"La convocatoria debe tener exactamente un temario; encontrados: {len(temarios)}"
            )
        temario_id = int(temarios[0]["id"])

        refs, inv_refs, dup_refs = constructor.cargar_referencias_juridicas(con, temario_id)
        eqs, inv_eq, dup_eq = constructor.cargar_equivalencias_no_juridicas(con, temario_id)

        jur = constructor.seleccionar_juridicas(con, refs, {})
        nojur = constructor.seleccionar_no_juridicas(con, eqs, {})

        existentes = {
            int(r[0])
            for r in con.execute(
                "SELECT pregunta_id FROM banco_preguntas WHERE convocatoria_id = ?",
                (args.convocatoria_id,),
            )
        }

        nuevas = [
            *[dict(x) for x in jur["nuevas"] if int(x["id"]) not in existentes],
            *[dict(x) for x in nojur["nuevas"] if int(x["id"]) not in existentes],
        ]

        incidencias = []
        resolubles = []
        for fila in nuevas:
            tema = con.execute(
                """
                SELECT id, parte, numero_tema, titulo, tipo_contenido
                FROM temario_temas
                WHERE id = ?
                """,
                (int(fila["tema_id"]),),
            ).fetchone()
            if tema is None:
                incidencias.append({
                    "pregunta_id": fila["id"],
                    "error": "TEMA_INEXISTENTE",
                    "tema_id": fila.get("tema_id"),
                })
                continue

            pregunta = con.execute(
                """
                SELECT id, teorica_practica, tema_no_juridico,
                       tipo_clasificacion, nombre_norma_normalizado,
                       articulo_normalizado, origen_oposicion, tipo_fuente,
                       enunciado
                FROM lote_preguntas
                WHERE id = ?
                """,
                (int(fila["id"]),),
            ).fetchone()

            parte_id, error = constructor.resolver_parte_convocatoria(
                con, args.convocatoria_id, dict(pregunta), dict(tema)
            )

            base = {
                "pregunta_id": int(fila["id"]),
                "error": error or "",
                "parte_id_resuelta": parte_id,
                "tema_id": int(tema["id"]),
                "temario_parte": tema["parte"],
                "numero_tema": tema["numero_tema"],
                "tipo_contenido": tema["tipo_contenido"],
                "teorica_practica": pregunta["teorica_practica"],
                "tema_no_juridico": pregunta["tema_no_juridico"],
                "tipo_clasificacion": pregunta["tipo_clasificacion"],
                "nombre_norma_normalizado": pregunta["nombre_norma_normalizado"],
                "articulo_normalizado": pregunta["articulo_normalizado"],
                "origen_oposicion": pregunta["origen_oposicion"],
                "tipo_fuente": pregunta["tipo_fuente"],
                "enunciado": pregunta["enunciado"],
            }

            if error or parte_id is None:
                incidencias.append(base)
            else:
                resolubles.append(base)

        salida = RAIZ / "auditorias" / f"diagnostico_partes_banco_conv_{args.convocatoria_id}.csv"
        salida.parent.mkdir(parents=True, exist_ok=True)
        escribir_csv(salida, incidencias)

        conteo_error = Counter(str(x.get("error") or "SIN_ERROR") for x in incidencias)
        conteo_tp = Counter(str(x.get("teorica_practica")) for x in incidencias)

        print("=" * 78)
        print("DIAGNÓSTICO DE PARTES PARA NUEVAS PREGUNTAS DEL BANCO")
        print("=" * 78)
        print(f"Convocatoria: {conv['id']} | {conv['codigo']}")
        print("Modo: SOLO LECTURA")
        print()
        print(f"Nuevas candidatas totales:       {len(nuevas)}")
        print(f"Con parte resoluble:             {len(resolubles)}")
        print(f"Con incidencia de parte:         {len(incidencias)}")
        print()
        print("Incidencias por tipo:")
        if conteo_error:
            for clave, n in sorted(conteo_error.items()):
                print(f"  {clave:<30} {n}")
        else:
            print("  Ninguna")
        print()
        print("teorica_practica en incidencias:")
        if conteo_tp:
            for clave, n in sorted(conteo_tp.items()):
                print(f"  {clave:<30} {n}")
        else:
            print("  Ninguna")
        print()
        if incidencias:
            print("Primeros IDs afectados:")
            print("  " + ", ".join(str(x["pregunta_id"]) for x in incidencias[:50]))
        print()
        print(f"Detalle CSV: {salida}")
        print("=" * 78)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
