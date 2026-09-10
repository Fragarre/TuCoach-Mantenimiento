"""
Constructor Temario V2 - propuesta IA general de contenido pseudonormativo.

PILOTO / SOLO LECTURA respecto de BD y corpus.

Objetivo limitado:
- seleccionar UN tema JURIDICO_DOCTRINAL_TRANSVERSAL del PDF oficial;
- conservar literalmente el texto oficial y sus unidades P01..Pn;
- adjuntar candidatos locales recuperados por el mecanismo V2 existente;
- pedir a la IA propuestas estructuradas de proposiciones, fuentes oficiales a
  comprobar, prioridades entre candidatos locales, lagunas e incidencias;
- producir un JSON de trabajo para revisión posterior.

Reglas críticas:
- la IA NO acepta ninguna fuente, norma, artículo, relación ni pseudonorma;
- una coincidencia léxica nunca se convierte en pertinencia jurídica;
- las fuentes externas sugeridas NO incluyen URL inventadas: sólo identidad o
  consulta de búsqueda que deberá resolver y verificar otro paso;
- no consulta temario.csv ni temario_referencias;
- no modifica BD, corpus ni bancos;
- si existe OPOCOACH_MANTENIMIENTO_SESION_ID, se bloquea la ejecución para que
  openai_api.py no pueda registrar costes en la BD durante este piloto.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

import clasificar_alcance_juridico_v2 as alcance
import comun
import extraer_estructura_temario_v2 as estructura
import generar_borradores_pseudonormas_v2 as borradores_mod
import openai_api
import proponer_respaldos_pseudonormas_v2 as respaldos_mod


RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
ESTADOS_CONFIANZA = {"ALTA", "MEDIA", "BAJA"}
TIPOS_FUENTE = {
    "FUENTE_PRIMARIA_OFICIAL",
    "JURISPRUDENCIA",
    "FUENTE_INSTITUCIONAL",
    "DOCTRINA_DE_APOYO",
}
TIPOS_CONTENIDO = {"NORMATIVO", "JURISPRUDENCIAL", "DOCTRINAL", "MIXTO"}


def bloquear_sesion_mantenimiento() -> None:
    if os.getenv("OPOCOACH_MANTENIMIENTO_SESION_ID", "").strip():
        raise SystemExit(
            "ERROR: existe OPOCOACH_MANTENIMIENTO_SESION_ID. "
            "Este piloto se bloquea para garantizar que no registra costes en la BD."
        )


def seleccionar_tema(pdf: Path, parte: str, numero_tema: int):
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    matches = [
        t for t in temas
        if str(t.parte).strip().upper() == parte.strip().upper()
        and int(t.numero_tema) == int(numero_tema)
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"ERROR: se esperaba exactamente un tema {parte} {numero_tema}; "
            f"localizados={len(matches)}."
        )

    tema = matches[0]
    clase, evidencia = alcance.clasificar(tema)
    if clase != "JURIDICO_DOCTRINAL_TRANSVERSAL":
        raise SystemExit(
            f"ERROR: {parte} {numero_tema} está clasificado como {clase}, "
            "no como JURIDICO_DOCTRINAL_TRANSVERSAL."
        )
    return tema, evidencia


def construir_entrada(pdf: Path, db: Path, parte: str, numero_tema: int, top: int) -> dict:
    tema, evidencia = seleccionar_tema(pdf, parte, numero_tema)
    borrador = borradores_mod.construir_borrador(tema, evidencia)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        articulos = respaldos_mod.cargar_articulos(con)
    idf = respaldos_mod.construir_idf(articulos)

    unidades = []
    for unidad in borrador.unidades:
        candidatos = respaldos_mod.proponer(unidad.texto_temario, articulos, idf, top)
        unidades.append({
            "codigo": unidad.codigo,
            "texto_temario": unidad.texto_temario,
            "candidatos_locales": [asdict(x) for x in candidatos],
        })

    return {
        "identidad": borrador.identidad,
        "parte": borrador.parte,
        "numero_tema": borrador.numero_tema,
        "texto_tema_oficial": borrador.texto_tema_oficial,
        "clasificacion": borrador.clasificacion,
        "evidencia_clasificacion": borrador.evidencia_clasificacion,
        "unidades": unidades,
    }


def construir_prompt(entrada: dict) -> str:
    datos = json.dumps(entrada, ensure_ascii=False, indent=2)
    return f"""
Actúa como analista jurídico para preparar, NO aceptar, una pseudonorma de un
programa oficial de oposición de la Generalitat Valenciana.

Tu tarea es producir una propuesta avanzada que reduzca la revisión humana. No
estás autorizada a decidir que una fuente o relación es jurídicamente aceptada.

ENTRADA
{datos}

REGLAS OBLIGATORIAS
1. Conserva exactamente los códigos P01..Pn y analiza sólo el alcance literal de
   cada unidad del temario.
2. No amplíes el temario con materias conexas que no sean necesarias para explicar
   la unidad.
