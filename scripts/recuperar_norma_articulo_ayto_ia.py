#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recuperación verificable de norma y artículo para preguntas AYTO.

No altera una pregunta por una mera respuesta de IA. Primero recupera artículos
oficiales candidatos del catálogo, la IA selecciona uno aportando evidencia y
un segundo control independiente comprueba la respuesta ya almacenada. Por
defecto crea JSON/CSV/HTML de propuestas; solo ``--aplicar`` modifica la BD y
únicamente a partir de un JSON previamente generado por este mismo script.
"""
from __future__ import annotations

import argparse
import csv
import html as html_lib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from openai_api import seleccionar_fragmento_json

ROOT = Path(__file__).resolve().parent.parent
DB_DEFAULT = ROOT / "db" / "oposiciones.sqlite3"
OUT_DIR = ROOT / "auditorias" / "recuperacion_norma_articulo_ayto"
CODIGOS = {
    "Apoyo-A1-AYT": "AYTO-A1", "Apoyo-A2-AYT": "AYTO-A2",
    "Apoyo-C1-AYT": "AYTO-C1", "Apoyo-C2-AYT": "AYTO-C2",
}
STOP = {"para", "por", "con", "una", "uno", "las", "los", "del", "que", "qué", "como", "cómo", "desde", "hasta", "sobre", "segun", "según", "esta", "este", "estos", "estas", "ser", "son", "se", "su", "sus", "la", "el", "en", "de", "y", "o", "a", "al", "no", "si"}


def clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def folded(value: object) -> str:
    value = unicodedata.normalize("NFKD", clean(value)).lower()
    return "".join(c for c in value if not unicodedata.combining(c))


def art_norm(value: object) -> str:
    text = folded(value)
    text = re.sub(r"^art(?:iculo)?\.?\s*", "", text)
    match = re.search(r"\b(\d+(?:\s*(?:bis|ter|quater))?)\b", text)
    return re.sub(r"\s+", "", match.group(1)) if match else ""


def tokens(value: object) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]{4,}", folded(value)) if x not in STOP}


def option(row: sqlite3.Row) -> str:
    letter = clean(row["respuesta_correcta"]).upper()
    if letter not in "ABCD":
        raise ValueError("respuesta_correcta no es A/B/C/D")
    return clean(row[f"opcion_{letter.lower()}"])


def source_is_article(text: str, article: str) -> bool:
    """Descarta bloques que no empiezan realmente por su propio artículo."""
    a = art_norm(article)
    if not a:
        return False
    start = folded(text)[:160]
    # Se exige cabecera; una cita narrativa ('artículo 85 de la Ley...') no vale.
    return bool(re.match(rf"^articulo\s+{re.escape(a)}(?:\s|\.|-|$)", start))


def load_candidates(con: sqlite3.Connection, strict_text: bool = True, convocatoria_id: int | None = None) -> list[dict[str, Any]]:
    if convocatoria_id is None:
        rows = con.execute("""
            SELECT DISTINCT n.id norma_id, n.nombre_canonico, af.id articulo_fuente_id,
                   af.articulo_boe, af.texto
            FROM normas n JOIN norma_fuentes nf ON nf.norma_id=n.id
            JOIN articulos_fuente af ON af.id_boe=nf.id_fuente
            WHERE TRIM(COALESCE(af.texto,''))<>''
        """).fetchall()
    else:
        rows = con.execute("""
            SELECT DISTINCT n.id norma_id, n.nombre_canonico, af.id articulo_fuente_id,
                   af.articulo_boe, af.texto
            FROM temarios t JOIN temario_temas tt ON tt.temario_id=t.id
            JOIN temario_referencias tr ON tr.tema_id=tt.id
            JOIN normas n ON n.id=tr.norma_id
            JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
            WHERE t.convocatoria_id=? AND TRIM(COALESCE(af.texto,''))<>''
        """, (convocatoria_id,)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        text, art = clean(r["texto"]), clean(r["articulo_boe"])
        if len(text) < 20 or (strict_text and not source_is_article(text, art)):
            continue
        out.append({"norma_id": int(r["norma_id"]), "norma": clean(r["nombre_canonico"]),
                    "articulo": art_norm(art), "texto": text, "articulo_fuente_id": int(r["articulo_fuente_id"]),
                    "tokens": tokens(text)})
    return out


def rank_candidates(question: sqlite3.Row, catalog: list[dict[str, Any]], top: int) -> list[dict[str, Any]]:
    # La respuesta correcta pesa el doble; enunciado y todas las opciones dan contexto.
    correct = tokens(option(question))
    context = tokens(" ".join(clean(question[x]) for x in ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d")))
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in catalog:
        common_correct = len(correct & item["tokens"])
        common_context = len(context & item["tokens"])
        if not common_correct and not common_context:
            continue
        score = 2.2 * common_correct + common_context
        # Recompensa una frase relativamente distintiva, no solo vocabulario jurídico genérico.
        if len(correct) and common_correct / len(correct) >= 0.38:
            score += 4
        scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [dict(item, score=round(score, 2)) for score, item in scored[:top]]


def relevant_excerpt(text: str, needle: str, maximum: int) -> str:
    """Evita enviar artículos extensos completos cuando basta el pasaje relevante."""
    if len(text) <= maximum:
        return text
    haystack = folded(text)
    positions = [haystack.find(word) for word in tokens(needle)]
    positions = [p for p in positions if p >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - maximum // 4)
    end = min(len(text), start + maximum)
    return text[start:end]


def prompt_selection(q: sqlite3.Row, candidates: list[dict[str, Any]]) -> str:
    shown = []
    for i, c in enumerate(candidates, 1):
        shown.append(f"CANDIDATO {i}\nNORMA: {c['norma']}\nARTÍCULO: {c['articulo']}\nEXTRACTO OFICIAL:\n{relevant_excerpt(c['texto'], option(q), 1200)}")
    return f"""Actúas como jurista auditor. La respuesta almacenada de esta pregunta es un dato que debes COMPROBAR, no cambiar.

