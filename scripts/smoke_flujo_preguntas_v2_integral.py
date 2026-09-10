"""Smoke integral del Punto 6: contexto -> candidata -> validación -> publicación -> banco V2.

Usa una copia temporal de la BD real. No llama a IA: toda respuesta se aporta por fixture.
La BD real debe quedar byte a byte sin cambios.
"""
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
PREPARAR = RAIZ / "scripts" / "preparar_contexto_generacion_v2.py"
GENERAR = RAIZ / "scripts" / "generar_candidatas_preguntas_v2.py"
VALIDAR = RAIZ / "scripts" / "validar_candidatas_preguntas_v2.py"
PUBLICAR = RAIZ / "scripts" / "publicar_candidatas_preguntas_v2.py"
BANCO = RAIZ / "scripts" / "mantener_banco_preguntas_v2.py"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def ejecutar(args: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(args, cwd=RAIZ, check=False, text=True, capture_output=True)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise RuntimeError(f"Comando falló con código {r.returncode}: {' '.join(args)}")
    return r


def preparar_v2(copia: Path) -> tuple[str, int, int]:
    con = sqlite3.connect(copia)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        conv = con.execute(
            "SELECT id,codigo FROM convocatorias WHERE codigo='C1-01_58_26'"
        ).fetchone()
        if conv is None:
            raise RuntimeError("No existe C1-01_58_26 para el smoke integral.")
        cid = int(conv["id"])
        codigo = str(conv["codigo"])
        temario = con.execute(
            "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id LIMIT 1",
            (cid,),
        ).fetchone()
        if temario is None:
            raise RuntimeError("C1 no tiene temario.")
        tema = con.execute(
            """
            SELECT id FROM temario_temas
            WHERE temario_id=?
            ORDER BY CASE WHEN UPPER(parte)='GENERAL' THEN 0 ELSE 1 END, numero_tema,id
            LIMIT 1
            """,
            (int(temario["id"]),),
        ).fetchone()
        if tema is None:
            raise RuntimeError("C1 no tiene temas.")
        tema_id = int(tema["id"])

        cur = con.execute(
            "INSERT INTO temario_pseudonormas(tema_id,codigo,estado) VALUES(?,?,?)",
            (tema_id, "PSEUDO:SMOKE_FLUJO_INTEGRAL", "ACEPTADA"),
        )
        pn = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO temario_pseudoarticulos(
                pseudonorma_id,codigo,orden,texto_temario,tipo_contenido,sintesis,estado
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (pn, "P01", 1, "Contenido integral V2", "JURIDICO", "Síntesis integral verificada", "ACEPTADO"),
        )
        pa = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO temario_pseudoarticulo_proposiciones(
                pseudoarticulo_id,codigo,texto,esencial,tipo,estado
            ) VALUES(?,?,?,?,?,?)
            """,
            (pa, "P01-A", "La proposición integral es correcta.", 1, "JURIDICA", "ACEPTADA"),
        )
        prop = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO fuentes_conocimiento(
                tipo_fuente,organismo,nombre,identificador_oficial,localizador_oficial,estado_verificacion
            ) VALUES(?,?,?,?,?,?)
            """,
            ("OFICIAL", "SMOKE", "Fuente integral", "SMOKE-INTEGRAL-1", "https://example.invalid/integral", "VERIFICADO"),
        )
        fuente = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO temario_pseudoarticulo_respaldos(
                pseudoarticulo_id,proposicion_id,articulo_fuente_id,fuente_conocimiento_id,
                pertinencia,estado,motivo,evidencia_texto,evidencia_localizador
            ) VALUES(?,?,NULL,?,?,?,?,?,?)
            """,
            (pa, prop, fuente, "PERTINENTE", "VERIFICADO", "Smoke integral",
             "La proposición integral es correcta y está verificada.", "Localizador integral"),
        )
        respaldo = int(cur.lastrowid)
        con.commit()
        return codigo, cid, respaldo
    finally:
        con.close()


def main() -> int:
    antes = sha(DB)
    with tempfile.TemporaryDirectory(prefix="v2_flujo_integral_") as td:
        carpeta = Path(td)
        copia = carpeta / "oposiciones.sqlite3"
        contextos = carpeta / "contextos.jsonl"
        fixture_gen = carpeta / "generacion.json"
        candidatas = carpeta / "candidatas.jsonl"
        rev = carpeta / "revisiones.json"
        aud = carpeta / "auditoria.json"
        auditadas = carpeta / "auditadas.jsonl"
        shutil.copy2(DB, copia)

        ejecutar([sys.executable, str(MIGRACION), "--db", str(copia), "--aplicar"])
        codigo, cid, respaldo = preparar_v2(copia)

        ejecutar([
            sys.executable, str(PREPARAR), "--db", str(copia),
            "--codigo", codigo, "--salida", str(contextos),
        ])
        lineas = [json.loads(x) for x in contextos.read_text(encoding="utf-8").splitlines() if x.strip()]
        propios = [x for x in lineas if x.get("pseudonorma") == "PSEUDO:SMOKE_FLUJO_INTEGRAL"]
        if len(propios) != 1:
            raise RuntimeError(f"Esperado 1 contexto integral; encontrados={len(propios)}")
        contextos.write_text(json.dumps(propios[0], ensure_ascii=False) + "\n", encoding="utf-8")

        fixture_gen.write_text(json.dumps({
            "apta": True,
            "tipo_contexto": "PSEUDO",
            "enunciado": "¿Qué afirmación coincide con la evidencia verificada del flujo integral V2?",
            "opcion_a": "La proposición integral es correcta.",
            "opcion_b": "La proposición integral es falsa.",
            "opcion_c": "No existe evidencia verificable.",
            "opcion_d": "La pseudonorma es una ley formal.",
            "respuesta_correcta": "A",
            "explicacion": "La evidencia verificada respalda expresamente la opción A.",
            "respaldo_ids": [respaldo],
            "motivo_no_apta": None,
        }, ensure_ascii=False), encoding="utf-8")

        ejecutar([
            sys.executable, str(GENERAR), "--contextos", str(contextos),
            "--salida", str(candidatas), "--respuesta-fixture", str(fixture_gen),
        ])

        revision = {
            "aprobable": True, "correccion": "OK", "distractores": "OK",
            "usa_informacion_externa": False, "dificultad": "ALTA",
            "respaldos_suficientes": True, "observaciones": [],
        }
        rev.write_text(json.dumps([revision, revision], ensure_ascii=False), encoding="utf-8")
        aud.write_text(json.dumps({
            "respuesta_elegida": "A", "opcion_a": "CORRECTA",
            "opcion_b": "FALSA", "opcion_c": "FALSA", "opcion_d": "FALSA",
            "respuesta_unica": True, "usa_informacion_externa": False,
            "dificultad": "ALTA", "observaciones": [],
        }, ensure_ascii=False), encoding="utf-8")

        ejecutar([
            sys.executable, str(VALIDAR), "--db", str(copia),
            "--candidatas", str(candidatas), "--salida", str(auditadas),
            "--revisiones-fixture", str(rev), "--auditoria-fixture", str(aud),
        ])
        validada = json.loads(auditadas.read_text(encoding="utf-8").strip())
        if validada.get("estado") != "VALIDADA_AUDITADA_V2":
            raise RuntimeError("La candidata integral no quedó VALIDADA_AUDITADA_V2.")

        ejecutar([
            sys.executable, str(PUBLICAR), "--db", str(copia), "--codigo", codigo,
            "--candidatas", str(auditadas), "--aplicar",
        ])
        ejecutar([
            sys.executable, str(BANCO), "--db", str(copia),
            "--convocatoria-id", str(cid), "--guardar",
        ])

        con = sqlite3.connect(copia)
        try:
            con.row_factory = sqlite3.Row
            fila = con.execute(
                """
                SELECT lp.id,pc.tipo_contexto,bp.metodo_vinculacion,bp.estado
                FROM lote_preguntas lp
                JOIN pregunta_contextos_v2 pc ON pc.pregunta_id=lp.id
                JOIN banco_preguntas bp ON bp.pregunta_id=lp.id AND bp.convocatoria_id=?
                WHERE lp.enunciado=?
                """,
                (cid, validada["enunciado"]),
            ).fetchall()
            if len(fila) != 1:
                raise RuntimeError(f"Esperada 1 pregunta integral en banco; encontradas={len(fila)}")
            f = fila[0]
            if str(f["tipo_contexto"]) != "PSEUDO":
                raise RuntimeError("tipo_contexto final incorrecto.")
            if str(f["metodo_vinculacion"]) != "PSEUDOARTICULO_V2":
                raise RuntimeError("Método de vinculación final incorrecto.")
            if str(f["estado"]) != "INCLUIDA":
                raise RuntimeError("La pregunta integral no quedó INCLUIDA.")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("foreign_key_check detectó incidencias.")
        finally:
            con.close()

    if sha(DB) != antes:
        raise RuntimeError("La BD real cambió durante el smoke integral.")

    print("=" * 78)
    print("SMOKE FLUJO PREGUNTAS V2 INTEGRAL: OK")
    print("=" * 78)
    print("Contexto V2....................... OK")
    print("Generación candidata.............. OK")
    print("Doble revisión + auditoría........ OK")
    print("Publicación bloqueada por contrato. OK")
    print("lote_preguntas.................... OK")
    print("pregunta_contextos_v2............. OK")
    print("Banco PSEUDOARTICULO_V2........... OK")
    print("foreign_key_check................. OK")
    print("IA................................. NO EJECUTADA")
    print("BD real............................ SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
