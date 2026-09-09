"""
Constructor Temario V2 - auditoría GENERAL asistida de pertinencia jurídica.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA AUTOMÁTICAMENTE RESPALDOS.

Las anclas y la cobertura SOLO priorizan fragmentos para revisión humana.
Para fuentes normativas puede declararse `localizadores_estructurales`: se buscan antes
que las anclas léxicas y reciben prioridad de revisión. Sirven para localizar, no validar.

Los localizadores simples del tipo `Artículo 9`, `Artículo 70.` o equivalentes se
interpretan como identificadores estructurales completos: `Artículo 9` no puede
coincidir con `Artículo 90`, `Artículo 91`, etc.
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


def patron_localizador(valor: str, estructural: bool) -> str:
    """Devuelve regex segura para un localizador.

    En localizadores estructurales reconoce encabezados simples `Artículo N` y exige
    que tras el número no continúe otro dígito. El resto se trata como texto literal.
    """
    literal = (valor or "").strip()
    if estructural:
        m = re.fullmatch(r"Artículo\s+(\d+[A-Za-z]*)(\.)?", literal, flags=re.IGNORECASE)
        if m:
            numero = re.escape(m.group(1))
            # Permite punto, espacio o fin tras el número, pero nunca otro dígito/letra.
            return rf"artículo\s+{numero}(?![0-9A-Za-z])"
    return re.escape(literal.casefold())


def _candidatos(texto: str, patrones: list[str], anclas: list[str], radio: int, estructural: bool) -> list[dict]:
    bajo = texto.casefold()
    anclas_cf = [(a, a.casefold()) for a in anclas if a and a.strip()]
    salida = []
    for patron_original in patrones:
        regex = patron_localizador(patron_original, estructural)
        for m in re.finditer(regex, bajo):
            ini = max(0, m.start() - radio)
            fin = min(len(texto), m.end() + radio)
            ventana_cf = bajo[ini:fin]
            presentes = [a for a, p in anclas_cf if p in ventana_cf]
            salida.append({
                "ancla": patron_original,
                "tipo_localizador": "ESTRUCTURAL" if estructural else "LEXICO",
                "inicio": ini,
                "fin": fin,
                "cobertura_anclas": len(presentes),
                "anclas_presentes": presentes,
                "fragmento": norm(texto[ini:fin]),
            })
    return salida


def ventanas(texto: str, anclas: list[str], localizadores: list[str] | None = None,
             radio: int = 850, maximo: int = 10) -> list[dict]:
    localizadores = [x for x in (localizadores or []) if isinstance(x, str) and x.strip()]
    candidatos = _candidatos(texto, localizadores, anclas, radio, True)
    candidatos += _candidatos(texto, anclas, anclas, radio, False)
    candidatos.sort(key=lambda x: (
        0 if x["tipo_localizador"] == "ESTRUCTURAL" else 1,
        -x["cobertura_anclas"],
        x["inicio"],
    ))
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
    fichas, incidencias = [], []
    for prop in definicion["proposiciones"]:
        codigo, descripcion = prop["codigo"], prop.get("descripcion", "")
        fuentes = prop.get("fuentes") or []
        if not fuentes:
            incidencias.append(f"Proposición sin fuentes declaradas: {codigo}")
            continue
        print(f"\n{codigo} | {descripcion}")
        for f in fuentes:
            ident = f.get("identificador_oficial")
            anclas = f.get("anclas") or []
            localizadores = f.get("localizadores_estructurales") or []
            if not ident or (not anclas and not localizadores):
                incidencias.append(f"Fuente incompleta en {codigo}: identificador/localizadores")
                continue
            r = por_id.get(ident)
            if not r:
                incidencias.append(f"Falta fuente recuperada para {codigo}: {ident}")
                continue
            texto = texto_fuente(r)
            if not texto:
                incidencias.append(f"Fuente sin texto para {codigo}: {ident}")
                continue
            hs = ventanas(texto, anclas, localizadores,
                          int(f.get("radio", definicion.get("radio", 850))),
                          int(f.get("max_fragmentos", definicion.get("max_fragmentos", 10))))
            estructurales = sum(1 for h in hs if h.get("tipo_localizador") == "ESTRUCTURAL")
            mejor = max((h["cobertura_anclas"] for h in hs), default=0)
            estado = "CANDIDATO_PARA_REVISION" if hs else "REQUIERE_REVISION_MANUAL"
            print(f"  {ident:<48} | fragmentos={len(hs):>2} | estructurales={estructurales:>2} | mejor_cobertura={mejor}")
            fichas.append({"codigo_proposicion": codigo, "proposicion": descripcion,
                           "identificador_oficial": ident, "tipo_fuente": r.get("tipo_fuente"),
                           "estado": estado, "pertinencia_juridica": "NO_EVALUADA",
                           "anclas": anclas, "localizadores_estructurales": localizadores,
                           "fragmentos_candidatos": hs})
    estado = "LISTO_PARA_REVISION_HUMANA" if not incidencias else "BLOQUEADO"
    payload = {"version": 3, "naturaleza": "AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2",
               "caso": definicion["caso"], "pseudonorma": definicion.get("pseudonorma") or rec.get("pseudonorma"),
               "unidad": definicion.get("unidad") or rec.get("unidad"), "estado": estado,
               "fecha_utc": datetime.now(timezone.utc).isoformat(), "recuperacion_entrada": str(entrada),
               "definicion_entrada": str(definicion_path), "fichas": fichas, "incidencias": incidencias,
               "reglas": {"bd_modificada": False, "corpus_modificado": False,
                           "pertinencia_aceptada_automaticamente": False,
                           "coincidencia_textual_no_implica_pertinencia": True,
                           "localizador_estructural_no_implica_pertinencia": True,
                           "coincidencia_parcial_numero_articulo_bloqueada": True,
                           "ranking_solo_prioriza_revision": True}}
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nEstado............................ {estado}")
    print(f"Fichas fuente/proposición......... {len(fichas)}")
    print(f"Incidencias....................... {len(incidencias)}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.\nEl corpus NO ha sido modificado.")
    return 0 if not incidencias else 2


if __name__ == "__main__":
    raise SystemExit(main())
