"""
Constructor Temario V2 - auditoría de identidades oficiales externas del piloto P05.

NO MODIFICA LA BASE DE DATOS.
NO descarga ni persiste todavía el texto jurídico completo.

Primera pieza del adaptador general de fuentes externas:
- declara identidades oficiales esperadas del piloto P05;
- comprueba por HTTP que el proveedor oficial responde;
- verifica señales fuertes de identidad en la respuesta;
- genera JSON de auditoría;
- bloquea cualquier identidad que no pueda verificarse.

La recuperación completa y normalización del contenido se implementará después de
validar que estos puntos de entrada oficiales son estables desde el entorno local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


FUENTES = [
    {
        "tipo_fuente": "FUENTE_OFICIAL_UE",
        "identificador_oficial": "12008E/AFI/DCL/17",
        "descripcion": "Declaración n.º 17 relativa a la primacía",
        "organismo": "EUR-Lex",
        "url_oficial": (
            "https://eur-lex.europa.eu/legal-content/ES/TXT/"
            "?uri=CELEX:12008E/AFI/DCL/17"
        ),
        "senales_identidad": [
            "12008E/AFI/DCL/17",
            "Declaración relativa a la primacía",
        ],
    },
    {
        "tipo_fuente": "JURISPRUDENCIA_TJUE",
        "identificador_oficial": "ECLI:EU:C:1964:66",
        "descripcion": "TJUE, Costa/ENEL, asunto 6/64",
        "organismo": "CURIA",
        "url_oficial": (
            "https://curia.europa.eu/juris/documents.jsf"
            "?critereEcli=ECLI%3AEU%3AC%3A1964%3A66&language=ES"
        ),
        "senales_identidad": [
            "ECLI:EU:C:1964:66",
            "Costa",
        ],
    },
    {
        "tipo_fuente": "JURISPRUDENCIA_TJUE",
        "identificador_oficial": "ECLI:EU:C:1978:49",
        "descripcion": "TJUE, Simmenthal, asunto 106/77",
        "organismo": "CURIA",
        "url_oficial": (
            "https://curia.europa.eu/juris/documents.jsf"
            "?critereEcli=ECLI%3AEU%3AC%3A1978%3A49&language=ES"
        ),
        "senales_identidad": [
            "ECLI:EU:C:1978:49",
            "Simmenthal",
        ],
    },
    {
        "tipo_fuente": "JURISPRUDENCIA_CONSTITUCIONAL",
        "identificador_oficial": "BOE-T-2005-111",
        "descripcion": "Tribunal Constitucional, Declaración 1/2004",
        "organismo": "BOE",
        "url_oficial": "https://www.boe.es/buscar/doc.php?id=BOE-T-2005-111",
        "senales_identidad": [
            "BOE-T-2005-111",
            "Declaración 1/2004",
        ],
    },
]


def normalizar_espacios(texto: str) -> str:
    return " ".join((texto or "").split())


def descargar(url: str, timeout: int) -> tuple[str, str, int]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NetReto-Constructor-Temario-V2/1.0",
            "Accept-Language": "es,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        bruto = resp.read()
        content_type = resp.headers.get("Content-Type", "")
        charset = resp.headers.get_content_charset() or "utf-8"
        texto = bruto.decode(charset, errors="replace")
        return texto, content_type, int(getattr(resp, "status", 200) or 200)


def contiene_senal(texto: str, senal: str) -> bool:
    # Las páginas oficiales pueden codificar separadores/espacios de forma distinta.
    a = normalizar_espacios(texto).casefold()
    b = normalizar_espacios(senal).casefold()
    if b in a:
        return True
    # Respaldo sólo para identificadores: ignorar puntuación HTML/URL.
    aa = re.sub(r"[^a-z0-9]+", "", a)
    bb = re.sub(r"[^a-z0-9]+", "", b)
    return len(bb) >= 10 and bb in aa


def auditar_fuente(fuente: dict, timeout: int) -> dict:
    base = {
        **fuente,
        "estado": "BLOQUEADA",
        "http_status": None,
        "content_type": None,
        "chars_respuesta": 0,
        "senales": [],
        "hash_respuesta": None,
        "error": None,
    }
    try:
        texto, content_type, status = descargar(fuente["url_oficial"], timeout)
        comprobaciones = [
            {"senal": s, "localizada": contiene_senal(texto, s)}
            for s in fuente["senales_identidad"]
        ]
        todas = all(x["localizada"] for x in comprobaciones)
        base.update(
            {
                "estado": "IDENTIDAD_VERIFICADA" if todas else "BLOQUEADA",
                "http_status": status,
                "content_type": content_type,
                "chars_respuesta": len(texto),
                "senales": comprobaciones,
                "hash_respuesta": hashlib.sha256(
                    texto.encode("utf-8")
                ).hexdigest(),
                "error": None if todas else "No se localizaron todas las señales de identidad.",
            }
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
    return base


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audita puntos de entrada oficiales externos de P05 sin modificar la BD."
    )
    ap.add_argument("--salida-json", required=True)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    salida = Path(args.salida_json).expanduser().resolve()

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA FUENTES EXTERNAS P05 - SIN BD")
    print("=" * 78)
    print(f"Fuentes esperadas................ {len(FUENTES)}")
    print()

    resultados = []
    for fuente in FUENTES:
        r = auditar_fuente(fuente, args.timeout)
        resultados.append(r)
        marca = "OK" if r["estado"] == "IDENTIDAD_VERIFICADA" else "ERROR"
        print(
            f"  {marca:<5} | {fuente['tipo_fuente']:<32} | "
            f"{fuente['identificador_oficial']}"
        )
        if r["error"]:
            print(f"          {r['error']}")

    verificadas = sum(r["estado"] == "IDENTIDAD_VERIFICADA" for r in resultados)
    bloqueadas = len(resultados) - verificadas
    estado = "LISTO_PARA_RECUPERACION" if bloqueadas == 0 else "BLOQUEADO"

    payload = {
        "version": 1,
        "naturaleza": "AUDITORIA_IDENTIDADES_FUENTES_EXTERNAS",
        "piloto": "A1_ESPECIAL_1_P05",
        "estado": estado,
        "fecha_auditoria_utc": datetime.now(timezone.utc).isoformat(),
        "resultados": resultados,
        "resumen": {
            "fuentes": len(resultados),
            "identidades_verificadas": verificadas,
            "bloqueadas": bloqueadas,
        },
        "reglas": {
            "bd_modificada": False,
            "contenido_persistido": False,
            "pertinencia_aceptada": False,
            "solo_proveedores_oficiales": True,
        },
    }

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"Estado............................ {estado}")
    print(f"Identidades verificadas.......... {verificadas}")
    print(f"Bloqueadas........................ {bloqueadas}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.")
    return 0 if bloqueadas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
