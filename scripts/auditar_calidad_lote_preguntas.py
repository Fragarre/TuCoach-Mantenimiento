"""
OpoCoach-Mantenimiento - Auditoría estricta de calidad de lote_preguntas.

SOLO LECTURA.

Comprueba:
1. Autosuficiencia normativa de preguntas jurídicas: si se cita normativa,
   la norma de procedencia debe figurar expresamente en el enunciado.
2. Autosuficiencia contextual: detecta referencias dependientes de texto no
   reproducido ("apartado anterior", "párrafo anterior", etc.) y referencias
   a apartados/letras sin identificar el artículo.
3. Duplicados y casi duplicados jurídicos dentro de lote_preguntas.

No elimina, modifica ni reclasifica ninguna pregunta.
Genera informes HTML y CSV en registros/.
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
REGISTROS = ROOT / "registros"
UMBRAL_CASI_DUPLICADO = 0.94

CAMPOS_TEXTO = ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d")

PATRONES_DEICTICOS = [
    re.compile(r"\b(?:apartado|apartados|p[aá]rrafo|p[aá]rrafos|art[ií]culo|art[ií]culos|letra|letras|inciso|incisos|precepto|preceptos)\s+anterior(?:es)?\b", re.I),
    re.compile(r"\b(?:citado|citada|citados|citadas|mencionado|mencionada|mencionados|mencionadas|referido|referida|referidos|referidas)\s+(?:apartado|apartados|p[aá]rrafo|p[aá]rrafos|art[ií]culo|art[ií]culos|letra|letras|precepto|preceptos)\b", re.I),
]

PATRON_SUBREFERENCIA = re.compile(
    r"\b(?:apartado\s+\d+[\w.ºª-]*|letras?\s+[a-z](?:\s*(?:,|y|o)\s*[a-z])+\s+del\s+apartado\s+\d+)\b",
    re.I,
)
PATRON_ARTICULO = re.compile(r"\b(?:art[ií]culo|art\.)\s*\d+", re.I)
PATRON_TIPO_NORMA = re.compile(
    r"\b(?:constituci[oó]n|estatuto|ley(?:\s+org[aá]nica)?|decreto(?:-ley)?|real\s+decreto|orden|reglamento|directiva|tratado|resoluci[oó]n)\b",
    re.I,
)
PATRON_NUMERO_NORMA = re.compile(r"\b\d{1,4}\s*/\s*\d{2,4}\b")
PATRON_CITA_INTERNA = re.compile(
    r"\b(?:art[ií]culo|art\.|apartado|apartados|p[aá]rrafo|p[aá]rrafos|letra|letras|inciso|incisos|disposici[oó]n|anexo)\b",
    re.I,
)


def quitar_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(c)
    )


def normalizar(texto: Any) -> str:
    s = quitar_acentos(str(texto or "").casefold())
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"[^a-z0-9%]+", " ", s).strip()


def normalizar_numero_norma(texto: str) -> str:
    return re.sub(r"\s+", "", texto).casefold()


def articulo_principal(valor: Any) -> str:
    m = re.search(r"\d+", str(valor or ""))
    return m.group(0) if m else normalizar(valor)


def texto_completo(fila: sqlite3.Row) -> str:
    return normalizar(" | ".join(str(fila[c] or "") for c in CAMPOS_TEXTO))


def norma_explicita_en_enunciado(fila: sqlite3.Row) -> tuple[bool, str]:
    enunciado = str(fila["enunciado"] or "")
    nombres = " ".join(
        str(fila[c] or "")
        for c in ("nombre_norma", "nombre_norma_normalizado")
    )
    numeros = {
        normalizar_numero_norma(x)
        for x in PATRON_NUMERO_NORMA.findall(nombres)
    }
    enunciado_compacto = normalizar_numero_norma(enunciado)

    if numeros:
        if any(n in enunciado_compacto for n in numeros):
            return True, ""
        return False, "No figura en el enunciado el número identificador de la norma: " + ", ".join(sorted(numeros))

    # Normas sin número típico (Constitución, Estatuto, Tratados...).
    nombre_norm = normalizar(nombres)
    claves = []
    for clave in ("constitucion", "estatuto", "tratado", "reglamento", "directiva"):
        if clave in nombre_norm:
            claves.append(clave)
    en_norm = normalizar(enunciado)
    if claves:
        if any(clave in en_norm for clave in claves):
            return True, ""
        return False, "No figura en el enunciado una identificación reconocible de la norma."

    # Si la metadata no permite construir una clave fiable, exigimos al menos
    # una denominación normativa explícita en la pregunta.
    if PATRON_TIPO_NORMA.search(enunciado):
        return True, ""
    return False, "El enunciado no identifica expresamente la norma de procedencia."


def incidencias_autosuficiencia(fila: sqlite3.Row) -> list[str]:
    incidencias: list[str] = []
    enunciado = str(fila["enunciado"] or "")
    texto = " | ".join(str(fila[c] or "") for c in CAMPOS_TEXTO)

    if PATRON_CITA_INTERNA.search(enunciado):
        ok_norma, motivo = norma_explicita_en_enunciado(fila)
        if not ok_norma:
            incidencias.append("NORMA_NO_EXPLICITA: " + motivo)

    for patron in PATRONES_DEICTICOS:
        m = patron.search(texto)
        if m:
            incidencias.append(f"REFERENCIA_CONTEXTUAL: {m.group(0)!r}")
            break

    if PATRON_SUBREFERENCIA.search(enunciado) and not PATRON_ARTICULO.search(enunciado):
        incidencias.append("SUBREFERENCIA_SIN_ARTICULO: se cita apartado/letra sin identificar el artículo.")

    return incidencias


def ngramas(texto: str, n: int = 5) -> frozenset[str]:
    texto = " " + texto + " "
    if len(texto) <= n:
        return frozenset({texto})
    return frozenset(texto[i:i+n] for i in range(len(texto) - n + 1))


def similitud_ngram(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return (2.0 * len(a & b)) / (len(a) + len(b))


def auditar(db: Path, umbral: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        filas = con.execute(
            """
            SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d,
                   respuesta_correcta, tipo_fuente, tipo_clasificacion,
                   nombre_norma, nombre_norma_normalizado,
                   norma_id_normalizada, articulo, articulo_normalizado
            FROM lote_preguntas
            ORDER BY id
            """
        ).fetchall()

        calidad: list[dict[str, Any]] = []
        juridicas = [
            f for f in filas
            if str(f["tipo_clasificacion"] or "").strip().upper() == "JURIDICA"
        ]
        for f in juridicas:
            incidencias = incidencias_autosuficiencia(f)
            if incidencias:
                calidad.append({
                    "id": int(f["id"]),
                    "tipo_fuente": f["tipo_fuente"],
                    "norma": f["nombre_norma_normalizado"] or f["nombre_norma"],
                    "articulo": f["articulo_normalizado"] or f["articulo"],
                    "incidencias": " | ".join(incidencias),
                    "enunciado": f["enunciado"],
                })

        grupos: dict[tuple[Any, str], list[tuple[sqlite3.Row, str]]] = defaultdict(list)
        for f in juridicas:
            norma_id = f["norma_id_normalizada"]
            art = articulo_principal(f["articulo_normalizado"] or f["articulo"])
            if norma_id is None or not art:
                continue
            grupos[(int(norma_id), art)].append((f, texto_completo(f), None))

        duplicados: list[dict[str, Any]] = []
        for (norma_id, art), grupo in grupos.items():
            if len(grupo) < 2:
                continue
            preparados = [
                (f, texto, ngramas(texto))
                for f, texto, _ in grupo
            ]
            for i in range(len(preparados)):
                fa, ta, ga = preparados[i]
                for j in range(i + 1, len(preparados)):
                    fb, tb, gb = preparados[j]
                    if min(len(ta), len(tb)) / max(len(ta), len(tb)) < 0.72:
                        continue
                    s = similitud_ngram(ga, gb)
                    if s < umbral:
                        continue
                    duplicados.append({
                        "id_a": int(fa["id"]),
                        "id_b": int(fb["id"]),
                        "similitud": round(s, 6),
                        "norma_id": norma_id,
                        "articulo_principal": art,
                        "fuente_a": fa["tipo_fuente"],
                        "fuente_b": fb["tipo_fuente"],
                        "correcta_a": fa["respuesta_correcta"],
                        "correcta_b": fb["respuesta_correcta"],
                        "conflicto_respuesta": "SI" if fa["respuesta_correcta"] != fb["respuesta_correcta"] else "NO",
                        "enunciado_a": fa["enunciado"],
                        "enunciado_b": fb["enunciado"],
                    })

        duplicados.sort(key=lambda x: (-float(x["similitud"]), x["id_a"], x["id_b"]))
        return calidad, duplicados
    finally:
        con.close()


def escribir_informes(calidad: list[dict[str, Any]], duplicados: list[dict[str, Any]], umbral: float) -> tuple[Path, Path, Path]:
    REGISTROS.mkdir(parents=True, exist_ok=True)
    csv_calidad = REGISTROS / "auditoria_calidad_autosuficiencia.csv"
    csv_dup = REGISTROS / "auditoria_calidad_duplicados.csv"
    html_out = REGISTROS / "auditoria_calidad_lote_preguntas.html"

    with csv_calidad.open("w", encoding="utf-8-sig", newline="") as fh:
        campos = ["id", "tipo_fuente", "norma", "articulo", "incidencias", "enunciado"]
        w = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
        w.writeheader(); w.writerows(calidad)

    with csv_dup.open("w", encoding="utf-8-sig", newline="") as fh:
        campos = ["id_a", "id_b", "similitud", "norma_id", "articulo_principal", "fuente_a", "fuente_b", "correcta_a", "correcta_b", "conflicto_respuesta", "enunciado_a", "enunciado_b"]
        w = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
        w.writeheader(); w.writerows(duplicados)

    conflictos = sum(d["conflicto_respuesta"] == "SI" for d in duplicados)
    filas_calidad = "\n".join(
        f"<tr><td>{x['id']}</td><td>{html.escape(str(x['tipo_fuente'] or ''))}</td><td>{html.escape(str(x['norma'] or ''))}</td><td>{html.escape(str(x['articulo'] or ''))}</td><td>{html.escape(x['incidencias'])}</td><td>{html.escape(str(x['enunciado'] or ''))}</td></tr>"
        for x in calidad
    )
    filas_dup = "\n".join(
        f"<tr><td>{x['id_a']}</td><td>{x['id_b']}</td><td>{float(x['similitud']):.1%}</td><td>{x['norma_id']}</td><td>{html.escape(str(x['articulo_principal']))}</td><td>{html.escape(str(x['fuente_a']))}</td><td>{html.escape(str(x['fuente_b']))}</td><td>{html.escape(str(x['correcta_a']))}</td><td>{html.escape(str(x['correcta_b']))}</td><td>{x['conflicto_respuesta']}</td><td>{html.escape(str(x['enunciado_a']))}</td><td>{html.escape(str(x['enunciado_b']))}</td></tr>"
        for x in duplicados
    )
    html_out.write_text(f"""<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Auditoría calidad lote_preguntas</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #bbb;padding:6px;vertical-align:top}}th{{background:#eee;position:sticky;top:0}}h2{{margin-top:36px}}</style></head><body>
