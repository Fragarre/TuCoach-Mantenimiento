"""
Constructor Temario V2 - preparación asistida de revisión jurídica de pseudonormas.

SOLO LECTURA respecto de la base de datos.

Objetivo:
- reunir, por pseudoartículo, el texto literal del temario;
- adjuntar candidatos recuperados del corpus oficial local;
- marcar todos los candidatos como CANDIDATO, nunca PERTINENTE;
- generar una ficha JSON estable para revisión jurídica humana/asistida;
- permitir controles piloto conocidos (P02 y P05) únicamente en el tema piloto
  A1 ESPECIAL 1, sin convertirlos en reglas generales ni aceptar respaldos
  automáticamente.

La salida JSON es un artefacto de trabajo. No modifica BD, temario.csv, corpus ni
bancos.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance
import generar_borradores_pseudonormas_v2 as borradores_mod
import proponer_respaldos_pseudonormas_v2 as respaldos_mod

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def inferir_tipo_inicial(texto: str) -> str:
    """Clasificación conservadora sólo para orientar la revisión.

    No valida el contenido. Usa señales literales muy claras y, ante duda,
    devuelve MIXTO_PENDIENTE.
    """
    t = respaldos_mod.normalizar(texto)
    if any(x in t for x in (
        "primacia", "tribunal", "jurisprudencia", "control",
    )):
        return "MIXTO_PENDIENTE"
    if any(x in t for x in (
        "aplicacion", "eficacia", "entrada en vigor", "derogacion",
        "tratados internacionales", "constitucion", "estatutos de autonomia",
        "leyes de las comunidades autonomas", "legalidad", "reserva de ley",
        "jerarquia normativa", "competencia",
    )):
        return "NORMATIVO_PENDIENTE"
    return "DOCTRINAL_PENDIENTE"


def es_tema_piloto_a1_especial_1(borrador) -> bool:
    """Identifica de forma cerrada el único tema al que pertenecen P02/P05.

    Evita que una unidad llamada P02 o P05 de cualquier otro tema reciba por
    error los controles jurídicos construidos para A1 ESPECIAL 1.
    """
    parte = str(borrador.parte or "").strip().upper()
    texto = respaldos_mod.normalizar(borrador.texto_tema_oficial or "")
    return (
        parte == "ESPECIAL"
        and int(borrador.numero_tema) == 1
        and "fuentes del derecho administrativo" in texto
    )


def control_piloto(borrador, codigo: str, texto: str) -> dict:
    """Devuelve expectativas sólo para P02/P05 del tema piloto conocido."""
    if not es_tema_piloto_a1_especial_1(borrador):
        return {"es_control": False}

    if codigo == "P02":
        return {
            "es_control": True,
            "ambito_control": "A1_ESPECIAL_1",
            "patron": "NORMATIVO",
            "debe_aparecer": [
                "Código Civil - Título Preliminar",
                "Constitución Española - art. 9",
            ],
            "nota": (
                "La unidad debe poder explicarse principalmente desde normas "
                "reales. La pseudonorma estructura, pero no sustituye los preceptos."
            ),
        }
    if codigo == "P05":
        return {
            "es_control": True,
            "ambito_control": "A1_ESPECIAL_1",
            "patron": "MIXTO_NORMATIVO_JURISPRUDENCIAL",
            "debe_aparecer": [
                "Declaración 17 relativa a la primacía",
                "TJUE Costa/ENEL",
                "TJUE Simmenthal",
                "Tribunal Constitucional - Declaración 1/2004",
                "Constitución Española - art. 93",
            ],
            "nota": (
                "Una coincidencia local incidental no basta. Esta unidad exige "
                "fuentes UE, jurisprudencia TJUE y encaje constitucional español."
            ),
        }
    return {"es_control": False}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Prepara fichas de revisión jurídica de pseudonormas sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--parte", default=None)
    ap.add_argument("--tema", type=int, default=None)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    seleccion = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        if clase != "JURIDICO_DOCTRINAL_TRANSVERSAL":
            continue
        if args.parte and tema.parte.upper() != args.parte.upper():
            continue
        if args.tema is not None and tema.numero_tema != args.tema:
            continue
        seleccion.append(borradores_mod.construir_borrador(tema, evidencia))

    with comun.conectar_sqlite_solo_lectura(db) as con:
        articulos = respaldos_mod.cargar_articulos(con)
    idf = respaldos_mod.construir_idf(articulos)

    fichas = []
    total_unidades = 0
    total_candidatos = 0

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PREPARACION REVISION JURIDICA - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Artículos oficiales indexados.... {len(articulos)}")
    print(f"Pseudonormas seleccionadas........ {len(seleccion)}")
    print()

    for borrador in seleccion:
        print(f"{borrador.parte:<8} | {borrador.numero_tema:>2} | {borrador.identidad}")
        unidades = []
        for unidad in borrador.unidades:
            total_unidades += 1
            propuestas = respaldos_mod.proponer(
                unidad.texto_temario, articulos, idf, args.top
            )
            total_candidatos += len(propuestas)

            candidatos = []
            for p in propuestas:
                item = asdict(p)
                item["estado"] = "CANDIDATO"
                item["pertinencia_juridica"] = "NO_EVALUADA"
                candidatos.append(item)

            control = control_piloto(borrador, unidad.codigo, unidad.texto_temario)
            ficha = {
                "codigo": unidad.codigo,
                "texto_temario": unidad.texto_temario,
                "tipo_contenido_inicial": inferir_tipo_inicial(unidad.texto_temario),
                "estado": "REVISION",
                "candidatos_locales": candidatos,
                "respaldos_pertinentes": [],
                "respaldos_descartados": [],
                "fuentes_externas_pendientes": [],
                "lagunas": [],
                "sintesis": None,
                "control_piloto": control,
            }
            unidades.append(ficha)

            marca = " CONTROL" if control.get("es_control") else ""
            print(
                f"  {unidad.codigo} | {ficha['tipo_contenido_inicial']:<24} | "
                f"candidatos={len(candidatos)}{marca}"
            )

        fichas.append({
            "identidad": borrador.identidad,
            "parte": borrador.parte,
            "numero_tema": borrador.numero_tema,
            "texto_tema_oficial": borrador.texto_tema_oficial,
            "clasificacion": borrador.clasificacion,
            "estado": "REVISION",
            "unidades": unidades,
        })
        print()

    payload = {
        "version": 2,
        "naturaleza": "FICHAS_REVISION_JURIDICA_PSEUDONORMAS",
        "estado": "REVISION",
        "pdf": str(pdf),
        "db": str(db),
        "reglas": {
            "candidatos_no_son_respaldos": True,
            "aceptacion_automatica": False,
            "controles_piloto_acotados_a_tema": True,
            "bd_modificada": False,
        },
        "fichas": fichas,
    }

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Resumen:")
    print(f"  Unidades preparadas............. {total_unidades}")
    print(f"  Candidatos locales adjuntos..... {total_candidatos}")
    print(f"  Fichero de revisión............. {salida}")
    print()
    print("Todos los candidatos siguen en CANDIDATO / NO_EVALUADA.")
    print("No se han incorporado fuentes externas automáticamente.")
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
