"""
TuCoach-Mantenimiento - Generador de preguntas jurídicas IA, versión 5 (dificultad).

QUÉ CAMBIA RESPECTO A generar_preguntas_juridicas_ia.py (que NO se modifica)
------------------------------------------------------------------------------
- Reglas de redacción probadas en el piloto: diseño cruzado de distractores,
  sin palabras absolutas, longitudes equilibradas, mezcla de formatos
  (directa, negativa, combinada, supuesto práctico), norma siempre explícita.
- Varias preguntas por llamada y artículo (más barato).
- Sin los 25 ejemplos del banco en el prompt: solo una lista breve de los
  aspectos ya preguntados (más barato y evita copiar el estilo de las fáciles).
- Comprobaciones automáticas gratuitas antes de gastar en validar:
  pistas, opción central, longitudes, norma explícita.
- Dificultad objetiva: un modelo barato intenta acertar SIN ver el enunciado;
  si acierta, la pregunta se descarta.
- Validación: auditoría ciega con evidencia (la misma de la versión 4), con
  segunda revisión solo si hay dudas.
- Guarda formato, nivel objetivo y tipo de trampa de cada distractor en
  db/metadatos_preguntas_ia.sqlite3 (fichero aparte; no toca el esquema maestro).

Reutiliza del generador actual: selección de referencias, contexto, control de
duplicados y originalidad, auditoría ciega, publicación (aprobar) y
sincronización de bancos. Las publicadas se marcan con ORIGEN "IA-v5-2026-10".

USO (desde TuCoach-Mantenimiento)
---------------------------------
Prueba sin publicar (por defecto NO publica nada):
    python scripts/generar_preguntas_juridicas_v5.py --codigo C2-01_70_26 --cantidad 20
Elegir modelos:
    --modelo-generacion gpt-5-mini --modelo-auditoria gpt-5.4 --modelo-control gpt-5.4-nano
Limitar a una norma:   --norma-id 10145
Rellenar artículos sin preguntas:  --huecos
Publicar las aprobadas en lote_preguntas y sincronizar bancos:  --publicar
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import random
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import generar_preguntas_juridicas_ia as base

base.ORIGEN_IA = "IA-v5-2026-10"
base.PROMPT_VERSION = "juridicas-v5-dificultad"
DIFICULTAD_OBJETIVO = "SUPERIOR_EXAMEN_OFICIAL"
RUTA_METADATOS = base.ROOT / "db" / "metadatos_preguntas_ia.sqlite3"

# "negativa" queda fuera: la auditoría ciega exige una única opción CORRECTA y en una
# pregunta negativa hay tres afirmaciones verdaderas (incompatibilidad del método).
FORMATOS = ["directa", "supuesto_practico", "directa", "combinada", "directa", "supuesto_practico"]
NIVELES = ["DIFICIL", "MUY_DIFICIL", "DIFICIL", "MEDIO", "MUY_DIFICIL", "DIFICIL"]
TIPOS_TRAMPA = ["plazo", "organo", "sujeto", "efecto", "excepcion", "requisito",
                "verbo_podra_debera", "cuantia_porcentaje", "procedimiento", "otro"]

# ----------------------------------------------------------------------------
# Comprobaciones deterministas (sin coste)
# ----------------------------------------------------------------------------
_ABS = re.compile(
    r"\b(siempre|nunca|en ningún caso|en todo caso|exclusivamente|únicamente|solo|sólo|"
    r"cualquier|todos? los|jamás|sin excepción|con independencia)\b", re.I)
_TODAS = re.compile(
    r"todas (las )?(anteriores|opciones|respuestas)? ?(son )?correctas|todas las anteriores|ninguna de las", re.I)


def _palabras(t):
    return {p for p in re.findall(r"\w+", (t or "").lower()) if len(p) > 5}


def puntuacion_pistas(enunciado, opciones, correcta):
    """Idéntica a la del filtro de la web (backend/app/dificultad.py)."""
    s = 0
    largos = {k: len(v or "") for k, v in opciones.items()}
    orden = sorted(largos.values(), reverse=True)
    if largos[correcta] == orden[0] and orden[0] > orden[1]:
        s += 25
        if largos[correcta] >= 1.3 * orden[1]:
            s += 10
    if not _ABS.search(opciones[correcta] or ""):
        n = sum(1 for k, v in opciones.items() if k != correcta and _ABS.search(v or ""))
        if n:
            s += min(30, 12 * n)
    if _TODAS.search(opciones[correcta] or ""):
        s += 15
    e = _palabras(enunciado)
    co = {k: len(e & _palabras(v)) for k, v in opciones.items()}
    if co[correcta] > 0 and co[correcta] == max(co.values()) and list(co.values()).count(co[correcta]) == 1:
        s += 15
    if len(enunciado or "") < 90:
        s += 10
    return min(s, 100)


def _tok(t):
    return re.findall(r"\w+", (t or "").lower())


def correcta_central_destacada(opciones, correcta, margen=0.02) -> bool:
    """True si la correcta es la opción más parecida a las otras tres con un margen
    claro (>2 %). En un diseño cruzado bien hecho las cuatro empatan y no se rechaza.
    Margen calibrado: detecta el 70 % de las preguntas con este defecto y solo
    afecta a un 15 % de las preguntas de exámenes reales."""
    sc = {k: sum(difflib.SequenceMatcher(None, _tok(v), _tok(w)).ratio()
                 for j, w in opciones.items() if j != k) for k, v in opciones.items()}
    orden = sorted(sc.values(), reverse=True)
    return max(sc, key=sc.get) == correcta and (orden[0] - orden[1]) / max(orden[1], 1e-9) > margen


def marca_norma(nombre: str) -> re.Pattern | None:
    """Patrón que debe aparecer en el enunciado para que la norma sea explícita."""
    n = nombre or ""
    m = re.search(r"\b\d{1,4}/\d{4}\b", n)
    if m:
        return re.compile(re.escape(m.group(0)))
    low = n.lower()
    if "constituci" in low:
        return re.compile(r"constituci", re.I)
    if "estatut" in low:
        return re.compile(r"estatut", re.I)
    if "tratado de funcionamiento" in low:
        return re.compile(r"tratado de funcionamiento|TFUE", re.I)
    if "tratado de la uni" in low:
        return re.compile(r"tratado de la uni[oó]n|TUE\b", re.I)
    return None


def comprobaciones_deterministas(p: dict[str, Any], formato: str, patron_norma) -> list[str]:
    fallos = []
    ops = {k: str(p[f"opcion_{k.lower()}"]) for k in "ABCD"}
    corr = p["respuesta_correcta"]
    if puntuacion_pistas(p["enunciado"], ops, corr) >= 20:
        fallos.append("pistas")
    if formato in ("directa", "supuesto_practico") and correcta_central_destacada(ops, corr):
        fallos.append("correcta_central")
    largos = sorted(len(v) for v in ops.values())
    if largos[-1] > 170:
        fallos.append("opcion_demasiado_larga")
    if largos[0] < 0.55 * largos[-1]:
        fallos.append("longitudes_desequilibradas")
    if _TODAS.search(" ".join(ops.values())):
        fallos.append("todas_ninguna")
    e = _palabras(p["enunciado"])
    eco = {k: len(e & _palabras(v)) for k, v in ops.items()}
    otros = max(v for k, v in eco.items() if k != corr)
    if eco[corr] >= otros + 2:
        fallos.append("eco_enunciado")
    if patron_norma is not None and not patron_norma.search(p["enunciado"]):
        fallos.append("norma_no_explicita")
    if formato == "combinada" and not (re.search(r"\bI\b", p["enunciado"]) and re.search(r"\bII\b", p["enunciado"])
                                        and re.search(r"\bIII\b", p["enunciado"])):
        fallos.append("combinada_sin_afirmaciones")
    if re.search(r"\b(INCORRECTA|FALSA|NO es correcta|EXCEPTO)\b", p["enunciado"]):
        fallos.append("formato_negativo")
    return fallos


# ----------------------------------------------------------------------------
# Prompts
# ----------------------------------------------------------------------------
def aspectos_ya_preguntados(con, ctx, maximo=15) -> list[str]:
    ejemplos = base.cargar_ejemplos(con, ctx, 60)
    vistos = []
    for e in ejemplos:
        t = re.sub(r"\s+", " ", str(e["enunciado"] or ""))[:110]
        if t and t not in vistos:
            vistos.append(t)
    random.shuffle(vistos)
    return vistos[:maximo]


def construir_prompt_v5(ctx, aspectos: list[str], formatos: list[str], niveles: list[str]) -> str:
    encargos = "\n".join(
        f"  {i + 1}. formato={f}, nivel={n}" for i, (f, n) in enumerate(zip(formatos, niveles)))
    lista_aspectos = "\n".join(f"- {a}" for a in aspectos) or "- (ninguno)"
    return f"""
