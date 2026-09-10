"""Genera candidatas jurídicas PSEUDO del Constructor Temario V2.

Es una vía aditiva y separada del generador jurídico REAL consolidado.
Consume contextos JSONL producidos por preparar_contexto_generacion_v2.py y
produce candidatas JSONL. No modifica SQLite, lote_preguntas ni bancos.

Por seguridad, esta primera integración solo admite PSEUDO. MIXTA requiere una
referencia real explícita y se incorporará únicamente tras validar ese contrato.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MODELO_DEFECTO = "gpt-5.4-nano"


def leer_jsonl(path: Path) -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for numero, linea in enumerate(f, 1):
            if not linea.strip():
                continue
            try:
                dato = json.loads(linea)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"JSON inválido en {path}, línea {numero}: {exc}") from exc
            if not isinstance(dato, dict):
                raise RuntimeError(f"La línea {numero} de {path} no contiene un objeto JSON.")
            salida.append(dato)
    return salida


def validar_contexto(ctx: dict[str, Any]) -> None:
    if ctx.get("tipo_registro") != "CONTEXTO_GENERACION_PSEUDO_V2":
        raise RuntimeError("El registro no es un contexto de generación PSEUDO V2.")
    if str(ctx.get("tipo_contexto_propuesto") or "").upper() != "PSEUDO":
        raise RuntimeError("Esta fase solo admite contexto PSEUDO; MIXTA no se infiere.")
    for campo in ("tema_id", "pseudoarticulo_id", "pseudonorma", "pseudoarticulo", "texto_oficial_tema", "sintesis"):
        if ctx.get(campo) in (None, ""):
            raise RuntimeError(f"Falta campo obligatorio del contexto: {campo}")
    respaldos = ctx.get("respaldos_verificados")
    if not isinstance(respaldos, list) or not respaldos:
        raise RuntimeError("El contexto no contiene respaldos verificados.")


def evidencia_para_prompt(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    evidencias: list[dict[str, Any]] = []
    for r in ctx["respaldos_verificados"]:
        if not isinstance(r, dict) or r.get("respaldo_id") is None:
            raise RuntimeError("Respaldo V2 inválido o sin identificador.")
        fuente = r.get("fuente") or {}
        evidencias.append({
            "respaldo_id": int(r["respaldo_id"]),
            "proposicion_codigo": r.get("proposicion_codigo"),
            "proposicion_texto": r.get("proposicion_texto"),
            "fuente": fuente,
            "evidencia_texto": r.get("evidencia_texto"),
            "evidencia_localizador": r.get("evidencia_localizador"),
        })
    return evidencias


def construir_prompt(ctx: dict[str, Any]) -> str:
    evidencia = evidencia_para_prompt(ctx)
    return f"""Eres un generador de preguntas tipo test jurídicas para oposiciones españolas.

Genera UNA única pregunta de dificultad alta basada EXCLUSIVAMENTE en el contexto y evidencias proporcionados.

REGLAS OBLIGATORIAS:
- Cuatro opciones A, B, C y D; una sola correcta.
- No inventes normas, artículos, fechas, competencias ni requisitos.
- No presentes {ctx['pseudonorma']} ni {ctx['pseudoarticulo']} como si fueran una norma o artículo legal.
- La pregunta debe poder justificarse con uno o más respaldos verificados incluidos abajo.
- Si la evidencia no permite una pregunta inequívoca, devuelve apta=false y explica brevemente el motivo.
- No añadas una referencia legal que no figure expresamente en la evidencia.
- tipo_contexto debe ser exactamente PSEUDO.
- respaldo_ids debe contener únicamente IDs de respaldos usados realmente para justificar la respuesta correcta.

Devuelve SOLO JSON con esta forma:
{{
  "apta": true,
  "tipo_contexto": "PSEUDO",
  "enunciado": "...",
  "opcion_a": "...",
  "opcion_b": "...",
  "opcion_c": "...",
  "opcion_d": "...",
  "respuesta_correcta": "A|B|C|D",
  "explicacion": "...",
  "respaldo_ids": [1],
  "motivo_no_apta": null
}}

TEXTO OFICIAL DEL TEMA:
{ctx['texto_oficial_tema']}

UNIDAD DE ESTUDIO:
{ctx['texto_temario']}

SÍNTESIS ACEPTADA:
{ctx['sintesis']}

PROPOSICIONES:
{json.dumps(ctx.get('proposiciones') or [], ensure_ascii=False, indent=2)}

