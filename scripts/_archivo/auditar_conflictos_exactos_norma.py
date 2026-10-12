from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import unicodedata
from pathlib import Path
from datetime import datetime


RAIZ = Path(__file__).resolve().parents[1]
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
AUDITORIAS_DEFECTO = RAIZ / "auditorias" / "duplicados_juridicos"


def norm(s: str | None) -> str:
    x = "" if s is None else str(s)
    x = unicodedata.normalize("NFKD", x)
    x = "".join(c for c in x if not unicodedata.combining(c))
    x = x.lower().strip()
    x = re.sub(r"\s+", " ", x)
    return x


def norm_articulo(s: str | None) -> str:
    x = norm(s)
    x = re.sub(r"^(articulo|art\.?|article)\s*", "", x)
    x = x.rstrip(".")
    return x


def abrir_ro(db: Path) -> sqlite3.Connection:
    uri = db.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def tabla_existe(con: sqlite3.Connection, nombre: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nombre,),
    ).fetchone() is not None


def buscar_csv_conflictos(carpeta: Path) -> Path:
    candidatos = sorted(
        carpeta.glob("duplicados_conflictivos_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidatos:
        raise RuntimeError(
            f"No se encuentra duplicados_conflictivos_*.csv en {carpeta}"
        )
    return candidatos[0]


def leer_pares_exactos(csv_path: Path) -> list[tuple[int, int, str]]:
    pares = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh, delimiter=";")
        for fila in r:
            if fila.get("tipo") != "EXACTO_RESPUESTA_DISTINTA":
                continue
            pares.append((int(fila["id_1"]), int(fila["id_2"]), fila["grupo"]))
    return pares


def fuentes_norma(con: sqlite3.Connection, norma_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT nf.id_fuente, nf.titulo_fuente, nf.departamento
        FROM norma_fuentes nf
        WHERE nf.norma_id=?
        ORDER BY nf.id
        """,
        (norma_id,),
    ).fetchall()


def articulos_fuente_para(
    con: sqlite3.Connection,
    ids_fuente: list[str],
    articulo: str,
) -> list[sqlite3.Row]:
    if not ids_fuente:
        return []

    marcadores = ",".join("?" for _ in ids_fuente)
    filas = con.execute(
        f"""
        SELECT id, id_boe, articulo_boe, titulo_bloque, departamento, texto
        FROM articulos_fuente
        WHERE id_boe IN ({marcadores})
        ORDER BY id_boe, id
        """,
        ids_fuente,
    ).fetchall()

    objetivo = norm_articulo(articulo)
    exactas = [f for f in filas if norm_articulo(f["articulo_boe"]) == objetivo]
    return exactas


def main() -> int:
    p = argparse.ArgumentParser(
        description="Audita SOLO LECTURA los conflictos exactos y adjunta el texto normativo real."
    )
    p.add_argument("--db", type=Path, default=DB_DEFECTO)
    p.add_argument("--csv", type=Path)
    p.add_argument("--salida", type=Path, default=AUDITORIAS_DEFECTO)
    args = p.parse_args()

    db = args.db.resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la base: {db}")

    salida = args.salida.resolve()
    salida.mkdir(parents=True, exist_ok=True)
    csv_conf = args.csv.resolve() if args.csv else buscar_csv_conflictos(salida)

    pares = leer_pares_exactos(csv_conf)
    if not pares:
        print("No hay conflictos exactos en el CSV.")
        return 0

    ids = sorted({x for a, b, _ in pares for x in (a, b)})

    with abrir_ro(db) as con:
        marcadores = ",".join("?" for _ in ids)
        preguntas = {
            int(f["id"]): f
            for f in con.execute(
                f"""
                SELECT
                    id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d,
                    respuesta_correcta, origen_oposicion, tipo_fuente,
                    importacion_fichero_id, pagina_origen,
                    norma_id_normalizada, nombre_norma, nombre_norma_normalizado,
                    articulo, articulo_normalizado
                FROM lote_preguntas
                WHERE id IN ({marcadores})
                """,
                ids,
            ).fetchall()
        }

        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        txt = salida / f"conflictos_exactos_con_texto_normativo_{marca}.txt"
        csv_out = salida / f"conflictos_exactos_con_texto_normativo_{marca}.csv"

        filas_csv = []
        bloques = []

        for a_id, b_id, grupo in pares:
            a = preguntas[a_id]
            b = preguntas[b_id]

            norma_id = a["norma_id_normalizada"] or b["norma_id_normalizada"]
            articulo = a["articulo_normalizado"] or a["articulo"] or b["articulo_normalizado"] or b["articulo"]

            fuente_rows = fuentes_norma(con, int(norma_id)) if norma_id is not None else []
            ids_fuente = [str(f["id_fuente"]) for f in fuente_rows]
            articulos = articulos_fuente_para(con, ids_fuente, str(articulo or ""))

            texto_normativo = "\n\n".join(
                f"[{f['id_boe']} | art. {f['articulo_boe']} | {f['titulo_bloque']}]\n{f['texto']}"
                for f in articulos
            )

            bloque = []
            bloque.append("=" * 100)
            bloque.append(f"{grupo} | IDs {a_id} vs {b_id}")
            bloque.append(f"Norma ID: {norma_id} | Artículo: {articulo}")
            bloque.append(f"Respuesta 1: {a['respuesta_correcta']} | {a['tipo_fuente']} | {a['origen_oposicion']} | importacion_fichero_id={a['importacion_fichero_id']} | página={a['pagina_origen']}")
            bloque.append(f"Respuesta 2: {b['respuesta_correcta']} | {b['tipo_fuente']} | {b['origen_oposicion']} | importacion_fichero_id={b['importacion_fichero_id']} | página={b['pagina_origen']}")
            bloque.append("")
            bloque.append("ENUNCIADO")
            bloque.append(str(a["enunciado"]))
            bloque.append("")
            for letra, campo in (("A","opcion_a"),("B","opcion_b"),("C","opcion_c"),("D","opcion_d")):
                bloque.append(f"{letra}) {a[campo]}")
            bloque.append("")
            bloque.append("FUENTES DE LA NORMA")
            if fuente_rows:
                for f in fuente_rows:
                    bloque.append(f"- {f['id_fuente']} | {f['titulo_fuente'] or ''} | {f['departamento'] or ''}")
            else:
                bloque.append("- SIN FUENTE EN norma_fuentes")
            bloque.append("")
            bloque.append("TEXTO NORMATIVO ENLAZADO")
            bloque.append(texto_normativo if texto_normativo else "[NO LOCALIZADO AUTOMÁTICAMENTE]")
            bloques.append("\n".join(bloque))

            filas_csv.append({
                "grupo": grupo,
                "id_1": a_id,
                "respuesta_1": a["respuesta_correcta"],
                "tipo_fuente_1": a["tipo_fuente"],
                "origen_1": a["origen_oposicion"],
                "importacion_fichero_id_1": a["importacion_fichero_id"],
                "pagina_1": a["pagina_origen"],
                "id_2": b_id,
                "respuesta_2": b["respuesta_correcta"],
                "tipo_fuente_2": b["tipo_fuente"],
                "origen_2": b["origen_oposicion"],
                "importacion_fichero_id_2": b["importacion_fichero_id"],
                "pagina_2": b["pagina_origen"],
                "norma_id": norma_id,
                "articulo": articulo,
                "fuentes_norma": " | ".join(ids_fuente),
                "texto_normativo_localizado": "SI" if articulos else "NO",
                "enunciado": a["enunciado"],
                "opcion_a": a["opcion_a"],
                "opcion_b": a["opcion_b"],
                "opcion_c": a["opcion_c"],
                "opcion_d": a["opcion_d"],
                "texto_normativo": texto_normativo,
            })

    txt.write_text("\n\n".join(bloques) + "\n", encoding="utf-8")

    campos = list(filas_csv[0].keys())
    with csv_out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
        w.writeheader()
        w.writerows(filas_csv)

    encontrados = sum(1 for f in filas_csv if f["texto_normativo_localizado"] == "SI")
    print("=" * 78)
    print("CONFLICTOS EXACTOS + TEXTO NORMATIVO - SOLO LECTURA")
    print("=" * 78)
    print(f"Base: {db}")
    print(f"CSV origen: {csv_conf}")
    print(f"Pares exactos conflictivos............... {len(pares)}")
    print(f"Con texto normativo localizado........... {encontrados}")
    print(f"Sin texto localizado automáticamente..... {len(pares) - encontrados}")
    print()
    print(f"Informe TXT: {txt}")
    print(f"Informe CSV: {csv_out}")
    print()
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
