"""
Constructor Temario V2 - piloto IA V4 de pseudonormas.

SOLO LECTURA respecto de BD y corpus.

Evita exceder la ventana de contexto mediante dos niveles:
1. evalua cada articulo candidato de forma individual frente a una unidad;
2. construye la propuesta de la unidad usando solo evaluaciones compactas.

No consulta temario.csv ni temario_referencias. No acepta relaciones y no
modifica BD, corpus ni bancos.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import openai_api
import proponer_contenido_pseudonormas_ia_v2 as base


DECISIONES = {"PLAUSIBLE_DIRECTO", "NO_PERTINENTE", "DUDOSO"}


def prompt_candidato(entrada: dict, unidad: dict, candidato: dict) -> str:
    datos = {
        "tema_oficial": entrada["texto_tema_oficial"],
        "unidad": {
            "codigo": unidad["codigo"],
            "texto_temario": unidad["texto_temario"],
        },
        "candidato": candidato,
    }
    return f"""
Evalua UN unico articulo oficial candidato como posible respaldo de una unidad
pseudonormativa de un temario de oposicion.

No aceptes juridicamente ninguna relacion. Solo clasifica la utilidad material
aparente del texto literal aportado.

ENTRADA
{json.dumps(datos, ensure_ascii=False, indent=2)}

CRITERIOS
- PLAUSIBLE_DIRECTO: el texto aporta respaldo material claro y directo a algun
  aspecto de la unidad.
- NO_PERTINENTE: la coincidencia es lexical/incidental o el articulo trata otra
  materia.
- DUDOSO: existe una conexion posible pero el texto no basta para decidir con
  seguridad.
- No uses conocimiento de temario.csv ni de mapeos previos.
- No inventes normas, articulos, IDs ni URLs.
- Conserva exactamente articulo_fuente_id.

Devuelve exclusivamente JSON:
{{
  "articulo_fuente_id": 123,
  "decision": "PLAUSIBLE_DIRECTO|NO_PERTINENTE|DUDOSO",
  "motivo": "explicacion breve basada en el texto literal"
}}
""".strip()


def validar_candidato(candidato: dict, salida: dict) -> dict:
    if not isinstance(salida, dict):
        raise ValueError("La evaluacion de candidato no es un objeto JSON.")
    aid = int(candidato["articulo_fuente_id"])
    if int(salida.get("articulo_fuente_id")) != aid:
        raise ValueError(f"La IA altero articulo_fuente_id={aid}.")
    if salida.get("decision") not in DECISIONES:
        raise ValueError(f"Decision invalida para articulo_fuente_id={aid}.")
    motivo = str(salida.get("motivo") or "").strip()
    if not motivo:
        raise ValueError(f"Falta motivo para articulo_fuente_id={aid}.")
    return {
        "articulo_fuente_id": aid,
        "decision": salida["decision"],
        "motivo": motivo,
    }


def evaluar_candidatos(entrada: dict, unidad: dict, modelo: str) -> list[dict]:
    evaluaciones = []
    total = len(unidad["candidatos_locales"])
    for i, candidato in enumerate(unidad["candidatos_locales"], 1):
        print(
            f"    candidato {i}/{total}: articulo_fuente_id="
            f"{candidato['articulo_fuente_id']}"
        )
        salida = openai_api.seleccionar_fragmento_json(
            prompt=prompt_candidato(entrada, unidad, candidato),
            modelo=modelo,
            operacion="constructor_pseudonorma_v4_evaluar_candidato",
            max_output_tokens=700,
        )
        evaluaciones.append(validar_candidato(candidato, salida))
    return evaluaciones


def prompt_unidad(entrada: dict, unidad: dict, evaluaciones: list[dict]) -> str:
    candidatos_por_id = {
        int(c["articulo_fuente_id"]): c for c in unidad["candidatos_locales"]
    }
    compactos = []
    for e in evaluaciones:
        c = candidatos_por_id[e["articulo_fuente_id"]]
        compactos.append({
            "articulo_fuente_id": e["articulo_fuente_id"],
            "norma_id": c["norma_id"],
            "norma": c["norma"],
            "articulo": c["articulo"],
            "titulo_bloque": c["titulo_bloque"],
            "decision_previa": e["decision"],
            "motivo_previo": e["motivo"],
        })

    datos = {
        "identidad": entrada["identidad"],
        "tema_oficial": entrada["texto_tema_oficial"],
        "unidad": {
            "codigo": unidad["codigo"],
            "texto_temario": unidad["texto_temario"],
        },
        "evaluaciones_candidatos": compactos,
    }
    codigo = unidad["codigo"]
    return f"""
