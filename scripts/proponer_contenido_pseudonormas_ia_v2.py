"""
Constructor Temario V2 - propuesta IA general de contenido pseudonormativo.

PILOTO / SOLO LECTURA respecto de BD y corpus.

Procesa UN tema JURIDICO_DOCTRINAL_TRANSVERSAL, pero llama a la IA unidad por
unidad (P01..Pn). Así cada llamada recibe el texto literal de los artículos
candidatos sin superar la ventana de contexto por acumular todo el tema.

Reglas críticas:
- la IA NO acepta ninguna fuente, norma, artículo, relación ni pseudonorma;
- una coincidencia léxica nunca se convierte en pertinencia jurídica;
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
    articulo_por_id = {art.articulo_fuente_id: art for art in articulos}

    unidades = []
    for unidad in borrador.unidades:
        candidatos = respaldos_mod.proponer(unidad.texto_temario, articulos, idf, top)
        candidatos_enriquecidos = []
        for candidato in candidatos:
            datos = asdict(candidato)
            articulo = articulo_por_id.get(candidato.articulo_fuente_id)
            if articulo is None:
                raise RuntimeError(
                    "No se localizó en memoria el artículo candidato "
                    f"{candidato.articulo_fuente_id}."
                )
            datos["texto_articulo"] = articulo.texto
            candidatos_enriquecidos.append(datos)
        unidades.append({
            "codigo": unidad.codigo,
            "texto_temario": unidad.texto_temario,
            "candidatos_locales": candidatos_enriquecidos,
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


def entrada_para_unidad(entrada: dict, unidad: dict) -> dict:
    return {
        "identidad": entrada["identidad"],
        "parte": entrada["parte"],
        "numero_tema": entrada["numero_tema"],
        "texto_tema_oficial": entrada["texto_tema_oficial"],
        "clasificacion": entrada["clasificacion"],
        "evidencia_clasificacion": entrada["evidencia_clasificacion"],
        "unidades": [unidad],
    }


def construir_prompt(entrada: dict) -> str:
    datos = json.dumps(entrada, ensure_ascii=False, indent=2)
    return f"""
Actúa como analista jurídico para preparar, NO aceptar, una pseudonorma de un
programa oficial de oposición de la Generalitat Valenciana.

Analiza únicamente la unidad incluida en ENTRADA. Tu tarea es producir una
propuesta avanzada que reduzca la revisión humana. No estás autorizada a decidir
que una fuente o relación es jurídicamente aceptada.

ENTRADA
{datos}

REGLAS OBLIGATORIAS
1. Conserva exactamente el código y el texto literal de la unidad.
2. Analiza sólo el alcance literal de esa unidad dentro del tema oficial completo.
3. No amplíes el temario con materias conexas no necesarias.
4. Formula las proposiciones mínimas que habría que respaldar para construir una
   explicación rigurosa y útil para estudio, RAG y futura generación de preguntas.
5. Distingue NORMATIVO, JURISPRUDENCIAL, DOCTRINAL y MIXTO.
6. Los candidatos locales son coincidencias recuperadas y dispones de su
   texto_articulo literal. Evalúa materialmente ese texto. Un candidato sólo debe
   ir a candidatos_locales_prioritarios si aporta respaldo material plausible y
   directo a alguna proposición. Si la coincidencia es léxica, incidental o trata
   de otra materia, inclúyelo en candidatos_locales_probablemente_no_pertinentes.
7. No mantengas como prioritario un candidato que describas como débil, incidental,
   ajeno o probable falso positivo. No declares nunca PERTINENTE un candidato.
8. Si recomiendas o descartas un candidato, usa exclusivamente un
   articulo_fuente_id presente en la entrada. No inventes IDs, normas ni artículos.
9. Cuando el corpus local no baste, sugiere qué fuente externa oficial debe
   localizarse. Prioriza fuentes primarias oficiales y jurisprudencia oficial.
10. Para fuentes externas NO inventes URL. Si no conoces con suficiente seguridad
    el identificador oficial, déjalo en null y formula una consulta verificable.
11. Explicita sólo lagunas e incidencias reales después de leer los textos locales.
    No declares como laguna la falta de literalidad de los artículos locales.
12. No redactes aún una síntesis final.
13. No uses temario.csv ni conocimiento de mapeos previos; no están disponibles.
14. Todo el resultado es PROPUESTA_IA_PARA_REVISION.

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
                    raise ValueError(
                        f"{codigo}: articulo_fuente_id={aid} aparece en dos decisiones propuestas."
                    )
                ids_usados.add(aid)
                if c.get("estado") != "SOLO_PROPUESTA":
                    raise ValueError(f"{codigo}: estado local no permitido.")
                if not str(c.get("motivo") or "").strip():
                    raise ValueError(f"{codigo}: candidato local sin motivo.")

        for f in _lista(
            unidad.get("fuentes_externas_a_buscar"),
            f"{codigo}.fuentes_externas_a_buscar",
        ):
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


def proponer_por_unidades(entrada: dict, modelo: str) -> dict:
    unidades_salida = []
    total = len(entrada["unidades"])
    for indice, unidad in enumerate(entrada["unidades"], 1):
        entrada_unidad = entrada_para_unidad(entrada, unidad)
        print(
            f"Procesando {unidad['codigo']} ({indice}/{total}) "
            f"con {len(unidad['candidatos_locales'])} candidatos locales..."
        )
        propuesta_unidad = openai_api.seleccionar_fragmento_json(
            prompt=construir_prompt(entrada_unidad),
            modelo=modelo,
            operacion="constructor_pseudonorma_v2_propuesta_unidad",
            max_output_tokens=5000,
        )
        validar_salida(entrada_unidad, propuesta_unidad)
        unidades_salida.append(propuesta_unidad["unidades"][0])

    propuesta = {
        "identidad": entrada["identidad"],
        "estado": "PROPUESTA_IA_PARA_REVISION",
        "unidades": unidades_salida,
    }
    validar_salida(entrada, propuesta)
    return propuesta


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
    print("Modo IA........................... UNA LLAMADA POR UNIDAD")
    print("Estado esperado................... PROPUESTA_IA_PARA_REVISION")
    print()

    propuesta = proponer_por_unidades(entrada, args.modelo)

    payload = {
        "version": 3,
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
            "texto_literal_candidatos_locales": True,
            "procesamiento_ia_por_unidad": True,
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
    print("Ninguna relación ha sido aceptada automáticamente.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
