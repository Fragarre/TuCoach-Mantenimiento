"""
Constructor Temario V2 - planificador de alta de una fuente BOE nueva.

SOLO LECTURA respecto de la base de datos.

Objetivo:
- comprobar si un BOE-A-* ya está enlazado en norma_fuentes;
- comprobar si ya tiene artículos en articulos_fuente;
- localizar posibles normas canónicas existentes por nombre;
- consultar el índice oficial BOE mediante la infraestructura ya consolidada
  de ampliar_corpus_chat.py;
- verificar que los artículos objetivo existen en el índice actual;
- producir un plan de alta, sin insertar nada.

No modifica normas, norma_fuentes, articulos_fuente, temarios, bancos ni corpus.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import comun
import ampliar_corpus_chat as ampliar

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
PATRON_BOE = re.compile(r"^BOE-A-\d{4}-\d+$", re.I)


def normalizar(texto: str) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", x).strip()


def parsear_articulos(valor: str | None) -> list[str]:
    if not valor:
        return []
    salida = []
    vistos = set()
    for bruto in valor.split(","):
        art = ampliar.normalizar_articulo(bruto.strip())
        if art and art not in vistos:
            vistos.add(art)
            salida.append(art)
    return salida


def buscar_normas_por_nombre(con, nombre: str) -> list[dict]:
    if not nombre.strip():
        return []
    tokens = [t for t in normalizar(nombre).split() if len(t) >= 3]
    filas = con.execute(
        "SELECT id, nombre_canonico, clave_normalizada FROM normas ORDER BY id"
    ).fetchall()
    resultados = []
    for r in filas:
        bolsa = normalizar(f"{r['nombre_canonico'] or ''} {r['clave_normalizada'] or ''}")
        if tokens and all(t in bolsa for t in tokens):
            resultados.append({
                "norma_id": int(r["id"]),
                "nombre_canonico": str(r["nombre_canonico"] or ""),
                "clave_normalizada": str(r["clave_normalizada"] or ""),
            })
    return resultados


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Planifica el alta de una nueva fuente BOE sin modificar la BD."
    )
    ap.add_argument("--id-boe", required=True)
    ap.add_argument("--nombre-esperado", default="")
    ap.add_argument("--articulos", default="")
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--salida-json")
    args = ap.parse_args()

    id_boe = args.id_boe.strip().upper()
    if not PATRON_BOE.fullmatch(id_boe):
        raise SystemExit(f"Identificador BOE no válido: {id_boe}")

    objetivos = parsear_articulos(args.articulos)
    db = Path(args.db).expanduser().resolve()

    with comun.conectar_sqlite_solo_lectura(db) as con:
        enlaces = con.execute(
            """
            SELECT nf.norma_id, n.nombre_canonico, nf.id_fuente, nf.titulo_fuente,
                   nf.departamento
            FROM norma_fuentes nf
            JOIN normas n ON n.id = nf.norma_id
            WHERE UPPER(nf.id_fuente) = UPPER(?)
            ORDER BY nf.norma_id
            """,
            (id_boe,),
        ).fetchall()
        articulos_locales = con.execute(
            """
            SELECT articulo_boe, id_bloque, titulo_bloque
            FROM articulos_fuente
            WHERE UPPER(id_boe)=UPPER(?)
            ORDER BY id
            """,
            (id_boe,),
        ).fetchall()
        normas_candidatas = buscar_normas_por_nombre(con, args.nombre_esperado)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PLAN ALTA NUEVA FUENTE BOE - SOLO LECTURA")
    print("=" * 78)
    print(f"BD............................... {db}")
    print(f"Fuente BOE....................... {id_boe}")
    print(f"Nombre esperado.................. {args.nombre_esperado or '(no indicado)'}")
    print(f"Enlaces norma_fuentes existentes. {len(enlaces)}")
    print(f"Artículos locales existentes..... {len(articulos_locales)}")
    print(f"Normas canónicas candidatas...... {len(normas_candidatas)}")
    for n in normas_candidatas:
        print(f"  CAND | [{n['norma_id']}] {n['nombre_canonico']}")
    print()

    error_boe = None
    incidencias = []
    actuales = {}
    try:
        candidatos = ampliar.candidatos_indice(id_boe)
        candidatos, inc1 = ampliar.corregir_ambiguedades(id_boe, candidatos)
        actuales, inc2 = ampliar.seleccionar_bloques_actuales(candidatos)
        incidencias = list(inc1) + list(inc2)
    except Exception as exc:
        error_boe = f"{type(exc).__name__}: {exc}"

    if error_boe:
        print(f"Consulta índice BOE............... ERROR")
        print(f"  {error_boe}")
        estado = "NO_PLANIFICABLE"
    else:
        print(f"Consulta índice BOE............... OK")
        print(f"Artículos actuales detectados..... {len(actuales)}")
        print(f"Incidencias índice................ {len(incidencias)}")
        for inc in incidencias[:20]:
            print(f"  INC  | {inc}")
        if len(incidencias) > 20:
            print(f"  ... {len(incidencias) - 20} incidencias más")

        faltan_objetivos = []
        print()
        print("Artículos objetivo:")
        if not objetivos:
            print("  (ninguno indicado)")
        for art in objetivos:
            bloque = actuales.get(art)
            if bloque is None:
                faltan_objetivos.append(art)
                print(f"  FALTA | art. {art}")
            else:
                print(f"  OK    | art. {art} | bloque={bloque.id_bloque} | {bloque.titulo}")

        if faltan_objetivos or incidencias:
            estado = "REVISAR"
        else:
            estado = "PLANIFICABLE"

    requiere_norma = len(enlaces) == 0 and len(normas_candidatas) == 0
    requiere_enlace = len(enlaces) == 0
    requiere_articulos = len(articulos_locales) == 0

    print()
    print("Plan estructural:")
    print(f"  Crear/revisar norma canónica.... {'SI' if requiere_norma else 'NO'}")
    print(f"  Crear enlace norma_fuentes...... {'SI' if requiere_enlace else 'NO'}")
    print(f"  Incorporar artículos fuente..... {'SI' if requiere_articulos else 'NO'}")
    print(f"  Estado.......................... {estado}")
    print()
    print("La base de datos NO ha sido modificada.")

    payload = {
        "version": 1,
        "naturaleza": "PLAN_ALTA_NUEVA_FUENTE_BOE",
        "estado": estado,
        "db": str(db),
        "id_boe": id_boe,
        "nombre_esperado": args.nombre_esperado,
        "enlaces_existentes": [dict(r) for r in enlaces],
        "articulos_locales_existentes": [dict(r) for r in articulos_locales],
        "normas_canonicas_candidatas": normas_candidatas,
        "indice_boe": {
            "error": error_boe,
            "articulos_actuales": len(actuales),
            "incidencias": incidencias,
            "articulos_objetivo": [
                {
                    "articulo": art,
                    "estado": "LOCALIZADO_EN_INDICE" if art in actuales else "NO_LOCALIZADO_EN_INDICE",
                    "id_bloque": actuales[art].id_bloque if art in actuales else None,
                    "titulo": actuales[art].titulo if art in actuales else None,
                }
                for art in objetivos
            ],
        },
        "plan": {
            "requiere_norma_canonica": requiere_norma,
            "requiere_enlace_norma_fuentes": requiere_enlace,
            "requiere_articulos_fuente": requiere_articulos,
        },
        "reglas": {
            "solo_lectura_bd": True,
            "sin_inserciones": True,
            "sin_actualizaciones": True,
        },
    }

    if args.salida_json:
        salida = Path(args.salida_json).expanduser().resolve()
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON escrito...................... {salida}")
        print("La escritura anterior afecta sólo al fichero de salida solicitado.")

    return 0 if estado in {"PLANIFICABLE", "REVISAR"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
