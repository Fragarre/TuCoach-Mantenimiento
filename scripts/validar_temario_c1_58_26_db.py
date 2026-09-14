from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from importar_temario import normalizar
from sincronizar_temario_c1_58_26 import CSV_DEFECTO, construir_esperado, leer_csv

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
CODIGO = "C1-01_58_26"


def clave_csv(fila: dict[str, str]) -> tuple[str, int, str, str]:
    return (
        fila["parte"].strip().upper(),
        int(fila["tema"]),
        normalizar(fila["LEY"]),
        fila["articulo"].strip().replace(",", "."),
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Valida que C1-01_58_26 coincide exactamente con el temario esperado, está normalizado y tiene corpus completo."
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--csv", default=str(CSV_DEFECTO))
    args = p.parse_args()

    db = Path(args.db).expanduser().resolve()
    csv = Path(args.csv).expanduser().resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 2
    if not csv.is_file():
        print(f"ERROR: no existe el CSV: {csv}")
        return 2

    filas, _ = leer_csv(csv)
    esperado_filas = construir_esperado(filas)
    esperado = {
        clave_csv(f)
        for f in esperado_filas
        if f["tipo"].strip().upper() == "JURIDICO"
    }

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        conv = con.execute("SELECT id FROM convocatorias WHERE codigo=?", (CODIGO,)).fetchone()
        if conv is None:
            print(f"ERROR: no existe la convocatoria {CODIGO}")
            return 2
        temarios = con.execute(
            "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id", (int(conv["id"]),)
        ).fetchall()
        if len(temarios) != 1:
            print(f"ERROR: se esperaba un único temario y hay {len(temarios)}")
            return 2
        temario_id = int(temarios[0]["id"])
        refs = con.execute(
            """
            SELECT tt.parte,tt.numero_tema,tr.nombre_norma_normalizada,
                   tr.articulo_solicitado,tr.estado,tr.articulo_fuente_id,tr.norma_id
            FROM temario_referencias tr
            JOIN temario_temas tt ON tt.id=tr.tema_id
            WHERE tt.temario_id=?
            ORDER BY tt.parte,tt.numero_tema,tr.nombre_norma_normalizada,tr.articulo_solicitado
            """,
            (temario_id,),
        ).fetchall()

    real = {
        (
            str(r["parte"]).strip().upper(),
            int(r["numero_tema"]),
            str(r["nombre_norma_normalizada"]).strip(),
            str(r["articulo_solicitado"]).strip().replace(",", "."),
        )
        for r in refs
    }
    faltan = sorted(esperado - real)
    sobran = sorted(real - esperado)
    sin_norma = [r for r in refs if r["norma_id"] is None]
    incompletas = [
        r for r in refs
        if str(r["estado"] or "").strip() != "COMPLETADO" or r["articulo_fuente_id"] is None
    ]

    print("=" * 78)
    print("VALIDACIÓN FINAL TEMARIO C1-01_58_26")
    print("=" * 78)
    print(f"Referencias esperadas: {len(esperado)}")
    print(f"Referencias en BD:     {len(real)}")
    print(f"Faltan:                {len(faltan)}")
    print(f"Sobran:                {len(sobran)}")
    print(f"Referencias sin norma_id: {len(sin_norma)}")
    print(f"Corpus incompleto:      {len(incompletas)}")
    print(f"SQLite integrity_check: {integrity}")
    print(f"Foreign key check:      {len(fk)} incidencias")

    if faltan:
        print("\nFALTAN EN BD:")
        for x in faltan[:50]: print("  -", x)
    if sobran:
        print("\nSOBRAN EN BD:")
        for x in sobran[:50]: print("  -", x)
    if sin_norma:
        print("\nREFERENCIAS SIN IDENTIDAD NORMATIVA:")
        for r in sin_norma[:50]:
            print(f"  - {r['parte']} {int(r['numero_tema']):02d} | {r['nombre_norma_normalizada']} | art. {r['articulo_solicitado']}")
    if incompletas:
        print("\nREFERENCIAS SIN CORPUS COMPLETO:")
        for r in incompletas[:50]:
            print(
                f"  - {r['parte']} {int(r['numero_tema']):02d} | "
                f"{r['nombre_norma_normalizada']} | art. {r['articulo_solicitado']} | "
                f"estado={r['estado']} | fuente={r['articulo_fuente_id']}"
            )

    if integrity != "ok" or fk or faltan or sobran or sin_norma or incompletas:
        print("\nRESULTADO: ERROR")
        return 1
    print("\nRESULTADO: OK - temario, normalización y corpus completos e íntegros.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
