"""Smoke aislado de validación independiente de candidatas PSEUDO V2."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
MIGRACION = RAIZ / "scripts" / "migrar_persistencia_constructor_v2.py"
VALIDADOR = RAIZ / "scripts" / "validar_candidatas_preguntas_v2.py"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def ejecutar(args: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(args, cwd=RAIZ, check=False, text=True, capture_output=True)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise RuntimeError(f"Comando falló con código {r.returncode}")
    return r


def preparar(copia: Path) -> tuple[int, int]:
    con = sqlite3.connect(copia)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        conv = con.execute("SELECT id FROM convocatorias WHERE codigo='C1-01_58_26'").fetchone()
        if conv is None:
            raise RuntimeError("No existe C1-01_58_26 para el smoke.")
        temario = con.execute("SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id LIMIT 1", (int(conv["id"]),)).fetchone()
        tema = con.execute("SELECT id FROM temario_temas WHERE temario_id=? ORDER BY id LIMIT 1", (int(temario["id"]),)).fetchone()
        tema_id = int(tema["id"])
        cur = con.execute("INSERT INTO temario_pseudonormas(tema_id,codigo,estado) VALUES(?,?,?)", (tema_id,"PSEUDO:SMOKE_VALIDACION","ACEPTADA"))
        pn = int(cur.lastrowid)
        cur = con.execute("INSERT INTO temario_pseudoarticulos(pseudonorma_id,codigo,orden,texto_temario,tipo_contenido,sintesis,estado) VALUES(?,?,?,?,?,?,?)", (pn,"P01",1,"Contenido","JURIDICO","Síntesis validada","ACEPTADO"))
        pa = int(cur.lastrowid)
        cur = con.execute("INSERT INTO temario_pseudoarticulo_proposiciones(pseudoarticulo_id,codigo,texto,esencial,tipo,estado) VALUES(?,?,?,?,?,?)", (pa,"P1","La afirmación correcta es A",1,"JURIDICA","ACEPTADA"))
        prop = int(cur.lastrowid)
        cur = con.execute("INSERT INTO fuentes_conocimiento(tipo_fuente,organismo,nombre,identificador_oficial,localizador_oficial,estado_verificacion) VALUES(?,?,?,?,?,?)", ("OFICIAL","SMOKE","Fuente smoke","SMOKE-VAL-1","https://example.invalid","VERIFICADO"))
        fuente = int(cur.lastrowid)
        cur = con.execute("INSERT INTO temario_pseudoarticulo_respaldos(pseudoarticulo_id,proposicion_id,articulo_fuente_id,fuente_conocimiento_id,pertinencia,estado,motivo,evidencia_texto,evidencia_localizador) VALUES(?,?,NULL,?,?,?,?,?,?)", (pa,prop,fuente,"PERTINENTE","VERIFICADO","Smoke","La afirmación correcta es A","L1"))
        respaldo = int(cur.lastrowid)
        con.commit()
        return tema_id, pa, respaldo
    finally:
        con.close()


def main() -> int:
    antes = sha(DB)
    with tempfile.TemporaryDirectory(prefix="v2_validate_", ignore_cleanup_errors=True) as td:
        carpeta = Path(td)
        copia = carpeta / "oposiciones.sqlite3"
        candidatas = carpeta / "candidatas.jsonl"
        revisiones = carpeta / "revisiones.json"
        auditoria = carpeta / "auditoria.json"
        salida = carpeta / "validadas.jsonl"
        shutil.copy2(DB, copia)
        ejecutar([sys.executable, str(MIGRACION), "--db", str(copia), "--aplicar"])
        tema_id, pa, respaldo = preparar(copia)

        candidata = {
            "tipo_registro":"CANDIDATA_PREGUNTA_V2","estado":"CANDIDATA_VALIDADA",
            "tema_id":tema_id,"pseudoarticulo_id":pa,"pseudonorma":"PSEUDO:SMOKE_VALIDACION",
            "pseudoarticulo":"P01","tipo_contexto":"PSEUDO",
            "enunciado":"¿Cuál es la afirmación correcta del smoke?",
            "opcion_a":"Afirmación correcta.","opcion_b":"Falsa B.","opcion_c":"Falsa C.","opcion_d":"Falsa D.",
            "respuesta_correcta":"A","explicacion":"La evidencia respalda A.","respaldo_ids":[respaldo]
        }
        candidatas.write_text(json.dumps(candidata, ensure_ascii=False)+"\n", encoding="utf-8")
        rev_ok = {"aprobable":True,"correccion":"OK","distractores":"OK","usa_informacion_externa":False,"dificultad":"ALTA","respaldos_suficientes":True,"observaciones":[]}
        revisiones.write_text(json.dumps([rev_ok, rev_ok], ensure_ascii=False), encoding="utf-8")
        aud_ok = {"respuesta_elegida":"A","opcion_a":"CORRECTA","opcion_b":"FALSA","opcion_c":"FALSA","opcion_d":"FALSA","respuesta_unica":True,"usa_informacion_externa":False,"dificultad":"ALTA","observaciones":[]}
        auditoria.write_text(json.dumps(aud_ok, ensure_ascii=False), encoding="utf-8")
        ejecutar([sys.executable,str(VALIDADOR),"--db",str(copia),"--candidatas",str(candidatas),"--salida",str(salida),"--revisiones-fixture",str(revisiones),"--auditoria-fixture",str(auditoria)])
        resultado = json.loads(salida.read_text(encoding="utf-8").strip())
        if resultado["estado"] != "VALIDADA_AUDITADA_V2":
            raise RuntimeError("La candidata válida no quedó VALIDADA_AUDITADA_V2.")

        # Un auditor ciego que elige otra respuesta debe provocar rechazo.
        aud_ko = dict(aud_ok)
        aud_ko["respuesta_elegida"] = "B"
        aud_ko["opcion_a"] = "FALSA"
        aud_ko["opcion_b"] = "CORRECTA"
        auditoria.write_text(json.dumps(aud_ko, ensure_ascii=False), encoding="utf-8")
        ejecutar([sys.executable,str(VALIDADOR),"--db",str(copia),"--candidatas",str(candidatas),"--salida",str(salida),"--revisiones-fixture",str(revisiones),"--auditoria-fixture",str(auditoria)])
        resultado_ko = json.loads(salida.read_text(encoding="utf-8").strip())
        if resultado_ko["estado"] != "RECHAZADA_VALIDACION_V2":
            raise RuntimeError("La discrepancia del auditor ciego no provocó rechazo.")

    if sha(DB) != antes:
        raise RuntimeError("La BD real cambió durante el smoke.")
    print("="*78)
    print("SMOKE VALIDACION CANDIDATAS V2: OK")
    print("="*78)
    print("Doble revisión independiente........ OK")
    print("Auditoría ciega..................... OK")
    print("Discrepancia ciega rechazada........ OK")
    print("Solo respaldos verificados.......... OK")
    print("IA.................................. NO EJECUTADA")
    print("BD real............................. SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
