from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

from boe_api import texto_articulo_suficiente

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SCRIPTS = Path(__file__).resolve().parent


def ejecutar(*args: str) -> None:
    cmd = [sys.executable, *args]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError("Falló: " + " ".join(cmd))


def verificar(db: Path) -> tuple[list[dict], int]:
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        filas = con.execute(
            """
            SELECT
                c.id AS convocatoria_id,
                c.codigo,
                tr.id AS referencia_id,
                tt.parte,
                tt.numero_tema,
                tr.nombre_norma_csv,
                tr.articulo_solicitado,
                tr.estado,
                tr.articulo_fuente_id,
                af.titulo_bloque,
                af.texto
            FROM convocatorias c
            JOIN temarios t ON t.convocatoria_id = c.id
            JOIN temario_temas tt ON tt.temario_id = t.id
            JOIN temario_referencias tr ON tr.tema_id = tt.id
            LEFT JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
            ORDER BY c.id, tr.id
            """
        ).fetchall()

        defectos: list[dict] = []
        for f in filas:
            motivo = None
            if str(f["estado"] or "").strip() != "COMPLETADO":
                motivo = f"estado={f['estado']}"
            elif f["articulo_fuente_id"] is None:
                motivo = "sin articulo_fuente_id"
            elif not texto_articulo_suficiente(f["texto"], f["titulo_bloque"]):
                motivo = "texto incompleto"
            if motivo:
                d = dict(f)
                d["motivo"] = motivo
                defectos.append(d)

        huerfanos = con.execute(
            """
            SELECT COUNT(*)
            FROM articulos_fuente af
            WHERE NOT EXISTS (
                SELECT 1 FROM temario_referencias tr
                WHERE tr.articulo_fuente_id = af.id
            )
            AND NOT EXISTS (
                SELECT 1 FROM resoluciones_boe rb
                WHERE rb.articulo_fuente_id = af.id
            )
            """
        ).fetchone()[0]

        return defectos, int(huerfanos)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repara, limpia y valida el corpus jurídico de todas las convocatorias."
    )
    parser.add_argument("--db", default=str(DB_DEFECTO))
    args = parser.parse_args()
    db = Path(args.db).resolve()

    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1

    ejecutar(
        str(SCRIPTS / "resolver_referencias_boe.py"),
        "--db", str(db),
        "--reparar-textos-incompletos",
    )
    ejecutar(
        str(SCRIPTS / "resolver_referencias_boe.py"),
        "--db", str(db),
        "--limpiar-huerfanos-incompletos",
    )

    defectos, huerfanos = verificar(db)
    if defectos or huerfanos:
        print("\nCORPUS NO CERRADO")
        print(f"Referencias defectuosas: {len(defectos)}")
        print(f"Artículos fuente huérfanos: {huerfanos}")
        for d in defectos[:50]:
            print(
                f"  {d['codigo']} | ref {d['referencia_id']} | "
                f"{d['nombre_norma_csv']} | art. {d['articulo_solicitado']} | {d['motivo']}"
            )
        return 2

    ejecutar(str(SCRIPTS / "auditar_corpus_temario.py"), "--db", str(db))

    print("\nCORPUS CERRADO Y LIMPIO")
    print("Referencias jurídicas incompletas: 0")
    print("Artículos fuente huérfanos: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
