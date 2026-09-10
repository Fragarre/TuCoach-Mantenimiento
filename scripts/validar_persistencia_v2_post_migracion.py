"""
Valida una migración incremental de persistencia V2 comparando la BD actual
con el backup inmediatamente anterior. SOLO LECTURA.

Comprueba:
- la BD actual contiene exactamente todas las tablas V2 previstas;
- las tablas nuevas respecto al backup son exactamente las V2 que faltaban;
- todas las tablas previas conservan exactamente estructura y filas;
- C1/C2 no contienen datos V2;
- las tablas V2 siguen vacías tras una migración exclusivamente de esquema;
- PRAGMA foreign_key_check = OK.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
PREFIJO_BACKUP = "oposiciones_antes_persistencia_v2_"
TABLAS_V2 = {
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "fuentes_conocimiento",
    "temario_pseudoarticulo_respaldos",
    "pregunta_contextos_v2",
}


def conectar_ro(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def tablas(con: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def firma_tabla(con: sqlite3.Connection, tabla: str) -> tuple[str, int, str]:
    ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,),
    ).fetchone()
    ddl_txt = "" if ddl is None or ddl[0] is None else str(ddl[0])

    columnas = [str(r[1]) for r in con.execute(f'PRAGMA table_info("{tabla}")')]
    if not columnas:
        raise RuntimeError(f"No se pudo leer la estructura de {tabla}.")
    qcols = ", ".join('"' + c.replace('"','""') + '"' for c in columnas)
    filas = con.execute(f'SELECT {qcols} FROM "{tabla}" ORDER BY rowid').fetchall()
    h = hashlib.sha256()
    for fila in filas:
        for valor in fila:
            if valor is None:
                dato = b"<NULL>"
            elif isinstance(valor, bytes):
                dato = b"<BLOB>" + valor
            else:
                dato = str(valor).encode("utf-8", errors="surrogatepass")
            h.update(len(dato).to_bytes(8, "big"))
            h.update(dato)
        h.update(b"\n")
    return ddl_txt, len(filas), h.hexdigest()


def resolver_backup(explicito: str | None) -> Path:
    if explicito:
        p = Path(explicito).expanduser().resolve()
        if not p.is_file():
            raise RuntimeError(f"No existe backup: {p}")
        return p
    candidatos = sorted((RAIZ / "db").glob(PREFIJO_BACKUP + "*.sqlite3"))
    if not candidatos:
        raise RuntimeError("No se encontró backup anterior a persistencia V2.")
    return candidatos[-1]


def convocatoria_ids(con: sqlite3.Connection, codigos: tuple[str, ...]) -> dict[str, int]:
    salida = {}
    for codigo in codigos:
        r = con.execute("SELECT id FROM convocatorias WHERE codigo=?", (codigo,)).fetchone()
        if r is not None:
            salida[codigo] = int(r[0])
    return salida


def filas_v2_convocatoria(con: sqlite3.Connection, convocatoria_id: int) -> int:
    pseudo = int(con.execute(
        """
        SELECT COUNT(*)
        FROM temario_pseudonormas p
        JOIN temario_temas tt ON tt.id=p.tema_id
        JOIN temarios t ON t.id=tt.temario_id
        WHERE t.convocatoria_id=?
        """,
        (convocatoria_id,),
    ).fetchone()[0])

    contextos = int(con.execute(
        """
        SELECT COUNT(*)
        FROM pregunta_contextos_v2 pc
        JOIN temario_temas tt ON tt.id=pc.tema_id
        JOIN temarios t ON t.id=tt.temario_id
        WHERE t.convocatoria_id=?
        """,
        (convocatoria_id,),
    ).fetchone()[0])
    return pseudo + contextos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--backup")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    backup = resolver_backup(args.backup)
    if not db.is_file():
        raise RuntimeError(f"No existe BD: {db}")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - VALIDACION POST-MIGRACION - SOLO LECTURA")
    print("=" * 78)
    print(f"BD............................... {db}")
    print(f"Backup comparación............... {backup}")

    with conectar_ro(db) as actual, conectar_ro(backup) as anterior:
        ta = tablas(actual)
        tb = tablas(anterior)

        faltan_v2 = TABLAS_V2 - ta
        if faltan_v2:
            raise RuntimeError("Faltan tablas V2 actuales: " + ", ".join(sorted(faltan_v2)))

        desaparecidas = tb - ta
        if desaparecidas:
            raise RuntimeError("Tablas previas desaparecidas: " + ", ".join(sorted(desaparecidas)))

        nuevas = ta - tb
        esperadas_nuevas = TABLAS_V2 - tb
        if nuevas != esperadas_nuevas:
            raise RuntimeError(
                "Tablas nuevas inesperadas. Esperadas=" + str(sorted(esperadas_nuevas)) +
                " encontradas=" + str(sorted(nuevas))
            )

        print(f"Tablas V2 presentes............... OK ({len(TABLAS_V2)})")
        print(f"Tablas nuevas desde backup........ OK ({len(nuevas)})")
        for tabla in sorted(nuevas):
            print(f"  + {tabla}")

        cambios = []
        for tabla in sorted(tb):
            ddl0, n0, h0 = firma_tabla(anterior, tabla)
            ddl1, n1, h1 = firma_tabla(actual, tabla)
            if (ddl0, n0, h0) != (ddl1, n1, h1):
                cambios.append((tabla, n0, n1))
        if cambios:
            detalle = "; ".join(f"{t}: {a}->{b}" for t, a, b in cambios)
            raise RuntimeError("Tabla previa alterada: " + detalle)
        print(f"Tablas previas comparadas.......... OK ({len(tb)})")

        for tabla in sorted(TABLAS_V2):
            n = int(actual.execute(f'SELECT COUNT(*) FROM "{tabla}"').fetchone()[0])
            if n != 0:
                raise RuntimeError(f"{tabla} contiene {n} filas; se esperaban 0.")
            print(f"  {tabla}: 0 filas")

        ids = convocatoria_ids(actual, ("C1-01_58_26", "C2-01_70_26"))
        for codigo, cid in ids.items():
            n = filas_v2_convocatoria(actual, cid)
            if n != 0:
                raise RuntimeError(f"{codigo} tiene {n} filas V2.")
            print(f"{codigo} filas V2................. 0")

        fk = actual.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"PRAGMA foreign_key_check devolvió {len(fk)} incidencias.")
        print("PRAGMA foreign_key_check......... OK")

    print("RESULTADO......................... OK")
    print("No se ha modificado la base de datos.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR VALIDACION POST-MIGRACION: {exc}")
        raise SystemExit(1)
