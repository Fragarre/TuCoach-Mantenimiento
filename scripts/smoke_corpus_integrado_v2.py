"""Smoke del orquestador de corpus integrado V2. No modifica la BD real."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "scripts" / "construir_corpus_integrado_v2.py"


def crear_db(path: Path) -> None:
    ddl = """
    PRAGMA foreign_keys=ON;
    CREATE TABLE convocatorias(id INTEGER PRIMARY KEY, codigo TEXT UNIQUE NOT NULL);
    CREATE TABLE temarios(id INTEGER PRIMARY KEY, convocatoria_id INTEGER NOT NULL, nombre TEXT NOT NULL);
    CREATE TABLE temario_temas(id INTEGER PRIMARY KEY, temario_id INTEGER NOT NULL, parte TEXT NOT NULL, numero_tema INTEGER NOT NULL, titulo TEXT);
    CREATE TABLE temario_referencias(
        id INTEGER PRIMARY KEY, tema_id INTEGER NOT NULL,
        nombre_norma_csv TEXT, nombre_norma_normalizada TEXT,
        articulo_solicitado TEXT, estado TEXT, mensaje_error TEXT,
        articulo_fuente_id INTEGER
    );
    CREATE TABLE articulos_fuente(
        id INTEGER PRIMARY KEY, id_boe TEXT, id_bloque TEXT,
        articulo_boe TEXT, titulo_bloque TEXT, texto TEXT
    );
    CREATE TABLE temario_pseudonormas(
        id INTEGER PRIMARY KEY, tema_id INTEGER NOT NULL,
        codigo TEXT NOT NULL, estado TEXT NOT NULL
    );
    CREATE TABLE temario_pseudoarticulos(
        id INTEGER PRIMARY KEY, pseudonorma_id INTEGER NOT NULL,
        codigo TEXT NOT NULL, orden INTEGER NOT NULL,
        texto_temario TEXT NOT NULL, tipo_contenido TEXT NOT NULL,
        sintesis TEXT, estado TEXT NOT NULL
    );
    CREATE TABLE temario_pseudoarticulo_respaldos(
        id INTEGER PRIMARY KEY, pseudoarticulo_id INTEGER NOT NULL,
        proposicion_id INTEGER, articulo_fuente_id INTEGER,
        fuente_conocimiento_id INTEGER, pertinencia TEXT NOT NULL,
        estado TEXT NOT NULL, motivo TEXT, evidencia_texto TEXT,
        evidencia_localizador TEXT
    );
    INSERT INTO convocatorias VALUES(1,'A1-V2-SMOKE');
    INSERT INTO temarios VALUES(1,1,'Temario V2 smoke');
    INSERT INTO temario_temas VALUES(1,1,'ESPECIAL',53,'Gobierno abierto');
    INSERT INTO temario_pseudonormas VALUES(1,1,'PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO','ACEPTADA');
    INSERT INTO temario_pseudoarticulos VALUES(
        1,1,'P01',1,'Gobierno abierto: principios','MIXTO',
        'Síntesis pseudonormativa verificada','ACEPTADA'
    );
    INSERT INTO temario_pseudoarticulo_respaldos VALUES(
        1,1,NULL,NULL,99,'PERTINENTE','VERIFICADO',
        'decisión humana','evidencia','localizador'
    );
    """
    con = sqlite3.connect(path)
    try:
        con.executescript(ddl)
        con.commit()
    finally:
        con.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v2_corpus_integrado_") as td:
        base = Path(td)
        db = base / "test.sqlite3"
        salida = base / "pseudo_v2.jsonl"
        crear_db(db)
        cmd = [
            sys.executable, str(SCRIPT),
            "--db", str(db),
            "--codigo", "A1-V2-SMOKE",
            "--salida-v2", str(salida),
        ]
        proc = subprocess.run(cmd, cwd=RAIZ, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"Orquestador integrado rc={proc.returncode}")
        filas = [
            json.loads(x)
            for x in salida.read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        if len(filas) != 1:
            raise RuntimeError(f"Se esperaba 1 pseudoartículo V2; filas={len(filas)}")
        fila = filas[0]
        if fila.get("pseudonorma") != "PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO":
            raise RuntimeError("Identidad pseudonormativa incorrecta")
        if fila.get("sintesis") != "Síntesis pseudonormativa verificada":
            raise RuntimeError("Síntesis V2 incorrecta")
        if int(fila.get("respaldos_verificados") or 0) != 1:
            raise RuntimeError("Respaldo verificado no reflejado")

        print("\n" + "=" * 78)
        print("SMOKE CORPUS INTEGRADO V2: OK")
        print("=" * 78)
        print("Flujo consolidado sin referencias...... compatible")
        print("Estado ACEPTADA......................... admitido")
        print("Capa pseudonormativa.................... 1 registro")
        print("BD real................................ SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR SMOKE CORPUS INTEGRADO V2: {exc}")
        raise SystemExit(1)
