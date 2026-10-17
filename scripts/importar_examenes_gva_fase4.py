"""Fase 4: importa preguntas nuevas de exámenes oficiales GVA (2021-2022).

Entrada: data_examenes_gva/fase4_candidatas.json
  Preguntas extraídas de los PDF (versión en castellano) que NO están ya en
  lote_preguntas (similitud < 0,85), con los 6 artículos del temario más
  parecidos a cada una.

Para cada pregunta, la IA (gpt-5-mini, razonamiento low):
  1. decide si es jurídica y se responde con alguno de los artículos candidatos;
  2. elige el artículo y copia una cita literal que justifique la clave oficial;
  3. comprueba que, con el texto VIGENTE, la clave oficial sigue siendo la
     única correcta (los exámenes son de 2021-2022 y algunas normas han cambiado).
Solo se acepta si la cita aparece de verdad en el artículo elegido y la clave
sigue siendo correcta. Lo demás se descarta (queda en el informe).

Uso (desde TuCoach-Mantenimiento):
    python scripts/importar_examenes_gva_fase4.py            -> resumen, sin IA ni cambios
    python scripts/importar_examenes_gva_fase4.py --ia       -> llama a la IA (reanudable) e informe CSV
    python scripts/importar_examenes_gva_fase4.py --aplicar  -> copia de seguridad + inserta las aceptadas
                                                                + sincroniza bancos + niveles
Para deshacer: restaurar la copia db/oposiciones_backup_antes_fase4_gva_*.sqlite3
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
ENTRADA = RAIZ / "data_examenes_gva" / "fase4_candidatas.json"
CHECKPOINT = RAIZ / "registros" / "fase4_gva_ia.jsonl"
MODELO = "gpt-5-mini"
TIPO_FUENTE = "apoyo"

PDF_DE_EXAMEN = {
    "A2-141/21": "A2_141_21.pdf",
    "C1-7/22": "C1-01_7_8_22.pdf",
    "C1-8/22": "C1-01_7_8_22.pdf",
    "APF-179/21": "Otros_179_21.pdf",
    "A2-138/21": "otros_138_21.pdf",
}
ORIGEN_DE_EXAMEN = {
    "A2-141/21": "A2", "C1-7/22": "C1", "C1-8/22": "C1",
    "APF-179/21": None, "A2-138/21": "A2",
}

_lock = threading.Lock()


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def clave(q: dict) -> str:
    return f"{q['examen']}#{q['n']}"


def cargar_checkpoint() -> dict[str, dict]:
    hechos = {}
    if CHECKPOINT.exists():
        for linea in CHECKPOINT.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                r = json.loads(linea)
                hechos[r["clave"]] = r
    return hechos


def textos_articulos(con, ids):
    marcas = ",".join("?" * len(ids))
    return dict(con.execute(f"SELECT id, texto FROM articulos_fuente WHERE id IN ({marcas})", ids))


def construir_prompt(q, cands, nombres, textos):
    bloques = []
    for i, c in enumerate(cands, 1):
        texto = re.sub(r"\s+", " ", textos.get(c["articulo_fuente_id"], ""))[:3000]
        bloques.append(f"[{i}] {nombres.get(c['norma_id'], '?')}, artículo {c['articulo']}:\n{texto}")
    return (
        "Eres un jurista experto en oposiciones de la Generalitat Valenciana.\n"
        "Te doy una pregunta de un examen oficial de 2021-2022, su clave oficial y "
        "varios artículos candidatos con su TEXTO VIGENTE ACTUAL.\n\n"
        f"PREGUNTA: {q['enunciado']}\n"
        f"A) {q['A']}\nB) {q['B']}\nC) {q['C']}\nD) {q['D']}\n"
        f"CLAVE OFICIAL: {q['correcta']}\n\n"
        "ARTÍCULOS CANDIDATOS:\n" + "\n\n".join(bloques) + "\n\n"
        "Tareas:\n"
        "1. ¿Es una pregunta jurídica que se resuelve con UNO de los artículos candidatos? "
        "(Si es de materia técnica, informática, prevención de riesgos, agronomía, etc., o "
        "ningún candidato la resuelve, responde juridica=false o candidato=null.)\n"
        "2. Número del artículo que fundamenta la respuesta correcta.\n"
        "3. Cita LITERAL de ese artículo (copiada exactamente, 20-300 caracteres) que "
        "justifique la clave.\n"
        "4. Con el texto vigente, ¿la opción de la clave oficial sigue siendo la única "
        "correcta? SI, NO o DUDA. Si no, indica cuál lo sería.\n"
        "5. tipo: PRACTICA si plantea un caso concreto (persona, expediente, plazos de "
        "un caso), TEORICA en otro caso.\n\n"
        'Responde solo JSON: {"juridica": true|false, "candidato": n|null, "cita": "...", '
        '"clave_vigente": "SI"|"NO"|"DUDA", "correcta_segun_texto": "A"|"B"|"C"|"D"|null, '
        '"tipo": "TEORICA"|"PRACTICA", "motivo": "máx. 25 palabras"}'
    )


def evaluar(q, r, cands, textos) -> tuple[bool, str]:
    if not r.get("juridica"):
        return False, "NO_JURIDICA"
    n = r.get("candidato")
    if not isinstance(n, int) or not 1 <= n <= len(cands):
        return False, "SIN_ARTICULO_DEL_TEMARIO"
    cita = norm(r.get("cita") or "")
    if len(cita) < 15 or cita not in norm(textos.get(cands[n - 1]["articulo_fuente_id"], "")):
        return False, "CITA_NO_VERIFICADA"
    if str(r.get("clave_vigente") or "").upper() != "SI":
        return False, "CLAVE_NO_VIGENTE_O_DUDOSA"
    otra = r.get("correcta_segun_texto")
    if otra and str(otra).upper() != q["correcta"]:
        return False, "CLAVE_DISTINTA_SEGUN_TEXTO"
    return True, "ACEPTADA"


# Revisión manual (16/10/2026): preguntas de materia específica sin norma citada
# que la IA asoció a un artículo que no las resuelve.
EXCLUIDAS_REVISION = {"A2-141/21#10", "A2-141/21#46", "A2-141/21#69", "A2-141/21#74", "A2-141/21#78"}


def _citas_normas(texto: str) -> set[str]:
    out = set()
    for a, b in re.findall(r"\b(\d{1,4})\s*/\s*(\d{2,4})\b", texto or ""):
        if len(b) == 2:
            b = ("19" if int(b) > 30 else "20") + b
        out.add(f"{int(a)}/{b}")
    return out


def contexto_revision():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    nombres = {i: f"{n} {c}" for i, n, c in con.execute("SELECT id, nombre_canonico, clave_normalizada FROM normas")}
    refs = {(int(n), str(a).strip()) for n, a in con.execute(
        "SELECT norma_id, articulo_solicitado FROM temario_referencias")}
    con.close()
    return nombres, refs


def decision_final(q, h, ctx):
    """Aplica a las aceptadas por la IA las reglas de la casa. Devuelve (aceptada, motivo, artículo)."""
    if not h:
        return False, "PENDIENTE", None
    if not h.get("aceptada"):
        return False, h["motivo"], None
    nombres, refs = ctx
    if clave(q) in EXCLUIDAS_REVISION:
        return False, "REVISION_MANUAL", None
    # Si se cita una norma, debe ser la asignada (nunca se cambia la norma de un enunciado).
    citadas = _citas_normas(q["enunciado"])
    if citadas and not (citadas & _citas_normas(nombres.get(h["norma_id"], ""))):
        return False, "NORMA_CITADA_DISTINTA", None
    # Si se cita un artículo distinto del asignado, se usa el citado si está en el temario.
    articulo = str(h["articulo"]).strip()
    arts = set(re.findall(r"art[ií]culo\s+(\d+)", q["enunciado"], re.I))
    if arts and articulo.split(".")[0] not in arts:
        validos = [a for a in arts if (int(h["norma_id"]), a) in refs]
        if len(validos) != 1:
            return False, "ARTICULO_CITADO_FUERA_TEMARIO", None
        articulo = validos[0]
    return True, "ACEPTADA", articulo


def paso_ia(preguntas, hilos):
    from openai_api import seleccionar_fragmento_json
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    nombres = dict(con.execute("SELECT id, nombre_canonico FROM normas"))
    ids = sorted({c["articulo_fuente_id"] for q in preguntas for c in q["candidatos"]})
    textos = textos_articulos(con, ids) if ids else {}
    con.close()
    hechos = cargar_checkpoint()
    pendientes = [q for q in preguntas if clave(q) not in hechos]
    print(f"Preguntas: {len(preguntas)} · ya revisadas: {len(hechos)} · pendientes: {len(pendientes)}")
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)

    def una(q):
        cands = q["candidatos"]
        try:
            r = seleccionar_fragmento_json(
                prompt=construir_prompt(q, cands, nombres, textos), modelo=MODELO,
                operacion="fase4_gva_examen_oficial", max_output_tokens=8000, razonamiento="low")
        except Exception as exc:  # se reintentará en la siguiente ejecución
            print(f"  ERROR {clave(q)}: {exc}")
            return
        ok, motivo = evaluar(q, r, cands, textos)
        reg = {"clave": clave(q), "ia": r, "aceptada": ok, "motivo": motivo}
        if ok:
            c = cands[r["candidato"] - 1]
            reg.update(norma_id=c["norma_id"], articulo=c["articulo"])
        with _lock:
            with CHECKPOINT.open("a", encoding="utf-8") as f:
                f.write(json.dumps(reg, ensure_ascii=False) + "\n")
            print(f"  {clave(q)} -> {motivo}")

    with ThreadPoolExecutor(max_workers=hilos) as ex:
        for _ in as_completed([ex.submit(una, q) for q in pendientes]):
            pass


def informe(preguntas, hechos):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    nombres = dict(con.execute("SELECT id, nombre_canonico FROM normas"))
    con.close()
    ctx = contexto_revision()
    ruta = RAIZ / "registros" / f"fase4_gva_informe_{datetime.now():%Y%m%d_%H%M}.csv"
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["examen", "n", "decision", "norma", "articulo", "tipo", "clave_oficial",
                    "enunciado", "motivo_ia", "cita"])
        for q in preguntas:
            h = hechos.get(clave(q))
            if not h:
                w.writerow([q["examen"], q["n"], "PENDIENTE"] + [""] * 3 + [q["correcta"], q["enunciado"], "", ""])
                continue
            ia = h["ia"]
            ok, motivo, art = decision_final(q, h, ctx)
            w.writerow([q["examen"], q["n"], motivo, nombres.get(h.get("norma_id"), ""),
                        art or h.get("articulo", ""), ia.get("tipo", ""), q["correcta"], q["enunciado"],
                        ia.get("motivo", ""), ia.get("cita", "")])
    return ruta


def resumen(preguntas, hechos):
    por = Counter()
    ctx = contexto_revision()
    for q in preguntas:
        por[(q["examen"], decision_final(q, hechos.get(clave(q)), ctx)[1])] += 1
    examenes = sorted({q["examen"] for q in preguntas})
    motivos = sorted({m for _, m in por})
    print("\n" + "=" * 78)
    print(f"{'examen':12s}" + "".join(f"{m[:14]:>16s}" for m in motivos))
    for e in examenes:
        print(f"{e:12s}" + "".join(f"{por[(e, m)]:16d}" for m in motivos))
    total = Counter(m for (_, m), n in por.items() for _ in range(n))
    print(f"{'TOTAL':12s}" + "".join(f"{total[m]:16d}" for m in motivos))
    print("=" * 78)


def metadatos(con, norma_id):
    fila = con.execute(
        """SELECT tipo_norma, nombre_norma, tipo_norma_normalizado, nombre_norma_normalizado
           FROM lote_preguntas WHERE tipo_clasificacion='JURIDICA' AND norma_id_normalizada=?
             AND TRIM(COALESCE(tipo_norma_normalizado,''))<>'' AND TRIM(COALESCE(nombre_norma_normalizado,''))<>''
           ORDER BY id LIMIT 1""", (norma_id,)).fetchone()
    if fila:
        return dict(zip(("tipo_norma", "nombre_norma", "tipo_norma_normalizado", "nombre_norma_normalizado"), fila))
    import generar_preguntas_juridicas_ia as base
    nombre, nombre_n = con.execute(
        "SELECT nombre_norma_csv, nombre_norma_normalizada FROM temario_referencias WHERE norma_id=? LIMIT 1",
        (norma_id,)).fetchone()
    tipo, tipo_n = base._tipo_norma_desde_nombre(nombre)
    return {"tipo_norma": tipo, "nombre_norma": nombre, "tipo_norma_normalizado": tipo_n,
            "nombre_norma_normalizado": nombre_n}


def aplicar(preguntas, hechos):
    ctx = contexto_revision()
    finales = {clave(q): decision_final(q, hechos.get(clave(q)), ctx) for q in preguntas}
    aceptadas = [q for q in preguntas if finales[clave(q)][0]]
    if len(hechos) < len(preguntas):
        print(f"Faltan {len(preguntas) - len(hechos)} preguntas por revisar con IA. Ejecuta antes --ia.")
        return 1
    if not aceptadas:
        print("No hay preguntas aceptadas.")
        return 0
    copia = DB.with_name(f"oposiciones_backup_antes_fase4_gva_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    o = sqlite3.connect(DB); d = sqlite3.connect(copia); o.backup(d); d.close(); o.close()
    print(f"Copia de seguridad: {copia}")

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    insertadas = duplicadas = 0
    ahora = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        con.execute("BEGIN IMMEDIATE")
        imp_ids = {}
        for examen in sorted({q["examen"] for q in aceptadas}):
            pdf = RAIZ / "data_examenes_gva" / PDF_DE_EXAMEN[examen]
            h = hashlib.sha256(pdf.read_bytes() + examen.encode()).hexdigest()
            fila = con.execute("SELECT id FROM importaciones_ficheros WHERE hash_sha256=? AND tipo_fuente=?",
                               (h, TIPO_FUENTE)).fetchone()
            if fila:
                raise RuntimeError(f"{examen} ya se importó (importación {fila[0]}). No se repite.")
            cur = con.execute(
                """INSERT INTO importaciones_ficheros (ruta_relativa, nombre_fichero, hash_sha256, tipo_fuente,
                   estado, paginas_totales, paginas_insertadas, paginas_omitidas, paginas_error,
                   fecha_inicio, fecha_fin, reimportar, ultimo_error)
                   VALUES (?, ?, ?, ?, 'EN_PROCESO', NULL, 0, 0, 0, ?, NULL, 0, NULL)""",
                (f"data_examenes_gva/{pdf.name}", f"GVA-fase4_{examen.replace('/', '_')}.pdf", h, TIPO_FUENTE, ahora))
            imp_ids[examen] = cur.lastrowid
        cuenta = Counter()
        for q in aceptadas:
            hh = hechos[clave(q)]
            if con.execute("""SELECT 1 FROM lote_preguntas WHERE enunciado=? AND opcion_a=? AND opcion_b=?
                              AND opcion_c=? AND opcion_d=?""",
                           (q["enunciado"], q["A"], q["B"], q["C"], q["D"])).fetchone():
                duplicadas += 1
                continue
            articulo = finales[clave(q)][2]
            m = metadatos(con, hh["norma_id"])
            tipo = "PRACTICA" if str(hh["ia"].get("tipo", "")).upper() == "PRACTICA" else "TEORICA"
            con.execute(
                """INSERT INTO lote_preguntas (enunciado, opcion_a, opcion_b, opcion_c, opcion_d,
                   respuesta_correcta, tipo_clasificacion, tipo_norma, nombre_norma, articulo,
                   tema_no_juridico, origen_oposicion, tipo_fuente, importacion_fichero_id, pagina_origen,
                   norma_id_normalizada, articulo_normalizado, teorica_practica, tipo_norma_normalizado,
                   nombre_norma_normalizado, estado_vigencia, vigencia_revision_clave, criterio_inclusion_banco)
                   VALUES (?,?,?,?,?,?, 'JURIDICA', ?,?,?, NULL, ?,?,?, NULL, ?,?,?,?,?, NULL, NULL, 'NORMA_ARTICULO')""",
                (q["enunciado"], q["A"], q["B"], q["C"], q["D"], q["correcta"],
                 m["tipo_norma"], m["nombre_norma"], articulo,
                 ORIGEN_DE_EXAMEN[q["examen"]], TIPO_FUENTE, imp_ids[q["examen"]],
                 int(hh["norma_id"]), articulo, tipo,
                 m["tipo_norma_normalizado"], m["nombre_norma_normalizado"]))
            insertadas += 1
            cuenta[q["examen"]] += 1
        for examen, iid in imp_ids.items():
            con.execute("""UPDATE importaciones_ficheros SET estado='COMPLETADO', paginas_insertadas=?,
                           fecha_fin=? WHERE id=?""", (cuenta[examen], ahora, iid))
        import validacion_completa as vc
        exactos, casi = vc.auditar_duplicados_lote(con)
        if exactos or casi:
            raise RuntimeError(f"Se crearían duplicados en lote_preguntas (exactos={exactos}, casi={casi}). "
                               "No se guarda nada.")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Errores de claves foráneas.")
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    print(f"Insertadas: {insertadas} · ya existían (exactas): {duplicadas} · integridad: {integridad}")
    for e, n in sorted(cuenta.items()):
        print(f"  {e:12s} {n}")

    import generar_preguntas_juridicas_ia as base
    print("\nSincronizando bancos...")
    base.sincronizar_todos_bancos(DB, aplicar=True, validar_final=True)
    from calcular_nivel_dificultad import aplicar_niveles
    aplicar_niveles(DB)
    print("RESULTADO:", "OK" if integridad == "ok" else "REVISAR")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--ia", action="store_true", help="Revisa con IA las preguntas pendientes (reanudable).")
    g.add_argument("--aplicar", action="store_true", help="Inserta las aceptadas y sincroniza bancos.")
    ap.add_argument("--hilos", type=int, default=4)
    a = ap.parse_args()
    preguntas = json.loads(ENTRADA.read_text(encoding="utf-8"))
    if a.ia:
        paso_ia(preguntas, a.hilos)
    if a.aplicar:
        return aplicar(preguntas, cargar_checkpoint())
    hechos = cargar_checkpoint()
    resumen(preguntas, hechos)
    if hechos:
        print(f"Informe: {informe(preguntas, hechos)}")
    if not a.ia:
        print("\nSin cambios en la base. Opciones: --ia (revisión con IA) · --aplicar (insertar aceptadas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