3. Formula las proposiciones mínimas que habría que respaldar para construir una
   explicación rigurosa y útil para estudio, RAG y futura generación de preguntas.
4. Distingue NORMATIVO, JURISPRUDENCIAL, DOCTRINAL y MIXTO.
5. Los candidatos locales son sólo coincidencias recuperadas. Puedes priorizarlos
   o señalar que parecen no pertinentes, pero nunca declararlos PERTINENTE.
6. Si recomiendas un candidato local, usa exclusivamente su articulo_fuente_id
   presente en la entrada. No inventes IDs, normas ni artículos locales.
7. Cuando el corpus local no baste, sugiere qué fuente externa oficial debe
   localizarse. Prioriza fuentes primarias oficiales y jurisprudencia oficial.
8. Para fuentes externas NO inventes URL. Indica organismo, nombre/identificador
   oficial sólo si lo conoces con suficiente seguridad, consulta_busqueda y motivo.
9. Si no puedes identificar una fuente con suficiente seguridad, deja el
   identificador_oficial como null y formula una consulta de búsqueda verificable.
10. Explicita lagunas e incidencias. No ocultes incertidumbre.
11. No redactes aún una síntesis final. Primero deben verificarse identidad,
    recuperación y evidencia.
12. No uses temario.csv ni conocimiento de mapeos previos; no están disponibles.
13. Todo el resultado es PROPUESTA_IA_PARA_REVISION.

