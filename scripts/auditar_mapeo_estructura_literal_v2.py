"""
Constructor Temario V2 - auditoría de mapeo estructura literal -> artículos oficiales.

SOLO LECTURA.

Objetivo acotado:
- tomar los temas JURIDICO_EXPLICITO que contienen Título/Capítulo/Sección literal;
- resolver sus normas con el resolvedor existente;
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


def norm(s: str) -> str:
    return resolver.normalizar(s or "")


def clave_estructura(tipo: str, texto: str) -> tuple[str, str]:
    n = norm(texto)
    if tipo == "PREAMBULO":
        return tipo, "preambulo"
    if tipo == "DISPOSICIONES_COMUNES":
        return tipo, "disposiciones comunes"
    pref = {"TITULO": "titulo", "CAPITULO": "capitulo", "SECCION": "seccion"}[tipo]
    resto = n[len(pref):].strip() if n.startswith(pref) else n
    return tipo, resto


def indice_bloques(payload: dict) -> dict[tuple[str, str], list[dict]]:
    salida: dict[tuple[str, str], list[dict]] = {}
    vistos = set()
    for a in payload.get("articulos", []):
        for tipo, campo in (("TITULO", "titulo"), ("CAPITULO", "capitulo"), ("SECCION", "seccion")):
            valor = a.get(campo)
            if not valor:
                continue
            k = clave_estructura(tipo, valor)
            identidad = (k, valor)
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
            salida.setdefault(k, []).append({"texto": valor, "articulos": arts})
    return salida


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
        normas = []
        for cita in citas:
            candidatos = resolver.candidatos_para_cita(cita, catalogo)
            if len(candidatos) == 1:
                nid = candidatos[0].norma_id
                if nid not in normas:
                    normas.append(nid)

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | normas={normas or 'NINGUNA'}")
        if not normas:
            fallo += 1
            print("  FALLO: norma no resuelta de forma unívoca")
            print()
            continue

        pendientes = [h for h in hallazgos if h["tipo"] not in {"PREAMBULO", "DISPOSICIONES_COMUNES"}]
        encontrados = []
        ambiguos = []
        sin_match = []

        indices = []
        for nid in normas:
            for fid in fuentes.get(nid, []):
                if fid not in payload_cache:
                    payload_cache[fid] = online.cargar_payload(fid)
                payload, origen = payload_cache[fid]
                if payload:
                    indices.append((nid, fid, origen, indice_bloques(payload)))

        for h in pendientes:
            k = clave_estructura(h["tipo"], h["texto"])
            matches = []
            for nid, fid, origen, idx in indices:
                for b in idx.get(k, []):
                    matches.append((nid, fid, origen, b))
            if len(matches) == 1:
                nid, fid, origen, b = matches[0]
                encontrados.append((h, nid, fid, origen, b))
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
            print(f"  OK {h['tipo']} {h['texto']} -> norma {nid} | {fid} | {origen} | n={len(arts)} | {muestra}")
        for h, matches in ambiguos:
            print(f"  AMBIGUO {h['tipo']} {h['texto']} -> {len(matches)} coincidencias")
        for h in sin_match:
            print(f"  SIN MATCH {h['tipo']} {h['texto']}")
        if any(h["tipo"] in {"PREAMBULO", "DISPOSICIONES_COMUNES"} for h in hallazgos):
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
