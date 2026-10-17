"""Generador v5 con la Batch API de OpenAI (50 % más barato, para lotes grandes y desatendidos).

Mismas reglas y controles que generar_preguntas_juridicas_v5.py, pero cada fase se envía a
OpenAI como un lote asíncrono (tarda de minutos a unas horas; máximo 24 h por fase):
  1. generación (gpt-5-mini) ............ 3 preguntas por artículo y petición
  2. reparación de distractores .......... solo las que fallan por pistas/longitudes
  3. comprobaciones locales gratuitas .... duplicados, originalidad, norma explícita
  4. control sin enunciado (gpt-5.4-nano)  se descarta la que se acierta sin leer la pregunta
  5. auditoría ciega (gpt-5.4) ........... solo se publican las CONFIRMADAS
  6. publicación (--publicar) ............ lote_preguntas + sincronización de bancos + niveles

Diferencia con el v5 normal: no hay segunda auditoría (en el v5 solo servía para distinguir
errores de dudas; nunca aprobaba una pregunta), y se publican todas las aprobadas.

Es reanudable: el estado se guarda en registros/batch_v5_<fecha>/estado.json. Si se corta,
    python scripts/generar_preguntas_v5_batch.py --reanudar registros\\batch_v5_<fecha>
continúa donde se quedó sin volver a pagar las fases terminadas.

IMPORTANTE: no ejecutarlo a la vez que el generador v5 normal ni que el menú de generación
(comparten la base auxiliar temporal de generaciones).

Uso (desde TuCoach-Mantenimiento):
    python scripts/generar_preguntas_v5_batch.py --codigo C1-01_58_26 --modelo-examen --peticiones 60 --publicar
    python scripts/generar_preguntas_v5_batch.py --codigo A1-01_01_26_ADM --huecos --peticiones 80 --publicar
Rendimiento orientativo: ~0,4 preguntas aprobadas por petición -> 60 peticiones ~ 25 preguntas.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import generar_preguntas_juridicas_v5 as v5
from openai_api import PRECIOS, cliente, registrar_coste

base = v5.base
MODELOS = {"generacion": "gpt-5-mini", "auditoria": "gpt-5.4", "control": "gpt-5.4-nano", "razonamiento": "low"}
ESPERA_S = 60


# ----------------------------------------------------------------------------
# Batch API
# ----------------------------------------------------------------------------
def _coste_batch(modelo, entrada, cacheada, salida) -> float:
    p = PRECIOS[modelo]
    pc = p.get("cached_input", p["input"])
    return (max(0, entrada - cacheada) * p["input"] + cacheada * pc + salida * p["output"]) / 1_000_000 / 2


def ejecutar_fase(estado, carpeta: Path, nombre: str, peticiones: list[tuple[str, str]], modelo: str,
                  razonamiento: str | None, max_tokens: int) -> dict[str, Any]:
    """Envía (o reanuda) un lote y devuelve {custom_id: json_respuesta | None}."""
    salida = carpeta / f"{nombre}_salida.json"
    if salida.exists():
        return json.loads(salida.read_text(encoding="utf-8"))
    if not peticiones:
        salida.write_text("{}", encoding="utf-8")
        return {}
    fases = estado.setdefault("lotes", {})
    if nombre not in fases:
        ruta = carpeta / f"{nombre}_entrada.jsonl"
        with open(ruta, "w", encoding="utf-8") as f:
            for cid, prompt in peticiones:
                body = {"model": modelo, "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"}, "max_completion_tokens": max_tokens}
                if razonamiento:
                    body["reasoning_effort"] = razonamiento
                f.write(json.dumps({"custom_id": cid, "method": "POST", "url": "/v1/chat/completions",
                                    "body": body}, ensure_ascii=False) + "\n")
        with open(ruta, "rb") as fh:
            fichero = cliente.files.create(file=fh, purpose="batch")
        lote = cliente.batches.create(input_file_id=fichero.id, endpoint="/v1/chat/completions",
                                      completion_window="24h", metadata={"tucoach": nombre})
        fases[nombre] = {"id": lote.id, "enviado": datetime.now().isoformat(timespec="seconds"),
                         "peticiones": len(peticiones), "modelo": modelo}
        guardar_estado(carpeta, estado)
        print(f"  [{nombre}] lote enviado: {lote.id} · {len(peticiones)} peticiones · {modelo}")
    lote_id = fases[nombre]["id"]
    t0 = time.time()
    while True:
        b = cliente.batches.retrieve(lote_id)
        rc = getattr(b, "request_counts", None)
        hechos = f"{getattr(rc, 'completed', '?')}/{getattr(rc, 'total', '?')}" if rc else ""
        if b.status in ("completed", "failed", "expired", "cancelled"):
            break
        print(f"  [{nombre}] {datetime.now():%H:%M} estado={b.status} {hechos}", flush=True)
        time.sleep(ESPERA_S)
    if not getattr(b, "output_file_id", None):
        raise RuntimeError(f"El lote {nombre} ({lote_id}) terminó con estado {b.status} y sin resultados.")
    texto = cliente.files.content(b.output_file_id).text
    (carpeta / f"{nombre}_salida.jsonl").write_text(texto, encoding="utf-8")
    resultados: dict[str, Any] = {}
    ent = cach = sal = 0
    errores = 0
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        r = json.loads(linea)
        cid = r.get("custom_id")
        resp = r.get("response") or {}
        if resp.get("status_code") != 200:
            resultados[cid] = None; errores += 1
            continue
        cuerpo = resp.get("body") or {}
        uso = cuerpo.get("usage") or {}
        ent += int(uso.get("prompt_tokens") or 0)
        cach += int((uso.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        sal += int(uso.get("completion_tokens") or 0)
        try:
            contenido = cuerpo["choices"][0]["message"]["content"] or ""
            resultados[cid] = json.loads(contenido)
        except Exception:
            resultados[cid] = None; errores += 1
    coste = _coste_batch(modelo, ent, cach, sal)
    registrar_coste(modelo=modelo, operacion=f"batch_{nombre}", tiempo=time.time() - t0, input_tokens=ent,
                    cached_tokens=cach, output_tokens=sal, coste=coste)
    estado.setdefault("costes", {})[nombre] = round(coste, 6)
    guardar_estado(carpeta, estado)
    salida.write_text(json.dumps(resultados, ensure_ascii=False), encoding="utf-8")
    print(f"  [{nombre}] terminado: {len(resultados)} respuestas ({errores} con error) · coste {coste:.3f} USD")
    return resultados


def guardar_estado(carpeta: Path, estado: dict) -> None:
    (carpeta / "estado.json").write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def barajar_determinista(p: dict, semilla: str) -> None:
    """Como base.barajar_opciones, pero reproducible (necesario para poder reanudar)."""
    letras = ["a", "b", "c", "d"]
    textos = [str(p[f"opcion_{l}"]) for l in letras]
    if any(base._RE_OPCION_DEPENDIENTE.search(t) for t in textos):
        return
    correcta = letras.index(str(p["respuesta_correcta"]).strip().lower())
    orden = list(range(4))
    random.Random(semilla).shuffle(orden)
    for nueva, vieja in enumerate(orden):
        p[f"opcion_{letras[nueva]}"] = textos[vieja]
    p["respuesta_correcta"] = letras[orden.index(correcta)].upper()


# ----------------------------------------------------------------------------
# Selección de artículos (igual que el v5)
# ----------------------------------------------------------------------------
def seleccionar_secuencia(con, a) -> tuple[dict, list]:
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
    contextos = [c for c in contextos if base.fuente_oficial_suficiente(c.texto_articulo, "TEORICA")]
    if not contextos:
        raise RuntimeError("No hay referencias aptas con esos filtros.")
    random.shuffle(contextos)
    if a.modelo_examen:
        modelo = base.cargar_modelo_generacion_ia(con, cid)
        secuencia, avisos = base.secuencia_contextos_segun_modelo(contextos, modelo, a.peticiones)
        for aviso in avisos:
            print(f"  AVISO: {aviso}")
    else:
        secuencia = (contextos * (a.peticiones // len(contextos) + 1))[: a.peticiones]
    return dict(conv), secuencia


# ----------------------------------------------------------------------------
# Flujo
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Generador v5 por lotes (Batch API, -50 %).")
    ap.add_argument("--db", default=str(base.DB_DEFECTO))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--codigo")
    g.add_argument("--convocatoria-id", type=int)
    g.add_argument("--reanudar", type=Path, help="Carpeta registros/batch_v5_<fecha> de una ejecución cortada.")
    ap.add_argument("--modelo-examen", action="store_true")
    ap.add_argument("--tema-id", type=int)
    ap.add_argument("--norma-id", type=int)
    ap.add_argument("--huecos", action="store_true")
    ap.add_argument("--peticiones", type=int, default=60, help="Peticiones de generación (3 candidatas cada una).")
    ap.add_argument("--por-peticion", type=int, default=3)
    ap.add_argument("--publicar", action="store_true")
    a = ap.parse_args()
    ruta_db = Path(a.db).resolve()

    if a.reanudar:
        carpeta = a.reanudar if a.reanudar.is_absolute() else base.ROOT / a.reanudar
        estado = json.loads((carpeta / "estado.json").read_text(encoding="utf-8"))
        a.publicar = estado["publicar"]
        if estado.get("publicado"):
            print(f"Esa ejecución ya se publicó. Informe: {carpeta / 'informe.csv'}")
            return 0
        print(f"Reanudando {carpeta}")
    else:
        if not (a.codigo or a.convocatoria_id):
            ap.error("indica --codigo, --convocatoria-id o --reanudar")
        carpeta = base.REGISTROS / f"batch_v5_{datetime.now():%Y%m%d_%H%M%S}"
        carpeta.mkdir(parents=True, exist_ok=True)
        estado = {"publicar": a.publicar, "argumentos": vars(a) | {"reanudar": None, "db": str(ruta_db)}}

    with base.conectar_maestra(ruta_db) as con:
        # ---- Fase 0: peticiones de generación
        if "peticiones" not in estado:
            conv, secuencia = seleccionar_secuencia(con, a)
            peticiones = []
            for i, ctx in enumerate(secuencia):
                ini = random.randrange(len(v5.FORMATOS))
                formatos = [v5.FORMATOS[(ini + j) % len(v5.FORMATOS)] for j in range(a.por_peticion)]
                niveles = [v5.NIVELES[(ini + j) % len(v5.NIVELES)] for j in range(a.por_peticion)]
                peticiones.append({"cid": f"g{i}", "convocatoria_id": int(ctx.convocatoria_id),
                                   "referencia_id": int(ctx.referencia_id), "formatos": formatos, "niveles": niveles})
            estado.update(peticiones=peticiones, convocatoria=conv["codigo"])
            guardar_estado(carpeta, estado)
            print(f"GENERADOR v5 BATCH · {conv['codigo']} · {len(peticiones)} peticiones · carpeta {carpeta}")
        ctxs: dict[int, Any] = {}

        def ctx_de(pet):
            rid = pet["referencia_id"]
            if rid not in ctxs:
                ctxs[rid] = base.cargar_contexto(con, pet["convocatoria_id"], rid)
            return ctxs[rid]

        # ---- Fase 1: generación
        prompts = []
        for pet in estado["peticiones"]:
            ctx = ctx_de(pet)
            prompts.append((pet["cid"], v5.construir_prompt_v5(ctx, v5.aspectos_ya_preguntados(con, ctx),
                                                                pet["formatos"], pet["niveles"])))
        gen = ejecutar_fase(estado, carpeta, "1_generacion", prompts, MODELOS["generacion"], "low", 16000)

        # ---- Fase 2: comprobaciones automáticas y reparación de distractores
        cand: dict[str, dict] = {}
        informe: list[dict] = []
        for pet in estado["peticiones"]:
            ctx = ctx_de(pet)
            patron = v5.marca_norma(ctx.nombre_norma_csv)
            for j, p in enumerate(((gen.get(pet["cid"]) or {}).get("preguntas") or [])[: a.por_peticion]):
                cid = f"{pet['cid']}_{j}"
                fila = {"id": cid, "norma": ctx.nombre_norma_csv, "articulo": ctx.articulo_solicitado,
                        "formato": p.get("formato"), "nivel": p.get("nivel_objetivo"), "estado": "", "motivo": "",
                        "reparada": "", "pistas": "", "lote_pregunta_id": "", "enunciado": p.get("enunciado"),
                        "A": p.get("opcion_a"), "B": p.get("opcion_b"), "C": p.get("opcion_c"),
                        "D": p.get("opcion_d"), "correcta": p.get("respuesta_correcta")}
                informe.append(fila)
                if str(p.get("formato")) == "combinada":
                    fila.update(estado="RECHAZADA", motivo="formato combinada desactivado"); continue
                p["articulo_referencia"] = ctx.articulo_solicitado
                p["tipo_pregunta"] = "PRACTICA" if p.get("formato") == "supuesto_practico" else "TEORICA"
                try:
                    base.validar_estructura_pregunta(p)
                except Exception as exc:
                    fila.update(estado="RECHAZADA", motivo=f"estructura: {exc}"); continue
                fallos = v5.comprobaciones_deterministas(p, str(p.get("formato") or "directa"), patron)
                cand[cid] = {"p": p, "pet": pet, "fallos": fallos, "fila": fila}
        reparar = [(cid, v5.construir_prompt_distractores(ctx_de(c["pet"]), c["p"], c["fallos"]))
                   for cid, c in cand.items()
                   if c["fallos"] and set(c["fallos"]) <= v5.FALLOS_REPARABLES_DISTRACTORES]
        rep = ejecutar_fase(estado, carpeta, "2_reparacion", reparar, MODELOS["generacion"], "low", 8000)
        vistos = set()
        for cid, c in list(cand.items()):
            p, fila = c["p"], c["fila"]
            r = rep.get(cid)
            if r:
                corr = p["respuesta_correcta"]
                nuevos = {k: str(r.get(k) or "").strip() for k in "ABCD" if k != corr}
                if all(nuevos.values()):
                    for k, v in nuevos.items():
                        p[f"opcion_{k.lower()}"] = v
                    fila["reparada"] = "SI"
                    c["fallos"] = v5.comprobaciones_deterministas(
                        p, str(p.get("formato") or "directa"), v5.marca_norma(ctx_de(c["pet"]).nombre_norma_csv))
            ops = {k: p[f"opcion_{k.lower()}"] for k in "ABCD"}
            fila["pistas"] = v5.puntuacion_pistas(p["enunciado"], ops, p["respuesta_correcta"])
            if c["fallos"]:
                fila.update(estado="RECHAZADA", motivo="automática: " + ",".join(c["fallos"]))
                del cand[cid]; continue
            # ---- Fase 3: barajado, duplicados y originalidad (local, gratis)
            antes = {k: str(p[f"opcion_{k.lower()}"]) for k in "ABCD"}
            barajar_determinista(p, cid)  # mismo orden si se reanuda
            p["trampas"] = v5._remapear_trampas(p.get("trampas"), antes, p)
            clave = base.clave_exacta(p)
            try:
                if clave in vistos:
                    raise RuntimeError("duplicada dentro del lote")
                base.comprobar_no_duplicada(con, p)
            except Exception as exc:
                fila.update(estado="RECHAZADA", motivo=f"duplicada: {exc}"); del cand[cid]; continue
            vistos.add(clave)
            original, sim, sim_id = base.comprobar_originalidad_masiva(con, ctx_de(c["pet"]), p)
            if not original:
                fila.update(estado="RECHAZADA", motivo=f"similitud {sim:.3f} con {sim_id}"); del cand[cid]; continue
            c["sim"], c["sim_id"] = sim, sim_id
            fila.update(correcta=p["respuesta_correcta"], A=p["opcion_a"], B=p["opcion_b"],
                        C=p["opcion_c"], D=p["opcion_d"])

        # ---- Fase 4: control sin enunciado
        ctrl = ejecutar_fase(estado, carpeta, "3_control",
                             [(cid, v5.construir_prompt_sin_enunciado(c["p"])) for cid, c in cand.items()],
                             MODELOS["control"], "low", 4000)
        for cid in list(cand):
            r = ctrl.get(cid) or {}
            if str(r.get("respuesta") or "").strip().upper()[:1] == cand[cid]["p"]["respuesta_correcta"]:
                cand[cid]["fila"].update(estado="RECHAZADA", motivo="se acierta sin leer el enunciado")
                del cand[cid]

        # ---- Fase 5: auditoría ciega
        aud = ejecutar_fase(estado, carpeta, "4_auditoria",
                            [(cid, base.construir_prompt_auditoria_ciega(ctx_de(c["pet"]), c["p"], 1))
                             for cid, c in cand.items()], MODELOS["auditoria"], None, 8000)
        aprobadas = []
        for cid, c in cand.items():
            r = aud.get(cid)
            clasif, _, motivo = base.clasificar_auditoria(r or {}, c["p"]["respuesta_correcta"]) if r else \
                ("NO_DETERMINABLE", None, "sin respuesta de la auditoría")
            if clasif != "CONFIRMADA":
                c["fila"].update(estado="RECHAZADA", motivo=f"auditoría: {clasif} - {motivo}"); continue
            c["auditoria"] = r
            c["fila"]["estado"] = "APROBADA"
            aprobadas.append(c)

        # ---- Fase 6: publicación
        publicadas = 0
        if a.publicar and aprobadas and not estado.get("publicado"):
            copia = ruta_db.with_name(f"oposiciones_backup_antes_batch_v5_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
            d = sqlite3.connect(copia); con.backup(d); d.close()
            print(f"Copia de seguridad antes de publicar: {copia}")
            base.iniciar_ejecucion_generacion()
            meta = v5.conectar_metadatos()
            try:
                for c in aprobadas:
                    ctx, p, fila = ctx_de(c["pet"]), c["p"], c["fila"]
                    validacion = {"version": "v5-batch", "comprobaciones": "OK", "pistas": fila["pistas"],
                                  "control_sin_enunciado": "FALLA", "dificultad": p.get("nivel_objetivo")}
                    try:
                        gid = v5.guardar_auxiliar(ctx, p, {"lote": carpeta.name}, validacion, c["auditoria"], None,
                                                  "CONFIRMADA", "VALIDADA_IA", "", c["sim"], c["sim_id"], MODELOS)
                        lote_id = base.aprobar(con, gid, modo_publicacion="IA_V5_AUDITADA")
                        fila.update(estado="PUBLICADA", lote_pregunta_id=lote_id)
                        publicadas += 1
                        meta.execute(
                            """INSERT OR REPLACE INTO pregunta_metadatos (pregunta_id, version_generador, formato,
                               nivel_objetivo, trampas_json, puntuacion_pistas, control_sin_enunciado,
                               modelo_generacion, modelo_auditoria, norma_id, articulo, convocatoria_contexto)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (lote_id, base.PROMPT_VERSION + "-batch", p.get("formato"), p.get("nivel_objetivo"),
                             json.dumps(p.get("trampas"), ensure_ascii=False), fila["pistas"], "FALLA",
                             MODELOS["generacion"], MODELOS["auditoria"], ctx.norma_id,
                             ctx.articulo_solicitado, ctx.convocatoria_codigo))
                        meta.commit()
                    except Exception as exc:
                        fila.update(estado="ERROR_PUBLICACION", motivo=str(exc))
            finally:
                meta.close()
                base.finalizar_ejecucion_generacion()
            estado["publicado"] = True
            guardar_estado(carpeta, estado)

    ruta = carpeta / "informe.csv"
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(informe[0].keys()) if informe else ["estado"], delimiter=";")
        w.writeheader(); w.writerows(informe)
    from collections import Counter
    coste = sum(estado.get("costes", {}).values())
    n_ok = sum(1 for f in informe if f["estado"] in ("APROBADA", "PUBLICADA"))
    print("\n" + "=" * 78)
    print(f"Candidatas: {len(informe)} · " + " · ".join(f"{k}: {v}" for k, v in Counter(f['estado'] for f in informe).items()))
    print("Motivos de rechazo:", dict(Counter(f["motivo"].split(":")[0] for f in informe if f["estado"] == "RECHAZADA")))
    print(f"Coste total (precio Batch): {coste:.3f} USD" + (f" · {coste / n_ok:.4f} USD por pregunta" if n_ok else ""))
    print(f"Informe: {ruta}")
    if publicadas:
        print(f"\nPublicadas {publicadas}. Sincronizando bancos...")
        base.sincronizar_todos_bancos(ruta_db, aplicar=True, validar_final=True)
        from calcular_nivel_dificultad import aplicar_niveles
        aplicar_niveles(ruta_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