Devuelve exclusivamente un objeto JSON con esta forma:
{{
  "identidad": "...",
  "estado": "PROPUESTA_IA_PARA_REVISION",
  "unidades": [
    {{
      "codigo": "P01",
      "texto_temario": "literal de entrada",
      "tipo_contenido_propuesto": "NORMATIVO|JURISPRUDENCIAL|DOCTRINAL|MIXTO",
      "proposiciones": [
        {{
          "codigo": "P01-A",
          "texto": "proposición concreta a respaldar",
          "esencial": true,
          "tipo": "NORMATIVO|JURISPRUDENCIAL|DOCTRINAL|MIXTO",
          "evidencia_necesaria": "qué debe demostrar la fuente"
        }}
      ],
      "candidatos_locales_prioritarios": [
        {{
          "articulo_fuente_id": 123,
          "motivo": "por qué merece comprobación",
          "estado": "SOLO_PROPUESTA"
        }}
      ],
      "candidatos_locales_probablemente_no_pertinentes": [
        {{
          "articulo_fuente_id": 456,
          "motivo": "por qué parece incidental o ajeno",
          "estado": "SOLO_PROPUESTA"
        }}
      ],
      "fuentes_externas_a_buscar": [
        {{
          "tipo_fuente": "FUENTE_PRIMARIA_OFICIAL|JURISPRUDENCIA|FUENTE_INSTITUCIONAL|DOCTRINA_DE_APOYO",
          "organismo": "...",
          "nombre_fuente": "...",
          "identificador_oficial": null,
          "consulta_busqueda": "...",
          "motivo": "qué proposición o parte debe respaldar",
          "confianza_identidad": "ALTA|MEDIA|BAJA",
          "estado": "SOLO_PROPUESTA"
        }}
      ],
      "lagunas": ["..."],
      "incidencias": ["..."]
    }}
  ]
}}
""".strip()


def _lista(valor, campo: str) -> list:
    if not isinstance(valor, list):
        raise ValueError(f"{campo} debe ser una lista.")
    return valor


def validar_salida(entrada: dict, salida: dict) -> None:
    if not isinstance(salida, dict):
        raise ValueError("La respuesta IA no es un objeto JSON.")
    if salida.get("identidad") != entrada["identidad"]:
        raise ValueError("La IA alteró la identidad de la pseudonorma.")
    if salida.get("estado") != "PROPUESTA_IA_PARA_REVISION":
        raise ValueError("Estado IA no permitido.")

    origen = {u["codigo"]: u for u in entrada["unidades"]}
    unidades = _lista(salida.get("unidades"), "unidades")
    if [u.get("codigo") for u in unidades] != list(origen):
        raise ValueError("La IA alteró, omitió o reordenó los códigos de unidad.")

    for unidad in unidades:
        codigo = unidad["codigo"]
        original = origen[codigo]
        if unidad.get("texto_temario") != original["texto_temario"]:
            raise ValueError(f"{codigo}: la IA alteró el texto literal del temario.")
        if unidad.get("tipo_contenido_propuesto") not in TIPOS_CONTENIDO:
            raise ValueError(f"{codigo}: tipo_contenido_propuesto inválido.")

        proposiciones = _lista(unidad.get("proposiciones"), f"{codigo}.proposiciones")
        if not proposiciones:
            raise ValueError(f"{codigo}: no contiene proposiciones.")
        codigos_prop = set()
        for prop in proposiciones:
            pc = str(prop.get("codigo") or "")
            if not pc.startswith(f"{codigo}-") or pc in codigos_prop:
                raise ValueError(f"{codigo}: código de proposición inválido o duplicado: {pc!r}.")
            codigos_prop.add(pc)
            if not str(prop.get("texto") or "").strip():
                raise ValueError(f"{pc}: proposición vacía.")
            if not isinstance(prop.get("esencial"), bool):
                raise ValueError(f"{pc}: esencial debe ser booleano.")
            if prop.get("tipo") not in TIPOS_CONTENIDO:
                raise ValueError(f"{pc}: tipo inválido.")
            if not str(prop.get("evidencia_necesaria") or "").strip():
                raise ValueError(f"{pc}: falta evidencia_necesaria.")

        ids_disponibles = {
            int(c["articulo_fuente_id"])
            for c in original.get("candidatos_locales", [])
        }
        ids_usados = set()
        for campo in (
            "candidatos_locales_prioritarios",
            "candidatos_locales_probablemente_no_pertinentes",
        ):
            for c in _lista(unidad.get(campo), f"{codigo}.{campo}"):
                try:
                    aid = int(c.get("articulo_fuente_id"))
                except (TypeError, ValueError):
                    raise ValueError(f"{codigo}: articulo_fuente_id inválido en {campo}.")
                if aid not in ids_disponibles:
                    raise ValueError(f"{codigo}: la IA inventó articulo_fuente_id={aid}.")
                if aid in ids_usados:
                    raise ValueError(f"{codigo}: articulo_fuente_id={aid} aparece en dos decisiones propuestas.")
                ids_usados.add(aid)
                if c.get("estado") != "SOLO_PROPUESTA":
                    raise ValueError(f"{codigo}: estado local no permitido.")
                if not str(c.get("motivo") or "").strip():
                    raise ValueError(f"{codigo}: candidato local sin motivo.")

        for f in _lista(unidad.get("fuentes_externas_a_buscar"), f"{codigo}.fuentes_externas_a_buscar"):
            if f.get("tipo_fuente") not in TIPOS_FUENTE:
                raise ValueError(f"{codigo}: tipo de fuente externa inválido.")
            if f.get("confianza_identidad") not in ESTADOS_CONFIANZA:
                raise ValueError(f"{codigo}: confianza_identidad inválida.")
            if f.get("estado") != "SOLO_PROPUESTA":
                raise ValueError(f"{codigo}: estado de fuente externa no permitido.")
            if "url" in f or "url_oficial" in f or "urls_documento" in f:
                raise ValueError(f"{codigo}: la IA no puede proponer URL en este paso.")
            if not str(f.get("organismo") or "").strip():
                raise ValueError(f"{codigo}: fuente externa sin organismo.")
            if not str(f.get("consulta_busqueda") or "").strip():
                raise ValueError(f"{codigo}: fuente externa sin consulta_busqueda.")
            if not str(f.get("motivo") or "").strip():
                raise ValueError(f"{codigo}: fuente externa sin motivo.")

        _lista(unidad.get("lagunas"), f"{codigo}.lagunas")
        _lista(unidad.get("incidencias"), f"{codigo}.incidencias")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Propone con IA contenido pseudonormativo para UN tema, sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--parte", required=True)
    ap.add_argument("--tema", required=True, type=int)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--modelo", default="gpt-5.4-mini")
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    bloquear_sesion_mantenimiento()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF: {pdf}")
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD: {db}")

    entrada = construir_entrada(pdf, db, args.parte, args.tema, max(1, args.top))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PROPUESTA IA PSEUDONORMATIVA - PILOTO")
    print("=" * 78)
    print(f"Tema.............................. {entrada['parte']} {entrada['numero_tema']}")
    print(f"Pseudonorma....................... {entrada['identidad']}")
    print(f"Unidades.......................... {len(entrada['unidades'])}")
    print("Estado esperado................... PROPUESTA_IA_PARA_REVISION")
    print()

    prompt = construir_prompt(entrada)
    propuesta = openai_api.seleccionar_fragmento_json(
        prompt=prompt,
        modelo=args.modelo,
        operacion="constructor_pseudonorma_v2_propuesta",
        max_output_tokens=12000,
    )
    validar_salida(entrada, propuesta)

    payload = {
        "version": 1,
        "naturaleza": "PROPUESTA_CONTENIDO_PSEUDONORMA_IA_V2",
        "modo": "PILOTO_SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "modelo": args.modelo,
        "entrada": entrada,
        "propuesta_ia": propuesta,
        "reglas": {
            "consulta_temario_csv": False,
            "consulta_temario_referencias": False,
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

    print("Resultado validado estructuralmente.")
    for u in propuesta["unidades"]:
        print(
            f"  {u['codigo']} | {u['tipo_contenido_propuesto']:<16} | "
            f"proposiciones={len(u['proposiciones'])} | "
            f"locales_prioritarios={len(u['candidatos_locales_prioritarios'])} | "
            f"externas={len(u['fuentes_externas_a_buscar'])} | "
            f"lagunas={len(u['lagunas'])} | incidencias={len(u['incidencias'])}"
        )
    print()
    print(f"JSON.............................. {salida}")
    print("Ninguna relación ha sido aceptada automáticamente.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
