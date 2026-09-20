from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "db" / "oposiciones.sqlite3"
REGISTROS = ROOT / "registros"

MODEL = "gpt-5.4-nano"
MAX_LOTE = 20

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
        ""
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

    # Defensa adicional
    con.execute("PRAGMA query_only = ON")

    return con


# ----------------------------------------------------------------------
# CHECKPOINT
# ----------------------------------------------------------------------

def cargar_checkpoint(path: Path):
    registros = {}

    if not path.exists():
        return registros

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        for linea in f:

            linea = linea.strip()

            if not linea:
                continue

            try:
                dato = json.loads(linea)
            except Exception:
                continue

            clave = (
                dato.get("fase"),
                int(dato.get("id")),
            )

            registros[clave] = dato

    return registros


def guardar_checkpoint(path: Path, dato: dict):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "a",
        encoding="utf-8",
    ) as f:

        f.write(
            json.dumps(
                dato,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

        f.flush()

        # Asegura persistencia antes de continuar.
        os.fsync(f.fileno())


# ----------------------------------------------------------------------
# DATOS
# ----------------------------------------------------------------------

def cargar_preguntas(
    con,
    ids: list[int] | None,
):
    sql = """
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
    """

    params = []

    if ids:
        marcas = ",".join(
            "?" for _ in ids
        )

        sql += f" AND id IN ({marcas})"

        params.extend(ids)

    sql += " ORDER BY id"

    return con.execute(
        sql,
        params,
    ).fetchall()


def preparar_verificables(
    filas,
    indice,
):
    verificables = []
    no_verificables = []

    for r in filas:

        textos = resolver_textos(
            indice,
            r,
        )

        unicos = list(
            dict.fromkeys(
                t[3].strip()
                for t in textos
                if t[3].strip()
            )
        )

        if len(unicos) != 1:

            no_verificables.append({
                "id": r["id"],
                "estado": (
                    "SIN_TEXTO_OFICIAL"
                    if len(unicos) == 0
                    else "MULTIPLES_TEXTOS"
                ),
            })

            continue

        verificables.append(
            (r, unicos[0])
        )

    return (
        verificables,
        no_verificables,
    )


# ----------------------------------------------------------------------
# PROMPT PRIMERA PASADA
# ----------------------------------------------------------------------

def construir_prompt_lote(
    texto_oficial,
    lote,
):
    preguntas = []

    for r in lote:

        preguntas.append(
            f"""ID {r["id"]}
ENUNCIADO: {r["enunciado"]}
A: {r["opcion_a"]}
B: {r["opcion_b"]}
C: {r["opcion_c"]}
D: {r["opcion_d"]}"""
        )

    bloque = "\n\n".join(
        preguntas
    )

    return f"""
Actua como auditor juridico de preguntas tipo test.

Resuelve TODAS las preguntas exclusivamente a partir del TEXTO
OFICIAL proporcionado.

REGLAS OBLIGATORIAS:

1. No conoces la respuesta almacenada.
2. Respeta exactamente la polaridad del enunciado:
   correcta, verdadera, incorrecta, falsa, NO, excepto, etc.
3. Examina completamente A, B, C y D.
4. Una opcion solo es correcta si TODA su proposicion es compatible
   con el texto oficial.
5. Cualquier adicion que cambie el significado invalida la opcion.
   Presta especial atencion a "y", "o", "no", publico/privado,
   cantidades, plazos, requisitos, excepciones y enumeraciones.
6. No elijas una opcion por simple parecido textual.
7. Si el texto oficial no permite resolver con seguridad,
   responde "?".
8. Si no existe una unica respuesta determinable,
   responde "?".
9. Debes devolver exactamente una respuesta para cada ID recibido.

Devuelve EXCLUSIVAMENTE JSON compacto:

{{"resultados":[{{"id":123,"r":"A"}},{{"id":456,"r":"?"}}]}}

Sin explicaciones ni texto adicional.

TEXTO OFICIAL:

{texto_oficial}

PREGUNTAS:

{bloque}
""".strip()


# ----------------------------------------------------------------------
# PRIMERA PASADA
# ----------------------------------------------------------------------

def ejecutar_primera_pasada(
    verificables,
    checkpoint_path,
    checkpoint,
):
    grupos = defaultdict(list)

    for r, texto in verificables:
        grupos[texto].append(r)

    total_lotes = sum(
        (len(grupo) + MAX_LOTE - 1)
        // MAX_LOTE
        for grupo in grupos.values()
    )

    numero_lote = 0

    for texto, grupo in grupos.items():

        for inicio in range(
            0,
            len(grupo),
            MAX_LOTE,
        ):

            lote = grupo[
                inicio:inicio + MAX_LOTE
            ]

            numero_lote += 1

            pendientes = [
                r for r in lote
                if ("P1", r["id"])
                not in checkpoint
            ]

            if not pendientes:
                print(
                    f"P1 lote "
                    f"{numero_lote}/{total_lotes}: "
                    f"ya procesado"
                )
                continue

            # Si un lote fue parcialmente
            # procesado antes de una interrupcion,
            # solo reenviamos los IDs pendientes.
            prompt = construir_prompt_lote(
                texto,
                pendientes,
            )

            ids_esperados = {
                r["id"]
                for r in pendientes
            }

            print()
            print(
                f"P1 lote "
                f"{numero_lote}/{total_lotes} "
                f"- preguntas={len(pendientes)}"
            )

            try:

                res = seleccionar_fragmento_json(
                    prompt=prompt,
                    modelo=MODEL,
                    operacion=(
                        "auditoria_tests_juridicos_p1"
                    ),
                    max_output_tokens=1000,
                )

                items = res.get(
                    "resultados",
                    []
                )

                recibidas = {}

                for item in items:

                    try:
                        qid = int(
                            item["id"]
                        )

                        respuesta = str(
                            item["r"]
                        ).strip().upper()

                    except Exception:
                        continue

                    if (
                        qid in ids_esperados
                        and respuesta
                        in {
                            "A",
                            "B",
                            "C",
                            "D",
                            "?",
                        }
                    ):
                        recibidas[qid] = (
                            respuesta
                        )

                for r in pendientes:

                    qid = r["id"]

                    respuesta = recibidas.get(
                        qid
                    )

                    if respuesta is None:
                        # No lo marcamos como procesado.
                        # Se reintentara al reanudar.
                        print(
                            f"  ID {qid}: "
                            f"SIN RESPUESTA VALIDA"
                        )
                        continue

                    almacenada = (
                        r["respuesta_correcta"]
                        or ""
                    ).strip().upper()

                    if respuesta == "?":
                        resultado = (
                            "INDETERMINADA_P1"
                        )

                    elif respuesta == almacenada:
                        resultado = (
                            "VALIDADA_1"
                        )

                    else:
                        resultado = (
                            "CONFLICTO_P1"
                        )

                    dato = {
                        "fase": "P1",
                        "id": qid,
                        "respuesta_almacenada":
                            almacenada,
                        "respuesta_auditor":
                            respuesta,
                        "resultado":
                            resultado,
                        "fecha":
                            datetime.now().isoformat(
                                timespec="seconds"
                            ),
                    }

                    guardar_checkpoint(
                        checkpoint_path,
                        dato,
                    )

                    checkpoint[
                        ("P1", qid)
                    ] = dato

            except Exception as exc:

                print(
                    "ERROR API P1:",
                    repr(exc),
                )

                print(
                    "Lote no registrado. "
                    "Sus preguntas quedan pendientes."
                )

                # No se escribe checkpoint para este lote.
                # Continuamos con los siguientes grupos.
                continue


# ----------------------------------------------------------------------
# PROMPT SEGUNDA PASADA
# ----------------------------------------------------------------------



# ----------------------------------------------------------------------
# RECUPERACION INDIVIDUAL P1
# ----------------------------------------------------------------------

def ejecutar_recuperacion_p1(
    verificables,
    checkpoint_path,
    checkpoint,
):
    pendientes = [
        (r, texto)
        for r, texto in verificables
        if ("P1", r["id"]) not in checkpoint
    ]

    print()
    print("=" * 76)
    print("RECUPERACION INDIVIDUAL P1")
    print("=" * 76)
    print(
        "Pendientes.............................:",
        len(pendientes)
    )

    for numero, (r, texto) in enumerate(pendientes, 1):
        qid = r["id"]

        prompt = construir_prompt_lote(
            texto,
            [r],
        )

        print()
        print(
            f"P1 recuperacion {numero}/{len(pendientes)} "
            f"- ID {qid}"
        )

        try:
            res = seleccionar_fragmento_json(
                prompt=prompt,
                modelo=MODEL,
                operacion="auditoria_tests_juridicos_p1_recuperacion",
                max_output_tokens=1000,
            )

            items = res.get("resultados", [])
            respuesta = None

            for item in items:
                try:
                    item_id = int(item["id"])
                    item_r = str(item["r"]).strip().upper()
                except Exception:
                    continue

                if (
                    item_id == qid
                    and item_r in {"A", "B", "C", "D", "?"}
                ):
                    respuesta = item_r
                    break

            if respuesta is None:
                print(
                    f"  ID {qid}: SIN RESPUESTA VALIDA"
                )
                continue

            almacenada = (
                r["respuesta_correcta"] or ""
            ).strip().upper()

            if respuesta == "?":
                resultado = "INDETERMINADA_P1"
            elif respuesta == almacenada:
                resultado = "VALIDADA_1"
            else:
                resultado = "CONFLICTO_P1"

            dato = {
                "fase": "P1",
                "id": qid,
                "respuesta_almacenada": almacenada,
                "respuesta_auditor": respuesta,
                "resultado": resultado,
                "fecha": datetime.now().isoformat(
                    timespec="seconds"
                ),
            }

            guardar_checkpoint(
                checkpoint_path,
                dato,
            )

            checkpoint[("P1", qid)] = dato

        except Exception as exc:
            print(
                f"ERROR API recuperacion P1 ID {qid}:",
                repr(exc),
            )
            print(
                "No registrado; permanece pendiente."
            )

def construir_prompt_estricto(
    r,
    texto_oficial,
):
    opts = options_dict(r)

    return f"""
Actua como auditor juridico estricto de UNA pregunta tipo test.

Tu unica fuente juridica es el TEXTO OFICIAL proporcionado.

Debes comprobar cada opcion A, B, C y D por separado.

REGLAS:

1. Respeta exactamente la polaridad del enunciado:
   correcta, verdadera, incorrecta, falsa, NO, excepto, etc.

2. No elijas una opcion por parecido textual.

3. Una opcion solo esta respaldada cuando TODA su proposicion
   concuerda con el texto oficial.

4. Si una opcion contiene una adicion, exclusion, negacion,
   conjuncion, alternativa, cantidad, plazo, requisito o excepcion
   que altera lo establecido por la norma, debes considerarlo.

5. Para cada opcion clasifica:
   RESPALDADA
   CONTRADICHA
   NO_DETERMINABLE

6. Solo puedes resolver A/B/C/D cuando, teniendo en cuenta la
   polaridad del enunciado, existe una unica respuesta justificable.

7. Si el texto proporcionado no basta para decidir una respuesta
   unica, usa "?".

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
# SEGUNDA PASADA
# ----------------------------------------------------------------------

def ejecutar_segunda_pasada(
    verificables,
    checkpoint_path,
    checkpoint,
):
    mapa = {
        r["id"]: (r, texto)
        for r, texto in verificables
    }

    candidatos = []

    for (fase, qid), dato in checkpoint.items():

        if fase != "P1":
            continue

        if dato.get("resultado") in {
            "CONFLICTO_P1",
            "INDETERMINADA_P1",
        }:
            candidatos.append(qid)

    candidatos = sorted(
        set(candidatos)
    )

    print()
    print("=" * 76)
    print("SEGUNDA PASADA")
    print("=" * 76)
    print(
        "Candidatas............................:",
        len(candidatos)
    )

    for numero, qid in enumerate(
        candidatos,
        1,
    ):

        if ("P2", qid) in checkpoint:
            continue

        if qid not in mapa:
            continue

        r, texto = mapa[qid]

        prompt = construir_prompt_estricto(
            r,
            texto,
        )

        print()
        print(
            f"P2 {numero}/{len(candidatos)} "
            f"- ID {qid}"
        )

        try:

            res = seleccionar_fragmento_json(
                prompt=prompt,
                modelo=MODEL,
                operacion=(
                    "auditoria_tests_juridicos_p2"
                ),
                max_output_tokens=700,
            )

            respuesta = str(
                res.get(
                    "respuesta",
                    "?",
                )
            ).strip().upper()

            confianza = str(
                res.get(
                    "confianza",
                    "",
                )
            ).strip().upper()

            fundamento = str(
                res.get(
                    "fundamento",
                    "",
                )
            ).strip()

            if respuesta not in {
                "A",
                "B",
                "C",
                "D",
                "?",
            }:
                respuesta = "?"

            almacenada = (
                r["respuesta_correcta"]
                or ""
            ).strip().upper()

            if respuesta == "?":

                resultado = (
                    "INDETERMINADA"
                )

            elif respuesta == almacenada:

                resultado = (
                    "VALIDADA_2"
                )

            else:

                resultado = (
                    "CONFLICTO_CONFIRMADO"
                )

            dato = {
                "fase": "P2",
                "id": qid,
                "respuesta_almacenada":
                    almacenada,
                "respuesta_auditor":
                    respuesta,
                "resultado":
                    resultado,
                "confianza":
                    confianza,
                "fundamento":
                    fundamento,
                "fecha":
                    datetime.now().isoformat(
                        timespec="seconds"
                    ),
            }

            guardar_checkpoint(
                checkpoint_path,
                dato,
            )

            checkpoint[
                ("P2", qid)
            ] = dato

        except Exception as exc:

            print(
                "ERROR API P2:",
                repr(exc),
            )

            print(
                "Puede reanudar "
                "posteriormente."
            )

            raise


# ----------------------------------------------------------------------
# CSV FINAL
# ----------------------------------------------------------------------

def exportar_csv(
    verificables,
    no_verificables,
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
        "respuesta_p1",
        "resultado_p1",
        "respuesta_p2",
        "resultado_final",
        "confianza",
        "fundamento",
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

        for r, _texto in verificables:

            qid = r["id"]

            p1 = checkpoint.get(
                ("P1", qid),
                {},
            )

            p2 = checkpoint.get(
                ("P2", qid),
                {},
            )

            resultado_final = (
                p2.get("resultado")
                or p1.get("resultado")
                or "PENDIENTE"
            )

            w.writerow({
                "id": qid,
                "norma":
                    r["nombre_norma_normalizado"]
                    or r["nombre_norma"]
                    or "",
                "articulo":
                    r["articulo_normalizado"]
                    or r["articulo"]
                    or "",
                "respuesta_almacenada":
                    r["respuesta_correcta"],
                "respuesta_p1":
                    p1.get(
                        "respuesta_auditor",
                        "",
                    ),
                "resultado_p1":
                    p1.get(
                        "resultado",
                        "",
                    ),
                "respuesta_p2":
                    p2.get(
                        "respuesta_auditor",
                        "",
                    ),
                "resultado_final":
                    resultado_final,
                "confianza":
                    p2.get(
                        "confianza",
                        "",
                    ),
                "fundamento":
                    p2.get(
                        "fundamento",
                        "",
                    ),
                "enunciado":
                    r["enunciado"],
                "opcion_a":
                    r["opcion_a"],
                "opcion_b":
                    r["opcion_b"],
                "opcion_c":
                    r["opcion_c"],
                "opcion_d":
                    r["opcion_d"],
            })

    # No verificables en CSV separado
    nv = salida.with_name(
        salida.stem
        + "_no_verificables.csv"
    )

    with nv.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        w = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "estado",
            ],
        )

        w.writeheader()
        w.writerows(no_verificables)

    return nv


# ----------------------------------------------------------------------
# RESUMEN
# ----------------------------------------------------------------------

def resumen(
    verificables,
    no_verificables,
    checkpoint,
):
    estados = defaultdict(int)

    for r, _ in verificables:

        qid = r["id"]

        p2 = checkpoint.get(
            ("P2", qid)
        )

        p1 = checkpoint.get(
            ("P1", qid)
        )

        if p2:
            estado = p2["resultado"]

        elif p1:
            estado = p1["resultado"]

        else:
            estado = "PENDIENTE"

        estados[estado] += 1

    print()
    print("=" * 76)
    print("RESUMEN AUDITORIA")
    print("=" * 76)

    print(
        "Verificables........................:",
        len(verificables)
    )

    for estado in sorted(estados):
        print(
            f"{estado:35}: "
            f"{estados[estado]}"
        )

    print(
        "No verificables.....................:",
        len(no_verificables)
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
        "--ids",
        nargs="*",
        type=int,
        default=None,
        help="Audita solo estos IDs.",
    )

    ap.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
    )

    ap.add_argument(
        "--salida",
        type=Path,
        default=None,
    )

    args = ap.parse_args()

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

    if args.checkpoint is None:

        if args.ids:
            nombre = (
                "auditoria_tests_control"
            )
        else:
            nombre = (
                "auditoria_tests_completa"
            )

        checkpoint_path = (
            REGISTROS
            / f"{nombre}.jsonl"
        )

    else:
        checkpoint_path = (
            args.checkpoint
        )

    if args.salida is None:
        salida = (
            REGISTROS
            / f"auditoria_tests_juridicos_{stamp}.csv"
        )
    else:
        salida = args.salida

    print("=" * 76)
    print("AUDITORIA TESTS JURIDICOS IA")
    print("=" * 76)

    print("Modelo...............................:", MODEL)
    print("Maximo preguntas/lote...............:", MAX_LOTE)
    print("BD...................................:", args.db)
    print("Checkpoint...........................:", checkpoint_path)

    if args.ids:
        print(
            "IDs seleccionados....................:",
            args.ids
        )
    else:
        print(
            "Alcance..............................:",
            "TODOS LOS TESTS JURIDICOS"
        )

    print()
    print("BD ABIERTA EN MODO SOLO LECTURA.")

    con = abrir_bd_solo_lectura(
        args.db
    )

    indice = construir_indice_textos(
        con
    )

    filas = cargar_preguntas(
        con,
        args.ids,
    )

    verificables, no_verificables = (
        preparar_verificables(
            filas,
            indice,
        )
    )

    print(
        "Preguntas encontradas...............:",
        len(filas)
    )

    print(
        "Verificables........................:",
        len(verificables)
    )

    print(
        "No verificables.....................:",
        len(no_verificables)
    )

    checkpoint = cargar_checkpoint(
        checkpoint_path
    )

    print(
        "Registros checkpoint cargados.......:",
        len(checkpoint)
    )

    ejecutar_primera_pasada(
        verificables,
        checkpoint_path,
        checkpoint,
    )

    ejecutar_recuperacion_p1(
        verificables,
        checkpoint_path,
        checkpoint,
    )

    ejecutar_segunda_pasada(
        verificables,
        checkpoint_path,
        checkpoint,
    )

    nv = exportar_csv(
        verificables,
        no_verificables,
        checkpoint,
        salida,
    )

    resumen(
        verificables,
        no_verificables,
        checkpoint,
    )

    con.close()

    print()
    print("CSV final............................:", salida)
    print("CSV no verificables.................:", nv)
    print("Checkpoint...........................:", checkpoint_path)
    print()
    print("BD MODIFICADA: NO")


if __name__ == "__main__":
    main()

