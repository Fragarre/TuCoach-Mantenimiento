"""
Constructor Temario V2 - auditoría de mapeo estructura literal -> artículos oficiales.

SOLO LECTURA.

Objetivo acotado:
- tomar los temas JURIDICO_EXPLICITO que contienen Título/Capítulo/Sección literal;
- resolver sus normas con el resolvedor existente;
- asociar cada referencia estructural a la norma literal que la precede;
- conservar el contexto padre Título -> Capítulo -> Sección;
- normalizar equivalencias ordinales literales/romanas (p. ej. Capítulo I = Capítulo Primero);
- comprobar si la estructura literal puede localizarse de forma unívoca en la
  jerarquía oficial disponible;
- expandir mecánicamente el bloque localizado a sus artículos;
- NO usar temario.csv ni temario_referencias;
- NO modificar BD, corpus, caché ni bancos.

Esta auditoría NO valida todavía el contenido final frente al temario consolidado.
Sólo comprueba si existe un camino determinista texto estructural literal -> bloque oficial.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance
import resolver_normas_temario_v2 as resolver
import auditar_explicitos_estructura_v2 as aud_estructura
import auditar_representabilidad_estructural_online_v2 as online

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"

ORDINALES = {
    "primero": "1", "primer": "1",
    "segundo": "2",
    "tercero": "3", "tercer": "3",
    "cuarto": "4",
    "quinto": "5",
    "sexto": "6",
    "septimo": "7",
    "octavo": "8",
    "noveno": "9",
    "decimo": "10",
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
    """Reduce una referencia estructural a nivel + ordinal estable."""
    n = norm(texto)
    if tipo == "PREAMBULO":
        return tipo, "preambulo"
    if tipo == "DISPOSICIONES_COMUNES":
        return tipo, "disposiciones comunes"

    pref = {"TITULO": "titulo", "CAPITULO": "capitulo", "SECCION": "seccion"}[tipo]
    resto = n[len(pref):].strip() if n.startswith(pref) else n

    if tipo == "TITULO":
        m = re.match(r"^(preliminar|[ivxlcdm]+|\d+|primero|primer|segundo|tercero|tercer|cuarto|quinto|sexto|septimo|octavo|noveno|decimo)\b", resto, re.I)
    elif tipo == "CAPITULO":
        m = re.match(r"^([ivxlcdm]+|\d+|primero|primer|segundo|tercero|tercer|cuarto|quinto|sexto|septimo|octavo|noveno|decimo)\b", resto, re.I)
    else:
        m = re.match(r"^(\d+|[ivxlcdm]+|primera|primero|segunda|segundo|tercera|tercero)\b", resto, re.I)

    if not m:
        return tipo, resto
    valor = m.group(1).lower()
    if valor == "preliminar":
        return tipo, valor
    return tipo, normalizar_ordinal(valor)


def localizar_citas(texto: str, citas, catalogo) -> list[dict]:
    """Localiza cada cita literal en el texto y conserva sólo resoluciones unívocas."""
    salida = []
    texto_lower = texto.lower()
    for cita in citas:
        pos = texto_lower.find(cita.texto.lower())
        if pos < 0:
            continue
        candidatos = resolver.candidatos_para_cita(cita, catalogo)
        if len(candidatos) != 1:
            continue
        salida.append({
            "inicio": pos,
            "fin": pos + len(cita.texto),
            "norma_id": candidatos[0].norma_id,
            "cita": cita.texto,
        })
    salida.sort(key=lambda x: x["inicio"])
    return salida


def asociar_estructura_a_norma(hallazgos: list[dict], citas_pos: list[dict]) -> list[dict]:
    """
    Asocia cada estructura a la última norma literal precedente y conserva
    el Título/Capítulo padre dentro de esa misma norma.
    """
    salida = []
    contexto: dict[int, dict[str, tuple[str, str] | None]] = {}

    for h in sorted(hallazgos, key=lambda x: x["inicio"]):
        previas = [c for c in citas_pos if c["inicio"] <= h["inicio"]]
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
    """Construye bloques oficiales con su identidad y contexto padre."""
    salida = []
    vistos = set()

    for a in payload.get("articulos", []):
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

            padres = {
                "titulo": (),
                "capitulo": ("titulo",),
                "seccion": ("titulo", "capitulo"),
            }[campo]
            arts = [
                x for x in payload.get("articulos", [])
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for t in temas:
        estructura.clasificar_tema(t)

    casos = []
    for t in temas:
        clase, _ = alcance.clasificar(t)
        est = aud_estructura.detectar_estructura(t.texto) if clase == "JURIDICO_EXPLICITO" else []
        if est:
            casos.append((t, est))

    with comun.conectar_sqlite_solo_lectura(db) as con:
        catalogo = resolver.cargar_catalogo(con)
        fuentes = {}
        for row in con.execute("SELECT norma_id, id_fuente FROM norma_fuentes ORDER BY norma_id, id_fuente"):
            fuentes.setdefault(int(row[0]), []).append(str(row[1]))

    payload_cache = {}
    ok = parcial = fallo = 0

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - MAPEO ESTRUCTURA LITERAL -> ARTICULOS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Casos con estructura literal..... {len(casos)}")
    print()

    for tema, hallazgos in casos:
        citas = resolver.detectar_citas(tema.texto)
        citas_pos = localizar_citas(tema.texto, citas, catalogo)
        normas = list(dict.fromkeys(c["norma_id"] for c in citas_pos))
        asociados = asociar_estructura_a_norma(hallazgos, citas_pos)

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | normas={normas or 'NINGUNA'}")
        if not normas:
            fallo += 1
            print("  FALLO: norma no resuelta de forma unívoca")
            print()
            continue

        pendientes = [h for h in asociados if h["tipo"] not in {"PREAMBULO", "DISPOSICIONES_COMUNES"}]
        encontrados = []
        ambiguos = []
        sin_match = []

        indices = {}
        for nid in normas:
            indices[nid] = []
            for fid in fuentes.get(nid, []):
                if fid not in payload_cache:
                    payload_cache[fid] = online.cargar_payload(fid)
                payload, origen = payload_cache[fid]
                if payload:
                    indices[nid].append((fid, origen, indice_bloques(payload)))

        for h in pendientes:
            nid = h.get("norma_id")
            if nid is None:
                sin_match.append(h)
                continue

            matches = []
            for fid, origen, bloques in indices.get(nid, []):
                for b in bloques:
                    if coincide_bloque(h, b):
                        matches.append((nid, fid, origen, b))

            if len(matches) == 1:
                nid2, fid, origen, b = matches[0]
                encontrados.append((h, nid2, fid, origen, b))
            elif len(matches) > 1:
                ambiguos.append((h, matches))
            else:
                sin_match.append(h)

        if pendientes and len(encontrados) == len(pendientes):
            estado = "MAPEO_UNIVOCO"
            ok += 1
        elif encontrados:
            estado = "MAPEO_PARCIAL"
            parcial += 1
        else:
            estado = "SIN_MAPEO_UNIVOCO"
            fallo += 1

        print(f"  estado: {estado}")
        for h, nid, fid, origen, b in encontrados:
            arts = [str(a.get("articulo")) for a in b["articulos"]]
            muestra = ", ".join(arts[:12]) + (" ..." if len(arts) > 12 else "")
            padre = ""
            if h.get("titulo_padre"):
                padre += f" | bajo {h['titulo_padre'][0]} {h['titulo_padre'][1]}"
            if h.get("capitulo_padre"):
                padre += f" > {h['capitulo_padre'][0]} {h['capitulo_padre'][1]}"
            print(
                f"  OK {h['tipo']} {h['texto']} -> norma {nid}{padre} | "
                f"{fid} | {origen} | n={len(arts)} | {muestra}"
            )
        for h, matches in ambiguos:
            print(f"  AMBIGUO {h['tipo']} {h['texto']} -> norma {h.get('norma_id')} | {len(matches)} coincidencias")
        for h in sin_match:
            print(f"  SIN MATCH {h['tipo']} {h['texto']} -> norma {h.get('norma_id')}")
        if any(h["tipo"] in {"PREAMBULO", "DISPOSICIONES_COMUNES"} for h in asociados):
            print("  AVISO: Preámbulo/Disposiciones comunes no se evalúan en este piloto.")
        print()

    print("Resumen:")
    print(f"  Casos........................... {len(casos)}")
    print(f"  Mapeo unívoco................... {ok}")
    print(f"  Mapeo parcial................... {parcial}")
    print(f"  Sin mapeo unívoco............... {fallo}")
    print()
    print("No se ha consultado temario_referencias.")
    print("No se ha consultado temario.csv.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("La caché NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