Construye una propuesta pseudonormativa para UNA unidad de un temario oficial.
Las evaluaciones de candidatos proceden de una lectura previa individual de su
texto literal. Son auxiliares: ninguna relacion esta aceptada juridicamente.

ENTRADA
{json.dumps(datos, ensure_ascii=False, indent=2)}

REGLAS
1. Conserva identidad, codigo y texto literal de la unidad.
2. Formula solo las proposiciones minimas necesarias para explicar el alcance
   literal de la unidad de forma util para estudio, RAG y preguntas.
3. candidatos_locales_prioritarios: solo candidatos con respaldo material
   plausible. NO_PERTINENTE no puede entrar aqui.
4. candidatos_locales_probablemente_no_pertinentes: incluye los NO_PERTINENTE.
5. Los DUDOSO pueden quedar fuera de ambas listas y reflejarse como incidencia,
   o priorizarse solo si existe una razon concreta para verificarlos.
6. No declares PERTINENTE ni aceptes ninguna relacion.
7. Si el respaldo local no cubre la unidad, propone fuentes externas oficiales a
   buscar, sin inventar URLs. Si no sabes identificador, usa null.
8. Explicita solo lagunas e incidencias reales.
9. No uses temario.csv ni mapeos previos.
10. Todo el resultado es PROPUESTA_IA_PARA_REVISION.