PREGUNTA: {clean(q['enunciado'])}
A) {clean(q['opcion_a'])}
B) {clean(q['opcion_b'])}
C) {clean(q['opcion_c'])}
D) {clean(q['opcion_d'])}
RESPUESTA ALMACENADA: {clean(q['respuesta_correcta']).upper()}

Solo puedes elegir UN candidato de la lista y solo si su texto oficial permite demostrar de forma inequívoca que la respuesta almacenada es correcta y las demás falsas. Si no basta, responde NO_ENCONTRADO. No uses memoria ni normas externas.

{chr(10).join(shown)}

Devuelve SOLO JSON:
{{"decision":"ENCONTRADO|NO_ENCONTRADO","candidato":numero_o_null,"respuesta_demostrada":"A|B|C|D|null","cita_literal":"fragmento exacto del texto o vacio","fundamento":"breve"}}"""


def prompt_audit(q: sqlite3.Row, c: dict[str, Any]) -> str:
    return f"""Audita estrictamente esta pregunta usando SOLO el artículo oficial dado. No uses memoria.
NORMA: {c['norma']}
ARTÍCULO: {c['articulo']}
TEXTO: {relevant_excerpt(c['texto'], option(q), 7000)}
PREGUNTA: {clean(q['enunciado'])}
A) {clean(q['opcion_a'])}
B) {clean(q['opcion_b'])}
C) {clean(q['opcion_c'])}
D) {clean(q['opcion_d'])}
RESPUESTA ALMACENADA: {clean(q['respuesta_correcta']).upper()}
Devuelve SOLO JSON:
{{"resultado":"VALIDADA|NO_VERIFICABLE|CONFLICTO","respuesta_demostrada":"A|B|C|D|null","cita_literal":"fragmento exacto o vacio","fundamento":"breve"}}"""


def prompt_identification(q: sqlite3.Row) -> str:
    """Solo propone una identidad; nunca basta por sí misma para actualizar."""
    return f"""Identifica con prudencia la norma española y el artículo que podrían demostrar la respuesta ya almacenada. Si no tienes seguridad alta, responde NO_SEGURO. No cambies la respuesta ni inventes una cita.
PREGUNTA: {clean(q['enunciado'])}
A) {clean(q['opcion_a'])}
B) {clean(q['opcion_b'])}
C) {clean(q['opcion_c'])}
D) {clean(q['opcion_d'])}
RESPUESTA ALMACENADA: {clean(q['respuesta_correcta']).upper()}
Devuelve SOLO JSON:
{{"decision":"PROPUESTA|NO_SEGURO","norma":"denominación o vacio","articulo":"número o vacio","fundamento":"breve"}}"""


def candidate_from_identity(proposal: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Convierte una hipótesis libre en un artículo real del catálogo local."""
    proposed_art = art_norm(proposal.get("articulo"))
    proposed_name = tokens(proposal.get("norma"))
    if not proposed_art or not proposed_name:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for item in catalog:
        if item["articulo"] != proposed_art:
            continue
        name_tokens = tokens(item["norma"])
        common = len(proposed_name & name_tokens)
        # Identidades numéricas (39/2015, 7/1985...) son decisivas.
        numero_norma = re.search(r"\b\d{1,3}\s*/\s*\d{4}\b", folded(proposal.get("norma")))
        numero_catalogo = re.search(r"\b\d{1,3}\s*/\s*\d{4}\b", folded(item["norma"]))
        identidad_numerica = bool(numero_norma and numero_catalogo and numero_norma.group(0).replace(" ", "") == numero_catalogo.group(0).replace(" ", ""))
        score = common + (8 if identidad_numerica else 0)
        if best is None or score > best[0]:
            best = (score, item)
    if best is None or best[0] < 2:
        return None
    return dict(best[1], score=round(best[0], 2))


