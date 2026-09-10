"""Smoke aislado de candidata PSEUDO V2 -> lote_preguntas + pregunta_contextos_v2."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import mantener_banco_preguntas as legacy

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
MIGRACION = RAIZ / "scripts" / "migrar_persistencia_constructor_v2.py"
PUBLICADOR = RAIZ / "scripts" / "publicar_candidatas_preguntas_v2.py"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def ejecutar(args: list[str], esperar_ok: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(args, cwd=RAIZ, check=False, text=True, capture_output=True)
    if esperar_ok and r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise RuntimeError(f"Comando falló con código {r.returncode}")
    return r


def preparar_datos(copia: Path) -> tuple[str, int, int, int, int, int]:
    con = sqlite3.connect(copia)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        conv = con.execute(
            "SELECT id,codigo FROM convocatorias WHERE codigo='C1-01_58_26'"
        ).fetchone()
        if conv is None:
            raise RuntimeError("No existe C1-01_58_26 para el smoke.")
        cid = int(conv["id"])
        codigo = str(conv["codigo"])
        temario = con.execute(
            "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id LIMIT 1",
            (cid,),
        ).fetchone()
        if temario is None:
            raise RuntimeError("C1 no tiene temario.")
        tid = int(temario["id"])
        tema = con.execute(
            """
            SELECT id FROM temario_temas
            WHERE temario_id=?
            ORDER BY CASE WHEN UPPER(parte)='GENERAL' THEN 0 ELSE 1 END, numero_tema,id
            LIMIT 1
            """,
            (tid,),
        ).fetchone()
        if tema is None:
            raise RuntimeError("C1 no tiene temas.")
        tema_id = int(tema["id"])

        cur = con.execute(
            "INSERT INTO temario_pseudonormas(tema_id,codigo,estado) VALUES(?,?,?)",
            (tema_id, "PSEUDO:SMOKE_PUBLICACION", "ACEPTADA"),
        )
        pn = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO temario_pseudoarticulos(
                pseudonorma_id,codigo,orden,texto_temario,tipo_contenido,sintesis,estado
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (pn, "P01", 1, "Contenido smoke publicación", "JURIDICO", "Síntesis validada", "ACEPTADO"),
        )
        pa = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO temario_pseudoarticulo_proposiciones(
                pseudoarticulo_id,codigo,texto,esencial,tipo,estado
            ) VALUES(?,?,?,?,?,?)
            """,
            (pa, "SMOKE-P01", "Proposición smoke", 1, "JURIDICA", "ACEPTADA"),
        )
        prop = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO fuentes_conocimiento(
                tipo_fuente,organismo,nombre,identificador_oficial,localizador_oficial,estado_verificacion
            ) VALUES(?,?,?,?,?,?)
            """,
            ("OFICIAL", "SMOKE", "Fuente smoke", "SMOKE-FUENTE-1", "https://example.invalid/smoke", "VERIFICADO"),
        )
        fuente = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO temario_pseudoarticulo_respaldos(
                pseudoarticulo_id,proposicion_id,articulo_fuente_id,fuente_conocimiento_id,
                pertinencia,estado,motivo,evidencia_texto,evidencia_localizador
            ) VALUES(?,?,NULL,?,?,?,?,?,?)
            """,
            (pa, prop, fuente, "PERTINENTE", "VERIFICADO", "Smoke", "Evidencia smoke", "Localizador smoke"),
        )
        respaldo = int(cur.lastrowid)
        con.commit()
        return codigo, cid, tid, tema_id, pa, respaldo
    finally:
        con.close()


def comprobar_legacy_no_captura(copia: Path, cid: int, tid: int, pregunta_id: int) -> None:
    con = sqlite3.connect(copia)
    try:
        con.row_factory = sqlite3.Row
        refs, _, _ = legacy.cargar_referencias_juridicas(con, tid)
        existentes = legacy.cargar_existentes(con, cid)
        resultado = legacy.seleccionar_juridicas(con, cid, refs, existentes)
        capturadas = {int(x["id"]) for x in resultado["nuevas"]}
        if pregunta_id in capturadas:
            raise RuntimeError("El selector legacy capturó la pregunta PSEUDO V2.")
    finally:
        con.close()


def consultar_una(copia: Path, sql: str, params: tuple = ()): 
    con = sqlite3.connect(copia)
    try:
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