Actúas como redactor experto de exámenes de oposición de la Generalitat Valenciana.
Los opositores tienen formación jurídica amplia (muchos son licenciados en Derecho).
Las preguntas deben ser MÁS exigentes que las del examen oficial.

{base.bloque_fuente_prompt(ctx)}
---
{ctx.texto_articulo}
---
{base.reglas_fuente_prompt(ctx, para_generacion=True)}

ASPECTOS YA PREGUNTADOS EN EL BANCO (evítalos o búscales un ángulo distinto):
{lista_aspectos}

ENCARGO: redacta {len(formatos)} preguntas NUEVAS, una por línea de encargo:
{encargos}

FORMATOS
- directa: enunciado o pregunta incompleta + 4 opciones.
- combinada: el enunciado incluye tres afirmaciones I, II, III; las opciones son combinaciones
  ("I y II", "Solo III", "I y III", "II y III"...). Solo una combinación es exactamente la verdadera.
- supuesto_practico: caso breve y realista (interesado, órgano, fechas o plazos) que exige aplicar la fuente;
  las opciones son consecuencias jurídicas.

NIVEL: MEDIO = como el examen oficial; DIFICIL = exige el dato preciso; MUY_DIFICIL = combina dos
condiciones de la fuente o una excepción poco evidente.

