"""
Constructor Temario V2 - auditoría GENERAL asistida de pertinencia jurídica.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA AUTOMÁTICAMENTE RESPALDOS.

Consume:
- un JSON de recuperación generado por recuperar_fuentes_externas_v2.py;
- una definición declarativa de proposiciones, fuentes y anclas.

Las anclas y la cobertura SOLO priorizan fragmentos para revisión humana.
Nunca convierten una fuente candidata en jurídicamente pertinente.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def norm(s: str) -> str:
    return " ".join((s or "").split())


def texto_fuente(r: dict) -> str:
    for clave in ("texto_extraido", "texto", "texto_recuperado", "contenido", "texto_normalizado"):
        valor = r.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    return ""


def ventanas(texto: str, anclas: list[str], radio: int = 850, maximo: int = 10) -> list[dict]:
    bajo = texto.casefold()
    anclas_cf = [(a, a.casefold()) for a in anclas if a and a.strip()]
    candidatos: list[dict] = []

    for ancla, patron in anclas_cf:
        for m in re.finditer(re.escape(patron), bajo):
            ini = max(0, m.start() - radio)
            fin = min(len(texto), m.end() + radio)
            ventana_cf = bajo[ini:fin]
            presentes = [a for a, p in anclas_cf if p in ventana_cf]
            candidatos.append({
                "ancla": ancla,
                "inicio": ini,
                "fin": fin,
                "cobertura_anclas": len(presentes),
                "anclas_presentes": presentes,
                "fragmento": norm(texto[ini:fin]),
            })

    candidatos.sort(key=lambda x: (-x["cobertura_anclas"], x["inicio"]))
    seleccionados: list[dict] = []
    for cand in candidatos:
        if any(abs(cand["inicio"] - x["inicio"]) < 300 for x in seleccionados):
            continue
        seleccionados.append(cand)
        if len(seleccionados) >= maximo:
            break
    return seleccionados


def validar_definicion(data: dict) -> None:
    if data.get("naturaleza") != "DEFINICION_PERTINENCIA_FUENTES_EXTERNAS_V2":
        raise SystemExit("ERROR: naturaleza de definición de pertinencia no válida.")
    if not data.get("caso"):
        raise SystemExit("ERROR: falta caso en definición.")
    proposiciones = data.get("proposiciones") or []
    if not proposiciones:
        raise SystemExit("ERROR: la definición no contiene proposiciones.")
    codigos = [p.get("codigo") for p in proposiciones]
    if any(not c for c in codigos) or len(codigos) != len(set(codigos)):
        raise SystemExit("ERROR: códigos de proposición vacíos o duplicados.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Audita pertinencia jurídica candidata de fuentes externas sin BD.")
    ap.add_argument("--recuperacion-json", required=True)
    ap.add_argument("--definicion-json", required=True)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    entrada = Path(args.recuperacion_json).expanduser().resolve()
    definicion_path = Path(args.definicion_json).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()

    rec = json.loads(entrada.read_text(encoding="utf-8"))
    definicion = json.loads(definicion_path.read_text(encoding="utf-8"))
    validar_definicion(definicion)

    if rec.get("estado") != "LISTO_PARA_REVISION_CONTENIDO":
        raise SystemExit("ERROR: la recuperación no está LISTO_PARA_REVISION_CONTENIDO.")
    if rec.get("caso") != definicion.get("caso"):
        raise SystemExit("ERROR: caso distinto entre recuperación y definición de pertinencia.")

    resultados = rec.get("resultados") or rec.get("fuentes") or []
    por_id = {r.get("identificador_oficial"): r for r in resultados}

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PERTINENCIA JURIDICA GENERAL - SOLO REVISION")
    print("=" * 78)
    print(f"Caso.............................. {definicion['caso']}")

    fichas = []
    incidencias = []
    for prop in definicion["proposiciones"]:
        codigo = prop["codigo"]
        descripcion = prop.get("descripcion", "")
        fuentes = prop.get("fuentes") or []
        if not fuentes:
            incidencias.append(f"Proposición sin fuentes declaradas: {codigo}")
            continue

        print(f"\n{codigo} | {descripcion}")
        for f in fuentes:
            ident = f.get("identificador_oficial")
            anclas = f.get("anclas") or []
            if not ident or not anclas:
                incidencias.append(f"Fuente incompleta en {codigo}: identificador/anclas")
                continue
            r = por_id.get(ident)
            if not r:
                incidencias.append(f"Falta fuente recuperada para {codigo}: {ident}")
                continue
            texto = texto_fuente(r)
            if not texto:
                incidencias.append(f"Fuente sin texto para {codigo}: {ident}")
                continue

            hs = ventanas(
                texto,
                anclas,
                radio=int(f.get("radio", definicion.get("radio", 850))),
                maximo=int(f.get("max_fragmentos", definicion.get("max_fragmentos", 10))),
            )
            estado = "CANDIDATO_PARA_REVISION" if hs else "REQUIERE_REVISION_MANUAL"
            mejor = max((h["cobertura_anclas"] for h in hs), default=0)
            print(f"  {ident:<48} | fragmentos={len(hs):>2} | mejor_cobertura={mejor}")
            fichas.append({
                "codigo_proposicion": codigo,
                "proposicion": descripcion,
                "identificador_oficial": ident,
                "tipo_fuente": r.get("tipo_fuente"),
                "estado": estado,
                "pertinencia_juridica": "NO_EVALUADA",
                "anclas": anclas,
                "fragmentos_candidatos": hs,
            })

    estado = "LISTO_PARA_REVISION_HUMANA" if not incidencias else "BLOQUEADO"
    payload = {
        "version": 1,
        "naturaleza": "AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2",
        "caso": definicion["caso"],
        "pseudonorma": definicion.get("pseudonorma") or rec.get("pseudonorma"),
        "unidad": definicion.get("unidad") or rec.get("unidad"),
        "estado": estado,
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "recuperacion_entrada": str(entrada),
        "definicion_entrada": str(definicion_path),
        "fichas": fichas,
        "incidencias": incidencias,
        "reglas": {
            "bd_modificada": False,
            "corpus_modificado": False,
            "pertinencia_aceptada_automaticamente": False,
            "coincidencia_textual_no_implica_pertinencia": True,
            "ranking_solo_prioriza_revision": True,
        },
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"Estado............................ {estado}")
    print(f"Fichas fuente/proposición......... {len(fichas)}")
    print(f"Incidencias....................... {len(incidencias)}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0 if not incidencias else 2


if __name__ == "__main__":
    raise SystemExit(main())