RESPALDOS VERIFICADOS:
{json.dumps(evidencia, ensure_ascii=False, indent=2)}
"""


def validar_candidata(ctx: dict[str, Any], candidata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidata, dict):
        raise RuntimeError("La respuesta de generación no es un objeto JSON.")
    if candidata.get("apta") is not True:
        motivo = str(candidata.get("motivo_no_apta") or "Evidencia insuficiente.").strip()
        return {
            "tipo_registro": "CANDIDATA_PREGUNTA_V2",
            "estado": "NO_APTA",
            "tema_id": int(ctx["tema_id"]),
            "pseudoarticulo_id": int(ctx["pseudoarticulo_id"]),
            "pseudonorma": str(ctx["pseudonorma"]),
            "pseudoarticulo": str(ctx["pseudoarticulo"]),
            "tipo_contexto": "PSEUDO",
            "motivo_no_apta": motivo,
        }

    if str(candidata.get("tipo_contexto") or "").upper() != "PSEUDO":
        raise RuntimeError("La candidata intentó cambiar el contexto PSEUDO.")
    obligatorios = ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d", "respuesta_correcta", "explicacion")
    for campo in obligatorios:
        if not str(candidata.get(campo) or "").strip():
            raise RuntimeError(f"Candidata incompleta: {campo}")
    correcta = str(candidata["respuesta_correcta"]).strip().upper()
    if correcta not in {"A", "B", "C", "D"}:
        raise RuntimeError("respuesta_correcta debe ser A, B, C o D.")

    ids_disponibles = {int(r["respaldo_id"]) for r in ctx["respaldos_verificados"]}
    ids = candidata.get("respaldo_ids")
    if not isinstance(ids, list) or not ids:
        raise RuntimeError("La candidata no identifica los respaldos utilizados.")
    try:
        ids_usados = [int(x) for x in ids]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("respaldo_ids contiene identificadores inválidos.") from exc
    if len(ids_usados) != len(set(ids_usados)):
        raise RuntimeError("respaldo_ids contiene duplicados.")
    ajenos = sorted(set(ids_usados) - ids_disponibles)
    if ajenos:
        raise RuntimeError(f"La candidata cita respaldos ajenos al contexto: {ajenos}")

    return {
        "tipo_registro": "CANDIDATA_PREGUNTA_V2",
        "estado": "CANDIDATA_VALIDADA",
        "tema_id": int(ctx["tema_id"]),
        "pseudoarticulo_id": int(ctx["pseudoarticulo_id"]),
        "pseudonorma": str(ctx["pseudonorma"]),
        "pseudoarticulo": str(ctx["pseudoarticulo"]),
        "tipo_contexto": "PSEUDO",
        "enunciado": str(candidata["enunciado"]).strip(),
        "opcion_a": str(candidata["opcion_a"]).strip(),
        "opcion_b": str(candidata["opcion_b"]).strip(),
        "opcion_c": str(candidata["opcion_c"]).strip(),
        "opcion_d": str(candidata["opcion_d"]).strip(),
        "respuesta_correcta": correcta,
        "explicacion": str(candidata["explicacion"]).strip(),
        "respaldo_ids": ids_usados,
    }


def cargar_fixture(path: Path) -> list[dict[str, Any]]:
    datos = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(datos, dict):
        return [datos]
    if isinstance(datos, list) and all(isinstance(x, dict) for x in datos):
        return datos
    raise RuntimeError("El fixture debe ser un objeto JSON o una lista de objetos JSON.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera candidatas PSEUDO V2 sin modificar la BD.")
    ap.add_argument("--contextos", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--modelo", default=MODELO_DEFECTO)
    ap.add_argument("--respuesta-fixture", help="Respuesta JSON prefijada para smoke; no llama a IA.")
    args = ap.parse_args()

    contextos = leer_jsonl(Path(args.contextos).expanduser().resolve())
    if not contextos:
        raise RuntimeError("No hay contextos V2 para generar.")
    for ctx in contextos:
        validar_contexto(ctx)

    fixtures = cargar_fixture(Path(args.respuesta_fixture).expanduser().resolve()) if args.respuesta_fixture else None
    if fixtures is not None and len(fixtures) != len(contextos):
        raise RuntimeError("El número de respuestas fixture no coincide con los contextos.")

    resultados: list[dict[str, Any]] = []
    for i, ctx in enumerate(contextos):
        if fixtures is not None:
            respuesta = fixtures[i]
        else:
            from openai_api import seleccionar_fragmento_json
            respuesta = seleccionar_fragmento_json(
                prompt=construir_prompt(ctx),
                modelo=args.modelo,
                operacion="generacion_pregunta_pseudo_v2",
                max_output_tokens=2200,
            )
        resultados.append(validar_candidata(ctx, respuesta))

    salida = Path(args.salida).expanduser().resolve()
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8") as f:
        for r in resultados:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    aptas = sum(r["estado"] == "CANDIDATA_VALIDADA" for r in resultados)
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - GENERACION CANDIDATAS PSEUDO")
    print("=" * 78)
    print(f"Contextos........................ {len(contextos)}")
    print(f"Candidatas validadas............. {aptas}")
    print(f"No aptas......................... {len(resultados) - aptas}")
    print(f"IA............................... {'NO EJECUTADA (FIXTURE)' if fixtures is not None else args.modelo}")
    print(f"Salida........................... {salida}")
    print("lote_preguntas................... SIN CAMBIOS")
    print("Banco............................ SIN CAMBIOS")
    print("BD............................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