<h1>Auditoría estricta de lote_preguntas</h1>
<p><b>Solo lectura.</b> Umbral de casi duplicado: {umbral:.1%}.</p>
<ul><li>Incidencias de autosuficiencia: <b>{len(calidad)}</b></li><li>Pares duplicados/casi duplicados: <b>{len(duplicados)}</b></li><li>Pares con distinta letra correcta: <b>{conflictos}</b></li></ul>
<h2>Autosuficiencia normativa/contextual</h2><table><tr><th>ID</th><th>Fuente</th><th>Norma</th><th>Art.</th><th>Incidencias</th><th>Enunciado</th></tr>{filas_calidad}</table>
<h2>Duplicados y casi duplicados</h2><table><tr><th>ID A</th><th>ID B</th><th>Sim.</th><th>Norma ID</th><th>Art.</th><th>Fuente A</th><th>Fuente B</th><th>Correcta A</th><th>Correcta B</th><th>Conflicto</th><th>Enunciado A</th><th>Enunciado B</th></tr>{filas_dup}</table>
</body></html>""", encoding="utf-8")
    return html_out, csv_calidad, csv_dup


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_DEFECTO)
    ap.add_argument("--umbral", type=float, default=UMBRAL_CASI_DUPLICADO)
    args = ap.parse_args()
    if not args.db.is_file():
        print(f"ERROR: no existe la base: {args.db}")
        return 2
    if not 0.80 <= args.umbral <= 1.0:
        print("ERROR: --umbral debe estar entre 0.80 y 1.0")
        return 2

    calidad, duplicados = auditar(args.db, args.umbral)
    html_out, csv_calidad, csv_dup = escribir_informes(calidad, duplicados, args.umbral)
    conflictos = sum(d["conflicto_respuesta"] == "SI" for d in duplicados)

    print("=" * 78)
    print("AUDITORÍA ESTRICTA DE CALIDAD - lote_preguntas - SOLO LECTURA")
    print("=" * 78)
    print(f"Base: {args.db}")
    print(f"Incidencias autosuficiencia........ {len(calidad)}")
    print(f"Pares duplicados/casi duplicados... {len(duplicados)}")
    print(f"Con distinta letra correcta........ {conflictos}")
    print(f"Umbral casi duplicado............... {args.umbral:.1%}")
    print(f"Informe HTML........................ {html_out}")
    print(f"CSV autosuficiencia................. {csv_calidad}")
    print(f"CSV duplicados...................... {csv_dup}")
    print("\nLa base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
