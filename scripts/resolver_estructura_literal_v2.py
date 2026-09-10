"""
Constructor Temario V2 - resolución reutilizable de estructura literal -> artículos.

SOLO LECTURA respecto de SQLite/corpus.

Contrato:
- parte del texto oficial del tema;
- asocia Título/Capítulo/Sección a la norma literal precedente;
- usa únicamente jerarquía oficial disponible;
- normaliza ordinales romanos/literales;
- no infiere alcance cuando una norma sólo contiene descripción conceptual;
- no consulta temario.csv ni temario_referencias;
- no persiste resultados.
"""
from __future__ import annotations

import re

import resolver_normas_temario_v2 as resolver
import auditar_explicitos_estructura_v2 as aud_estructura
import auditar_representabilidad_estructural_online_v2 as online


ORDINALES = {
    "primero": "1", "primer": "1", "primera": "1",
    "segundo": "2", "segunda": "2",
    "tercero": "3", "tercer": "3", "tercera": "3",
    "cuarto": "4", "cuarta": "4",
    "quinto": "5", "quinta": "5",
    "sexto": "6", "sexta": "6",
    "septimo": "7", "septima": "7",
    "octavo": "8", "octava": "8",
    "noveno": "9", "novena": "9",
    "decimo": "10", "decima": "10",
}
ROMANOS = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15",
}


def norm(s: str) -> str:
    return resolver.normalizar(s or "")


def normalizar_ordinal(valor: str) -> str:
    v = norm(valor)
    if v.isdigit():
        return str(int(v))
    if v in ROMANOS:
        return ROMANOS[v]
    if v in ORDINALES:
        return ORDINALES[v]
    return v


def clave_estructura(tipo: str, texto: str) -> tuple[str, str]:
    n = norm(texto)
    if tipo == "PREAMBULO":
        return tipo, "preambulo"
    if tipo == "DISPOSICIONES_COMUNES":
        return tipo, "disposiciones comunes"

    pref = {"TITULO": "titulo", "CAPITULO": "capitulo", "SECCION": "seccion"}[tipo]
    resto = n[len(pref):].strip() if n.startswith(pref) else n
    palabras = "primero|primer|primera|segundo|segunda|tercero|tercer|tercera|cuarto|cuarta|quinto|quinta|sexto|sexta|septimo|septima|octavo|octava|noveno|novena|decimo|decima"
    if tipo == "TITULO":
        patron = rf"^(preliminar|[ivxlcdm]+|\d+|{palabras})\b"
    elif tipo == "CAPITULO":
        patron = rf"^([ivxlcdm]+|\d+|{palabras})\b"
    else:
        patron = rf"^(\d+|[ivxlcdm]+|{palabras})\b"
    m = re.match(patron, resto, re.I)
    if not m:
        return tipo, resto
    valor = m.group(1).lower()
    if valor == "preliminar":
        return tipo, valor
    return tipo, normalizar_ordinal(valor)


def localizar_citas(texto: str, catalogo) -> list[dict]:
    salida = []
    texto_lower = texto.lower()
    for cita in resolver.detectar_citas(texto):
        pos = texto_lower.find(cita.texto.lower())
        if pos < 0:
            continue
        candidatos = resolver.candidatos_para_cita(cita, catalogo)
        salida.append({
            "inicio": pos,
            "fin": pos + len(cita.texto),
            "cita": cita,
            "candidatos": candidatos,
            "norma_id": candidatos[0].norma_id if len(candidatos) == 1 else None,
        })
    salida.sort(key=lambda x: x["inicio"])
    return salida


def asociar_estructura(texto: str, citas_pos: list[dict]) -> list[dict]:
    hallazgos = aud_estructura.detectar_estructura(texto)
    salida = []
    contexto: dict[int, dict[str, tuple[str, str] | None]] = {}

    for h in sorted(hallazgos, key=lambda x: x["inicio"]):
        previas = [c for c in citas_pos if c["inicio"] <= h["inicio"] and c["norma_id"] is not None]
        nid = previas[-1]["norma_id"] if previas else None
        item = dict(h)
        item["norma_id"] = nid
        item["clave"] = clave_estructura(h["tipo"], h["texto"])
        item["titulo_padre"] = None
        item["capitulo_padre"] = None

        if nid is not None:
            ctx = contexto.setdefault(nid, {"titulo": None, "capitulo": None})
            if h["tipo"] == "TITULO":
                ctx["titulo"] = item["clave"]
                ctx["capitulo"] = None
            elif h["tipo"] == "CAPITULO":
                item["titulo_padre"] = ctx["titulo"]
                ctx["capitulo"] = item["clave"]
            elif h["tipo"] == "SECCION":
                item["titulo_padre"] = ctx["titulo"]
                item["capitulo_padre"] = ctx["capitulo"]
        salida.append(item)
    return salida


def indice_bloques(payload: dict) -> list[dict]:
    salida = []
    vistos = set()
    articulos = payload.get("articulos", [])
    for a in articulos:
        titulo = a.get("titulo")
        capitulo = a.get("capitulo")
        seccion = a.get("seccion")
        titulo_k = clave_estructura("TITULO", titulo) if titulo else None
        capitulo_k = clave_estructura("CAPITULO", capitulo) if capitulo else None
        definiciones = (
            ("TITULO", "titulo", titulo, None, None),
            ("CAPITULO", "capitulo", capitulo, titulo_k, None),
            ("SECCION", "seccion", seccion, titulo_k, capitulo_k),
        )
        for tipo, campo, valor, titulo_padre, capitulo_padre in definiciones:
            if not valor:
                continue
            k = clave_estructura(tipo, valor)
            identidad = (tipo, k, titulo_padre, capitulo_padre, valor)
            if identidad in vistos:
                continue
            vistos.add(identidad)
            padres = {"titulo": (), "capitulo": ("titulo",), "seccion": ("titulo", "capitulo")}[campo]
            arts = [
                x for x in articulos
                if x.get(campo) == valor
                and all(x.get(parent) == a.get(parent) for parent in padres)
            ]
            salida.append({
                "tipo": tipo,
                "clave": k,
                "texto": valor,
                "titulo_padre": titulo_padre,
                "capitulo_padre": capitulo_padre,
                "articulos": arts,
            })
    return salida