def main() -> int:
    antes = sha(DB)
    with tempfile.TemporaryDirectory(prefix="v2_publish_") as td:
        carpeta = Path(td)
        copia = carpeta / "oposiciones.sqlite3"
        candidatas = carpeta / "candidatas.jsonl"
        malas = carpeta / "candidatas_malas.jsonl"
        shutil.copy2(DB, copia)

        ejecutar([sys.executable, str(MIGRACION), "--db", str(copia), "--aplicar"])
        codigo, cid, tid, tema_id, pa, respaldo = preparar_datos(copia)

        candidata = {
            "tipo_registro": "CANDIDATA_PREGUNTA_V2",
            "estado": "CANDIDATA_VALIDADA",
            "tema_id": tema_id,
            "pseudoarticulo_id": pa,
            "pseudonorma": "PSEUDO:SMOKE_PUBLICACION",
            "pseudoarticulo": "P01",
            "tipo_contexto": "PSEUDO",
            "enunciado": "¿Qué afirmación corresponde al contenido verificado del smoke V2?",
            "opcion_a": "La afirmación correcta del smoke.",
            "opcion_b": "Una afirmación falsa B.",
            "opcion_c": "Una afirmación falsa C.",
            "opcion_d": "Una afirmación falsa D.",
            "respuesta_correcta": "A",
            "explicacion": "Se justifica por el respaldo verificado del smoke.",
            "respaldo_ids": [respaldo],
        }
        candidatas.write_text(json.dumps(candidata, ensure_ascii=False) + "\n", encoding="utf-8")

        ejecutar([
            sys.executable, str(PUBLICADOR), "--db", str(copia),
            "--codigo", codigo, "--candidatas", str(candidatas),
        ])
        n0 = consultar_una(
            copia,
            "SELECT COUNT(*) FROM lote_preguntas WHERE enunciado=?",
            (candidata["enunciado"],),
        )[0]
        if n0 != 0:
            raise RuntimeError("SOLO REVISION modificó lote_preguntas.")

        ejecutar([
            sys.executable, str(PUBLICADOR), "--db", str(copia),
            "--codigo", codigo, "--candidatas", str(candidatas), "--aplicar",
        ])
        con = sqlite3.connect(copia)
        try:
            con.row_factory = sqlite3.Row
            preguntas = con.execute(
                """
                SELECT id,tipo_clasificacion,tipo_fuente,teorica_practica,
                       criterio_inclusion_banco,norma_id_normalizada,articulo_normalizado,
                       tipo_norma_normalizado,nombre_norma_normalizado
                FROM lote_preguntas WHERE enunciado=?
                """,
                (candidata["enunciado"],),
            ).fetchall()
            if len(preguntas) != 1:
                raise RuntimeError(f"Esperada 1 pregunta publicada; encontradas={len(preguntas)}")
            p = preguntas[0]
            pregunta_id = int(p["id"])
            if str(p["tipo_clasificacion"]) != "JURIDICA":
                raise RuntimeError("tipo_clasificacion incorrecto.")
            if str(p["tipo_fuente"]) != "ia_generada_v2":
                raise RuntimeError("tipo_fuente incorrecto.")
            if str(p["teorica_practica"]) != "TEORICA":
                raise RuntimeError("teorica_practica incorrecto.")
            if str(p["criterio_inclusion_banco"]) != "PSEUDOARTICULO_V2":
                raise RuntimeError("criterio_inclusion_banco incorrecto.")
            if p["norma_id_normalizada"] is not None:
                raise RuntimeError("norma_id_normalizada debe ser NULL.")
            if str(p["articulo_normalizado"] or "").strip():
                raise RuntimeError("articulo_normalizado debe quedar vacío.")
            if str(p["tipo_norma_normalizado"] or "").strip():
                raise RuntimeError("tipo_norma_normalizado debe quedar vacío.")
            if str(p["nombre_norma_normalizado"] or "").strip():
                raise RuntimeError("nombre_norma_normalizado debe quedar vacío.")
            ctx = con.execute(
                """
                SELECT tema_id,tipo_contexto,pseudoarticulo_id,temario_referencia_id
                FROM pregunta_contextos_v2 WHERE pregunta_id=?
                """,
                (pregunta_id,),
            ).fetchall()
            if len(ctx) != 1:
                raise RuntimeError("No existe exactamente un contexto V2 para la pregunta.")
            c = ctx[0]
            if (
                int(c["tema_id"]) != tema_id
                or str(c["tipo_contexto"]) != "PSEUDO"
                or int(c["pseudoarticulo_id"]) != pa
                or c["temario_referencia_id"] is not None
            ):
                raise RuntimeError("El contexto V2 publicado no coincide con la candidata.")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("foreign_key_check detectó incidencias.")
        finally:
            con.close()

        comprobar_legacy_no_captura(copia, cid, tid, pregunta_id)

        ejecutar([
            sys.executable, str(PUBLICADOR), "--db", str(copia),
            "--codigo", codigo, "--candidatas", str(candidatas), "--aplicar",
        ])
        con = sqlite3.connect(copia)
        try:
            n_preg = con.execute(
                "SELECT COUNT(*) FROM lote_preguntas WHERE enunciado=?",
                (candidata["enunciado"],),
            ).fetchone()[0]
            n_ctx = con.execute(
                "SELECT COUNT(*) FROM pregunta_contextos_v2 WHERE pregunta_id=?",
                (pregunta_id,),
            ).fetchone()[0]
        finally:
            con.close()
        if n_preg != 1 or n_ctx != 1:
            raise RuntimeError("La segunda publicación no fue idempotente.")

        mala = dict(candidata)
        mala["enunciado"] = candidata["enunciado"] + " CASO MALO"
        mala["respaldo_ids"] = [respaldo + 999999]
        malas.write_text(json.dumps(mala, ensure_ascii=False) + "\n", encoding="utf-8")
        r = ejecutar([
            sys.executable, str(PUBLICADOR), "--db", str(copia),
            "--codigo", codigo, "--candidatas", str(malas), "--aplicar",
        ], esperar_ok=False)
        if r.returncode == 0:
            raise RuntimeError("Una candidata con respaldo inválido fue aceptada.")

    if sha(DB) != antes:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("=" * 78)
    print("SMOKE PUBLICACION CANDIDATAS V2: OK")
    print("=" * 78)
    print("SOLO REVISION..................... OK")
    print("Candidata -> lote_preguntas....... OK")
    print("pregunta_contextos_v2............. OK")
    print("Trazabilidad respaldo validada.... OK")
    print("Legacy no captura PSEUDO.......... OK")
    print("Idempotencia 2 ejecuciones........ OK")
    print("Respaldo inválido rechazado........ OK")
    print("foreign_key_check.................. OK")
    print("BD real............................ SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
