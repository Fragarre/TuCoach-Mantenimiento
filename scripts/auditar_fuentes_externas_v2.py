"""
Constructor Temario V2 - auditoría GENERAL de identidades de fuentes externas.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA PERTINENCIA JURÍDICA.

Lee una definición declarativa JSON de fuentes y verifica su identidad. Una fuente
puede declarar una `url_oficial` de ficha y, opcionalmente, una lista ordenada
`urls_documento` con representaciones documentales oficiales (HTML/PDF/texto).
El auditor prueba esas representaciones sin salir de las URL declaradas y conserva
trazabilidad de cada intento.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import fitz


def normalizar_texto(texto: str) -> str:
    texto = html.unescape(texto or "")
    texto = re.sub(r"<[^>]+>", " ", texto)
    return " ".join(texto.split()).casefold()


def contiene_senal(texto_normalizado: str, senal: str) -> bool:
    b = normalizar_texto(senal)
    if b in texto_normalizado:
        return True
    aa = re.sub(r"[^a-z0-9]+", "", texto_normalizado)
    bb = re.sub(r"[^a-z0-9]+", "", b)
    return len(bb) >= 6 and bb in aa


def descargar(url: str, timeout: int) -> tuple[bytes, str, int, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 NetReto-Constructor-Temario-V2/1.0",
        "Accept-Language": "es,en;q=0.8",
        "Accept": "text/html,application/pdf,text/plain,application/xhtml+xml,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        bruto = resp.read()
        return bruto, resp.headers.get("Content-Type", ""), int(getattr(resp, "status", 200) or 200), resp.geturl()


def extraer_texto_pdf(bruto: bytes) -> str:
    doc = fitz.open(stream=bruto, filetype="pdf")
    try:
        return "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def decodificar_respuesta(bruto: bytes, content_type: str) -> tuple[str, str]:
    ctype = (content_type or "").casefold()
    if "pdf" in ctype or bruto[:5] == b"%PDF-":
        return extraer_texto_pdf(bruto), "PDF"
    charset = "utf-8"
    m = re.search(r"charset=([^;\s]+)", content_type or "", flags=re.I)
    if m:
        charset = m.group(1).strip('"\'')
    try:
        texto = bruto.decode(charset, errors="replace")
    except LookupError:
        texto = bruto.decode("utf-8", errors="replace")
    return texto, "HTML_O_TEXTO"


def cargar_definicion(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("naturaleza") != "DEFINICION_FUENTES_EXTERNAS_PSEUDONORMA":
        raise ValueError("El JSON no es una definición de fuentes externas V2.")
    fuentes = payload.get("fuentes") or []
    if not fuentes:
        raise ValueError("La definición no contiene fuentes.")
    requeridos = {"tipo_fuente", "identificador_oficial", "descripcion", "organismo", "url_oficial", "grupos_senales"}
    ids = set()
    for i, fuente in enumerate(fuentes, 1):
        faltan = requeridos - set(fuente)
        if faltan:
            raise ValueError(f"Fuente {i}: faltan campos {sorted(faltan)}")
        ident = fuente["identificador_oficial"]
        if ident in ids:
            raise ValueError(f"Identificador duplicado: {ident}")
        ids.add(ident)
        if not fuente["grupos_senales"]:
            raise ValueError(f"Fuente {ident}: sin grupos de señales.")
        urls = fuente.get("urls_documento")
        if urls is not None and (not isinstance(urls, list) or not all(isinstance(x, str) and x.strip() for x in urls)):
            raise ValueError(f"Fuente {ident}: urls_documento debe ser una lista de URL no vacías.")
    return payload


def comprobar_grupos(texto: str, grupos_senales: list) -> list[dict]:
    texto_norm = normalizar_texto(texto)
    grupos = []
    for alternativas in grupos_senales:
        comprobaciones = [{"senal": s, "localizada": contiene_senal(texto_norm, s)} for s in alternativas]
        grupos.append({"alternativas": comprobaciones, "grupo_localizado": any(x["localizada"] for x in comprobaciones)})
    return grupos


def auditar_fuente(fuente: dict, timeout: int) -> dict:
    base = {**fuente, "estado": "BLOQUEADA", "http_status": None, "content_type": None,
            "formato_detectado": None, "url_final": None, "url_documento_verificada": None,
            "bytes_respuesta": 0, "chars_texto_comprobacion": 0, "grupos_senales_resultado": [],
            "hash_respuesta": None, "intentos_documentales": [], "error": None}
    urls = []
    for u in [*(fuente.get("urls_documento") or []), fuente["url_oficial"]]:
        if u not in urls:
            urls.append(u)
    ultimo_grupos = []
    errores = []
    for url in urls:
        intento = {"url": url, "estado": "ERROR", "url_final": None, "http_status": None,
                   "content_type": None, "formato_detectado": None, "chars_texto": 0, "error": None}
        try:
            bruto, content_type, status, url_final = descargar(url, timeout)
            texto, formato = decodificar_respuesta(bruto, content_type)
            grupos = comprobar_grupos(texto, fuente["grupos_senales"])
            todas = all(g["grupo_localizado"] for g in grupos)
            intento.update({"estado": "IDENTIDAD_VERIFICADA" if todas else "SEÑALES_INCOMPLETAS",
                            "url_final": url_final, "http_status": status, "content_type": content_type,
                            "formato_detectado": formato, "chars_texto": len(texto)})
            base["intentos_documentales"].append(intento)
            ultimo_grupos = grupos
            if todas:
                base.update({"estado": "IDENTIDAD_VERIFICADA", "http_status": status,
                             "content_type": content_type, "formato_detectado": formato,
                             "url_final": url_final, "url_documento_verificada": url,
                             "bytes_respuesta": len(bruto), "chars_texto_comprobacion": len(texto),
                             "grupos_senales_resultado": grupos,
                             "hash_respuesta": hashlib.sha256(bruto).hexdigest(), "error": None})
                return base
        except Exception as exc:
            intento["error"] = f"{type(exc).__name__}: {exc}"
            base["intentos_documentales"].append(intento)
            errores.append(intento["error"])
    base["grupos_senales_resultado"] = ultimo_grupos
    base["error"] = "No se localizaron todos los grupos de identidad en ninguna representación oficial declarada."
    if errores and not ultimo_grupos:
        base["error"] += " " + " | ".join(errores)
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description="Audita fuentes externas declarativas sin modificar BD/corpus.")
    ap.add_argument("--definicion-json", required=True)
    ap.add_argument("--salida-json", required=True)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()
    entrada = Path(args.definicion_json).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    try:
        definicion = cargar_definicion(entrada)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2
    fuentes = definicion["fuentes"]
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA GENERAL FUENTES EXTERNAS - SIN BD")
    print("=" * 78)
    print(f"Caso.............................. {definicion.get('caso')}")
    print(f"Fuentes esperadas................ {len(fuentes)}\n")
    resultados = []
    for fuente in fuentes:
        r = auditar_fuente(fuente, args.timeout)
        resultados.append(r)
        marca = "OK" if r["estado"] == "IDENTIDAD_VERIFICADA" else "ERROR"
        print(f"  {marca:<5} | {fuente['tipo_fuente']:<30} | {fuente['identificador_oficial']}")
        if r["estado"] == "IDENTIDAD_VERIFICADA" and r.get("url_documento_verificada") != fuente["url_oficial"]:
            print(f"          Representación documental: {r['url_documento_verificada']}")
        if r["error"]:
            print(f"          {r['error']}")
            for i, grupo in enumerate(r.get("grupos_senales_resultado", []), 1):
                if not grupo["grupo_localizado"]:
                    print(f"          Falta grupo {i}: " + " OR ".join(x["senal"] for x in grupo["alternativas"]))
    verificadas = sum(r["estado"] == "IDENTIDAD_VERIFICADA" for r in resultados)
    bloqueadas = len(resultados) - verificadas
    estado = "LISTO_PARA_RECUPERACION" if bloqueadas == 0 else "BLOQUEADO"
    payload = {"version": 2, "naturaleza": "AUDITORIA_IDENTIDADES_FUENTES_EXTERNAS_V2",
               "caso": definicion.get("caso"), "pseudonorma": definicion.get("pseudonorma"),
               "unidad": definicion.get("unidad"), "estado": estado,
               "fecha_auditoria_utc": datetime.now(timezone.utc).isoformat(), "definicion_entrada": str(entrada),
               "resultados": resultados,
               "resumen": {"fuentes": len(resultados), "identidades_verificadas": verificadas, "bloqueadas": bloqueadas},
               "reglas": {"bd_modificada": False, "corpus_modificado": False, "pertinencia_aceptada": False,
                           "identidad_no_equivale_a_pertinencia": True,
                           "solo_representaciones_oficiales_declaradas": True}}
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nEstado............................ {estado}")
    print(f"Identidades verificadas.......... {verificadas}")
    print(f"Bloqueadas........................ {bloqueadas}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.\nEl corpus NO ha sido modificado.")
    return 0 if bloqueadas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
