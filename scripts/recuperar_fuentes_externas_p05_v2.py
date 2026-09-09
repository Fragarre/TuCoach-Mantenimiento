"""
Constructor Temario V2 - recuperación controlada de fuentes oficiales externas P05.

NO MODIFICA LA BASE DE DATOS.
NO incorpora documentos al corpus.
NO acepta pertinencia jurídica.

Contrato del piloto:
1. Parte de una auditoría de identidades ya validada.
2. Vuelve a descargar cada fuente desde el proveedor oficial.
3. Revalida la identidad contra las señales declaradas por el auditor.
4. Extrae texto legible del HTML sin interpretar jurídicamente su contenido.
5. Genera un único JSON de recuperación para revisión posterior.

La finalidad de este script es probar la capa técnica de recuperación e integridad.
La selección del fragmento jurídicamente pertinente y su asociación definitiva a
P05 se realizará en una fase posterior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import auditar_fuentes_externas_p05_v2 as auditor


class ExtractorTextoHTML(HTMLParser):
    """Extractor conservador de texto visible de una respuesta HTML."""

    BLOQUES_IGNORADOS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._profundidad_ignorada = 0
        self._partes: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in self.BLOQUES_IGNORADOS:
            self._profundidad_ignorada += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self.BLOQUES_IGNORADOS and self._profundidad_ignorada:
            self._profundidad_ignorada -= 1

    def handle_data(self, data: str) -> None:
        if self._profundidad_ignorada == 0 and data.strip():
            self._partes.append(data)

    def texto(self) -> str:
        return normalizar_espacios(" ".join(self._partes))


def normalizar_espacios(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def extraer_texto_legible(html: str) -> str:
    parser = ExtractorTextoHTML()
    parser.feed(html)
    parser.close()
    return parser.texto()


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def grupos_identidad_ok(texto: str, fuente: dict) -> tuple[bool, list[dict]]:
    texto_norm = auditor.normalizar_texto(texto)
    detalle: list[dict] = []
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


def cargar_auditoria(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("naturaleza") != "AUDITORIA_IDENTIDADES_FUENTES_EXTERNAS":
        raise ValueError("El JSON indicado no es una auditoría de fuentes externas.")
    if payload.get("piloto") != "A1_ESPECIAL_1_P05":
        raise ValueError("La auditoría no corresponde al piloto A1_ESPECIAL_1_P05.")
    if payload.get("estado") != "LISTO_PARA_RECUPERACION":
        raise ValueError(
            "La auditoría no está en estado LISTO_PARA_RECUPERACION; se bloquea la recuperación."
        )
    resultados = payload.get("resultados") or []
    if len(resultados) != len(auditor.FUENTES):
        raise ValueError("La auditoría no contiene exactamente las fuentes esperadas del piloto.")
    return payload


def comprobar_contrato_auditoria(auditoria: dict) -> None:
    por_id = {
        r.get("identificador_oficial"): r
        for r in auditoria.get("resultados", [])
    }
    for fuente in auditor.FUENTES:
        ident = fuente["identificador_oficial"]
        previo = por_id.get(ident)
        if not previo:
            raise ValueError(f"Falta en la auditoría la fuente esperada {ident}.")
        if previo.get("estado") != "IDENTIDAD_VERIFICADA":
            raise ValueError(f"La identidad {ident} no consta como verificada en la auditoría.")
        if previo.get("url_oficial") != fuente.get("url_oficial"):
            raise ValueError(f"La URL auditada de {ident} no coincide con la definición actual.")


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
        "estado_identidad": "BLOQUEADA",
        "estado_recuperacion": "ERROR",
        "grupos_identidad": [],
        "chars_respuesta_html": 0,
        "chars_texto_extraido": 0,
        "hash_respuesta_html": None,
        "hash_texto_extraido": None,
        "texto_extraido": None,
        "error": None,
    }

    try:
        html, content_type, status, url_final = auditor.descargar(
            fuente["url_oficial"], timeout
        )
        identidad_ok, grupos = grupos_identidad_ok(html, fuente)
        texto = extraer_texto_legible(html)

        if not identidad_ok:
            base.update(
                {
                    "url_final": url_final,
                    "http_status": status,
                    "content_type": content_type,
                    "grupos_identidad": grupos,
                    "chars_respuesta_html": len(html),
                    "hash_respuesta_html": hash_texto(html),
                    "error": "La identidad dejó de verificarse durante la recuperación.",
                }
            )
            return base

        if not texto:
            base.update(
                {
                    "url_final": url_final,
                    "http_status": status,
                    "content_type": content_type,
                    "estado_identidad": "IDENTIDAD_VERIFICADA",
                    "grupos_identidad": grupos,
                    "chars_respuesta_html": len(html),
                    "hash_respuesta_html": hash_texto(html),
                    "error": "La respuesta oficial no produjo texto legible.",
                }
            )
            return base

        base.update(
            {
                "url_final": url_final,
                "http_status": status,
                "content_type": content_type,
                "estado_identidad": "IDENTIDAD_VERIFICADA",
                "estado_recuperacion": "RECUPERADA_PARA_REVISION",
                "grupos_identidad": grupos,
                "chars_respuesta_html": len(html),
                "chars_texto_extraido": len(texto),
                "hash_respuesta_html": hash_texto(html),
                "hash_texto_extraido": hash_texto(texto),
                "texto_extraido": texto,
                "error": None,
            }
        )
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
    return base


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Recupera contenido de las fuentes externas verificadas de P05 sin modificar la BD."
        )
    )
    ap.add_argument("--auditoria-json", required=True)
    ap.add_argument("--salida-json", required=True)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    entrada = Path(args.auditoria_json).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()

    try:
        auditoria = cargar_auditoria(entrada)
        comprobar_contrato_auditoria(auditoria)
    except Exception as exc:
        print("ERROR: recuperación bloqueada antes de realizar descargas.")
        print(f"       {type(exc).__name__}: {exc}")
        print("La base de datos NO ha sido modificada.")
        return 2

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - RECUPERACION FUENTES EXTERNAS P05 - SIN BD")
    print("=" * 78)
    print(f"Auditoría de entrada.............. {entrada}")
    print(f"Fuentes a recuperar............... {len(auditor.FUENTES)}")
    print()

    resultados = []
    for fuente in auditor.FUENTES:
        r = recuperar_fuente(fuente, args.timeout)
        resultados.append(r)
        ok = r["estado_recuperacion"] == "RECUPERADA_PARA_REVISION"
        marca = "OK" if ok else "ERROR"
        print(
            f"  {marca:<5} | {fuente['tipo_fuente']:<32} | "
            f"{fuente['identificador_oficial']}"
        )
        if ok:
            print(
                f"          texto={r['chars_texto_extraido']} chars | "
                f"sha256={r['hash_texto_extraido'][:12]}..."
            )
        else:
            print(f"          {r['error']}")

    recuperadas = sum(
        r["estado_recuperacion"] == "RECUPERADA_PARA_REVISION"
        for r in resultados
    )
    fallidas = len(resultados) - recuperadas
    estado = "LISTO_PARA_REVISION_CONTENIDO" if fallidas == 0 else "BLOQUEADO"

    payload = {
        "version": 1,
        "naturaleza": "RECUPERACION_FUENTES_EXTERNAS",
        "piloto": "A1_ESPECIAL_1_P05",
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
            "pseudonorma_aceptada": False,
            "solo_proveedores_oficiales": True,
            "identidad_revalidada_en_recuperacion": True,
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