REGLAS OBLIGATORIAS
1. Una sola respuesta correcta, demostrable EXCLUSIVAMENTE con la fuente. Sin "todas" ni "ninguna".
2. La norma aparece SIEMPRE de forma explícita en el enunciado (nombre o número). Citar el número de
   artículo es opcional: hazlo aproximadamente en la mitad de las preguntas.
3. DISEÑO CRUZADO de distractores: elige dos elementos variables (p. ej., plazo y órgano) y combina
   sus valores en las cuatro opciones, de modo que cada valor aparezca en al menos dos opciones.
   La correcta NO debe ser la opción que más se parece a las otras tres.
4. Distractores verosímiles: cada uno cambia un dato preciso (plazo, órgano, sujeto, efecto, excepción,
   requisito, "podrá"/"deberá", cuantía). Mejor si el dato cambiado existe en otro lugar de la norma.
5. Prohibidas en los distractores las palabras absolutas (siempre, nunca, en todo caso, en ningún caso,
   exclusivamente, únicamente, solo, con independencia de), salvo que la correcta también las tenga.
6. LONGITUD: redacta primero la opción correcta y después los distractores con igual o mayor
   longitud; al menos UN distractor debe ser más largo que la correcta. Diferencia máxima ±20%.
   Máximo 140 caracteres por opción. Pon el contexto en el enunciado, no en las opciones.
7. La correcta y los distractores no deben repetir palabras del enunciado más que las otras opciones.
8. No inventes plazos, órganos, excepciones ni consecuencias que no estén en la fuente.
9. {base.instruccion_referencia_prompt(ctx)}

ANTES DE RESPONDER, revisa cada pregunta: (a) ¿la correcta es la más larga? -> alarga un distractor;
(b) ¿algún distractor contiene una palabra absoluta? -> reformúlalo; (c) ¿la correcta repite palabras
del enunciado más que las demás? -> reformúlala; (d) ¿la norma aparece en el enunciado?

