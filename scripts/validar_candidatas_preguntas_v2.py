"""Valida de forma independiente candidatas PSEUDO V2. No modifica SQLite.

Consume candidatas producidas por generar_candidatas_preguntas_v2.py y usa
exclusivamente los respaldos PERTINENTE/VERIFICADO persistidos en la BD para
realizar dos revisiones independientes y una auditoría ciega final.

Por defecto llama a IA. Para smoke admite fixtures JSON. El resultado apto se
marca como VALIDADA_AUDITADA_V2; las rechazadas quedan RECHAZADA_VALIDACION_V2.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

MODELO_DEFECTO = "gpt-5.4-nano"


def leer_jsonl(path: Path) -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for n, linea in enumerate(f, 1):
            if not linea.strip():
                continue
            try:
                dato = json.loads(linea)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"JSON inválido en {path}, línea {n}: {exc}") from exc
            if not isinstance(dato, dict):
                raise RuntimeError(f"La línea {n} no contiene un objeto JSON.")
            salida.append(dato)
    return salida


def cargar_fixture(path: Path) -> list[dict[str, Any]]:
    dato = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(dato, dict):
        return [dato]
    if isinstance(dato, list) and all(isinstance(x, dict) for x in dato):
        return dato
    raise RuntimeError("El fixture debe ser un objeto JSON o una lista de objetos JSON.")


def validar_entrada(c: dict[str, Any]) -> None:
    if c.get("tipo_registro") != "CANDIDATA_PREGUNTA_V2":
        raise RuntimeError("El registro no es CANDIDATA_PREGUNTA_V2.")
    if str(c.get("estado") or "") != "CANDIDATA_VALIDADA":
        raise RuntimeError("Solo se validan candidatas en estado CANDIDATA_VALIDADA.")
    if str(c.get("tipo_contexto") or "").upper() != "PSEUDO":
        raise RuntimeError("Esta fase solo admite PSEUDO.")
    for campo in (
        "tema_id", "pseudoarticulo_id", "enunciado", "opcion_a", "opcion_b",
        "opcion_c", "opcion_d", "respuesta_correcta", "explicacion", "respaldo_ids",
    ):
        if c.get(campo) in (None, "", []):
            raise RuntimeError(f"Candidata incompleta: {campo}")
    if str(c["respuesta_correcta"]).strip().upper() not in {"A", "B", "C", "D"}:
        raise RuntimeError("respuesta_correcta debe ser A/B/C/D.")


def cargar_evidencia(con: sqlite3.Connection, c: dict[str, Any]) -> list[dict[str, Any]]:
    ids = c["respaldo_ids"]
    if not isinstance(ids, list) or not ids:
        raise RuntimeError("respaldo_ids debe ser una lista no vacía.")
    try:
        ids_int = [int(x) for x in ids]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("respaldo_ids contiene valores inválidos.") from exc
    if len(ids_int) != len(set(ids_int)):
        raise RuntimeError("respaldo_ids contiene duplicados.")
    marcas = ",".join("?" for _ in ids_int)
    filas = con.execute(
        f"""
        SELECT r.id,r.pseudoarticulo_id,r.pertinencia,r.estado,r.evidencia_texto,
               r.evidencia_localizador,p.codigo AS proposicion_codigo,p.texto AS proposicion_texto,
               af.id_boe,af.articulo_boe,af.titulo_bloque,af.texto AS articulo_texto,
               fc.tipo_fuente,fc.organismo,fc.nombre,fc.identificador_oficial,fc.localizador_oficial
        FROM temario_pseudoarticulo_respaldos r
        LEFT JOIN temario_pseudoarticulo_proposiciones p ON p.id=r.proposicion_id
        LEFT JOIN articulos_fuente af ON af.id=r.articulo_fuente_id
        LEFT JOIN fuentes_conocimiento fc ON fc.id=r.fuente_conocimiento_id
        WHERE r.id IN ({marcas})
        ORDER BY r.id
        """,
        tuple(ids_int),
    ).fetchall()
    por_id = {int(r["id"]): r for r in filas}
    faltan = [x for x in ids_int if x not in por_id]
    if faltan:
        raise RuntimeError(f"No existen respaldos citados: {faltan}")
    salida: list[dict[str, Any]] = []
    pseudo_id = int(c["pseudoarticulo_id"])
    for rid in ids_int:
        r = por_id[rid]
        if int(r["pseudoarticulo_id"]) != pseudo_id:
            raise RuntimeError(f"Respaldo {rid} ajeno al pseudoartículo.")
        if str(r["pertinencia"] or "").upper() != "PERTINENTE" or str(r["estado"] or "").upper() != "VERIFICADO":
            raise RuntimeError(f"Respaldo {rid} no está PERTINENTE/VERIFICADO.")
        fuente = (
            {
                "clase": "ARTICULO_FUENTE", "documento": r["id_boe"],
                "articulo": r["articulo_boe"], "titulo": r["titulo_bloque"],
                "texto": r["articulo_texto"],
            }
            if r["articulo_texto"] is not None else
            {
                "clase": "FUENTE_CONOCIMIENTO", "tipo": r["tipo_fuente"],
                "organismo": r["organismo"], "nombre": r["nombre"],
                "identificador_oficial": r["identificador_oficial"],
                "localizador_oficial": r["localizador_oficial"],
            }
        )
        salida.append({
            "respaldo_id": rid,
            "proposicion_codigo": r["proposicion_codigo"],
            "proposicion_texto": r["proposicion_texto"],
            "evidencia_texto": r["evidencia_texto"],
            "evidencia_localizador": r["evidencia_localizador"],
            "fuente": fuente,
        })
    return salida


def pregunta_sin_marca(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "enunciado": c["enunciado"],
        "A": c["opcion_a"], "B": c["opcion_b"],
        "C": c["opcion_c"], "D": c["opcion_d"],
    }


def prompt_revision(c: dict[str, Any], evidencia: list[dict[str, Any]], numero: int) -> str:
    return f"""Actúas como revisor independiente de una pregunta de oposición.