Devuelve exclusivamente JSON:
{{
  "identidad": "{entrada['identidad']}",
  "estado": "PROPUESTA_IA_PARA_REVISION",
  "unidades": [
    {{
      "codigo": "{codigo}",
      "texto_temario": {json.dumps(unidad['texto_temario'], ensure_ascii=False)},
      "tipo_contenido_propuesto": "NORMATIVO|JURISPRUDENCIAL|DOCTRINAL|MIXTO",
      "proposiciones": [
        {{
          "codigo": "{codigo}-A",
          "texto": "...",
          "esencial": true,
          "tipo": "NORMATIVO|JURISPRUDENCIAL|DOCTRINAL|MIXTO",
          "evidencia_necesaria": "..."
        }}
      ],
      "candidatos_locales_prioritarios": [
        {{"articulo_fuente_id": 123, "motivo": "...", "estado": "SOLO_PROPUESTA"}}
      ],
      "candidatos_locales_probablemente_no_pertinentes": [
        {{"articulo_fuente_id": 456, "motivo": "...", "estado": "SOLO_PROPUESTA"}}
      ],
      "fuentes_externas_a_buscar": [
        {{
          "tipo_fuente": "FUENTE_PRIMARIA_OFICIAL|JURISPRUDENCIA|FUENTE_INSTITUCIONAL|DOCTRINA_DE_APOYO",
          "organismo": "...",
          "nombre_fuente": "...",
          "identificador_oficial": null,
          "consulta_busqueda": "...",
          "motivo": "...",
          "confianza_identidad": "ALTA|MEDIA|BAJA",
          "estado": "SOLO_PROPUESTA"
        }}
      ],
      "lagunas": [],
      "incidencias": []
    }}
  ]
}}
""".strip()


def construir_propuesta(entrada: dict, modelo: str) -> tuple[dict, dict]:
    unidades_salida = []
    auditoria_evaluaciones = {}
    total = len(entrada["unidades"])

    for indice, unidad in enumerate(entrada["unidades"], 1):
        print(
            f"Procesando {unidad['codigo']} ({indice}/{total}) con "
            f"{len(unidad['candidatos_locales'])} candidatos locales..."
        )
        evaluaciones = evaluar_candidatos(entrada, unidad, modelo)
        auditoria_evaluaciones[unidad["codigo"]] = evaluaciones

        entrada_unidad = base.entrada_para_unidad(entrada, unidad)
        propuesta_unidad = openai_api.seleccionar_fragmento_json(
            prompt=prompt_unidad(entrada, unidad, evaluaciones),
            modelo=modelo,
            operacion="constructor_pseudonorma_v4_construir_unidad",
            max_output_tokens=4000,
        )
        base.validar_salida(entrada_unidad, propuesta_unidad)
        unidades_salida.append(propuesta_unidad["unidades"][0])

    propuesta = {
        "identidad": entrada["identidad"],
        "estado": "PROPUESTA_IA_PARA_REVISION",
        "unidades": unidades_salida,
    }
    base.validar_salida(entrada, propuesta)
    return propuesta, auditoria_evaluaciones


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Piloto V4: evalua cada articulo por separado y construye una propuesta por unidad."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(base.DB_PREDETERMINADA))
    ap.add_argument("--parte", required=True)
    ap.add_argument("--tema", required=True, type=int)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--modelo", default="gpt-5.4-mini")
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    base.bloquear_sesion_mantenimiento()
    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF: {pdf}")
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD: {db}")

    entrada = base.construir_entrada(pdf, db, args.parte, args.tema, max(1, args.top))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PROPUESTA IA PSEUDONORMATIVA - PILOTO V4")
    print("=" * 78)
    print(f"Tema.............................. {entrada['parte']} {entrada['numero_tema']}")
    print(f"Pseudonorma....................... {entrada['identidad']}")
    print(f"Unidades.......................... {len(entrada['unidades'])}")
    print("Modo IA........................... CANDIDATO INDIVIDUAL + SINTESIS POR UNIDAD")
    print("Estado esperado................... PROPUESTA_IA_PARA_REVISION")
    print()

    propuesta, evaluaciones = construir_propuesta(entrada, args.modelo)

    payload = {
        "version": 4,
        "naturaleza": "PROPUESTA_CONTENIDO_PSEUDONORMA_IA_V2",
        "modo": "PILOTO_SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "modelo": args.modelo,
        "entrada": entrada,
        "evaluaciones_candidatos": evaluaciones,
        "propuesta_ia": propuesta,
        "reglas": {
            "consulta_temario_csv": False,
            "consulta_temario_referencias": False,
            "texto_literal_candidatos_locales": True,
            "evaluacion_ia_candidato_individual": True,
            "sintesis_ia_por_unidad": True,
            "aceptacion_automatica": False,
            "urls_externas_generadas_por_ia": False,
            "requiere_verificacion_fuentes": True,
            "requiere_revision_humana_final": True,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_bancos": False,
        },
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("Resultado validado estructuralmente.")
    for u in propuesta["unidades"]:
        print(
            f"  {u['codigo']} | {u['tipo_contenido_propuesto']:<16} | "
            f"proposiciones={len(u['proposiciones'])} | "
            f"locales_prioritarios={len(u['candidatos_locales_prioritarios'])} | "
            f"locales_descartables={len(u['candidatos_locales_probablemente_no_pertinentes'])} | "
            f"externas={len(u['fuentes_externas_a_buscar'])} | "
            f"lagunas={len(u['lagunas'])} | incidencias={len(u['incidencias'])}"
        )
    print()
    print(f"JSON.............................. {salida}")
    print("Ninguna relacion ha sido aceptada automaticamente.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
