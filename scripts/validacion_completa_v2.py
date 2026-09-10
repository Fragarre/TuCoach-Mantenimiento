"""
Validación completa V2, aditiva y compatible hacia atrás.

Conserva los controles del validador consolidado y añade una excepción estricta
para preguntas PSEUDO/MIXTA únicamente cuando:
- criterio_inclusion_banco es PSEUDOARTICULO_V2 o MIXTO_V2;
- existe pregunta_contextos_v2 coherente;
- la pregunta incluida está vinculada por el método V2 correspondiente.

No modifica la base de datos.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import validacion_completa as legacy

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
SCRIPT_BANCO_V2 = RAIZ / "scripts" / "mantener_banco_preguntas_v2.py"

CRITERIOS = {
    "PSEUDO": "PSEUDOARTICULO_V2",
    "MIXTA": "MIXTO_V2",
}


def existe_tabla(con: sqlite3.Connection, nombre: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nombre,),
    ).fetchone() is not None


def contextos_v2_validos(con: sqlite3.Connection) -> tuple[set[int], list[str]]:
    validos: set[int] = set()
    errores: list[str] = []
    if not existe_tabla(con, "pregunta_contextos_v2"):
        return validos, errores

    filas = con.execute(
        """
        SELECT pc.id,pc.pregunta_id,pc.tema_id,pc.tipo_contexto,
               pc.pseudoarticulo_id,pc.temario_referencia_id,
               lp.tipo_clasificacion,lp.criterio_inclusion_banco,
               lp.norma_id_normalizada,lp.articulo_normalizado,
               lp.tipo_norma_normalizado,lp.nombre_norma_normalizado,
               pn.tema_id AS tema_pseudo,
               tr.tema_id AS tema_real
        FROM pregunta_contextos_v2 pc
        JOIN lote_preguntas lp ON lp.id=pc.pregunta_id
        LEFT JOIN temario_pseudoarticulos pa ON pa.id=pc.pseudoarticulo_id
        LEFT JOIN temario_pseudonormas pn ON pn.id=pa.pseudonorma_id
        LEFT JOIN temario_referencias tr ON tr.id=pc.temario_referencia_id
        ORDER BY pc.id
        """
    ).fetchall()

    for f in filas:
        tipo = str(f["tipo_contexto"] or "").strip().upper()
        motivos: list[str] = []
        if tipo not in CRITERIOS:
            motivos.append("TIPO_CONTEXTO")
        else:
            if str(f["criterio_inclusion_banco"] or "").strip().upper() != CRITERIOS[tipo]:
                motivos.append("CRITERIO")
        if str(f["tipo_clasificacion"] or "").strip().upper() != "JURIDICA":
            motivos.append("CLASIFICACION")
        if f["norma_id_normalizada"] is not None:
            motivos.append("NORMA_LEGACY_NO_NULA")
        if str(f["articulo_normalizado"] or "").strip():
            motivos.append("ARTICULO_LEGACY_NO_VACIO")
        if str(f["tipo_norma_normalizado"] or "").strip():
            motivos.append("TIPO_NORMA_LEGACY_NO_VACIO")
        if str(f["nombre_norma_normalizado"] or "").strip():
            motivos.append("NOMBRE_NORMA_LEGACY_NO_VACIO")
        if f["tema_pseudo"] is None or int(f["tema_pseudo"]) != int(f["tema_id"]):
            motivos.append("PSEUDO_TEMA")
        if tipo == "MIXTA":
            if f["tema_real"] is None or int(f["tema_real"]) != int(f["tema_id"]):
                motivos.append("REAL_TEMA")
        if motivos:
            errores.append(f"contexto_id={f['id']} pregunta_id={f['pregunta_id']}: {'|'.join(motivos)}")
        else:
            validos.add(int(f["pregunta_id"]))
    return validos, errores


def comprobar_sqlite_v2(db: Path) -> tuple[bool, dict[str, int | str | list[str]]]:
    datos: dict[str, int | str | list[str]] = {}
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        try:
            integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            contextos_validos, errores_contexto = contextos_v2_validos(con)

            duplicados_banco = con.execute(
                "SELECT COUNT(*) FROM (SELECT convocatoria_id,pregunta_id FROM banco_preguntas GROUP BY convocatoria_id,pregunta_id HAVING COUNT(*)>1)"
            ).fetchone()[0]
            partes_nulas = con.execute(
                "SELECT COUNT(*) FROM banco_preguntas WHERE convocatoria_parte_id IS NULL"
            ).fetchone()[0]
            dup_ex, dup_casi = legacy.auditar_duplicados_lote(con)

            incluidas = con.execute(
                """
                SELECT DISTINCT lp.id,lp.respuesta_correcta,lp.opcion_a,lp.opcion_b,lp.opcion_c,lp.opcion_d,
                                lp.tipo_clasificacion,lp.estado_vigencia,lp.norma_id_normalizada,
                                lp.articulo_normalizado,lp.tipo_norma_normalizado,lp.nombre_norma_normalizado,
                                lp.criterio_inclusion_banco,bp.metodo_vinculacion
                FROM banco_preguntas bp
                JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
                WHERE UPPER(TRIM(COALESCE(bp.estado,'')))='INCLUIDA'
                """
            ).fetchall()

            respuesta_invalida = 0
            correcta_dup = 0
            distractores_dup = 0
            obsoletas = 0
            juridicas_incompletas_no_v2 = 0
            v2_incluidas = 0
            v2_metodo_incorrecto = 0

            for f in incluidas:
                pid = int(f["id"])
                resp = str(f["respuesta_correcta"] or "").strip().upper()
                opciones = {
                    "A": legacy.normalizar_texto_duplicados(f["opcion_a"]),
                    "B": legacy.normalizar_texto_duplicados(f["opcion_b"]),
                    "C": legacy.normalizar_texto_duplicados(f["opcion_c"]),
                    "D": legacy.normalizar_texto_duplicados(f["opcion_d"]),
                }
                if resp not in {"A","B","C","D"}:
                    respuesta_invalida += 1
                else:
                    tc = opciones[resp]
                    if any(k != resp and v == tc for k,v in opciones.items()):
                        correcta_dup += 1
                    elif len(set(v for k,v in opciones.items() if k != resp)) < 3:
                        distractores_dup += 1

                if str(f["estado_vigencia"] or "").strip().upper().startswith("OBSOLETA"):
                    obsoletas += 1

                if str(f["tipo_clasificacion"] or "").strip().upper() == "JURIDICA":
                    incompleta = (
                        f["norma_id_normalizada"] is None
                        or not str(f["articulo_normalizado"] or "").strip()
                        or not str(f["tipo_norma_normalizado"] or "").strip()
                        or not str(f["nombre_norma_normalizado"] or "").strip()
                    )
                    if incompleta:
                        if pid in contextos_validos:
                            v2_incluidas += 1
                            criterio = str(f["criterio_inclusion_banco"] or "").strip().upper()
                            metodo = str(f["metodo_vinculacion"] or "").strip().upper()
                            if criterio != metodo or criterio not in {"PSEUDOARTICULO_V2","MIXTO_V2"}:
                                v2_metodo_incorrecto += 1
                        else:
                            juridicas_incompletas_no_v2 += 1

            revisiones = con.execute(
                "SELECT COUNT(*) FROM banco_preguntas WHERE UPPER(TRIM(COALESCE(estado,'')))='REVISION'"
            ).fetchone()[0]
        finally:
            con.close()
    except Exception as exc:
        return False, {"error": str(exc)}

    datos.update({
        "integrity_check": str(integridad),
        "foreign_key_errors": len(fk),
        "duplicados_banco": int(duplicados_banco),
        "duplicados_lote_exactos": int(dup_ex),
        "duplicados_lote_casi": int(dup_casi),
        "partes_nulas": int(partes_nulas),
        "respuestas_invalidas_incluidas": int(respuesta_invalida),
        "correcta_duplicada_incluida": int(correcta_dup),
        "distractores_duplicados_incluidos": int(distractores_dup),
        "obsoletas_banco": int(obsoletas),
        "juridicas_incompletas_no_v2": int(juridicas_incompletas_no_v2),
        "v2_incluidas": int(v2_incluidas),
        "v2_metodo_incorrecto": int(v2_metodo_incorrecto),
        "contextos_v2_invalidos": errores_contexto,
        "revisiones_banco": int(revisiones),
    })
    ok = (
        str(integridad).lower() == "ok"
        and not fk
        and int(duplicados_banco) == 0
        and int(dup_ex) == 0
        and int(dup_casi) == 0
        and int(partes_nulas) == 0
        and int(respuesta_invalida) == 0
        and int(correcta_dup) == 0
        and int(obsoletas) == 0
        and int(juridicas_incompletas_no_v2) == 0
        and int(v2_metodo_incorrecto) == 0
        and not errores_contexto
    )
    return ok, datos


def ejecutar_revision_banco_v2(db: Path, cid: int) -> tuple[bool, str]:
    if not SCRIPT_BANCO_V2.is_file():
        return False, "falta mantener_banco_preguntas_v2.py"
    r = subprocess.run(
        [sys.executable, str(SCRIPT_BANCO_V2), "--db", str(db), "--convocatoria-id", str(cid)],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    args = ap.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        print(f"ERROR: no existe la BD: {db}")
        return 1

    print("="*78)
    print("VALIDACION COMPLETA V2 - SOLO LECTURA")
    print("="*78)
    print(f"BD............................... {db}")

    ok, d = comprobar_sqlite_v2(db)
    if "error" in d:
        print(f"ERROR............................ {d['error']}")
        return 1

    print(f"integrity_check.................. {d['integrity_check']}")
    print(f"foreign_key_errors............... {d['foreign_key_errors']}")
    print(f"duplicados_banco................. {d['duplicados_banco']}")
    print(f"duplicados_lote_exactos.......... {d['duplicados_lote_exactos']}")
    print(f"duplicados_lote_casi............. {d['duplicados_lote_casi']}")
    print(f"partes_nulas..................... {d['partes_nulas']}")
    print(f"juridicas_incompletas_no_V2...... {d['juridicas_incompletas_no_v2']}")
    print(f"preguntas_V2_incluidas........... {d['v2_incluidas']}")
    print(f"V2_metodo_incorrecto............. {d['v2_metodo_incorrecto']}")
    print(f"contextos_V2_invalidos........... {len(d['contextos_v2_invalidos'])}")
    print(f"obsoletas_banco.................. {d['obsoletas_banco']}")

    fallos = 0 if ok else 1

    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        convocatorias = con.execute(
            "SELECT id,codigo FROM convocatorias WHERE COALESCE(activa,1)=1 ORDER BY id"
        ).fetchall()
        tiene_ctx = existe_tabla(con, "pregunta_contextos_v2")
        cids_v2 = set()
        if tiene_ctx:
            cids_v2 = {
                int(r[0]) for r in con.execute(
                    """
                    SELECT DISTINCT t.convocatoria_id
                    FROM pregunta_contextos_v2 pc
                    JOIN temario_temas tt ON tt.id=pc.tema_id
                    JOIN temarios t ON t.id=tt.temario_id
                    """
                ).fetchall()
            }

    print("\nRevisión banco V2:")
    for cid,codigo in convocatorias:
        if int(cid) not in cids_v2:
            print(f"  {cid} | {codigo:<24} SIN DATOS V2")
            continue
        ok_v2, salida = ejecutar_revision_banco_v2(db, int(cid))
        print(f"  {cid} | {codigo:<24} {'OK' if ok_v2 else 'ERROR'}")
        if not ok_v2:
            fallos += 1
            if salida:
                print(salida)

    print("\n" + "="*78)
    print("RESULTADO FINAL....................... " + ("CORRECTO" if fallos == 0 else f"ERROR ({fallos})"))
    print("="*78)
    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