def candidate_from_boe_hypothesis(proposal: dict[str, Any]) -> dict[str, Any] | None:
    """Localiza una hipótesis fuera de catálogo, exclusivamente en BOE.

    Devuelve una propuesta sin ``norma_id``: nunca puede aplicarse por la vía
    automática y exige revisión de la futura alta del catálogo.
    """
    if str(proposal.get("decision", "")).upper() != "PROPUESTA":
        return None
    name, article = clean(proposal.get("norma")), art_norm(proposal.get("articulo"))
    if not name or not article:
        return None
    try:
        from localizador_normativa import localizar_norma
        import boe_api
        norm = localizar_norma(name)
        source = boe_api.obtener_articulo(norm.id_boe, article)
    except Exception:
        return None
    text = clean(source.texto)
    if len(text) < 80 or not source_is_article(text, article):
        return None
    return {"norma_id": None, "norma": clean(norm.titulo), "articulo": article,
            "texto": text, "articulo_fuente_id": None, "id_boe": norm.id_boe,
            "score": "BOE_OFICIAL_NUEVO"}


def explicit_candidate(q: sqlite3.Row, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resuelve solo una cita inequívoca ya escrita por el examen.

    Las referencias múltiples se dejan a la auditoría IA: la tabla solo tiene
    una columna de artículo y no debe inventarse cuál de ellos fundamenta la
    respuesta correcta.
    """
    text = folded(q["enunciado"])
    m = re.search(r"\barticulo\s+(\d+(?:\s*(?:bis|ter|quater))?)(?!\s*(?:,|y|e))", text)
    if not m:
        return None
    article = art_norm(m.group(1))
    if "constitucion" in text:
        markers = {"constitucion"}
    elif "tratado de funcionamiento" in text or "tfue" in text:
        markers = {"tratado", "funcionamiento"}
    elif "codigo civil" in text:
        markers = {"codigo", "civil"}
    else:
        n = re.search(r"\b(ley organica|real decreto legislativo|real decreto|ley)\s+(\d+)\s*/\s*(\d{4})", text)
        if not n:
            return None
        markers = set(re.findall(r"[a-z0-9]+", n.group(1))) | {n.group(2), n.group(3)}
    matches = [c for c in catalog if c["articulo"] == article and markers <= tokens(c["norma"])]
    # La mención debe identificar exactamente una norma del catálogo.
    norma_ids = {c["norma_id"] for c in matches}
    if len(norma_ids) != 1:
        return None
    return dict(matches[0], score="CITA_EXPRESA")


def quote_is_in(text: str, quote: object) -> bool:
    q = folded(quote)
    return len(q) >= 18 and q in folded(text)


def type_from_name(name: str) -> str:
    x = folded(name)
    if x.startswith("ley organica"): return "LEY_ORGANICA"
    if x.startswith("real decreto legislativo"): return "REAL_DECRETO_LEGISLATIVO"
    if x.startswith("real decreto ley"): return "DECRETO_LEY"
    if x.startswith("real decreto"): return "REAL_DECRETO"
    if x.startswith("constitucion"): return "CONSTITUCION"
    if x.startswith("ley"): return "LEY"
    if x.startswith("decreto"): return "DECRETO"
    if x.startswith("orden"): return "ORDEN"
    if x.startswith("reglamento"): return "REGLAMENTO"
    return ""


def questions(con: sqlite3.Connection, origin: str, limit: int, offset: int = 0) -> list[sqlite3.Row]:
    return con.execute("""
        SELECT id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d,respuesta_correcta,
               norma_id_normalizada,articulo_normalizado
        FROM lote_preguntas
        WHERE origen_oposicion=? AND tipo_clasificacion='JURIDICA'
          AND (norma_id_normalizada IS NULL OR TRIM(COALESCE(articulo_normalizado,''))='')
        ORDER BY id LIMIT ? OFFSET ?
    """, (origin, limit, offset)).fetchall()


def write_outputs(records: list[dict[str, Any]], code: str) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = OUT_DIR / f"{code}_{stamp}"
    json_path, csv_path, html_path = base.with_suffix(".json"), base.with_suffix(".csv"), base.with_suffix(".html")
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ["pregunta_id","estado","norma_id","norma","articulo","tipo_norma","id_boe","score_recuperacion","motivo","fundamento"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(records)
    total, ok = len(records), sum(r["estado"] == "PROPUESTA_VERIFICADA" for r in records)
    rows = "".join("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        r["pregunta_id"], html_lib.escape(r["estado"]), html_lib.escape(r.get("norma", "")), html_lib.escape(r.get("articulo", "")), html_lib.escape(r.get("motivo", ""))) for r in records)
    html_path.write_text(f"""<!doctype html><meta charset=utf-8><title>Recuperación AYTO</title><style>body{{font:15px system-ui;margin:30px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:8px;text-align:left}}th{{background:#eee}}</style><h1>{html_lib.escape(code)} · recuperación norma/artículo</h1><p>Propuestas verificadas: <b>{ok}</b> de {total}. No se ha modificado la base. Solo las verificadas pueden aplicarse usando este JSON.</p><table><tr><th>ID</th><th>Estado</th><th>Norma</th><th>Artículo</th><th>Motivo</th></tr>{rows}</table>""", encoding="utf-8")
    return json_path, csv_path, html_path


def apply(con: sqlite3.Connection, records: list[dict[str, Any]], code: str) -> int:
    origin = CODIGOS[code]; updated = 0
    con.execute("BEGIN IMMEDIATE")
    try:
        for r in records:
            if r.get("estado") != "PROPUESTA_VERIFICADA":
                continue
            row = con.execute("SELECT origen_oposicion,norma_id_normalizada,articulo_normalizado FROM lote_preguntas WHERE id=?", (r["pregunta_id"],)).fetchone()
            if row is None or row["origen_oposicion"] != origin:
                continue
            # Nunca sustituye una pareja completa preexistente.
            if row["norma_id_normalizada"] is not None and clean(row["articulo_normalizado"]):
                continue
            con.execute("""UPDATE lote_preguntas SET tipo_norma=?, nombre_norma=?, articulo=?,
                    norma_id_normalizada=?, articulo_normalizado=?, tipo_norma_normalizado=?, nombre_norma_normalizado=?
                    WHERE id=?""", (r["tipo_norma"], r["norma"], r["articulo"], r["norma_id"], r["articulo"], r["tipo_norma"], r["norma"], r["pregunta_id"]))
            updated += 1
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("La aplicación produciría errores de integridad referencial.")
        con.commit()
    except Exception:
        con.rollback(); raise
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description="Recupera y verifica norma/artículo de preguntas jurídicas AYTO.")
    ap.add_argument("--codigo", choices=tuple(CODIGOS), required=True)
    ap.add_argument("--limite", type=int, default=25)
    ap.add_argument("--offset", type=int, default=0, help="Preguntas pendientes que se omiten antes del lote.")
    ap.add_argument("--candidatos", type=int, default=10)
    ap.add_argument("--modelo", default="gpt-5.4-nano")
    ap.add_argument("--solo-citas", action="store_true", help="Solo extrae citas unívocas escritas en el enunciado; no llama a IA.")
    ap.add_argument("--solo-temario", action="store_true", help="Limita los candidatos a los artículos del corpus IA del temario de la convocatoria.")
    ap.add_argument("--db", type=Path, default=DB_DEFAULT)
    ap.add_argument("--aplicar", type=Path, help="Aplica exclusivamente un JSON de propuestas previamente generado.")
    args = ap.parse_args()
    if args.limite <= 0 or args.candidatos <= 0 or args.offset < 0: raise SystemExit("limite/candidatos deben ser positivos y offset no negativo")
    if not args.db.is_file(): raise SystemExit(f"No existe {args.db}")
    with sqlite3.connect(args.db) as con:
        con.row_factory = sqlite3.Row
        if args.aplicar:
            records = json.loads(args.aplicar.read_text(encoding="utf-8"))
            n = apply(con, records, args.codigo)
            print(f"Aplicadas {n} propuestas verificadas. Base actualizada.")
            return 0
        convocatoria_id = con.execute("SELECT id FROM convocatorias WHERE codigo=?", (args.codigo,)).fetchone()[0]
        catalog = load_candidates(con, strict_text=not args.solo_citas, convocatoria_id=convocatoria_id if args.solo_temario else None)
        pending = questions(con, CODIGOS[args.codigo], args.limite, args.offset)
        print(f"Catálogo de artículos fiables: {len(catalog)} | preguntas a revisar: {len(pending)}")
        records: list[dict[str, Any]] = []
        for pos, q in enumerate(pending, 1):
            record: dict[str, Any] = {"pregunta_id": int(q["id"]), "estado": "SIN_PROPUESTA", "norma_id": None, "norma": "", "articulo": "", "tipo_norma": "", "score_recuperacion": "", "motivo": "", "fundamento": ""}
            try:
                candidate = explicit_candidate(q, catalog)
                if candidate is not None and args.solo_citas:
                    record.update({"estado": "PROPUESTA_VERIFICADA", "norma_id": candidate["norma_id"], "norma": candidate["norma"], "articulo": candidate["articulo"], "tipo_norma": type_from_name(candidate["norma"]), "score_recuperacion": candidate["score"], "motivo": "Cita única expresa en el enunciado", "fundamento": "La pregunta identifica de forma directa la norma y el artículo."})
                    records.append(record)
                    print(f"[{pos}/{len(pending)}] {q['id']} · {record['estado']}")
                    continue
                if args.solo_citas:
                    record["motivo"] = "No hay una cita única norma-artículo en el enunciado"; records.append(record)
                    print(f"[{pos}/{len(pending)}] {q['id']} · {record['estado']}")
                    continue
                choices = [] if candidate is not None else rank_candidates(q, catalog, args.candidatos)
                if choices:
                    selected = seleccionar_fragmento_json(prompt=prompt_selection(q, choices), modelo=args.modelo, operacion="recuperar_norma_articulo_ayto_seleccion")
                    idx = selected.get("candidato")
                    if str(selected.get("decision", "")).upper() == "ENCONTRADO" and isinstance(idx, int) and 1 <= idx <= len(choices):
                        possible = choices[idx - 1]
                        if clean(selected.get("respuesta_demostrada")).upper() == clean(q["respuesta_correcta"]).upper() and quote_is_in(possible["texto"], selected.get("cita_literal")):
                            candidate = possible
                # Si la búsqueda léxica no prueba nada, se admite una hipótesis
                # libre solo para localizar un artículo ya presente en catálogo.
                alta_nueva = False
                if candidate is None:
                    hypothesis = seleccionar_fragmento_json(prompt=prompt_identification(q), modelo=args.modelo, operacion="recuperar_norma_articulo_ayto_hipotesis")
                    if str(hypothesis.get("decision", "")).upper() == "PROPUESTA":
                        candidate = candidate_from_identity(hypothesis, catalog)
                        if candidate is None:
                            candidate = candidate_from_boe_hypothesis(hypothesis)
                            alta_nueva = candidate is not None
                if candidate is None:
                    record["motivo"] = "No se localiza un artículo del catálogo que pueda demostrarse"; records.append(record); continue
                audit = seleccionar_fragmento_json(prompt=prompt_audit(q, candidate), modelo=args.modelo, operacion="recuperar_norma_articulo_ayto_auditoria")
                if str(audit.get("resultado", "")).upper() != "VALIDADA" or clean(audit.get("respuesta_demostrada")).upper() != clean(q["respuesta_correcta"]).upper() or not quote_is_in(candidate["texto"], audit.get("cita_literal")):
                    record["motivo"] = "La auditoría independiente no demuestra la respuesta"; records.append(record); continue
                record.update({"estado": "PROPUESTA_ALTA_NORMA_VERIFICADA" if alta_nueva else "PROPUESTA_VERIFICADA", "norma_id": candidate["norma_id"], "norma": candidate["norma"], "articulo": candidate["articulo"], "tipo_norma": type_from_name(candidate["norma"]), "score_recuperacion": candidate["score"], "motivo": "BOE oficial + doble verificación; alta de catálogo pendiente" if alta_nueva else "Doble verificación con cita literal", "fundamento": clean(audit.get("fundamento")), "id_boe": candidate.get("id_boe", "")})
            except Exception as exc:
                record["estado"] = "ERROR"; record["motivo"] = f"{type(exc).__name__}: {exc}"
            records.append(record)
            print(f"[{pos}/{len(pending)}] {q['id']} · {record['estado']}")
        paths = write_outputs(records, args.codigo)
        print("JSON:", paths[0]); print("CSV:", paths[1]); print("HTML:", paths[2]); print("La base NO se ha modificado.")
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr); raise SystemExit(1)