Revisión nº {numero}.
Usa EXCLUSIVAMENTE las evidencias verificadas suministradas. La pseudonorma y el
pseudoartículo son unidades internas de estudio, no normas jurídicas.

CANDIDATA:
{json.dumps(c, ensure_ascii=False, indent=2)}

EVIDENCIAS VERIFICADAS:
{json.dumps(evidencia, ensure_ascii=False, indent=2)}

Comprueba: corrección de la respuesta marcada, falsedad inequívoca de los otros
tres distractores, ausencia de información externa, dificultad ALTA/MUY_ALTA y
que los respaldo_ids citados bastan para justificar la respuesta.

Devuelve SOLO JSON:
{{"aprobable":true,"correccion":"OK|ERROR","distractores":"OK|ERROR",
"usa_informacion_externa":false,"dificultad":"ALTA|MUY_ALTA|INSUFICIENTE",
"respaldos_suficientes":true,"observaciones":[]}}
"""


def prompt_auditoria(c: dict[str, Any], evidencia: list[dict[str, Any]]) -> str:
    return f"""Actúas como auditor ciego de una pregunta de oposición.
No conoces la respuesta marcada por el generador. Resuelve la pregunta desde cero
usando EXCLUSIVAMENTE las evidencias verificadas suministradas.

PREGUNTA:
{json.dumps(pregunta_sin_marca(c), ensure_ascii=False, indent=2)}

EVIDENCIAS VERIFICADAS:
{json.dumps(evidencia, ensure_ascii=False, indent=2)}

