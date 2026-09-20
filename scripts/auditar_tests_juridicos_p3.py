from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "db" / "oposiciones.sqlite3"
REGISTROS = ROOT / "registros"

CSV_P2_DEFAULT = REGISTROS / "auditoria_tests_juridicos_20260918_103552.csv"
CHECKPOINT_DEFAULT = REGISTROS / "auditoria_tests_juridicos_p3.jsonl"

MODEL = "gpt-5.4-nano"
EXPECTED_CANDIDATES = 309

sys.path.insert(0, str(ROOT / "scripts"))

from auditar_respuestas_juridicas import (
    construir_indice_textos,
    resolver_textos,
    options_dict,
)
from openai_api import seleccionar_fragmento_json


# ----------------------------------------------------------------------
# SEGURIDAD
# ----------------------------------------------------------------------

def comprobar_seguridad():
    sesion = os.getenv(
        "OPOCOACH_MANTENIMIENTO_SESION_ID",
        "",
    ).strip()

    if sesion:
        raise SystemExit(
            "ABORTADO: existe OPOCOACH_MANTENIMIENTO_SESION_ID. "
            "openai_api.py podria registrar costes en la BD."
        )


def abrir_bd_solo_lectura(path: Path):
    uri = path.resolve().as_uri() + "?mode=ro"

    con = sqlite3.connect(
        uri,
        uri=True,
        timeout=30,
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


# ----------------------------------------------------------------------
# CSV P2
# ----------------------------------------------------------------------

def cargar_candidatos_p2(path: Path):
    if not path.is_file():
        raise SystemExit(f"No existe el CSV P2: {path}")

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        filas = list(csv.DictReader(f))

    candidatos = []

    for fila in filas:
        if (
            fila.get("resultado_final", "").strip().upper()
            == "CONFLICTO_CONFIRMADO"
            and fila.get("confianza", "").strip().upper()
            == "ALTA"
        ):
            try:
                qid = int(fila["id"])
            except Exception as exc:
                raise SystemExit(
                    f"ID invalido en CSV P2: {fila.get('id')!r}"
                ) from exc

            candidatos.append({
                "id": qid,
                "respuesta_almacenada":
                    fila.get("respuesta_almacenada", "").strip().upper(),
                "respuesta_p2":
                    fila.get("respuesta_p2", "").strip().upper(),
            })

    ids = [x["id"] for x in candidatos]

    if len(ids) != len(set(ids)):
        raise SystemExit(
            "ABORTADO: existen IDs duplicados entre los candidatos P3."
        )

    if len(candidatos) != EXPECTED_CANDIDATES:
        raise SystemExit(
            "ABORTADO: numero inesperado de candidatos P3. "
            f"Esperados={EXPECTED_CANDIDATES}; encontrados={len(candidatos)}."
        )

    return candidatos


# ----------------------------------------------------------------------
# CHECKPOINT
# ----------------------------------------------------------------------

def cargar_checkpoint(path: Path):
    registros = {}

    if not path.exists():
        return registros

    with path.open("r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue

            try:
                dato = json.loads(linea)
                qid = int(dato["id"])
            except Exception:
                continue

            registros[qid] = dato

    return registros


def guardar_checkpoint(path: Path, dato: dict):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                dato,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        f.flush()
        os.fsync(f.fileno())


# ----------------------------------------------------------------------
# DATOS Y TEXTO OFICIAL
# ----------------------------------------------------------------------

def cargar_preguntas(con, ids):
    if not ids:
        return []

    marcas = ",".join("?" for _ in ids)

    sql = f"""
    SELECT
        id,
        enunciado,
        opcion_a,
        opcion_b,
        opcion_c,
        opcion_d,
        respuesta_correcta,
        tipo_fuente,
        nombre_norma,
        nombre_norma_normalizado,
        norma_id_normalizada,
        articulo,
        articulo_normalizado
    FROM lote_preguntas
    WHERE tipo_clasificacion='JURIDICA'
      AND tipo_fuente='tests'
      AND id IN ({marcas})
    ORDER BY id
    """

    return con.execute(sql, ids).fetchall()


def preparar_preguntas(filas, indice):
    mapa = {}
    incidencias = []

    for r in filas:
        textos = resolver_textos(indice, r)

        unicos = list(
            dict.fromkeys(
                t[3].strip()
                for t in textos
                if t[3].strip()
            )
        )

        if len(unicos) != 1:
            incidencias.append({
                "id": r["id"],
                "estado":
                    "SIN_TEXTO_OFICIAL"
                    if len(unicos) == 0
                    else "MULTIPLES_TEXTOS",
            })
            continue

        mapa[r["id"]] = (r, unicos[0])

    return mapa, incidencias


# ----------------------------------------------------------------------
# PROMPT P3
# ----------------------------------------------------------------------

def construir_prompt_p3(r, texto_oficial):
    opts = options_dict(r)

    return f"""
Actua como auditor juridico independiente de UNA pregunta tipo test.

Tu unica fuente juridica es el TEXTO OFICIAL proporcionado.

No dispones de ninguna respuesta previa ni debes intentar inferirla.
Resuelve la pregunta desde cero.

REGLAS OBLIGATORIAS:

1. Respeta exactamente la polaridad del enunciado:
   correcta, verdadera, incorrecta, falsa, NO, excepto, etc.

2. Analiza por separado y completamente A, B, C y D.

3. Para cada opcion decide si su proposicion completa esta:
   RESPALDADA
   CONTRADICHA
   NO_DETERMINABLE

4. No elijas una opcion por parecido textual.

5. Una adicion, exclusion, negacion, conjuncion, alternativa,
   cantidad, plazo, requisito o excepcion puede cambiar el sentido
   juridico de una opcion. Debes comprobarlo expresamente.

6. No uses conocimiento juridico externo ni completes el texto
   proporcionado con memoria o conocimientos generales.

7. Solo responde A, B, C o D cuando el TEXTO OFICIAL permita
   determinar una unica respuesta conforme a la polaridad del
   enunciado.

8. Si falta informacion, hay mas de una respuesta posible o no
   puedes decidir con seguridad exclusivamente con el texto,
   responde "?".

Devuelve EXCLUSIVAMENTE JSON:

{{
  "opciones": {{
    "A": {{
      "estado": "RESPALDADA|CONTRADICHA|NO_DETERMINABLE",
      "motivo": "explicacion breve"
    }},
    "B": {{
      "estado": "RESPALDADA|CONTRADICHA|NO_DETERMINABLE",
      "motivo": "explicacion breve"
    }},
    "C": {{
      "estado": "RESPALDADA|CONTRADICHA|NO_DETERMINABLE",
      "motivo": "explicacion breve"
    }},
    "D": {{
      "estado": "RESPALDADA|CONTRADICHA|NO_DETERMINABLE",
      "motivo": "explicacion breve"
    }}
  }},
  "respuesta": "A|B|C|D|?",
  "confianza": "ALTA|MEDIA|BAJA",
  "fundamento": "razon juridica decisiva basada exclusivamente en el texto oficial"
}}

TEXTO OFICIAL:

{texto_oficial}

PREGUNTA:

{r["enunciado"]}

A: {opts["A"]}
B: {opts["B"]}
C: {opts["C"]}
D: {opts["D"]}
""".strip()


# ----------------------------------------------------------------------
# P3
# ----------------------------------------------------------------------

def clasificar_p3(respuesta, almacenada, p2):
    if respuesta == "?":
        return "INDETERMINADA"

    if respuesta == p2:
        return "CONFIRMA_P2"

    if respuesta == almacenada:
        return "RECUPERA_ALMACENADA"

    return "TERCERA_RESPUESTA"


def ejecutar_p3(
    candidatos,
    mapa_preguntas,
    checkpoint_path,
    checkpoint,
):
    total = len(candidatos)

    for numero, candidato in enumerate(candidatos, 1):
        qid = candidato["id"]

        if qid in checkpoint:
            print(f"P3 {numero}/{total} - ID {qid}: ya procesado")
            continue

        if qid not in mapa_preguntas:
            print(
                f"P3 {numero}/{total} - ID {qid}: "
                "SIN TEXTO OFICIAL UNICO"
            )
            continue

        r, texto = mapa_preguntas[qid]

        prompt = construir_prompt_p3(
            r,
            texto,
        )

        print()
        print(
            f"P3 {numero}/{total} - ID {qid}"
        )

        try:
            res = seleccionar_fragmento_json(
                prompt=prompt,
                modelo=MODEL,
                operacion="auditoria_tests_juridicos_p3",
                max_output_tokens=1000,
            )
        except Exception as exc:
            print(
                f"ERROR API P3 ID {qid}:",
                repr(exc),
            )
            print(
                "No registrado; permanece pendiente."
            )
            continue

        respuesta = str(
            res.get("respuesta", "?")
        ).strip().upper()

        if respuesta not in {
            "A", "B", "C", "D", "?"
        }:
            respuesta = "?"

        confianza = str(
            res.get("confianza", "")
        ).strip().upper()

        if confianza not in {
            "ALTA", "MEDIA", "BAJA"
        }:
            confianza = ""

        fundamento = str(
            res.get("fundamento", "")
        ).strip()

        almacenada = candidato[
            "respuesta_almacenada"
        ]
        p2 = candidato["respuesta_p2"]

        resultado = clasificar_p3(
            respuesta,
            almacenada,
            p2,
        )

        dato = {
            "fase": "P3",
            "id": qid,
            "respuesta_almacenada": almacenada,
            "respuesta_p2": p2,
            "respuesta_p3": respuesta,
            "resultado_p3": resultado,
            "confianza_p3": confianza,
            "fundamento_p3": fundamento,
            "fecha": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

        guardar_checkpoint(
            checkpoint_path,
            dato,
        )
        checkpoint[qid] = dato


# ----------------------------------------------------------------------
# CSV Y RESUMEN
# ----------------------------------------------------------------------

def exportar_csv(
    candidatos,
    mapa_preguntas,
    checkpoint,
    salida,
):
    salida.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    campos = [
        "id",
        "norma",
        "articulo",
        "respuesta_almacenada",
        "respuesta_p2",
        "respuesta_p3",
        "resultado_p3",
        "confianza_p3",
        "fundamento_p3",
        "enunciado",
        "opcion_a",
        "opcion_b",
        "opcion_c",
        "opcion_d",
    ]

    with salida.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=campos,
        )
        w.writeheader()

        for candidato in candidatos:
            qid = candidato["id"]
            dato = checkpoint.get(qid, {})

            if qid in mapa_preguntas:
                r, _texto = mapa_preguntas[qid]
            else:
                r = None

            w.writerow({
                "id": qid,
                "norma":
                    (
                        r["nombre_norma_normalizado"]
                        or r["nombre_norma"]
                        or ""
                    )
                    if r else "",
                "articulo":
                    (
                        r["articulo_normalizado"]
                        or r["articulo"]
                        or ""
                    )
                    if r else "",
                "respuesta_almacenada":
                    candidato["respuesta_almacenada"],
                "respuesta_p2":
                    candidato["respuesta_p2"],
                "respuesta_p3":
                    dato.get("respuesta_p3", ""),
                "resultado_p3":
                    dato.get("resultado_p3", "PENDIENTE"),
                "confianza_p3":
                    dato.get("confianza_p3", ""),
                "fundamento_p3":
                    dato.get("fundamento_p3", ""),
                "enunciado":
                    r["enunciado"] if r else "",
                "opcion_a":
                    r["opcion_a"] if r else "",
                "opcion_b":
                    r["opcion_b"] if r else "",
                "opcion_c":
                    r["opcion_c"] if r else "",
                "opcion_d":
                    r["opcion_d"] if r else "",
            })


def mostrar_resumen(candidatos, checkpoint, incidencias):
    estados = Counter()

    for candidato in candidatos:
        dato = checkpoint.get(
            candidato["id"]
        )

        if dato:
            estados[
                dato.get(
                    "resultado_p3",
                    "PENDIENTE",
                )
            ] += 1
        else:
            estados["PENDIENTE"] += 1

    print()
    print("=" * 76)
    print("RESUMEN P3")
    print("=" * 76)
    print(
        "Candidatos P3.........................:",
        len(candidatos),
    )

    for estado in (
        "CONFIRMA_P2",
        "RECUPERA_ALMACENADA",
        "TERCERA_RESPUESTA",
        "INDETERMINADA",
        "PENDIENTE",
    ):
        print(
            f"{estado:35}: "
            f"{estados.get(estado, 0)}"
        )

    print(
        "Incidencias texto oficial.............:",
        len(incidencias),
    )


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--db",
        type=Path,
        default=DB_DEFAULT,
    )
    ap.add_argument(
        "--csv-p2",
        type=Path,
        default=CSV_P2_DEFAULT,
    )
    ap.add_argument(
        "--checkpoint",
        type=Path,
        default=CHECKPOINT_DEFAULT,
    )
    ap.add_argument(
        "--salida",
        type=Path,
        default=None,
    )
    ap.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Procesa solo los primeros N candidatos P3. Util para pruebas controladas.",
    )

    args = ap.parse_args()

    if args.limite is not None and args.limite <= 0:
        raise SystemExit("--limite debe ser un entero positivo.")

    comprobar_seguridad()

    if not args.db.is_file():
        raise SystemExit(
            f"No existe la BD: {args.db}"
        )

    REGISTROS.mkdir(
        parents=True,
        exist_ok=True,
    )

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    salida = (
        args.salida
        if args.salida is not None
        else REGISTROS
        / f"auditoria_tests_juridicos_p3_{stamp}.csv"
    )

    print("=" * 76)
    print("AUDITORIA TESTS JURIDICOS - P3")
    print("=" * 76)
    print(
        "Modelo...............................:",
        MODEL,
    )
    print(
        "CSV P2...............................:",
        args.csv_p2,
    )
    print(
        "BD...................................:",
        args.db,
    )
    print(
        "Checkpoint...........................:",
        args.checkpoint,
    )
    print()
    print("BD ABIERTA EN MODO SOLO LECTURA.")

    candidatos = cargar_candidatos_p2(
        args.csv_p2
    )

    if args.limite is not None:
        candidatos = candidatos[:args.limite]
        print(
            "MODO PRUEBA - limite..................:",
            args.limite,
        )

    con = abrir_bd_solo_lectura(
        args.db
    )

    try:
        indice = construir_indice_textos(
            con
        )

        ids = [
            x["id"]
            for x in candidatos
        ]

        filas = cargar_preguntas(
            con,
            ids,
        )

        if len(filas) != len(candidatos):
            encontrados = {
                r["id"] for r in filas
            }
            faltantes = sorted(
                set(ids) - encontrados
            )
            raise SystemExit(
                "ABORTADO: no se localizaron en la BD "
                "todos los candidatos P3 seleccionados. "
                f"Esperados={len(candidatos)}; "
                f"encontrados={len(filas)}; "
                f"faltantes={faltantes[:20]}"
            )

        mapa, incidencias = preparar_preguntas(
            filas,
            indice,
        )

        print(
            "Candidatos ALTA.......................:",
            len(candidatos),
        )
        print(
            "Con texto oficial unico...............:",
            len(mapa),
        )
        print(
            "Incidencias texto oficial.............:",
            len(incidencias),
        )

        checkpoint = cargar_checkpoint(
            args.checkpoint
        )

        print(
            "Registros checkpoint cargados.........:",
            len(checkpoint),
        )

        ejecutar_p3(
            candidatos,
            mapa,
            args.checkpoint,
            checkpoint,
        )

        exportar_csv(
            candidatos,
            mapa,
            checkpoint,
            salida,
        )

        mostrar_resumen(
            candidatos,
            checkpoint,
            incidencias,
        )

    finally:
        con.close()

    print()
    print(
        "CSV P3................................:",
        salida,
    )
    print(
        "Checkpoint............................:",
        args.checkpoint,
    )
    print()
    print("BD MODIFICADA: NO")


if __name__ == "__main__":
    main()
