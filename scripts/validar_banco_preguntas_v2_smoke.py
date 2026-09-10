"""Smoke test aislado de pregunta_contextos_v2 + banco PSEUDO/MIXTA."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import mantener_banco_preguntas as legacy

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
CARPETA = RAIZ / "auditorias" / "constructor_v2" / "smoke_banco_v2"
COPIA = CARPETA / "oposiciones_banco_v2_smoke.sqlite3"
MIGRACION = RAIZ / "scripts" / "migrar_persistencia_constructor_v2.py"
BANCO_V2 = RAIZ / "scripts" / "mantener_banco_preguntas_v2.py"
VALIDADOR_V2 = RAIZ / "scripts" / "validacion_completa_v2.py"
SINCRONIZADOR_V2 = RAIZ / "scripts" / "sincronizar_bancos_v2.py"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def ejecutar(args: list[str]) -> None:
    print("\n" + "=" * 78)
    print(" ".join(args))
    print("=" * 78)
    r = subprocess.run(args, cwd=RAIZ, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"Comando falló con código {r.returncode}")


def preparar_datos() -> tuple[int, int, int, int]:
    with sqlite3.connect(COPIA) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        conv = con.execute("SELECT id FROM convocatorias WHERE codigo='C1-01_58_26'").fetchone()
        if conv is None:
            raise RuntimeError("No existe C1-01_58_26 para el smoke.")
        cid = int(conv["id"])
        temario = con.execute("SELECT id FROM temarios WHERE convocatoria_id=?", (cid,)).fetchone()
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
        tema_id = int(tema["id"])
        ref = con.execute(
            """
            SELECT id FROM temario_referencias
            WHERE tema_id=? AND norma_id IS NOT NULL AND articulo_fuente_id IS NOT NULL
            ORDER BY id LIMIT 1
            """,
            (tema_id,),
        ).fetchone()
        if ref is None:
            raise RuntimeError("El tema del smoke no tiene referencia real utilizable.")
        ref_id = int(ref["id"])

        cur = con.execute(
            "INSERT INTO temario_pseudonormas(tema_id,codigo,estado) VALUES(?,?,?)",
            (tema_id, "SMOKE:PSEUDO", "ACEPTADA"),
        )
        pn = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO temario_pseudoarticulos(
                pseudonorma_id,codigo,orden,texto_temario,tipo_contenido,sintesis,estado
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (pn, "P01", 1, "Contenido smoke", "JURIDICO", "Síntesis smoke validada", "ACEPTADO"),
        )
        pa = int(cur.lastrowid)

        datos = (
            (
                "SMOKE V2 PSEUDO: principio general de prueba controlada",
                "La opción A expresa el contenido correcto del supuesto PSEUDO",
                "La opción B contradice de forma completa el supuesto PSEUDO",
                "La opción C introduce una regla inexistente en el supuesto PSEUDO",
                "La opción D atribuye el contenido a un órgano ajeno al supuesto PSEUDO",
                "PSEUDOARTICULO_V2",
            ),
            (
                "SMOKE V2 MIXTA: integración de fuente real y síntesis validada",
                "La opción A combina correctamente ambos fundamentos del caso MIXTO",
                "La opción B elimina por completo el fundamento normativo real",
                "La opción C sustituye la síntesis validada por una conclusión distinta",
                "La opción D afirma una consecuencia incompatible con ambos fundamentos",
                "MIXTO_V2",
            ),
        )
        preguntas: list[int] = []
        for enunciado,a,b,c,d,criterio in datos:
            cur = con.execute(
                """
                INSERT INTO lote_preguntas(
                    enunciado,opcion_a,opcion_b,opcion_c,opcion_d,respuesta_correcta,
                    tipo_clasificacion,tipo_fuente,teorica_practica,criterio_inclusion_banco
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (enunciado,a,b,c,d,"A","JURIDICA","ia_generada_v2","TEORICA",criterio),
            )
            preguntas.append(int(cur.lastrowid))

        con.execute(
            """
            INSERT INTO pregunta_contextos_v2(
                pregunta_id,tema_id,tipo_contexto,pseudoarticulo_id,temario_referencia_id
            ) VALUES(?,?,?,?,NULL)
            """,
            (preguntas[0], tema_id, "PSEUDO", pa),
        )
        con.execute(
            """
            INSERT INTO pregunta_contextos_v2(
                pregunta_id,tema_id,tipo_contexto,pseudoarticulo_id,temario_referencia_id
            ) VALUES(?,?,?,?,?)
            """,
            (preguntas[1], tema_id, "MIXTA", pa, ref_id),
        )
        con.commit()
    return cid, tid, preguntas[0], preguntas[1]


