from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "scripts" / "persistir_evidencia_humana_v2.py"


def crear_db(path: Path) -> None:
    ddl = """
    PRAGMA foreign_keys=ON;
    CREATE TABLE convocatorias(id INTEGER PRIMARY KEY, codigo TEXT UNIQUE NOT NULL);
    CREATE TABLE temarios(id INTEGER PRIMARY KEY, convocatoria_id INTEGER NOT NULL, FOREIGN KEY(convocatoria_id) REFERENCES convocatorias(id));
    CREATE TABLE temario_temas(id INTEGER PRIMARY KEY, temario_id INTEGER NOT NULL, parte TEXT NOT NULL, numero_tema INTEGER NOT NULL, FOREIGN KEY(temario_id) REFERENCES temarios(id));
    CREATE TABLE articulos_fuente(id INTEGER PRIMARY KEY);
    CREATE TABLE temario_pseudonormas(id INTEGER PRIMARY KEY, tema_id INTEGER NOT NULL, codigo TEXT NOT NULL, estado TEXT NOT NULL, UNIQUE(tema_id,codigo), FOREIGN KEY(tema_id) REFERENCES temario_temas(id));
    CREATE TABLE temario_pseudoarticulos(id INTEGER PRIMARY KEY, pseudonorma_id INTEGER NOT NULL, codigo TEXT NOT NULL, FOREIGN KEY(pseudonorma_id) REFERENCES temario_pseudonormas(id));
    CREATE TABLE temario_pseudoarticulo_proposiciones(id INTEGER PRIMARY KEY, pseudoarticulo_id INTEGER NOT NULL, codigo TEXT NOT NULL, texto TEXT NOT NULL, esencial INTEGER NOT NULL, tipo TEXT NOT NULL, estado TEXT NOT NULL, UNIQUE(pseudoarticulo_id,codigo), FOREIGN KEY(pseudoarticulo_id) REFERENCES temario_pseudoarticulos(id));
    CREATE TABLE fuentes_conocimiento(id INTEGER PRIMARY KEY AUTOINCREMENT, tipo_fuente TEXT NOT NULL, organismo TEXT NOT NULL, nombre TEXT NOT NULL, identificador_oficial TEXT, localizador_oficial TEXT, estado_verificacion TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(tipo_fuente,organismo,nombre,identificador_oficial));
    CREATE TABLE temario_pseudoarticulo_respaldos(id INTEGER PRIMARY KEY AUTOINCREMENT, pseudoarticulo_id INTEGER NOT NULL, proposicion_id INTEGER, articulo_fuente_id INTEGER, fuente_conocimiento_id INTEGER, pertinencia TEXT NOT NULL, estado TEXT NOT NULL, motivo TEXT, evidencia_texto TEXT, evidencia_localizador TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(pseudoarticulo_id) REFERENCES temario_pseudoarticulos(id), FOREIGN KEY(proposicion_id) REFERENCES temario_pseudoarticulo_proposiciones(id), FOREIGN KEY(articulo_fuente_id) REFERENCES articulos_fuente(id), FOREIGN KEY(fuente_conocimiento_id) REFERENCES fuentes_conocimiento(id), CHECK((articulo_fuente_id IS NOT NULL AND fuente_conocimiento_id IS NULL) OR (articulo_fuente_id IS NULL AND fuente_conocimiento_id IS NOT NULL)));
    INSERT INTO convocatorias VALUES(1,'A1-TEST');
    INSERT INTO temarios VALUES(1,1);
    INSERT INTO temario_temas VALUES(1,1,'ESPECIAL',53);
    INSERT INTO temario_pseudonormas VALUES(1,1,'PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO','ACEPTADA');
    INSERT INTO temario_pseudoarticulos VALUES(1,1,'P01');
    INSERT INTO temario_pseudoarticulo_proposiciones VALUES(1,1,'A1E53-P01','Gobierno abierto',1,'MIXTO','ACEPTADA');
    """
    with sqlite3.connect(path) as con:
        con.executescript(ddl)


def escribir(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def ejecutar(db: Path, rec: Path, dec: Path, evi: Path) -> None:
    cmd = [sys.executable, str(SCRIPT), "--recuperacion-json", str(rec), "--decisiones-json", str(dec), "--evidencia-json", str(evi), "--convocatoria", "A1-TEST", "--db", str(db), "--aplicar"]
    subprocess.run(cmd, cwd=RAIZ, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v2_evidencia_") as td:
        base = Path(td)
        db, recp, decp, evip = (base / "test.sqlite3", base / "rec.json", base / "dec.json", base / "evi.json")
        crear_db(db)
        comunes = {"caso":"A1_ESPECIAL_53","pseudonorma":"PSEUDO:ESPECIAL_53_GOBIERNO_ABIERTO","unidad":"TEMA_COMPLETO_P01_P07"}
        escribir(recp, {**comunes,"naturaleza":"RECUPERACION_FUENTES_EXTERNAS_V2","estado":"LISTO_PARA_REVISION_CONTENIDO","resultados":[{"tipo_fuente":"FUENTE_PRIMARIA_OFICIAL","identificador_oficial":"BOE-A-2022-8187","descripcion":"Ley 1/2022","organismo":"BOE","url_oficial":"https://www.boe.es/","estado_identidad":"IDENTIDAD_VERIFICADA","estado_recuperacion":"RECUPERADA_PARA_REVISION"}]})
        escribir(decp, {**comunes,"naturaleza":"DECISIONES_HUMANAS_PERTINENCIA_V2","estado":"REVISION_HUMANA_COMPLETADA","decisiones":[{"codigo_proposicion":"A1E53-P01","identificador_oficial":"BOE-A-2022-8187","decision":"PERTINENTE","fundamento":"Decisión humana de prueba"}]})
        escribir(evip, {**comunes,"naturaleza":"EVIDENCIA_HUMANA_PERTINENCIA_V2","estado":"EVIDENCIA_HUMANA_MATERIALIZADA","relaciones":[{"codigo_proposicion":"A1E53-P01","identificador_oficial":"BOE-A-2022-8187","tipo_fuente":"FUENTE_PRIMARIA_OFICIAL","decision_humana":"PERTINENTE","evidencia_aceptada":[{"fragmento":"Fragmento verificado","ancla":"gobierno abierto","tipo_localizador":"LEXICO","inicio":0,"fin":20,"anclas_presentes":["gobierno abierto"]}]}]})
        ejecutar(db, recp, decp, evip)
        ejecutar(db, recp, decp, evip)
        with sqlite3.connect(db) as con:
            fuentes = con.execute("SELECT COUNT(*) FROM fuentes_conocimiento").fetchone()[0]
            respaldos = con.execute("SELECT COUNT(*) FROM temario_pseudoarticulo_respaldos").fetchone()[0]
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            fila = con.execute("SELECT pertinencia,estado,evidencia_texto,evidencia_localizador FROM temario_pseudoarticulo_respaldos").fetchone()
        assert fuentes == 1, fuentes
        assert respaldos == 1, respaldos
        assert not fk, fk
        assert fila[0:2] == ('PERTINENTE','VERIFICADO'), fila
        assert 'Fragmento verificado' in fila[2]
        assert 'gobierno abierto' in fila[3]
        print("SMOKE PERSISTENCIA EVIDENCIA V2: OK")
        print("2 ejecuciones -> 1 fuente, 1 respaldo, FK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