Devuelve SOLO JSON:
{{
  "preguntas": [
    {{
      "formato": "directa|combinada|supuesto_practico",
      "nivel_objetivo": "MEDIO|DIFICIL|MUY_DIFICIL",
      "enunciado": "...",
      "opcion_a": "...", "opcion_b": "...", "opcion_c": "...", "opcion_d": "...",
      "respuesta_correcta": "A|B|C|D",
      "articulo_referencia": "...",
      "explicacion_literal": "fragmento literal de la fuente que demuestra la respuesta",
      "trampas": [
        {{"opcion": "B", "tipo": "{'|'.join(TIPOS_TRAMPA)}", "detalle": "qué dato cambia"}}
      ]
    }}
  ]
}}
""".strip()


DESCRIPCION_FALLOS = {
    "pistas": "la opción correcta se delata (es la más larga, repite palabras del enunciado o los distractores llevan palabras absolutas como siempre/nunca/en todo caso/solo/exclusivamente)",
    "correcta_central": "la opción correcta es la que más se parece a las otras tres; usa diseño cruzado (dos datos variables combinados en las cuatro opciones)",
    "longitudes_desequilibradas": "las opciones tienen longitudes muy distintas; iguálalas (±20%)",
    "opcion_demasiado_larga": "alguna opción supera los 140 caracteres; pasa el contexto al enunciado",
    "eco_enunciado": "la opción correcta repite palabras del enunciado más que los distractores",
}
REPARABLES = set(DESCRIPCION_FALLOS)


def construir_prompt_reparacion(ctx, p: dict[str, Any], fallos: list[str]) -> str:
    actual = {k: p.get(k) for k in ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d",
                                     "respuesta_correcta", "formato", "trampas")}
    problemas = "\n".join(f"- {DESCRIPCION_FALLOS[f]}" for f in fallos if f in DESCRIPCION_FALLOS)
    return f"""
Corrige esta pregunta de oposición SIN cambiar su contenido jurídico ni cuál es la respuesta correcta.
Fuente (única referencia válida):
---
{ctx.texto_articulo}
---
PREGUNTA ACTUAL
{json.dumps(actual, ensure_ascii=False, indent=1)}

PROBLEMAS DETECTADOS
{problemas}

Reescribe el enunciado y/o las opciones para eliminar esos problemas. Mantén la misma letra correcta,
la norma explícita en el enunciado y una sola respuesta correcta según la fuente. Ninguna opción
puede superar 140 caracteres y al menos un distractor debe ser más largo que la correcta.
Devuelve SOLO JSON con las mismas claves: enunciado, opcion_a, opcion_b, opcion_c, opcion_d,
respuesta_correcta, formato, trampas (lista de {{"opcion","tipo","detalle"}}).
""".strip()


def construir_prompt_sin_enunciado(p: dict[str, Any]) -> str:
    ops = {k: p[f"opcion_{k.lower()}"] for k in "ABCD"}
    return f"""
