"""
Constructor Temario V2 - auditoría asistida de pertinencia jurídica del piloto P05.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA AUTOMÁTICAMENTE RESPALDOS.

Consume el JSON de recuperación de las cuatro fuentes oficiales de P05 y localiza
fragmentos candidatos mediante anclas jurídicas explícitas. El resultado sirve para
revisión humana: la coincidencia textual nunca equivale por sí sola a pertinencia.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


REGLAS = {
    "12008E/AFI/DCL/17": {
        "proposiciones": [
            {
                "codigo": "P05-A",
                "descripcion": "Existencia y formulación del principio de primacía en la Declaración 17.",
                "anclas": ["primac", "jurisprudencia del Tribunal de Justicia"],
            }
        ]
    },
    "ECLI:EU:C:1964:66": {
        "proposiciones": [
            {
                "codigo": "P05-B",
                "descripcion": "Fundamento jurisprudencial de la primacía frente al Derecho interno.",
                "anclas": ["precedence", "cannot be overridden", "domestic legal provisions", "community law"],
            }
        ]
    },
    "ECLI:EU:C:1978:49": {
        "proposiciones": [
            {
                "codigo": "P05-C",
                "descripcion": "Deber del juez nacional de garantizar plena eficacia e inaplicar la norma interna incompatible.",
                "anclas": ["full effect", "national court", "set aside", "incompatible", "community law"],
            }
        ]
    },
    "BOE-T-2005-111": {
        "proposiciones": [
            {
                "codigo": "P05-D",
                "descripcion": "Distinción entre primacía del Derecho de la Unión y supremacía constitucional.",
                "anclas": ["primacía", "supremacía", "jerarquía", "aplicación preferente"],
            },
            {
                "codigo": "P05-E",
                "descripcion": "Art. 93 CE como fundamento constitucional de la primacía en el ámbito propio del Derecho de la Unión.",
                "anclas": ["art. 93", "primacía", "competencias", "Unión"],
            },
        ]
    },
}


def norm(s: str) -> str:
    return " ".join((s or "").split())


def texto_fuente(r: dict) -> str:
    # Contrato actual de recuperar_fuentes_externas_p05_v2.py:
    # el texto legible recuperado se guarda en `texto_extraido`.
    # Se conservan alias anteriores por compatibilidad con artefactos de prueba.
    for clave in (
        "texto_extraido",
        "texto",
        "texto_recuperado",
        "contenido",
        "texto_normalizado",
    ):
        valor = r.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    return ""


def ventanas(texto: str, anclas: list[str], radio: int = 700, maximo: int = 8) -> list[dict]:
    bajo = texto.casefold()
    hallazgos = []
    usados = []
    for ancla in anclas:
        patron = ancla.casefold()
        for m in re.finditer(re.escape(patron), bajo):
            ini = max(0, m.start() - radio)
            fin = min(len(texto), m.end() + radio)
            if any(abs(ini - x) < 250 for x in usados):
                continue
            usados.append(ini)
            fragmento = norm(texto[ini:fin])
            hallazgos.append({"ancla": ancla, "inicio": ini, "fin": fin, "fragmento": fragmento})
            if len(hallazgos) >= maximo:
                return hallazgos
    return hallazgos


def main() -> int:
    ap = argparse.ArgumentParser(description="Audita pertinencia jurídica candidata de P05 sin BD.")
    ap.add_argument("--recuperacion-json", required=True)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    entrada = Path(args.recuperacion_json).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    data = json.loads(entrada.read_text(encoding="utf-8"))
    if data.get("estado") != "LISTO_PARA_REVISION_CONTENIDO":
        raise SystemExit("ERROR: la recuperación no está LISTO_PARA_REVISION_CONTENIDO.")

    resultados_entrada = data.get("resultados") or data.get("fuentes") or []
    por_id = {r.get("identificador_oficial"): r for r in resultados_entrada}

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PERTINENCIA JURIDICA P05 - SOLO REVISION")
    print("=" * 78)

    fichas = []
    incidencias = []
    for ident, regla in REGLAS.items():
        r = por_id.get(ident)
        if not r:
            incidencias.append(f"Falta fuente recuperada: {ident}")
            continue
        texto = texto_fuente(r)
        if not texto:
            incidencias.append(f"Fuente sin texto recuperado: {ident}")
            continue
        print(f"\n{ident}")
        for prop in regla["proposiciones"]:
            hs = ventanas(texto, prop["anclas"])
            estado = "CANDIDATO_PARA_REVISION" if hs else "REQUIERE_REVISION_MANUAL"
            print(f"  {prop['codigo']} | {estado:<25} | fragmentos={len(hs)}")
            fichas.append({
                "identificador_oficial": ident,
                "tipo_fuente": r.get("tipo_fuente"),
                "codigo_proposicion": prop["codigo"],
                "proposicion": prop["descripcion"],
                "anclas": prop["anclas"],
                "estado": estado,
                "pertinencia_juridica": "NO_EVALUADA",
                "fragmentos_candidatos": hs,
            })

    estado = "LISTO_PARA_REVISION_HUMANA" if not incidencias else "BLOQUEADO"
    payload = {
        "version": 2,
        "naturaleza": "AUDITORIA_PERTINENCIA_JURIDICA_P05",
        "piloto": "A1_ESPECIAL_1_P05",
        "estado": estado,
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "entrada": str(entrada),
        "fichas": fichas,
        "incidencias": incidencias,
        "reglas": {
            "bd_modificada": False,
            "corpus_modificado": False,
            "pertinencia_aceptada_automaticamente": False,
            "coincidencia_textual_no_implica_pertinencia": True,
        },
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"Estado............................ {estado}")
    print(f"Fichas para revisión.............. {len(fichas)}")
    print(f"Incidencias....................... {len(incidencias)}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0 if not incidencias else 2


if __name__ == "__main__":
    raise SystemExit(main())