Devuelve SOLO JSON:
{{"respuesta_elegida":"A|B|C|D","opcion_a":"CORRECTA|FALSA",
"opcion_b":"CORRECTA|FALSA","opcion_c":"CORRECTA|FALSA",
"opcion_d":"CORRECTA|FALSA","respuesta_unica":true,
"usa_informacion_externa":false,"dificultad":"ALTA|MUY_ALTA|INSUFICIENTE",
"observaciones":[]}}
"""


def revision_ok(v: dict[str, Any]) -> bool:
    return (
        bool(v.get("aprobable"))
        and str(v.get("correccion") or "").upper() == "OK"
        and str(v.get("distractores") or "").upper() == "OK"
        and not bool(v.get("usa_informacion_externa"))
        and str(v.get("dificultad") or "").upper() in {"ALTA", "MUY_ALTA"}
        and bool(v.get("respaldos_suficientes"))
    )


def auditoria_ok(a: dict[str, Any], correcta: str) -> bool:
    correcta = correcta.strip().upper()
    if str(a.get("respuesta_elegida") or "").upper() != correcta:
        return False
    if not bool(a.get("respuesta_unica")) or bool(a.get("usa_informacion_externa")):
        return False
    for letra in "ABCD":
        esperado = "CORRECTA" if letra == correcta else "FALSA"
        if str(a.get(f"opcion_{letra.lower()}") or "").upper() != esperado:
            return False
    return str(a.get("dificultad") or "").upper() in {"ALTA", "MUY_ALTA"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Valida candidatas PSEUDO V2 sin modificar BD.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--candidatas", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--modelo", default=MODELO_DEFECTO)
    ap.add_argument("--revisiones-fixture")
    ap.add_argument("--auditoria-fixture")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    candidatas = leer_jsonl(Path(args.candidatas).expanduser().resolve())
    if not candidatas:
        raise RuntimeError("No hay candidatas para validar.")

    rev_fixture = cargar_fixture(Path(args.revisiones_fixture).expanduser().resolve()) if args.revisiones_fixture else None
    aud_fixture = cargar_fixture(Path(args.auditoria_fixture).expanduser().resolve()) if args.auditoria_fixture else None
    if rev_fixture is not None and len(rev_fixture) != len(candidatas) * 2:
        raise RuntimeError("El fixture de revisiones debe contener exactamente 2 respuestas por candidata.")
    if aud_fixture is not None and len(aud_fixture) != len(candidatas):
        raise RuntimeError("El fixture de auditoría debe contener 1 respuesta por candidata.")

    resultados: list[dict[str, Any]] = []
    uri = db.as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        for i, c in enumerate(candidatas):
            validar_entrada(c)
            evidencia = cargar_evidencia(con, c)
            if rev_fixture is None:
                from openai_api import seleccionar_fragmento_json
                v1 = seleccionar_fragmento_json(prompt_revision(c, evidencia, 1), modelo=args.modelo, operacion="validar_pregunta_pseudo_v2_1")
                v2 = seleccionar_fragmento_json(prompt_revision(c, evidencia, 2), modelo=args.modelo, operacion="validar_pregunta_pseudo_v2_2")
            else:
                v1, v2 = rev_fixture[2*i], rev_fixture[2*i+1]
            if aud_fixture is None:
                from openai_api import seleccionar_fragmento_json
                aud = seleccionar_fragmento_json(prompt_auditoria(c, evidencia), modelo=args.modelo, operacion="auditar_pregunta_pseudo_v2_ciega")
            else:
                aud = aud_fixture[i]
            ok = revision_ok(v1) and revision_ok(v2) and auditoria_ok(aud, str(c["respuesta_correcta"]))
            resultados.append({
                **c,
                "estado": "VALIDADA_AUDITADA_V2" if ok else "RECHAZADA_VALIDACION_V2",
                "validacion_v2": {"revision_1": v1, "revision_2": v2, "auditoria_ciega": aud},
            })
    finally:
        con.close()

    salida = Path(args.salida).expanduser().resolve()
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8") as f:
        for r in resultados:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    aprobadas = sum(r["estado"] == "VALIDADA_AUDITADA_V2" for r in resultados)
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - VALIDACION INDEPENDIENTE CANDIDATAS")
    print("=" * 78)
    print(f"Candidatas........................ {len(resultados)}")
    print(f"Validadas y auditadas............. {aprobadas}")
    print(f"Rechazadas......................... {len(resultados)-aprobadas}")
    print(f"IA................................. {'NO EJECUTADA (FIXTURE)' if rev_fixture is not None and aud_fixture is not None else args.modelo}")
    print("BD................................ SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