def coincide_bloque(h: dict, b: dict) -> bool:
    if h["tipo"] != b["tipo"] or h["clave"] != b["clave"]:
        return False
    if h.get("titulo_padre") and h["titulo_padre"] != b.get("titulo_padre"):
        return False
    if h.get("capitulo_padre") and h["capitulo_padre"] != b.get("capitulo_padre"):
        return False
    return True


def es_bloque_efectivo(h: dict, resueltos: list[dict]) -> bool:
    """Un padre explícito sólo aporta contexto si el temario limita su contenido con hijos explícitos."""
    if h["tipo"] == "TITULO":
        return not any(
            x["hallazgo"].get("norma_id") == h.get("norma_id")
            and x["hallazgo"]["tipo"] in {"CAPITULO", "SECCION"}
            and x["hallazgo"].get("titulo_padre") == h["clave"]
            for x in resueltos
        )
    if h["tipo"] == "CAPITULO":
        return not any(
            x["hallazgo"].get("norma_id") == h.get("norma_id")
            and x["hallazgo"]["tipo"] == "SECCION"
            and x["hallazgo"].get("titulo_padre") == h.get("titulo_padre")
            and x["hallazgo"].get("capitulo_padre") == h["clave"]
            for x in resueltos
        )
    return True


def resolver_tema(texto: str, catalogo, fuentes_por_norma: dict[int, list[str]], payload_cache: dict | None = None) -> dict:
    """Devuelve alcance estructural comprobado sin tomar decisiones jurídicas inferidas."""
    payload_cache = payload_cache if payload_cache is not None else {}
    citas_pos = localizar_citas(texto, catalogo)
    normas_univocas = list(dict.fromkeys(c["norma_id"] for c in citas_pos if c["norma_id"] is not None))
    asociados = asociar_estructura(texto, citas_pos)

    indices: dict[int, list[tuple[str, str, list[dict]]]] = {}
    fuentes_sin_estructura: dict[int, list[str]] = {}
    for nid in normas_univocas:
        indices[nid] = []
        fuentes_sin_estructura[nid] = []
        for fid in fuentes_por_norma.get(nid, []):
            if fid not in payload_cache:
                payload_cache[fid] = online.cargar_payload(fid)
            payload, origen = payload_cache[fid]
            if payload and payload.get("articulos"):
                indices[nid].append((fid, origen, indice_bloques(payload)))
            else:
                fuentes_sin_estructura[nid].append(fid)

    resueltos = []
    ambiguos = []
    sin_match = []
    no_articulo = []
    for h in asociados:
        if h["tipo"] in {"PREAMBULO", "DISPOSICIONES_COMUNES"}:
            no_articulo.append(h)
            continue
        nid = h.get("norma_id")
        if nid is None:
            sin_match.append(h)
            continue
        matches = []
        for fid, origen, bloques in indices.get(nid, []):
            for b in bloques:
                if coincide_bloque(h, b):
                    matches.append((fid, origen, b))
        if len(matches) == 1:
            fid, origen, b = matches[0]
            resueltos.append({"hallazgo": h, "fuente": fid, "origen": origen, "bloque": b})
        elif len(matches) > 1:
            ambiguos.append({"hallazgo": h, "coincidencias": len(matches)})
        else:
            sin_match.append(h)

    efectivos = [r for r in resueltos if es_bloque_efectivo(r["hallazgo"], resueltos)]
    articulos = []
    vistos_art = set()
    for r in efectivos:
        nid = r["hallazgo"]["norma_id"]
        for a in r["bloque"]["articulos"]:
            clave = (nid, r["fuente"], str(a.get("articulo")))
            if clave in vistos_art:
                continue
            vistos_art.add(clave)
            articulos.append({
                "norma_id": nid,
                "fuente": r["fuente"],
                "articulo": str(a.get("articulo")),
                "origen_decision": "ESTRUCTURA_LITERAL_UNIVOCA",
            })

    normas_con_estructura = {h["norma_id"] for h in asociados if h.get("norma_id") is not None and h["tipo"] not in {"PREAMBULO", "DISPOSICIONES_COMUNES"}}
    normas_sin_estructura_literal = [nid for nid in normas_univocas if nid not in normas_con_estructura]
    normas_fuente_no_materializable = [
        nid for nid in normas_con_estructura
        if not indices.get(nid) and fuentes_sin_estructura.get(nid)
    ]

    completo = bool(normas_univocas) and not ambiguos and not sin_match and not normas_sin_estructura_literal and all(
        any(a["norma_id"] == nid for a in articulos) for nid in normas_univocas
    )

    return {
        "citas": citas_pos,
        "normas_univocas": normas_univocas,
        "estructura": asociados,
        "bloques_resueltos": resueltos,
        "bloques_efectivos": efectivos,
        "articulos": articulos,
        "ambiguos": ambiguos,
        "sin_match": sin_match,
        "no_articulo": no_articulo,
        "normas_sin_estructura_literal": normas_sin_estructura_literal,
        "normas_fuente_no_materializable": normas_fuente_no_materializable,
        "completo": completo,
    }
