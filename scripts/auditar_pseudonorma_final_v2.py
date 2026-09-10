"""
Constructor Temario V2 - auditoría final formal de pseudonormas.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA AUTOMÁTICAMENTE CONTENIDO JURÍDICO.

Comprueba mecánicamente los siete requisitos definidos en
PROCEDIMIENTO_GENERAL_PSEUDONORMAS_V2.md a partir de una síntesis ya revisada,
la auditoría de pertinencia y la evidencia humana materializada.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def cargar_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def extraer_seccion(md: str, titulo: str, siguiente_nivel: int = 2) -> str:
    patron = re.compile(rf"^##\s+{re.escape(titulo)}\s*$", re.MULTILINE | re.IGNORECASE)
    m = patron.search(md)
    if not m:
        return ""
    inicio = m.end()
    siguiente = re.search(r"^##\s+", md[inicio:], re.MULTILINE)
    fin = inicio + siguiente.start() if siguiente else len(md)
    return md[inicio:fin].strip()


def ids_respaldos(md: str) -> set[str]:
    sec = extraer_seccion(md, "RESPALDOS")
    return set(re.findall(r"`((?:BOE-A-[0-9-]+)|(?:CELEX:[^`]+)|(?:A/RES/[^`]+))`", sec))


def main() -> int:
    ap = argparse.ArgumentParser(description="Auditoría final formal de pseudonorma V2, sin BD ni corpus.")
    ap.add_argument("--sintesis-md", required=True)
    ap.add_argument("--pertinencia-json", required=True)
    ap.add_argument("--evidencia-json", required=True)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    sintesis_path = Path(args.sintesis_md).expanduser().resolve()
    pertinencia_path = Path(args.pertinencia_json).expanduser().resolve()
    evidencia_path = Path(args.evidencia_json).expanduser().resolve()
    salida_path = Path(args.salida_json).expanduser().resolve()

    md = sintesis_path.read_text(encoding="utf-8")
    pertinencia = cargar_json(pertinencia_path)
    evidencia = cargar_json(evidencia_path)

    caso_p = pertinencia.get("caso")
    caso_e = evidencia.get("caso")
    pseudo_p = pertinencia.get("pseudonorma")
    pseudo_e = evidencia.get("pseudonorma")

    relaciones = evidencia.get("relaciones") or evidencia.get("evidencias") or []
    if not relaciones:
        raise SystemExit("ERROR: evidencia humana sin relaciones materializadas.")

    decisiones = [r.get("decision") for r in relaciones]
    ids_evidencia = {r.get("identificador_oficial") for r in relaciones if r.get("identificador_oficial")}
    ids_md = ids_respaldos(md)
    fragmentos = sum(len(r.get("fragmentos_evidencia") or r.get("fragmentos") or []) for r in relaciones)

    texto_temario = extraer_seccion(md, "TEXTO_TEMARIO")
    sintesis = extraer_seccion(md, "SINTESIS")
    respaldos = extraer_seccion(md, "RESPALDOS")
    limites = extraer_seccion(md, "Límites jurídicos")
    notas = extraer_seccion(md, "NOTAS_DIDACTICAS")

    fichas = pertinencia.get("fichas") or []
    claves_p = {(f.get("codigo_proposicion"), f.get("identificador_oficial")) for f in fichas}
    claves_e = {(r.get("codigo_proposicion"), r.get("identificador_oficial")) for r in relaciones}

    controles = []

    def control(numero: int, nombre: str, ok: bool, detalle: str) -> None:
        controles.append({"numero": numero, "nombre": nombre, "ok": bool(ok), "detalle": detalle})

    control(1, "Conserva texto oficial que delimita el alcance",
            bool(texto_temario and ">" in texto_temario),
            "Sección TEXTO_TEMARIO presente y no vacía." if texto_temario else "Falta TEXTO_TEMARIO.")

    props_p = {f.get("codigo_proposicion") for f in fichas if f.get("codigo_proposicion")}
    props_e = {r.get("codigo_proposicion") for r in relaciones if r.get("codigo_proposicion")}
    todas_pertinentes = bool(relaciones) and all(d == "PERTINENTE" for d in decisiones)
    control(2, "Todas las proposiciones esenciales están PERTINENTE",
            todas_pertinentes and props_p == props_e,
            f"Proposiciones pertinencia={len(props_p)}; evidencia={len(props_e)}; relaciones={len(relaciones)}; no pertinentes={sum(d != 'PERTINENTE' for d in decisiones)}.")

    identidad_ok = pertinencia.get("estado") == "LISTO_PARA_REVISION_HUMANA"
    control(3, "Fuentes con identidad verificada",
            identidad_ok and caso_p == caso_e and (not pseudo_e or pseudo_p == pseudo_e),
            f"Pertinencia={pertinencia.get('estado')}; caso coherente={caso_p == caso_e}; pseudonorma coherente={not pseudo_e or pseudo_p == pseudo_e}.")

    control(4, "Evidencia seleccionada respalda materialmente las proposiciones",
            claves_p == claves_e and fragmentos > 0 and todas_pertinentes,
            f"Relaciones 1:1={claves_p == claves_e}; fragmentos materializados={fragmentos}; decisión humana pertinente={todas_pertinentes}.")

    secciones_ok = bool(sintesis and respaldos and notas)
    fuentes_cubiertas = ids_evidencia <= ids_md
    control(5, "Síntesis no excede alcance del temario ni de las fuentes",
            secciones_ok and fuentes_cubiertas,
            f"Secciones estructurales completas={secciones_ok}; fuentes de evidencia presentes en RESPALDOS={fuentes_cubiertas}. Requiere que la síntesis haya sido jurídicamente revisada antes de esta auditoría mecánica.")

    control(6, "Identifica afirmaciones que no deben deducirse",
            bool(limites and re.search(r"no autoriza|no se|no implica|no ", limites, re.IGNORECASE)),
            "Sección de límites negativos presente." if limites else "Falta sección de límites jurídicos.")

    trazabilidad = bool(texto_temario and props_e and claves_p == claves_e and fragmentos > 0 and fuentes_cubiertas)
    control(7, "Trazabilidad completa tema → unidad → proposición → fuente → evidencia",
            trazabilidad,
            f"Texto temario={bool(texto_temario)}; proposiciones={len(props_e)}; relaciones={len(claves_e)}; fragmentos={fragmentos}; respaldos cubiertos={fuentes_cubiertas}.")

    incidencias = [c for c in controles if not c["ok"]]
    estado = "ACEPTABLE_FORMALMENTE" if not incidencias else "REVISION"

    payload = {
        "version": 1,
        "naturaleza": "AUDITORIA_FINAL_PSEUDONORMA_V2",
        "caso": caso_e or caso_p,
        "pseudonorma": pseudo_e or pseudo_p,
        "estado": estado,
        "controles": controles,
        "resultado": {
            "controles_total": len(controles),
            "controles_ok": len(controles) - len(incidencias),
            "controles_fallidos": len(incidencias),
            "relaciones": len(relaciones),
            "fragmentos_evidencia": fragmentos,
            "fuentes_respaldadas": len(ids_evidencia),
        },
        "reglas": {
            "bd_modificada": False,
            "corpus_modificado": False,
            "aceptacion_juridica_automatica": False,
            "aceptacion_formal_solo_si_7_controles_ok": True,
        },
    }

    salida_path.parent.mkdir(parents=True, exist_ok=True)
    salida_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA FINAL PSEUDONORMA - SOLO LECTURA")
    print("=" * 78)
    print(f"Caso.............................. {payload['caso']}")
    print(f"Pseudonorma........................ {payload['pseudonorma']}")
    for c in controles:
        print(f"[{ 'OK' if c['ok'] else 'FALLO' }] {c['numero']}. {c['nombre']}")
        print(f"      {c['detalle']}")
    print(f"\nControles OK....................... {payload['resultado']['controles_ok']}/7")
    print(f"Relaciones.......................... {len(relaciones)}")
    print(f"Fragmentos evidencia................ {fragmentos}")
    print(f"Estado.............................. {estado}")
    print(f"JSON................................ {salida_path}")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0 if not incidencias else 2


if __name__ == "__main__":
    raise SystemExit(main())
