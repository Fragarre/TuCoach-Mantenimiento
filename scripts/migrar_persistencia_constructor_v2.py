"""
Constructor Temario V2 - migración aditiva de persistencia.

Crea únicamente las tablas V2 aprobadas para pseudonormas, pseudoartículos,
proposiciones, fuentes de conocimiento y respaldos. No modifica tablas existentes.

Por defecto funciona en modo SOLO REVISION. Para aplicar de verdad hay que usar
--aplicar. Antes de aplicar crea copia de seguridad de la SQLite y ejecuta la
migración dentro de una transacción.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"

TABLAS_V2 = (
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "fuentes_conocimiento",
    "temario_pseudoarticulo_respaldos",
)

DDL = """
CREATE TABLE IF NOT EXISTS temario_pseudonormas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tema_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    estado TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tema_id) REFERENCES temario_temas(id) ON DELETE CASCADE,
    UNIQUE (tema_id, codigo)
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudonormas_tema
    ON temario_pseudonormas(tema_id);

CREATE TABLE IF NOT EXISTS temario_pseudoarticulos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pseudonorma_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    orden INTEGER NOT NULL,
    texto_temario TEXT NOT NULL,
    tipo_contenido TEXT NOT NULL,
    sintesis TEXT,
    estado TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pseudonorma_id) REFERENCES temario_pseudonormas(id) ON DELETE CASCADE,
    UNIQUE (pseudonorma_id, codigo),
    UNIQUE (pseudonorma_id, orden)
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudoarticulos_pseudonorma
    ON temario_pseudoarticulos(pseudonorma_id);

CREATE TABLE IF NOT EXISTS temario_pseudoarticulo_proposiciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pseudoarticulo_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    texto TEXT NOT NULL,
    esencial INTEGER NOT NULL CHECK (esencial IN (0,1)),
    tipo TEXT NOT NULL,
    estado TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pseudoarticulo_id) REFERENCES temario_pseudoarticulos(id) ON DELETE CASCADE,
    UNIQUE (pseudoarticulo_id, codigo)
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudoarticulo_prop_pa
    ON temario_pseudoarticulo_proposiciones(pseudoarticulo_id);

CREATE TABLE IF NOT EXISTS fuentes_conocimiento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_fuente TEXT NOT NULL,
    organismo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    identificador_oficial TEXT,
    localizador_oficial TEXT,
    estado_verificacion TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tipo_fuente, organismo, nombre, identificador_oficial)
);
CREATE INDEX IF NOT EXISTS idx_fuentes_conocimiento_tipo
    ON fuentes_conocimiento(tipo_fuente);
CREATE INDEX IF NOT EXISTS idx_fuentes_conocimiento_identificador
    ON fuentes_conocimiento(identificador_oficial);

CREATE TABLE IF NOT EXISTS temario_pseudoarticulo_respaldos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pseudoarticulo_id INTEGER NOT NULL,
    proposicion_id INTEGER,
    articulo_fuente_id INTEGER,
    fuente_conocimiento_id INTEGER,
    pertinencia TEXT NOT NULL,
    estado TEXT NOT NULL,
    motivo TEXT,
    evidencia_texto TEXT,
    evidencia_localizador TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pseudoarticulo_id) REFERENCES temario_pseudoarticulos(id) ON DELETE CASCADE,
    FOREIGN KEY (proposicion_id) REFERENCES temario_pseudoarticulo_proposiciones(id) ON DELETE CASCADE,
    FOREIGN KEY (articulo_fuente_id) REFERENCES articulos_fuente(id) ON DELETE NO ACTION,
    FOREIGN KEY (fuente_conocimiento_id) REFERENCES fuentes_conocimiento(id) ON DELETE NO ACTION,
    CHECK (
        (articulo_fuente_id IS NOT NULL AND fuente_conocimiento_id IS NULL)
        OR
        (articulo_fuente_id IS NULL AND fuente_conocimiento_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_pa
    ON temario_pseudoarticulo_respaldos(pseudoarticulo_id);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_prop
    ON temario_pseudoarticulo_respaldos(proposicion_id);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_articulo
    ON temario_pseudoarticulo_respaldos(articulo_fuente_id);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_fuente
    ON temario_pseudoarticulo_respaldos(fuente_conocimiento_id);
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def backup(path: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = path.with_name(f"{path.stem}_antes_persistencia_v2_{marca}{path.suffix}")
    shutil.copy2(path, destino)
    return destino


def tablas_existentes(con: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD: {db}")

    hash_antes = sha256(db)
    with sqlite3.connect(db) as con:
        existentes = tablas_existentes(con)
    ya = [t for t in TABLAS_V2 if t in existentes]

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - MIGRACION PERSISTENCIA")
    print("=" * 78)
    print(f"BD............................... {db}")
    print(f"Modo............................. {'APLICAR' if args.aplicar else 'SOLO REVISION'}")
    print(f"Tablas V2 ya existentes.......... {len(ya)}")
    for t in ya:
        print(f"  - {t}")
    print(f"Tablas V2 objetivo............... {len(TABLAS_V2)}")

    if not args.aplicar:
        print("No se ha modificado la base de datos.")
        return 0

    copia = backup(db)
    try:
        with sqlite3.connect(db) as con:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("BEGIN IMMEDIATE")
            con.executescript(DDL)
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(f"foreign_key_check devolvió {len(fk)} incidencias")
            con.commit()
    except Exception:
        shutil.copy2(copia, db)
        raise

    with sqlite3.connect(db) as con:
        despues = tablas_existentes(con)
        faltan = [t for t in TABLAS_V2 if t not in despues]
        filas = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLAS_V2}
    if faltan:
        shutil.copy2(copia, db)
        raise RuntimeError("Faltan tablas V2 tras migración: " + ", ".join(faltan))

    print(f"Backup........................... {copia}")
    print("Tablas V2 creadas/verificadas.... OK")
    for t in TABLAS_V2:
        print(f"  {t}: {filas[t]} filas")
    print("PRAGMA foreign_key_check......... OK")
    print(f"Hash antes....................... {hash_antes}")
    print(f"Hash después..................... {sha256(db)}")
    print("No se han insertado datos V2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
