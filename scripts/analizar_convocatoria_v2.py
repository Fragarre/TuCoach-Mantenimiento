"""
Constructor Temario V2 - manifiesto GENERAL de análisis de una convocatoria.

SOLO LECTURA respecto de la base de datos y del corpus.

Objetivo:
- extraer literalmente los temas del PDF oficial;
- clasificar cada tema con el clasificador de alcance V2 ya existente;
- representar cada tema con el contrato común de salida V2;
- resolver referencias jurídicas literales y, cuando exista delimitación estructural
  literal verificable, materializar sus artículos desde la jerarquía oficial;
- preparar pseudoartículos de trabajo para el contenido que todavía deba ser
  completado por la capa pseudonormativa;
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
import resolver_estructura_literal_v2 as resolver_estructura
import generar_borradores_pseudonormas_v2 as borradores_pseudo


RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


ACCION_POR_CLASE = {
    "NO_JURIDICO": "SIN_TRATAMIENTO_NORMATIVO",
    "JURIDICO_EXPLICITO": "RESOLVER_REFERENCIAS_EXPLICITAS",
    "JURIDICO_DELIMITABLE": "CONSTRUIR_COBERTURA_HIBRIDA_V2",
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
        # articulos se conserva como alias de compatibilidad del manifiesto V2
        # mientras los consumidores se adaptan a articulos_reales.
        "articulos": [],
        "articulos_reales": [],
        "pseudoarticulos": [],
        "dudosos": [],
        "fuentes": [],
        "evidencia": [],
        "incidencias": [],
        "trazabilidad": [],
        "accion_siguiente": ACCION_POR_CLASE.get(clase, "REVISION_MANUAL"),
    }


def preparar_pseudoarticulos(tema) -> list[dict]:
    """Segmenta conservadoramente el texto oficial sin redactar derecho.

    Esta función sólo crea unidades de trabajo. No busca fuentes, no formula
    proposiciones jurídicas y no acepta ningún respaldo.
    """
    fragmentos = borradores_pseudo.segmentar_tema(tema.texto)
    if not fragmentos:
        fragmentos = [tema.texto]

    return [
        {
            "codigo": f"P{i:02d}",
            "texto_temario": fragmento,
            "titulo_provisional": borradores_pseudo.extraer_rotulo(fragmento),
            "proposiciones": [],
            "fuentes": [],
            "evidencia": [],
            "estado": "PENDIENTE_PROPUESTA_IA",
        }
        for i, fragmento in enumerate(fragmentos, 1)
    ]


def resolver_explicito(item: dict, catalogo, fuentes_por_norma: dict[int, list[str]], payload_cache: dict) -> None:
    texto = item["texto_oficial"]
    citas = resolver.detectar_citas(texto)
    if not citas:
        item["estado_v2"] = "BLOQUEADO_INCIDENCIA"
        item["requiere_revision_humana"] = True
        item["incidencias"].append({
            "tipo": "SIN_CITA_LITERAL_DETECTABLE",
            "detalle": "El tema fue clasificado como JURIDICO_EXPLICITO pero no se detectó una cita normativa literal resoluble por el parser actual.",
        })
        return

    referencias_no_resueltas = False
    normas_resueltas = []
    for cita in citas:
        candidatos = resolver.candidatos_para_cita(cita, catalogo)
        traza = {
            "cita_literal": cita.texto,
            "clave": cita.clave,
            "tipo": cita.tipo,
            "candidatos": [n.norma_id for n in candidatos],
        }
        if len(candidatos) != 1:
            referencias_no_resueltas = True
            motivo = "SIN_COINCIDENCIA" if not candidatos else f"AMBIGUA_{len(candidatos)}"
            item["incidencias"].append({
                "tipo": "REFERENCIA_EXPLICITA_NO_RESUELTA",
                "cita_literal": cita.texto,
                "motivo": motivo,
            })
            item["trazabilidad"].append(traza)
            continue

        norma = candidatos[0]
        if norma.norma_id not in normas_resueltas:
            normas_resueltas.append(norma.norma_id)
            item["normas"].append({
                "norma_id": norma.norma_id,
                "nombre_canonico": norma.nombre_canonico,
                "origen_decision": "CITA_LITERAL_UNIVOCA",
            })
        for fid in norma.ids_fuente:
            if fid not in item["fuentes"]:
                item["fuentes"].append(fid)
        if not norma.ids_fuente:
            item["incidencias"].append({
                "tipo": "NORMA_SIN_FUENTE_ENLAZADA",
                "norma_id": norma.norma_id,
                "nombre_canonico": norma.nombre_canonico,
            })
        traza["norma_id_resuelta"] = norma.norma_id
        traza["fuentes"] = list(norma.ids_fuente)
        item["trazabilidad"].append(traza)

    if referencias_no_resueltas:
        item["estado_v2"] = "PENDIENTE_REVISION_HUMANA"
        item["requiere_revision_humana"] = True
        return

    alcance_estructural = resolver_estructura.resolver_tema(
        texto,
        catalogo,
        fuentes_por_norma,
        payload_cache,
    )
    item["articulos"] = alcance_estructural["articulos"]
    item["articulos_reales"] = list(alcance_estructural["articulos"])

    for r in alcance_estructural["bloques_efectivos"]:
        h = r["hallazgo"]
        b = r["bloque"]
        item["evidencia"].append({
            "tipo": "ESTRUCTURA_LITERAL_UNIVOCA",
            "norma_id": h["norma_id"],
            "referencia_literal": h["texto"],
            "bloque_oficial": b["texto"],
            "fuente": r["fuente"],
            "articulos": [str(a.get("articulo")) for a in b["articulos"]],
        })

    for h in alcance_estructural["no_articulo"]:
        item["evidencia"].append({
            "tipo": "ESTRUCTURA_LITERAL_NO_ARTICULADA",
            "norma_id": h.get("norma_id"),
            "referencia_literal": h["texto"],
        })

    for nid in alcance_estructural["normas_sin_estructura_literal"]:
        item["incidencias"].append({
            "tipo": "NORMA_EXPLICITA_SIN_ALCANCE_ESTRUCTURAL_LITERAL",
            "norma_id": nid,
            "detalle": "La norma está citada literalmente, pero el tema delimita su contenido por materias y no por Título/Capítulo/Sección resoluble mecánicamente.",
        })

    for nid in alcance_estructural["normas_fuente_no_materializable"]:
        item["incidencias"].append({
            "tipo": "FUENTE_SIN_ARTICULOS_ESTRUCTURADOS",
            "norma_id": nid,
            "detalle": "La fuente oficial enlazada contiene estructura, pero el mecanismo actual no dispone de artículos materializables para ella.",
        })

    for x in alcance_estructural["ambiguos"]:
        h = x["hallazgo"]
        item["incidencias"].append({
            "tipo": "ESTRUCTURA_LITERAL_AMBIGUA",
            "norma_id": h.get("norma_id"),
            "referencia_literal": h["texto"],
            "coincidencias": x["coincidencias"],
        })

    for h in alcance_estructural["sin_match"]:
        item["incidencias"].append({
            "tipo": "ESTRUCTURA_LITERAL_NO_LOCALIZADA",
            "norma_id": h.get("norma_id"),
            "referencia_literal": h["texto"],
        })

    if alcance_estructural["completo"]:
        item["estado_v2"] = "RESUELTO_MECANICAMENTE"
        item["accion_siguiente"] = "AUDITAR_RESULTADO"
        return

    if alcance_estructural["normas_fuente_no_materializable"]:
        item["estado_v2"] = "PENDIENTE_FUENTE"
        item["accion_siguiente"] = "RESOLVER_FUENTE_ESTRUCTURADA"
    else:
        item["estado_v2"] = "PENDIENTE_REVISION_HUMANA"
        item["requiere_revision_humana"] = True
        item["accion_siguiente"] = "REVISION_HUMANA_ALCANCE"


def construir_manifiesto(pdf: Path, db: Path) -> dict:
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        catalogo = resolver.cargar_catalogo(con)
        fuentes_por_norma: dict[int, list[str]] = {}
        for row in con.execute("SELECT norma_id, id_fuente FROM norma_fuentes ORDER BY norma_id, id_fuente"):
            fuentes_por_norma.setdefault(int(row[0]), []).append(str(row[1]))

    items = []
    payload_cache = {}
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        item = resultado_base(tema, clase, evidencia)

        if clase == "NO_JURIDICO":
            item["estado_v2"] = "RESUELTO_MECANICAMENTE"
            item["accion_siguiente"] = "SIN_TRATAMIENTO_NORMATIVO"

        elif clase == "JURIDICO_EXPLICITO":
            resolver_explicito(item, catalogo, fuentes_por_norma, payload_cache)

        elif clase == "JURIDICO_DELIMITABLE":
            item["pseudoarticulos"] = preparar_pseudoarticulos(tema)
            item["estado_v2"] = "PENDIENTE_SINTESIS"
            item["accion_siguiente"] = "PROPONER_COBERTURA_HIBRIDA_IA"
            item["evidencia"].append({
                "tipo": "COBERTURA_HIBRIDA_PENDIENTE",
                "detalle": "El sistema debe intentar primero identificar cobertura real suficientemente segura y completar únicamente el resto mediante pseudoartículos respaldados. La revisión humana no es el camino principal.",
            })

        elif clase == "JURIDICO_DOCTRINAL_TRANSVERSAL":
            item["pseudoarticulos"] = preparar_pseudoarticulos(tema)
            item["estado_v2"] = "PENDIENTE_SINTESIS"
            item["accion_siguiente"] = "PROPONER_CONTENIDO_PSEUDONORMATIVO_IA"
            item["evidencia"].append({
                "tipo": "CAMINO_PSEUDONORMA_V2",
                "detalle": "Las unidades se han segmentado desde el texto oficial. Deben completarse con proposiciones, fuentes y evidencia antes de decidir si existe alguna incidencia humana bloqueante.",
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

    estados_bloqueantes = {"PENDIENTE_REVISION_HUMANA", "BLOQUEADO_INCIDENCIA"}
    bloqueantes = sum(1 for x in items if x["estado_v2"] in estados_bloqueantes)

    return {
        "version": 3,
        "naturaleza": "MANIFIESTO_ANALISIS_CONVOCATORIA_V2",
        "modo": "SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "estado": "PENDIENTE_REVISION_HUMANA" if bloqueantes else "ANALISIS_EN_CURSO",
        "reglas": {
            "texto_oficial_conservado": True,
            "aceptacion_juridica_automatica": False,
            "temario_referencias_consultado": False,
            "revision_humana_solo_incidencias_bloqueantes": True,
            "asignacion_real_separada_de_evidencia": True,
            "dudoso_no_equivale_a_incidencia": True,
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
            "bloqueantes": bloqueantes,
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
        print(f"{x['parte']:<8} | {x['numero_tema']:>2} | {x['clasificacion_v2']:<32} | {x['estado_v2']}")
        print(f"  siguiente: {x['accion_siguiente']}")
        if x["normas"]:
            print("  normas: " + "; ".join(f"[{n['norma_id']}] {n['nombre_canonico']}" for n in x["normas"]))
        if x["articulos_reales"]:
            por_norma = Counter(a["norma_id"] for a in x["articulos_reales"])
            print("  artículos reales: " + "; ".join(f"norma {nid}={n}" for nid, n in sorted(por_norma.items())))
        if x["pseudoarticulos"]:
            print(f"  pseudoartículos de trabajo: {len(x['pseudoarticulos'])}")
        if x["dudosos"]:
            print(f"  dudosos no bloqueantes: {len(x['dudosos'])}")
        if x["incidencias"]:
            for inc in x["incidencias"]:
                print(f"  incidencia: {inc['tipo']}")

    print()
    print("No se ha consultado temario_referencias.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("temario.csv NO ha sido modificado.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Analiza una convocatoria y crea el manifiesto común V2 sin modificar datos.")
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