Experimento. Se ha OCULTADO el enunciado de una pregunta tipo test de oposiciones (Derecho español).
Solo ves las cuatro opciones. Adivina cuál es la correcta guiándote por las pistas de las propias opciones.
{json.dumps(ops, ensure_ascii=False, indent=1)}
Devuelve SOLO JSON: {{"respuesta": "A|B|C|D"}}
""".strip()


# ----------------------------------------------------------------------------
# Metadatos (fichero aparte)
# ----------------------------------------------------------------------------
def conectar_metadatos() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_METADATOS)
    con.execute(
        """CREATE TABLE IF NOT EXISTS pregunta_metadatos (
            pregunta_id INTEGER PRIMARY KEY,
            version_generador TEXT NOT NULL,
            formato TEXT, nivel_objetivo TEXT, trampas_json TEXT,
            puntuacion_pistas INTEGER, control_sin_enunciado TEXT,
            modelo_generacion TEXT, modelo_auditoria TEXT,
            norma_id INTEGER, articulo TEXT, convocatoria_contexto TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    return con


def _remapear_trampas(trampas, orden_textos_antes, p):
    """Las trampas vienen por letra; tras barajar se guardan por texto y letra nueva."""
    texto_a_letra = {str(p[f"opcion_{k.lower()}"]): k for k in "ABCD"}
    texto_correcta = str(p[f"opcion_{p['respuesta_correcta'].lower()}"])
    salida = []
    for t in trampas or []:
        letra = str(t.get("opcion") or "").strip().upper()
        texto = orden_textos_antes.get(letra)
        if texto is None or texto == texto_correcta:
            continue  # una trampa solo puede ser un distractor
        salida.append({"opcion": texto_a_letra.get(texto, letra), "texto": texto,
                       "tipo": t.get("tipo"), "detalle": t.get("detalle")})
    return salida


# ----------------------------------------------------------------------------
# Flujo
# ----------------------------------------------------------------------------
def guardar_auxiliar(ctx, p, analisis, validacion, auditoria, auditoria2, clasificacion,
                     estado, motivo, similitud, similitud_id, modelos):
    with base.conectar_auxiliar() as aux:
        cur = aux.execute(
            """INSERT INTO generaciones_preguntas_ia (
                fecha_generacion, convocatoria_contexto_id, convocatoria_codigo, origen_oposicion,
                temario_referencia_id, tema_id, norma_id, nombre_norma, articulo_solicitado,
                articulo_fuente_id, tipo_pregunta, dificultad_objetivo, modelo_generacion,
                modelo_validacion, prompt_version, ejemplos_ids_json, analisis_json, pregunta_json,
                validacion_json, estado, dictamen_ia, auditoria_json, auditoria2_json,
                clasificacion_final, evidencia_json, similitud_maxima, similitud_pregunta_id,
                dictamen_auditoria, observaciones)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (base.ahora_iso(), ctx.convocatoria_id, ctx.convocatoria_codigo, ctx.origen_oposicion,
             ctx.referencia_id, ctx.tema_id, ctx.norma_id, ctx.nombre_norma_csv,
             ctx.articulo_solicitado, ctx.articulo_fuente_id,
             "PRACTICA" if p.get("formato") == "supuesto_practico" else "TEORICA",
             DIFICULTAD_OBJETIVO, modelos["generacion"], modelos["auditoria"],
             base.PROMPT_VERSION, "[]", json.dumps(analisis, ensure_ascii=False),
             json.dumps(p, ensure_ascii=False), json.dumps(validacion, ensure_ascii=False),
             estado, clasificacion,
             json.dumps(auditoria, ensure_ascii=False) if auditoria else None,
             json.dumps(auditoria2, ensure_ascii=False) if auditoria2 else None,
             clasificacion, json.dumps({"motivo": motivo}, ensure_ascii=False),
             float(similitud or 0), similitud_id, "OK" if estado == "VALIDADA_IA" else motivo, motivo))
        aux.commit()
        return int(cur.lastrowid)


def procesar_referencia(con, ctx, k, modelos, control_sin_enunciado, publicar, informe, meta,
                        restantes=10**9, cupos=None, reparar=False, solo_practica=False):
    from openai_api import seleccionar_fragmento_json

    if not base.fuente_oficial_suficiente(ctx.texto_articulo, "TEORICA"):
        return 0
    inicio = random.randrange(len(FORMATOS))
    formatos = [FORMATOS[(inicio + i) % len(FORMATOS)] for i in range(k)]
    if solo_practica:
        formatos = ["supuesto_practico"] * k
    niveles = [NIVELES[(inicio + i) % len(NIVELES)] for i in range(k)]
    patron_norma = marca_norma(ctx.nombre_norma_csv)
    prompt = construir_prompt_v5(ctx, aspectos_ya_preguntados(con, ctx), formatos, niveles)
    try:
        # Los modelos de razonamiento gastan parte del límite en "pensar": margen amplio
        # y esfuerzo bajo (más barato). Si aun así se corta, se reintenta con margen mayor.
        datos = seleccionar_fragmento_json(
            prompt=prompt, modelo=modelos["generacion"], operacion="generar_pregunta_juridica_v5",
            max_output_tokens=16000, razonamiento=modelos.get("razonamiento"))
    except RuntimeError as exc:
        if "TRUNCADA" not in str(exc):
            raise
        datos = seleccionar_fragmento_json(
            prompt=prompt, modelo=modelos["generacion"], operacion="generar_pregunta_juridica_v5_reintento",
            max_output_tokens=32000, razonamiento="low")
    aprobadas = 0
    for p in (datos.get("preguntas") or [])[:k]:
        if aprobadas >= restantes:
            break
        fila = {"reparada": "", "referencia": ctx.referencia_id, "norma": ctx.nombre_norma_csv,
                "articulo": ctx.articulo_solicitado, "formato": p.get("formato"),
                "nivel": p.get("nivel_objetivo"), "estado": "", "motivo": "", "pistas": "",
                "control_sin_enunciado": "", "auditoria": "", "lote_pregunta_id": "",
                "enunciado": p.get("enunciado"), "A": p.get("opcion_a"), "B": p.get("opcion_b"),
                "C": p.get("opcion_c"), "D": p.get("opcion_d"),
                "correcta": p.get("respuesta_correcta"), "trampas": ""}
        informe.append(fila)
        if solo_practica:
            p["formato"] = "supuesto_practico"
        # La referencia publicada es siempre el artículo del temario usado como fuente
        # (evita rechazos de publicación por referencias múltiples como "arts. 98.2 y 98.4").
        p["articulo_referencia"] = ctx.articulo_solicitado
        try:
            p["tipo_pregunta"] = "PRACTICA" if p.get("formato") == "supuesto_practico" else "TEORICA"
            base.validar_estructura_pregunta(p)
        except Exception as exc:
            fila.update(estado="RECHAZADA", motivo=f"estructura: {exc}")
            continue
        formato = str(p.get("formato") or "directa")
        if cupos is not None and formato == "combinada" and cupos["combinada"] >= cupos["max_combinada"]:
            fila.update(estado="RECHAZADA", motivo="cupo de formato combinada completo")
            continue
        fallos = comprobaciones_deterministas(p, formato, patron_norma)
        if reparar and fallos and set(fallos) <= REPARABLES:
            try:
                r = seleccionar_fragmento_json(
                    prompt=construir_prompt_reparacion(ctx, p, fallos), modelo=modelos["generacion"],
                    operacion="reparar_pregunta_v5", max_output_tokens=12000,
                    razonamiento=modelos.get("razonamiento"))
                if str(r.get("respuesta_correcta") or "").strip().upper() == p["respuesta_correcta"]:
                    for clave in ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d", "trampas"):
                        if r.get(clave):
                            p[clave] = r[clave]
                    fila["reparada"] = "SI"
                    fallos = comprobaciones_deterministas(p, formato, patron_norma)
            except Exception:
                pass
        ops = {k2: p[f"opcion_{k2.lower()}"] for k2 in "ABCD"}
        fila["pistas"] = puntuacion_pistas(p["enunciado"], ops, p["respuesta_correcta"])
        fila.update(enunciado=p["enunciado"])
        if fallos:
            fila.update(estado="RECHAZADA", motivo="automática: " + ",".join(fallos))
            continue
        textos_antes = {k2: str(p[f"opcion_{k2.lower()}"]) for k2 in "ABCD"}
        base.barajar_opciones(p)
        trampas = _remapear_trampas(p.get("trampas"), textos_antes, p)
        p["trampas"] = trampas
        fila.update(correcta=p["respuesta_correcta"], A=p["opcion_a"], B=p["opcion_b"],
                    C=p["opcion_c"], D=p["opcion_d"], trampas=json.dumps(trampas, ensure_ascii=False))
        try:
            base.comprobar_no_duplicada(con, p)
        except Exception as exc:
            fila.update(estado="RECHAZADA", motivo=f"duplicada: {exc}")
            continue
        original, similitud, similitud_id = base.comprobar_originalidad_masiva(con, ctx, p)
        if not original:
            fila.update(estado="RECHAZADA", motivo=f"similitud {similitud:.3f} con {similitud_id}")
            continue
        control = None
        if control_sin_enunciado:
            try:
                r = seleccionar_fragmento_json(prompt=construir_prompt_sin_enunciado(p),
                                               modelo=modelos["control"],
                                               operacion="control_sin_enunciado_v5",
                                               max_output_tokens=4000, razonamiento="low")
                control = str(r.get("respuesta") or "").strip().upper()[:1]
            except Exception as exc:
                fila.update(estado="RECHAZADA", motivo=f"error en control sin enunciado: {exc}")
                continue
            fila["control_sin_enunciado"] = "ACIERTA" if control == p["respuesta_correcta"] else "FALLA"
            if control == p["respuesta_correcta"]:
                fila.update(estado="RECHAZADA", motivo="se acierta sin leer el enunciado")
                continue
        try:
            auditoria, auditoria2, clasif, _, motivo = base.resolver_clasificacion_auditoria(
                ctx, p, modelos["auditoria"])
        except Exception as exc:
            fila.update(estado="RECHAZADA", motivo=f"error en auditoría: {exc}")
            continue
        fila["auditoria"] = clasif
        estado = "VALIDADA_IA" if clasif == "CONFIRMADA" else "RECHAZADA_IA"
        validacion = {"version": "v5", "comprobaciones": "OK", "pistas": fila["pistas"],
                      "control_sin_enunciado": fila["control_sin_enunciado"],
                      "dificultad": p.get("nivel_objetivo")}
        gid = guardar_auxiliar(ctx, p, {"formatos_pedidos": formatos}, validacion, auditoria,
                               auditoria2, clasif, estado, motivo, similitud, similitud_id, modelos)
        if estado != "VALIDADA_IA":
            fila.update(estado="RECHAZADA", motivo=f"auditoría: {clasif} - {motivo}")
            continue
        fila["estado"] = "APROBADA"
        aprobadas += 1
        if cupos is not None and formato == "combinada":
            cupos["combinada"] += 1
        if publicar:
            try:
                lote_id = base.aprobar(con, gid, modo_publicacion="IA_V5_AUDITADA")
                fila.update(estado="PUBLICADA", lote_pregunta_id=lote_id)
                meta.execute(
                    """INSERT OR REPLACE INTO pregunta_metadatos (pregunta_id, version_generador,
                       formato, nivel_objetivo, trampas_json, puntuacion_pistas,
                       control_sin_enunciado, modelo_generacion, modelo_auditoria, norma_id,
                       articulo, convocatoria_contexto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (lote_id, base.PROMPT_VERSION, formato, p.get("nivel_objetivo"),
                     json.dumps(trampas, ensure_ascii=False), fila["pistas"],
                     fila["control_sin_enunciado"], modelos["generacion"], modelos["auditoria"],
                     ctx.norma_id, ctx.articulo_solicitado, ctx.convocatoria_codigo))
                meta.commit()
            except Exception as exc:
                fila.update(estado="ERROR_PUBLICACION", motivo=str(exc))
    return aprobadas


def main() -> int:
    ap = argparse.ArgumentParser(description="Generador de preguntas jurídicas IA v5 (dificultad).")
    ap.add_argument("--db", default=str(base.DB_DEFECTO))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--codigo")
    g.add_argument("--convocatoria-id", type=int)
    ap.add_argument("--norma-id", type=int, help="Limitar a una norma (id del catálogo).")
    ap.add_argument("--tema-id", type=int)
    ap.add_argument("--huecos", action="store_true", help="Solo artículos sin preguntas en el banco.")
    ap.add_argument("--solo-practica", action="store_true",
                    help="Solo supuestos prácticos (preguntas PRACTICA) de las partes del temario que los admiten.")
    ap.add_argument("--cantidad", type=int, default=20, help="Preguntas aprobadas objetivo.")
    ap.add_argument("--por-llamada", type=int, default=3, help="Preguntas pedidas por artículo y llamada.")
    ap.add_argument("--max-llamadas", type=int, default=0, help="Tope de llamadas de generación (0 = automático).")
    ap.add_argument("--modelo-generacion", default="gpt-5-mini")
    ap.add_argument("--modelo-auditoria", default="gpt-5.4")
    ap.add_argument("--modelo-control", default="gpt-5.4-nano")
    ap.add_argument("--sin-control-enunciado", action="store_true")
    ap.add_argument("--reparar", action="store_true",
                    help="Intenta reparar una vez las preguntas que fallan solo por la forma (desactivado: no compensa).")
    ap.add_argument("--razonamiento", default="low", choices=["low", "medium", "high"],
                    help="Esfuerzo de razonamiento en la generación (por defecto low: más barato).")
    ap.add_argument("--publicar", action="store_true",
                    help="Publica en lote_preguntas las aprobadas y sincroniza bancos.")
    ap.add_argument("--semilla", type=int)
    a = ap.parse_args()
    if a.semilla is not None:
        random.seed(a.semilla)
    ruta_db = Path(a.db).resolve()
    modelos = {"generacion": a.modelo_generacion, "auditoria": a.modelo_auditoria,
               "control": a.modelo_control, "razonamiento": a.razonamiento}

    if a.publicar:
        copia = ruta_db.with_name(f"oposiciones_backup_antes_generacion_v5_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
        origen = sqlite3.connect(ruta_db); destino = sqlite3.connect(copia)
        origen.backup(destino); destino.close(); origen.close()
        print(f"Copia de seguridad antes de publicar: {copia}")
    base.iniciar_ejecucion_generacion()
    informe: list[dict[str, Any]] = []
    meta = conectar_metadatos()
    publicadas = 0
    try:
        with base.conectar_maestra(ruta_db) as con:
            conv = base.obtener_convocatoria(con, a.convocatoria_id, a.codigo)
            cid = int(conv["id"])
            if a.huecos:
                contextos, _ = base.obtener_referencias_huecos(con, cid, a.tema_id)
            elif a.tema_id is not None:
                contextos = base.obtener_referencias_aptas_del_tema(con, cid, a.tema_id, exigir_ejemplos=False)
            else:
                contextos, _ = base.obtener_referencias_aptas_todos_temas(con, cid)
            if a.norma_id is not None:
                contextos = [c for c in contextos if int(c.norma_id) == a.norma_id]
            if a.solo_practica:
                if not base.convocatoria_admite_practica(con, cid):
                    print("La convocatoria no tiene ninguna parte PRACTICA.")
                    return 0
                partes = base.partes_temario_admitidas_para_practica(con, cid)
                contextos = base.filtrar_contextos_para_practica(contextos, partes)
                contextos = [c for c in contextos if base.fuente_oficial_suficiente(c.texto_articulo, "PRACTICA")]
                print(f"Solo supuestos prácticos · partes del temario admitidas: {', '.join(partes)}")
            if not contextos:
                print("No hay referencias aptas con esos filtros.")
                return 0
            random.shuffle(contextos)
            # Con ~20 % de aprobación y 3 preguntas por llamada hacen falta ~1,7 llamadas por aprobada.
            max_llamadas = a.max_llamadas or max(5, a.cantidad * 2 + 5)
            print("=" * 78)
            print(f"GENERADOR v5 · {conv['codigo']} · referencias aptas: {len(contextos)}")
            print(f"Objetivo: {a.cantidad} aprobadas · {a.por_llamada} por llamada · máx. {max_llamadas} llamadas")
            print(f"Modelos: generación={a.modelo_generacion} · auditoría={a.modelo_auditoria} · "
                  f"control={a.modelo_control}")
            print("Modo:", "PUBLICAR" if a.publicar else "SOLO PRUEBA (no se publica nada)")
            print("=" * 78)
            aprobadas, llamadas = 0, 0
            cupos = {"combinada": 0, "max_combinada": max(1, round(a.cantidad * 0.25))}
            for ctx in contextos * 3:
                if aprobadas >= a.cantidad or llamadas >= max_llamadas:
                    break
                llamadas += 1
                try:
                    n = procesar_referencia(con, ctx, a.por_llamada, modelos,
                                            not a.sin_control_enunciado, a.publicar, informe, meta,
                                            restantes=a.cantidad - aprobadas, cupos=cupos,
                                            reparar=a.reparar, solo_practica=a.solo_practica)
                except Exception as exc:
                    print(f"  ! referencia {ctx.referencia_id}: {exc}")
                    continue
                aprobadas += n
                print(f"  llamada {llamadas}: {ctx.nombre_norma_csv[:40]} art. {ctx.articulo_solicitado} "
                      f"-> {n} aprobadas (total {aprobadas})")
            publicadas = sum(1 for f in informe if f["estado"] == "PUBLICADA")
    finally:
        meta.close()
        base.finalizar_ejecucion_generacion()

    base.REGISTROS.mkdir(parents=True, exist_ok=True)
    ruta = base.REGISTROS / f"preguntas_v5_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        campos = list(informe[0].keys()) if informe else ["estado"]
        w = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        w.writeheader()
        w.writerows(informe)
    total = len(informe)
    print("\n" + "=" * 78)
    print(f"Candidatas generadas: {total}")
    from collections import Counter
    for estado, n in Counter(f["estado"] for f in informe).most_common():
        print(f"  {estado or '(sin estado)'}: {n}")
    motivos = Counter(f["motivo"].split(":")[0] for f in informe if f["estado"] == "RECHAZADA")
    if motivos:
        print("Motivos de rechazo:", dict(motivos))
    print(f"Informe: {ruta}")
    print("Costes: ver logs/costes_ia.csv")
    if publicadas:
        print(f"\nPublicadas {publicadas}. Sincronizando bancos...")
        base.sincronizar_todos_bancos(ruta_db, aplicar=True, validar_final=True)
        try:
            from calcular_nivel_dificultad import aplicar_niveles
            aplicar_niveles(ruta_db)
        except Exception as exc:
            print(f"AVISO: no se pudieron recalcular los niveles de dificultad: {exc}. "
                  "Ejecuta: python scripts/calcular_nivel_dificultad.py --aplicar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
