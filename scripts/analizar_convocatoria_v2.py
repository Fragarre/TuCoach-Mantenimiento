"""
Constructor Temario V2 - manifiesto GENERAL de análisis de una convocatoria.

SOLO LECTURA respecto de la base de datos y del corpus.

Objetivo:
- extraer literalmente los temas del PDF oficial;
- clasificar cada tema con el clasificador de alcance V2 ya existente;
- representar cada tema con el contrato común de salida V2;
- resolver únicamente referencias jurídicas literales y unívocas del camino
  JURIDICO_EXPLICITO;
- no inferir ni aceptar automáticamente normas, artículos o pseudonormas;
- no ejecutar persistencia.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance
import resolver_normas_temario_v2 as resolver


RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


ACCION_POR_CLASE = {
    "NO_JURIDICO": "SIN_TRATAMIENTO_NORMATIVO",
    "JURIDICO_EXPLICITO": "RESOLVER_REFERENCIAS_EXPLICITAS",
    "JURIDICO_DELIMITABLE": "REVISION_HUMANA_NORMA_BLOQUES_ARTICULOS",
    "JURIDICO_DOCTRINAL_TRANSVERSAL": "CONSTRUIR_PSEUDONORMA_V2",
    "REVISION": "REVISION_HUMANA_DE_CLASIFICACION",
}


def resultado_base(tema, clase: str, evidencia: str) -> dict:
    return {
        "parte": tema.parte,
        "bloque_numero": tema.bloque_numero,
        "bloque_titulo": tema.bloque_titulo,
        "numero_tema": tema.numero_tema,
        "texto_oficial": tema.texto,
        "clasificacion_estructural": tema.clasificacion,
        "evidencia_estructural": tema.evidencia_clasificacion,
        "clasificacion_v2": clase,
        "evidencia_alcance": evidencia,
        "estado_v2": None,
        "requiere_revision_humana": False,
        "normas": [],
        "articulos": [],
        "fuentes": [],
        "evidencia": [],
        "incidencias": [],
        "trazabilidad": [],
        "accion_siguiente": ACCION_POR_CLASE.get(clase, "REVISION_MANUAL"),
    }


def resolver_explicito(item: dict, catalogo) -> None:
    citas = resolver.detectar_citas(item["texto_oficial"])
    if not citas:
        item["estado_v2"] = "BLOQUEADO_INCIDENCIA"
        item["requiere_revision_humana"] = True
        item["incidencias"].append({
            "tipo": "SIN_CITA_LITERAL_DETECTABLE",
            "detalle": "El tema fue clasificado como JURIDICO_EXPLICITO pero no se detectó una cita normativa literal resoluble por el parser actual.",
        })
        return

    pendiente_fuente = False
    pendiente_revision = False

    for cita in citas:
        candidatos = resolver.candidatos_para_cita(cita, catalogo)
        traza = {
            "cita_literal": cita.texto,
            "clave": cita.clave,
            "tipo": cita.tipo,
            "candidatos": [n.norma_id for n in candidatos],
        }

        if len(candidatos) != 1:
            pendiente_revision = True
            motivo = "SIN_COINCIDENCIA" if not candidatos else f"AMBIGUA_{len(candidatos)}"
            item["incidencias"].append({
                "tipo": "REFERENCIA_EXPLICITA_NO_RESUELTA",
                "cita_literal": cita.texto,
                "motivo": motivo,
            })
            item["trazabilidad"].append(traza)
            continue

        norma = candidatos[0]
        item["normas"].append({
            "norma_id": norma.norma_id,
            "nombre_canonico": norma.nombre_canonico,
            "origen_decision": "CITA_LITERAL_UNIVOCA",
        })
        for fid in norma.ids_fuente:
            if fid not in item["fuentes"]:
                item["fuentes"].append(fid)
        if not norma.ids_fuente:
            pendiente_fuente = True
            item["incidencias"].append({
                "tipo": "NORMA_SIN_FUENTE_ENLAZADA",
                "norma_id": norma.norma_id,
                "nombre_canonico": norma.nombre_canonico,
            })
        traza["norma_id_resuelta"] = norma.norma_id
        traza["fuentes"] = list(norma.ids_fuente)
        item["trazabilidad"].append(traza)

    # El resolver explícito actual no resuelve artículos. Por ello un tema con
    # norma identificada no se declara ACEPTABLE_FORMALMENTE: sólo se refleja
    # qué parte del alcance ha quedado resuelta mecánicamente.
    if pendiente_revision:
        item["estado_v2"] = "PENDIENTE_REVISION_HUMANA"
        item["requiere_revision_humana"] = True
    elif pendiente_fuente:
        item["estado_v2"] = "PENDIENTE_FUENTE"
    else:
        item["estado_v2"] = "RESUELTO_MECANICAMENTE"


def construir_manifiesto(pdf: Path, db: Path) -> dict:
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        catalogo = resolver.cargar_catalogo(con)

    items = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        item = resultado_base(tema, clase, evidencia)

        if clase == "NO_JURIDICO":
            item["estado_v2"] = "RESUELTO_MECANICAMENTE"
            item["accion_siguiente"] = "SIN_TRATAMIENTO_NORMATIVO"

        elif clase == "JURIDICO_EXPLICITO":
            resolver_explicito(item, catalogo)

        elif clase == "JURIDICO_DELIMITABLE":
            item["estado_v2"] = "PENDIENTE_REVISION_HUMANA"
            item["requiere_revision_humana"] = True
            item["evidencia"].append({
                "tipo": "LIMITE_AUTOMATIZACION",
                "detalle": "La selección automática tema -> norma -> bloque/artículos no está validada; requiere decisión humana explícita.",
            })

        elif clase == "JURIDICO_DOCTRINAL_TRANSVERSAL":
            item["estado_v2"] = "PENDIENTE_REVISION_HUMANA"
            item["requiere_revision_humana"] = True
            item["evidencia"].append({
                "tipo": "CAMINO_PSEUDONORMA_V2",
                "detalle": "Debe seguir el procedimiento general de pseudonormas y no puede aceptarse sin las revisiones humanas y auditoría final aplicables.",
            })

        else:
            item["estado_v2"] = "PENDIENTE_REVISION_HUMANA"
            item["requiere_revision_humana"] = True
            item["incidencias"].append({
                "tipo": "CLASIFICACION_NO_RESUELTA",
                "detalle": evidencia,
            })

        items.append(item)

    conteo_alcance = Counter(x["clasificacion_v2"] for x in items)
    conteo_estado = Counter(x["estado_v2"] for x in items)
    pendientes_humanos = sum(1 for x in items if x["requiere_revision_humana"])

    return {
        "version": 2,
        "naturaleza": "MANIFIESTO_ANALISIS_CONVOCATORIA_V2",
        "modo": "SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "estado": "PENDIENTE_REVISION_HUMANA" if pendientes_humanos else "ANALISIS_COMPLETADO",
        "reglas": {
            "texto_oficial_conservado": True,
            "aceptacion_juridica_automatica": False,
            "temario_referencias_consultado": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
            "modifica_bancos": False,
        },
        "resumen": {
            "temas_total": len(items),
            "por_alcance": dict(sorted(conteo_alcance.items())),
            "por_estado_v2": dict(sorted(conteo_estado.items())),
            "requieren_revision_humana": pendientes_humanos,
            "normas_catalogo": len(catalogo),
        },
        "temas": items,
    }


def imprimir(manifiesto: dict) -> None:
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - MANIFIESTO COMUN DE CONVOCATORIA - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {manifiesto['pdf']}")
    print(f"BD............................... {manifiesto['db']}")
    print(f"Temas............................ {manifiesto['resumen']['temas_total']}")
    print(f"Estado global.................... {manifiesto['estado']}")
    print()

    print("Por alcance:")
    for clase, total in manifiesto["resumen"]["por_alcance"].items():
        print(f"  {clase:<34} {total}")

    print("\nPor estado V2:")
    for estado, total in manifiesto["resumen"]["por_estado_v2"].items():
        print(f"  {estado:<34} {total}")

    print()
    for x in manifiesto["temas"]:
        print(
            f"{x['parte']:<8} | {x['numero_tema']:>2} | "
            f"{x['clasificacion_v2']:<32} | {x['estado_v2']}"
        )
        print(f"  siguiente: {x['accion_siguiente']}")
        if x["normas"]:
            print("  normas: " + "; ".join(
                f"[{n['norma_id']}] {n['nombre_canonico']}" for n in x["normas"]
            ))
        if x["incidencias"]:
            for inc in x["incidencias"]:
                print(f"  incidencia: {inc['tipo']}")

    print()
    print("No se ha consultado temario_referencias.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("temario.csv NO ha sido modificado.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Analiza una convocatoria y crea el manifiesto común V2 sin modificar datos."
    )
    ap.add_argument("--pdf", required=True, help="Ruta al PDF oficial del temario")
    ap.add_argument("--db", default=str(DB_PREDETERMINADA), help="SQLite de mantenimiento")
    ap.add_argument("--salida-json", required=True, help="Ruta del manifiesto JSON")
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()

    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF {pdf}")
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD {db}")

    manifiesto = construir_manifiesto(pdf, db)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")

    imprimir(manifiesto)
    print(f"Manifiesto JSON.................. {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
