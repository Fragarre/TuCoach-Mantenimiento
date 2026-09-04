#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
AUDITORIAS_DEFECTO = RAIZ / "auditorias"


TABLAS_REQUERIDAS = {
    "convocatorias",
    "banco_preguntas",
    "lote_preguntas",
    "normas",
    "norma_fuentes",
    "articulos_fuente",
}

COLUMNAS_REQUERIDAS = {
    "convocatorias": {"id", "codigo", "activa"},
    "banco_preguntas": {"convocatoria_id", "pregunta_id", "estado"},
    "lote_preguntas": {
        "id", "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d",
        "respuesta_correcta", "tipo_clasificacion", "tipo_fuente",
        "norma_id_normalizada", "articulo_normalizado",
        "nombre_norma_normalizado", "origen_oposicion",
    },
    "normas": {"id", "nombre_canonico"},
    "norma_fuentes": {"norma_id", "id_fuente"},
    "articulos_fuente": {
        "id", "id_boe", "id_bloque", "articulo_boe",
        "titulo_bloque", "texto",
    },
}


def abrir_ro(db: Path) -> sqlite3.Connection:
    uri = db.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def validar_estructura(con: sqlite3.Connection) -> None:
    tablas = {
        str(r[0])
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    faltan = sorted(TABLAS_REQUERIDAS - tablas)
    if faltan:
        raise RuntimeError("Faltan tablas requeridas: " + ", ".join(faltan))

    for tabla, requeridas in COLUMNAS_REQUERIDAS.items():
        cols = {str(r[1]) for r in con.execute(f"PRAGMA table_info({tabla})")}
        faltan_cols = sorted(requeridas - cols)
        if faltan_cols:
            raise RuntimeError(
                f"Faltan columnas en {tabla}: " + ", ".join(faltan_cols)
            )


def norm_texto(s: str | None) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_articulo(s: str | None) -> str:
    s = norm_texto(s)
    s = re.sub(r"^(articulo|art\.?)\s*", "", s)
    s = s.strip()
    # articulo_normalizado puede llevar apartado/letra (17.2.d, 51.1.5).
    # articulos_fuente almacena el artículo base. Conservamos solo número + bis/ter...
    m = re.match(r"^(\d+)(?:\.\d+)*(?:\.[a-z])?\s*(bis|ter|quater|quinquies)?", s)
    if m:
        base = m.group(1)
        suf = m.group(2)
        return f"{base} {suf}".strip() if suf else base
    return s


def clave_orden(pregunta_id: int) -> str:
    return hashlib.sha256(f"opo-auditoria-respuestas:{pregunta_id}".encode()).hexdigest()


def preguntas_incluidas(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            lp.id,
            lp.enunciado,
            lp.opcion_a,
            lp.opcion_b,
            lp.opcion_c,
            lp.opcion_d,
            lp.respuesta_correcta,
            lp.tipo_fuente,
            lp.origen_oposicion,
            lp.norma_id_normalizada,
            lp.articulo_normalizado,
            lp.nombre_norma_normalizado,
            GROUP_CONCAT(DISTINCT c.codigo) AS convocatorias
        FROM lote_preguntas lp
        JOIN banco_preguntas bp ON bp.pregunta_id = lp.id
        JOIN convocatorias c ON c.id = bp.convocatoria_id
        WHERE bp.estado = 'INCLUIDA'
          AND COALESCE(c.activa, 1) = 1
          AND lp.tipo_clasificacion = 'JURIDICA'
          AND lp.norma_id_normalizada IS NOT NULL
          AND TRIM(COALESCE(lp.articulo_normalizado, '')) <> ''
        GROUP BY lp.id
        ORDER BY lp.id
        """
    ).fetchall()


def seleccionar_piloto(
    filas: list[sqlite3.Row],
    cantidad: int,
    forzar_ids: list[int],
) -> list[sqlite3.Row]:
    por_id = {int(r["id"]): r for r in filas}
    seleccion: list[sqlite3.Row] = []
    usados: set[int] = set()

    for pid in forzar_ids:
        if pid not in por_id:
            raise RuntimeError(
                f"El ID forzado {pid} no es una pregunta jurídica incluida "
                "en banco activo con norma/artículo normalizados."
            )
        seleccion.append(por_id[pid])
        usados.add(pid)

    # Muestreo reproducible, intentando cubrir primero normas distintas.
    restantes = [r for r in filas if int(r["id"]) not in usados]
    por_norma: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for r in restantes:
        por_norma[int(r["norma_id_normalizada"])].append(r)

    for nid in por_norma:
        por_norma[nid].sort(key=lambda r: clave_orden(int(r["id"])))

    normas = sorted(
        por_norma,
        key=lambda nid: hashlib.sha256(f"norma:{nid}".encode()).hexdigest(),
    )

    while len(seleccion) < cantidad:
        avanzo = False
        for nid in normas:
            if len(seleccion) >= cantidad:
                break
            if por_norma[nid]:
                seleccion.append(por_norma[nid].pop(0))
                avanzo = True
        if not avanzo:
            break

    return seleccion[:cantidad]


def fuentes_norma(con: sqlite3.Connection, norma_id: int) -> list[str]:
    return [
        str(r[0])
        for r in con.execute(
            "SELECT id_fuente FROM norma_fuentes WHERE norma_id=? ORDER BY id_fuente",
            (norma_id,),
        )
    ]


def articulos_norma(
    con: sqlite3.Connection,
    fuentes: list[str],
) -> list[sqlite3.Row]:
    if not fuentes:
        return []
    marcas = ",".join("?" for _ in fuentes)
    return con.execute(
        f"""
        SELECT id, id_boe, id_bloque, articulo_boe, titulo_bloque, texto
        FROM articulos_fuente
        WHERE id_boe IN ({marcas})
          AND TRIM(COALESCE(texto,'')) <> ''
        ORDER BY id_boe, id
        """,
        fuentes,
    ).fetchall()


def localizar_articulo(
    articulos: list[sqlite3.Row],
    articulo_objetivo: str,
) -> list[sqlite3.Row]:
    objetivo = norm_articulo(articulo_objetivo)
    exactos = [
        r for r in articulos
        if norm_articulo(r["articulo_boe"]) == objetivo
    ]
    if exactos:
        return exactos

    # Segundo intento conservador sobre título de bloque.
    exactos_titulo = [
        r for r in articulos
        if norm_articulo(r["titulo_bloque"]) == objetivo
    ]
    return exactos_titulo


def referencias_internas(texto: str) -> list[str]:
    refs = set()
    for m in re.finditer(
        r"\bart[ií]culos?\s+(\d+(?:\.\d+)?(?:\s*(?:bis|ter|quater|quinquies))?)",
        texto or "",
        flags=re.IGNORECASE,
    ):
        refs.add(norm_articulo(m.group(1)))
    return sorted(refs)


def construir_registro(
    con: sqlite3.Connection,
    q: sqlite3.Row,
    cache_normas: dict[int, tuple[list[str], list[sqlite3.Row]]],
) -> dict:
    nid = int(q["norma_id_normalizada"])

    if nid not in cache_normas:
        fs = fuentes_norma(con, nid)
        arts = articulos_norma(con, fs)
        cache_normas[nid] = (fs, arts)

    fuentes, articulos = cache_normas[nid]
    localizados = localizar_articulo(articulos, str(q["articulo_normalizado"]))

    texto_principal = "\n\n".join(
        f"[{r['id_boe']} | {r['titulo_bloque']}]\n{r['texto']}"
        for r in localizados
    )

    refs = referencias_internas(texto_principal)
    relacionados = []
    for ref in refs:
        if ref == norm_articulo(str(q["articulo_normalizado"])):
            continue
        rr = localizar_articulo(articulos, ref)
        if rr:
            relacionados.append({
                "articulo": ref,
                "textos": [
                    {
                        "fuente": str(x["id_boe"]),
                        "titulo": str(x["titulo_bloque"]),
                        "texto": str(x["texto"]),
                    }
                    for x in rr
                ],
            })

    nombre = con.execute(
        "SELECT nombre_canonico FROM normas WHERE id=?",
        (nid,),
    ).fetchone()

    incidencias = []
    if not fuentes:
        incidencias.append("NORMA_SIN_FUENTE")
    if not articulos:
        incidencias.append("NORMA_SIN_TEXTO_RAG")
    if not localizados:
        incidencias.append("ARTICULO_PRINCIPAL_NO_LOCALIZADO")

    return {
        "pregunta_id": int(q["id"]),
        "convocatorias": str(q["convocatorias"] or ""),
        "enunciado": str(q["enunciado"]),
        "opciones": {
            "A": str(q["opcion_a"]),
            "B": str(q["opcion_b"]),
            "C": str(q["opcion_c"]),
            "D": str(q["opcion_d"]),
        },
        "respuesta_correcta_almacenada": str(q["respuesta_correcta"]),
        "tipo_fuente": str(q["tipo_fuente"]),
        "origen_oposicion": str(q["origen_oposicion"] or ""),
        "norma_id": nid,
        "norma": (
            str(nombre["nombre_canonico"])
            if nombre is not None
            else str(q["nombre_norma_normalizado"] or "")
        ),
        "articulo_normalizado": str(q["articulo_normalizado"]),
        "fuentes_rag": fuentes,
        "articulos_rag_no_vacios": len(articulos),
        "articulo_principal_localizado": bool(localizados),
        "texto_articulo_principal": texto_principal,
        "articulos_relacionados_citados": relacionados,
        "incidencias_evidencia": incidencias,
        # Deliberadamente NO se decide aquí si la respuesta es correcta.
        "dictamen": "PENDIENTE_REVISION_JURIDICA",
    }


def escribir_json(path: Path, meta: dict, registros: list[dict]) -> None:
    payload = {"meta": meta, "preguntas": registros}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def escribir_html(path: Path, meta: dict, registros: list[dict]) -> None:
    bloques = []
    for r in registros:
        opciones = "".join(
            f"<li><b>{letra})</b> {esc(texto)}</li>"
            for letra, texto in r["opciones"].items()
        )
        relacionados = ""
        for rel in r["articulos_relacionados_citados"]:
            for t in rel["textos"]:
                relacionados += (
                    f"<h5>Artículo relacionado {esc(rel['articulo'])} "
                    f"· {esc(t['fuente'])}</h5>"
                    f"<pre>{esc(t['texto'])}</pre>"
                )

        incidencias = ", ".join(r["incidencias_evidencia"]) or "NINGUNA"
        bloques.append(f"""
        <section>
          <h2>Pregunta {r['pregunta_id']}</h2>
          <p><b>Convocatoria(s):</b> {esc(r['convocatorias'])}
             · <b>Fuente pregunta:</b> {esc(r['tipo_fuente'])}
             · <b>Origen:</b> {esc(r['origen_oposicion'])}</p>
          <p><b>Norma:</b> [{r['norma_id']}] {esc(r['norma'])}
             · <b>Artículo:</b> {esc(r['articulo_normalizado'])}</p>
          <p><b>Enunciado:</b> {esc(r['enunciado'])}</p>
          <ol style="list-style:none;padding-left:0">{opciones}</ol>
          <p class="respuesta"><b>Respuesta almacenada:</b>
             {esc(r['respuesta_correcta_almacenada'])}</p>
          <p><b>Fuentes RAG:</b> {esc(", ".join(r['fuentes_rag']))}
             · <b>Artículos RAG con texto:</b> {r['articulos_rag_no_vacios']}
             · <b>Incidencias de evidencia:</b> {esc(incidencias)}</p>
          <h4>Artículo principal recuperado del Corpus RAG</h4>
          <pre>{esc(r['texto_articulo_principal'])}</pre>
          {relacionados}
        </section>
        """)

    doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Auditoría piloto de respuestas jurídicas</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 24px auto; line-height: 1.35; }}
section {{ border-top: 3px solid #444; padding: 18px 0 30px; }}
pre {{ white-space: pre-wrap; background: #f4f4f4; padding: 12px; }}
.respuesta {{ font-size: 1.1em; }}
.ok {{ font-weight: bold; }}
</style>
</head>
<body>
<h1>Auditoría piloto de respuestas jurídicas · SOLO LECTURA</h1>
<p><b>Base:</b> {esc(meta['base'])}</p>
<p><b>Preguntas jurídicas incluidas disponibles:</b> {meta['universo']}</p>
<p><b>Piloto:</b> {meta['cantidad']}</p>
<p><b>Regla:</b> este script NO modifica la base y NO cambia ni decide respuestas.
Solo prepara evidencia jurídica del Corpus RAG para revisión.</p>
{''.join(bloques)}
</body>
</html>"""
    path.write_text(doc, encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Prepara un piloto reproducible para auditar respuestas jurídicas "
            "contra el Corpus RAG. SOLO LECTURA."
        )
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--cantidad", type=int, default=100)
    p.add_argument(
        "--forzar-id",
        type=int,
        action="append",
        default=[],
        help="ID que debe entrar en el piloto. Puede repetirse.",
    )
    p.add_argument("--salida", default=str(AUDITORIAS_DEFECTO))
    args = p.parse_args()

    db = Path(args.db).resolve()
    salida = Path(args.salida).resolve()

    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1
    if args.cantidad <= 0:
        print("ERROR: --cantidad debe ser > 0")
        return 1

    forzados = list(dict.fromkeys(args.forzar_id or []))
    if 11362 not in forzados:
        forzados.insert(0, 11362)

    with abrir_ro(db) as con:
        validar_estructura(con)
        universo = preguntas_incluidas(con)
        seleccion = seleccionar_piloto(universo, args.cantidad, forzados)

        if len(seleccion) != min(args.cantidad, len(universo)):
            raise RuntimeError(
                f"No se pudo construir el piloto solicitado: "
                f"{len(seleccion)}/{args.cantidad}"
            )

        cache_normas = {}
        registros = [
            construir_registro(con, q, cache_normas)
            for q in seleccion
        ]

    salida.mkdir(parents=True, exist_ok=True)
    html_path = salida / "auditoria_respuestas_piloto.html"
    json_path = salida / "auditoria_respuestas_piloto.json"

    meta = {
        "base": str(db),
        "universo": len(universo),
        "cantidad": len(registros),
        "forzados": forzados,
        "escrituras_bd": 0,
    }
    escribir_html(html_path, meta, registros)
    escribir_json(json_path, meta, registros)

    con_incidencias = sum(bool(r["incidencias_evidencia"]) for r in registros)
    q11362 = next((r for r in registros if r["pregunta_id"] == 11362), None)

    print("=" * 78)
    print("AUDITORÍA PILOTO DE RESPUESTAS JURÍDICAS - PREPARACIÓN DE EVIDENCIA")
    print("=" * 78)
    print(f"Base...................................... {db}")
    print("Modo...................................... SOLO LECTURA")
    print(f"Jurídicas incluidas en bancos activos.... {len(universo)}")
    print(f"Preguntas del piloto...................... {len(registros)}")
    print(f"Con incidencias de evidencia.............. {con_incidencias}")
    print(f"Pregunta control 11362 incluida........... {'SÍ' if q11362 else 'NO'}")
    if q11362:
        print(
            "Control 11362 artículo localizado....... "
            f"{'SÍ' if q11362['articulo_principal_localizado'] else 'NO'}"
        )
        print(
            "Control 11362 respuesta almacenada....... "
            f"{q11362['respuesta_correcta_almacenada']}"
        )
    print(f"HTML...................................... {html_path}")
    print(f"JSON...................................... {json_path}")
    print("Cambios en BD............................. 0")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
