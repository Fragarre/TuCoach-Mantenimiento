"""
Constructor Temario V2 - recuperación GENERAL de fuentes externas verificadas.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA PERTINENCIA JURÍDICA.

Consume una auditoría generada por auditar_fuentes_externas_v2.py, vuelve a
descargar cada fuente, revalida identidad y extrae texto legible para revisión.
Admite HTML/texto y PDF.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import fitz

import auditar_fuentes_externas_v2 as auditor


class ExtractorTextoHTML(HTMLParser):
    BLOQUES_IGNORADOS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignorar = 0
        self._partes: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in self.BLOQUES_IGNORADOS:
            self._ignorar += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self.BLOQUES_IGNORADOS and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, data: str) -> None:
        if self._ignorar == 0 and data.strip():
            self._partes.append(data)

    def texto(self) -> str:
        return normalizar_espacios(" ".join(self._partes))


def normalizar_espacios(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def extraer_html(bruto: bytes, content_type: str) -> str:
    charset = "utf-8"
    m = re.search(r"charset=([^;\s]+)", content_type or "", flags=re.I)
    if m:
        charset = m.group(1).strip('"\'')
    try:
        raw = bruto.decode(charset, errors="replace")
    except LookupError:
        raw = bruto.decode("utf-8", errors="replace")
    parser = ExtractorTextoHTML()
    parser.feed(raw)
    parser.close()
    return parser.texto()


def extraer_pdf(bruto: bytes) -> str:
    doc = fitz.open(stream=bruto, filetype="pdf")
    try:
        return normalizar_espacios("\n".join(page.get_text("text") for page in doc))
    finally:
        doc.close()


def extraer_texto(bruto: bytes, content_type: str) -> tuple[str, str]:
    if "pdf" in (content_type or "").casefold() or bruto[:5] == b"%PDF-":
        return extraer_pdf(bruto), "PDF"
    return extraer_html(bruto, content_type), "HTML_O_TEXTO"


def hash_bytes(bruto: bytes) -> str:
    return hashlib.sha256(bruto).hexdigest()


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def cargar_auditoria(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("naturaleza") != "AUDITORIA_IDENTIDADES_FUENTES_EXTERNAS_V2":
        raise ValueError("El JSON indicado no es una auditoría externa V2.")
    if payload.get("estado") != "LISTO_PARA_RECUPERACION":
        raise ValueError("La auditoría no está LISTO_PARA_RECUPERACION.")
    resultados = payload.get("resultados") or []
    if not resultados:
        raise ValueError("La auditoría no contiene fuentes.")
    for fuente in resultados:
        if fuente.get("estado") != "IDENTIDAD_VERIFICADA":
            raise ValueError(
                f"La fuente {fuente.get('identificador_oficial')} no está verificada."
            )
    return payload


def grupos_identidad_ok(texto: str, fuente: dict) -> tuple[bool, list[dict]]:
    texto_norm = auditor.normalizar_texto(texto)
    detalle = []
    for alternativas in fuente["grupos_senales"]:
        comprobaciones = [
            {"senal": s, "localizada": auditor.contiene_senal(texto_norm, s)}
            for s in alternativas
        ]
        detalle.append(
            {
                "alternativas": comprobaciones,
                "grupo_localizado": any(x["localizada"] for x in comprobaciones),
            }
        )
    return all(x["grupo_localizado"] for x in detalle), detalle


def recuperar_fuente(fuente: dict, timeout: int) -> dict:
    base = {
        "tipo_fuente": fuente["tipo_fuente"],
        "identificador_oficial": fuente["identificador_oficial"],
        "identificador_recuperacion": fuente.get("identificador_recuperacion"),
        "descripcion": fuente["descripcion"],
        "organismo": fuente["organismo"],
        "url_oficial": fuente["url_oficial"],
        "url_final": None,
        "http_status": None,
        "content_type": None,
        "formato_detectado": None,
        "estado_identidad": "BLOQUEADA",
        "estado_recuperacion": "ERROR",
        "grupos_identidad": [],
        "bytes_respuesta": 0,
        "chars_texto_extraido": 0,
        "hash_respuesta": None,
        "hash_texto_extraido": None,
        "texto_extraido": None,
        "error": None,
    }
    try:
        bruto, content_type, status, url_final = auditor.descargar(
            fuente["url_oficial"], timeout
        )
        texto, formato = extraer_texto(bruto, content_type)
        identidad_ok, grupos = grupos_identidad_ok(texto, fuente)

        base.update(
            {
                "url_final": url_final,
                "http_status": status,
                "content_type": content_type,
                "formato_detectado": formato,
                "grupos_identidad": grupos,
                "bytes_respuesta": len(bruto),
                "hash_respuesta": hash_bytes(bruto),
            }
        )

        if not identidad_ok:
            base["error"] = "La identidad dejó de verificarse durante la recuperación."
            return base
        if not texto:
            base["estado_identidad"] = "IDENTIDAD_VERIFICADA"
            base["error"] = "La fuente no produjo texto legible."
            return base

        base.update(
            {
                "estado_identidad": "IDENTIDAD_VERIFICADA",
                "estado_recuperacion": "RECUPERADA_PARA_REVISION",
                "chars_texto_extraido": len(texto),
                "hash_texto_extraido": hash_texto(texto),
                "texto_extraido": texto,
            }
        )
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description="Recupera fuentes externas V2 verificadas.")
    ap.add_argument("--auditoria-json", required=True)
    ap.add_argument("--salida-json", required=True)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    entrada = Path(args.auditoria_json).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    try:
        auditoria = cargar_auditoria(entrada)
    except Exception as exc:
        print("ERROR: recuperación bloqueada antes de realizar descargas.")
        print(f"       {type(exc).__name__}: {exc}")
        return 2

    fuentes = auditoria["resultados"]
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - RECUPERACION GENERAL FUENTES EXTERNAS - SIN BD")
    print("=" * 78)
    print(f"Caso.............................. {auditoria.get('caso')}")
    print(f"Fuentes a recuperar............... {len(fuentes)}")
    print()

    resultados = []
    for fuente in fuentes:
        r = recuperar_fuente(fuente, args.timeout)
        resultados.append(r)
        ok = r["estado_recuperacion"] == "RECUPERADA_PARA_REVISION"
        marca = "OK" if ok else "ERROR"
        print(f"  {marca:<5} | {fuente['tipo_fuente']:<30} | {fuente['identificador_oficial']}")
        if ok:
            print(
                f"          formato={r['formato_detectado']} | "
                f"texto={r['chars_texto_extraido']} chars | "
                f"sha256={r['hash_texto_extraido'][:12]}..."
            )
        else:
            print(f"          {r['error']}")

    recuperadas = sum(
        r["estado_recuperacion"] == "RECUPERADA_PARA_REVISION" for r in resultados
    )
    fallidas = len(resultados) - recuperadas
    estado = "LISTO_PARA_REVISION_CONTENIDO" if fallidas == 0 else "BLOQUEADO"

    payload = {
        "version": 1,
        "naturaleza": "RECUPERACION_FUENTES_EXTERNAS_V2",
        "caso": auditoria.get("caso"),
        "pseudonorma": auditoria.get("pseudonorma"),
        "unidad": auditoria.get("unidad"),
        "estado": estado,
        "fecha_recuperacion_utc": datetime.now(timezone.utc).isoformat(),
        "auditoria_entrada": str(entrada),
        "resultados": resultados,
        "resumen": {
            "fuentes": len(resultados),
            "recuperadas_para_revision": recuperadas,
            "fallidas": fallidas,
        },
        "reglas": {
            "bd_modificada": False,
            "corpus_modificado": False,
            "pertinencia_aceptada": False,
            "identidad_revalidada": True,
            "texto_extraido_sin_interpretacion_juridica": True,
        },
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"Estado............................ {estado}")
    print(f"Recuperadas para revisión........ {recuperadas}")
    print(f"Fallidas.......................... {fallidas}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0 if fallidas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