def comprobar_legacy_no_captura(cid: int, tid: int, ids: set[int]) -> None:
    with sqlite3.connect(COPIA) as con:
        con.row_factory = sqlite3.Row
        refs, _, _ = legacy.cargar_referencias_juridicas(con, tid)
        existentes = legacy.cargar_existentes(con, cid)
        resultado = legacy.seleccionar_juridicas(con, cid, refs, existentes)
        capturadas = {int(x["id"]) for x in resultado["nuevas"]} & ids
        if capturadas:
            raise RuntimeError(f"El selector legacy capturó preguntas V2: {sorted(capturadas)}")


def main() -> int:
    CARPETA.mkdir(parents=True, exist_ok=True)
    antes = sha(DB)
    shutil.copy2(DB, COPIA)

    ejecutar([sys.executable, str(MIGRACION), "--db", str(COPIA), "--aplicar"])
    cid, tid, pseudo_id, mixta_id = preparar_datos()
    comprobar_legacy_no_captura(cid, tid, {pseudo_id, mixta_id})

    ejecutar([sys.executable, str(BANCO_V2), "--db", str(COPIA), "--convocatoria-id", str(cid)])
    ejecutar([sys.executable, str(BANCO_V2), "--db", str(COPIA), "--convocatoria-id", str(cid), "--guardar"])
    ejecutar([sys.executable, str(BANCO_V2), "--db", str(COPIA), "--convocatoria-id", str(cid)])

    with sqlite3.connect(COPIA) as con:
        filas = con.execute(
            """
            SELECT pregunta_id,metodo_vinculacion
            FROM banco_preguntas
            WHERE convocatoria_id=? AND pregunta_id IN (?,?)
            ORDER BY pregunta_id
            """,
            (cid, pseudo_id, mixta_id),
        ).fetchall()
        metodos = {int(i): str(m) for i, m in filas}
        if metodos.get(pseudo_id) != "PSEUDOARTICULO_V2":
            raise RuntimeError("La pregunta PSEUDO no quedó vinculada por PSEUDOARTICULO_V2.")
        if metodos.get(mixta_id) != "MIXTO_V2":
            raise RuntimeError("La pregunta MIXTA no quedó vinculada por MIXTO_V2.")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign_key_check detectó incidencias.")

    # Validación V2 con las dos preguntas ya incluidas.
    ejecutar([sys.executable, str(VALIDADOR_V2), "--db", str(COPIA)])

    # Orquestador conjunto, SOLO REVISION: debe aceptar coexistencia legacy+V2.
    ejecutar([sys.executable, str(SINCRONIZADOR_V2), "--db", str(COPIA)])

    if sha(DB) != antes:
        raise RuntimeError("La BD real cambió durante el smoke.")

    print("\n" + "=" * 78)
    print("SMOKE TEST BANCO PREGUNTAS V2: OK")
    print("=" * 78)
    print("PSEUDO -> PSEUDOARTICULO_V2........ OK")
    print("MIXTA  -> MIXTO_V2................ OK")
    print("Selector legacy no las captura..... OK")
    print("Segunda revisión/idempotencia....... OK")
    print("Validación completa V2............... OK")
    print("Orquestador conjunto revisión........ OK")
    print("foreign_key_check................... OK")
    print("BD real............................. SIN CAMBIOS")
    print(f"Copia temporal...................... {COPIA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
