"""Smoke aislado del preparador de contexto de generación V2. No toca la BD real."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "scripts" / "preparar_contexto_generacion_v2.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def crear_db(path: Path) -> None:
    ddl = """
    PRAGMA foreign_keys=ON;
    CREATE TABLE convocatorias(id INTEGER PRIMARY KEY,codigo TEXT UNIQUE NOT NULL);
    CREATE TABLE temarios(id INTEGER PRIMARY KEY,convocatoria_id INTEGER NOT NULL,nombre TEXT NOT NULL);
    CREATE TABLE temario_temas(id INTEGER PRIMARY KEY,temario_id INTEGER NOT NULL,parte TEXT,numero_tema INTEGER,titulo TEXT);
    CREATE TABLE articulos_fuente(id INTEGER PRIMARY KEY,id_boe TEXT,articulo_boe TEXT,titulo_bloque TEXT,texto TEXT);
    CREATE TABLE temario_pseudonormas(id INTEGER PRIMARY KEY,tema_id INTEGER NOT NULL,codigo TEXT,estado TEXT);
    CREATE TABLE temario_pseudoarticulos(id INTEGER PRIMARY KEY,pseudonorma_id INTEGER NOT NULL,codigo TEXT,orden INTEGER,texto_temario TEXT,tipo_contenido TEXT,sintesis TEXT,estado TEXT);
    CREATE TABLE temario_pseudoarticulo_proposiciones(id INTEGER PRIMARY KEY,pseudoarticulo_id INTEGER NOT NULL,codigo TEXT,texto TEXT,esencial INTEGER,tipo TEXT,estado TEXT);
    CREATE TABLE fuentes_conocimiento(id INTEGER PRIMARY KEY,tipo_fuente TEXT,organismo TEXT,nombre TEXT,identificador_oficial TEXT,localizador_oficial TEXT,estado_verificacion TEXT);
    CREATE TABLE temario_pseudoarticulo_respaldos(id INTEGER PRIMARY KEY,pseudoarticulo_id INTEGER NOT NULL,proposicion_id INTEGER,articulo_fuente_id INTEGER,fuente_conocimiento_id INTEGER,pertinencia TEXT,estado TEXT,motivo TEXT,evidencia_texto TEXT,evidencia_localizador TEXT);

    INSERT INTO convocatorias VALUES(1,'A1-GEN-SMOKE');
    INSERT INTO temarios VALUES(1,1,'Temario smoke');
    INSERT INTO temario_temas VALUES(1,1,'ESPECIAL',53,'Gobierno abierto');
    INSERT INTO temario_pseudonormas VALUES(1,1,'PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO','ACEPTADA');
    INSERT INTO temario_pseudoarticulos VALUES(1,1,'P01',1,'Principios de gobierno abierto','MIXTO','Síntesis aceptada','ACEPTADA');
    INSERT INTO temario_pseudoarticulo_proposiciones VALUES(1,1,'A1E53-P01','Proposición verificada',1,'DOCTRINAL','ACEPTADA');
    INSERT INTO fuentes_conocimiento VALUES(1,'FUENTE_INSTITUCIONAL','BOE','Ley de transparencia','BOE-A-2013-12887','https://www.boe.es/','VERIFICADO');
    INSERT INTO temario_pseudoarticulo_respaldos VALUES(1,1,1,NULL,1,'PERTINENTE','VERIFICADO','smoke','Evidencia aceptada','art. 1');

    INSERT INTO temario_pseudoarticulos VALUES(2,1,'P02',2,'Unidad sin evidencia','MIXTO','Síntesis no utilizable','ACEPTADA');
    INSERT INTO temario_pseudoarticulos VALUES(3,1,'P03',3,'Unidad no aceptada','MIXTO','Síntesis borrador','BORRADOR');
    """
    con = sqlite3.connect(path)
    try:
        con.executescript(ddl)
        con.commit()
    finally:
        con.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v2_gen_context_") as td:
        base = Path(td)
        db = base / "test.sqlite3"
        salida = base / "contextos.jsonl"
        crear_db(db)
        antes = sha256(db)
        proc = subprocess.run(
            [sys.executable,str(SCRIPT),"--codigo","A1-GEN-SMOKE","--db",str(db),"--salida",str(salida)],
            cwd=RAIZ,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"preparar_contexto_generacion_v2.py rc={proc.returncode}")
        despues = sha256(db)
        if antes != despues:
            raise RuntimeError("La BD temporal cambió en SOLO LECTURA")
        filas = [json.loads(x) for x in salida.read_text(encoding="utf-8").splitlines() if x.strip()]
        if len(filas) != 1:
            raise RuntimeError(f"Se esperaba exactamente 1 contexto apto; filas={len(filas)}")
        f = filas[0]
        if f.get("tipo_contexto_propuesto") != "PSEUDO":
            raise RuntimeError("El contexto base debe ser PSEUDO")
        if f.get("pseudonorma") != "PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO":
            raise RuntimeError("Identidad pseudonormativa incorrecta")
        if f.get("pseudoarticulo") != "P01":
            raise RuntimeError("Pseudoartículo incorrecto")
        if len(f.get("proposiciones") or []) != 1:
            raise RuntimeError("Proposiciones no preservadas")
        resp = f.get("respaldos_verificados") or []
        if len(resp) != 1 or resp[0].get("fuente",{}).get("identificador_oficial") != "BOE-A-2013-12887":
            raise RuntimeError("Respaldo verificado no preservado")
        if not str(f.get("regla_mixta") or "").startswith("NO_INFERIR"):
            raise RuntimeError("Barrera MIXTA ausente")

        print("\n" + "=" * 78)
        print("SMOKE CONTEXTO GENERACION V2: OK")
        print("=" * 78)
        print("Unidades aptas................... 1")
        print("Sin respaldo excluida............ OK")
        print("BORRADOR excluido................ OK")
        print("Trazabilidad fuente.............. OK")
        print("MIXTA no inferida................ OK")
        print("BD............................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR SMOKE CONTEXTO GENERACION V2: {exc}")
        raise SystemExit(1)
