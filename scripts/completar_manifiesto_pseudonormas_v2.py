"""
Constructor Temario V2 - completar manifiesto con propuesta pseudonormativa IA.

SOLO LECTURA respecto de BD, corpus, bancos y temario.csv.

Toma un manifiesto creado por analizar_convocatoria_v2.py y procesa los temas
JURIDICO_DOCTRINAL_TRANSVERSAL mediante el flujo ya validado:

  candidatos locales -> evaluación individual V4 -> síntesis por unidad
  -> normalización determinista V5 -> manifiesto común V2

No acepta automáticamente relaciones jurídicas. PLAUSIBLE_DIRECTO sigue siendo
una propuesta que debe verificarse; DUDOSO permanece separado y nunca se
promociona a prioritario. Las fuentes externas se proponen para búsqueda sin
inventar URLs.

Es reanudable: escribe un checkpoint del JSON de salida después de completar
cada tema. Nunca escribe en la BD ni en los datos consolidados.

Control de recursos:
- hasta MAX_TEXTO_IA caracteres, el texto se envía íntegro;
- por encima del límite duro, se convierte en extractos literales;
- los extractos se seleccionan determinísticamente alrededor de los términos que
  justificaron la candidatura y conservan trazabilidad de longitud/estrategia;
- nunca se altera el texto almacenado en la BD.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import unicodedata
from pathlib import Path

import proponer_contenido_pseudonormas_ia_v2 as base_ia
import proponer_contenido_pseudonormas_ia_v4 as ia_v4
import normalizar_propuesta_pseudonormas_ia_v4 as normalizador_v5


CLASE_DOCTRINAL = "JURIDICO_DOCTRINAL_TRANSVERSAL"
NATURALEZAS_ADMITIDAS = {
    "MANIFIESTO_ANALISIS_CONVOCATORIA_V2",
    "MANIFIESTO_CONSTRUCTOR_TEMARIO_V2",
}

# Límite duro aplicado únicamente a la COPIA enviada a IA. Nunca se altera el
# texto almacenado en la BD. Mantener un único umbral evita una franja en la que
# el texto pudiera superar MAX_TEXTO_IA sin ser convertido en extracto.
MAX_TEXTO_IA = 12_000
UMBRAL_TEXTO_COMPLETO = MAX_TEXTO_IA
TAM_FRAGMENTO = 1_800


def cargar_manifiesto(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("El manifiesto raíz debe ser un objeto JSON.")
    if data.get("naturaleza") not in NATURALEZAS_ADMITIDAS:
        raise ValueError("El JSON no es un manifiesto V2 admitido.")
    if data.get("modo") != "SOLO_LECTURA":
        raise ValueError("El manifiesto no declara modo SOLO_LECTURA.")
    if not isinstance(data.get("temas"), list):
        raise ValueError("El manifiesto no contiene temas[].")
    reglas = data.get("reglas") or {}
    for clave in ("modifica_bd", "modifica_corpus", "modifica_temario_csv", "modifica_bancos"):
        if reglas.get(clave) is not False:
            raise ValueError(f"Regla de seguridad inválida: {clave} debe ser false.")
    return data


def necesita_propuesta_ia(item: dict) -> bool:
    pseudo = item.get("pseudoarticulos") or []
    if not pseudo:
        return True
    return any(
        str(p.get("estado") or "") == "PENDIENTE_PROPUESTA_IA"
        for p in pseudo
    )


def seleccionar_temas(manifiesto: dict, parte: str | None, tema: int | None) -> list[dict]:
    salida = []
    for item in manifiesto["temas"]:
        if item.get("clasificacion_v2") != CLASE_DOCTRINAL:
            continue
        if parte is not None and str(item.get("parte") or "").upper() != parte.upper():
            continue
        if tema is not None and int(item.get("numero_tema")) != int(tema):
            continue
        if necesita_propuesta_ia(item):
            salida.append(item)
    return salida


def _normalizar_busqueda(texto: str) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", x).strip()


def _fragmentar_literal(texto: str) -> list[str]:
    """Divide texto sin reescribirlo ni resumirlo."""
    parrafos = [p.strip() for p in re.split(r"\n\s*\n+", texto) if p.strip()]
    if len(parrafos) <= 1:
        parrafos = [p.strip() for p in re.split(r"(?<=[.;:!?])\s+", texto) if p.strip()]

    salida: list[str] = []
    for p in parrafos:
        if len(p) <= TAM_FRAGMENTO:
            salida.append(p)
            continue
        inicio = 0
        while inicio < len(p):
            fin = min(len(p), inicio + TAM_FRAGMENTO)
            if fin < len(p):
                corte = p.rfind(" ", inicio + TAM_FRAGMENTO // 2, fin)
                if corte > inicio:
                    fin = corte
            trozo = p[inicio:fin].strip()
            if trozo:
                salida.append(trozo)
            inicio = max(fin, inicio + 1)
    return salida


def _extraer_relevante(texto: str, terminos: list[str]) -> tuple[str, dict]:
    original = texto or ""
    if len(original) <= MAX_TEXTO_IA:
        return original, {
            "texto_articulo_original_caracteres": len(original),
            "texto_articulo_enviado_caracteres": len(original),
            "texto_articulo_es_extracto": False,
            "texto_articulo_estrategia": "COMPLETO",
        }

    fragmentos = _fragmentar_literal(original)
    terminos_norm = [
        _normalizar_busqueda(t)
        for t in terminos
        if _normalizar_busqueda(t)
    ]

    puntuados: list[tuple[int, int]] = []
    for i, frag in enumerate(fragmentos):
        nf = _normalizar_busqueda(frag)
        score = sum(1 for t in terminos_norm if t in nf)
        if score:
            puntuados.append((score, i))

    indices: list[int] = []
    vistos: set[int] = set()
    orden_base = [i for _, i in sorted(puntuados, key=lambda x: (-x[0], x[1]))]
    if not orden_base and fragmentos:
        orden_base = [0, len(fragmentos) - 1]

    for i in orden_base:
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(fragmentos) and j not in vistos:
                vistos.add(j)
                indices.append(j)

    seleccionados: list[tuple[int, str]] = []
    usados = 0
    for i in indices:
        frag = fragmentos[i]
        coste = len(frag) + (2 if seleccionados else 0)
        if seleccionados and usados + coste > MAX_TEXTO_IA:
            continue
        if not seleccionados and len(frag) > MAX_TEXTO_IA:
            frag = frag[:MAX_TEXTO_IA]
            coste = len(frag)
        seleccionados.append((i, frag))
        usados += coste
        if usados >= MAX_TEXTO_IA:
            break

    seleccionados.sort(key=lambda x: x[0])
    extracto = "\n\n".join(frag for _, frag in seleccionados)
    if len(extracto) > MAX_TEXTO_IA:
        extracto = extracto[:MAX_TEXTO_IA]

    return extracto, {
        "texto_articulo_original_caracteres": len(original),
        "texto_articulo_enviado_caracteres": len(extracto),
        "texto_articulo_es_extracto": True,
        "texto_articulo_estrategia": "FRAGMENTOS_TERMINOS_RANKING",
        "texto_articulo_fragmentos": [i for i, _ in seleccionados],
        "texto_articulo_limite_caracteres": MAX_TEXTO_IA,
    }


def limitar_entrada_ia(entrada: dict) -> dict:
    """Limita sólo la copia de texto que se enviará a IA."""
    recortados = 0
    max_original = 0
    max_enviado = 0

    for unidad in entrada.get("unidades") or []:
        for candidato in unidad.get("candidatos_locales") or []:
            original = str(candidato.get("texto_articulo") or "")
            extracto, meta = _extraer_relevante(
                original,
                list(candidato.get("terminos") or []),
            )
            candidato["texto_articulo"] = extracto
            candidato.update(meta)
            max_original = max(max_original, int(meta["texto_articulo_original_caracteres"]))
            max_enviado = max(max_enviado, int(meta["texto_articulo_enviado_caracteres"]))
            if meta["texto_articulo_es_extracto"]:
                recortados += 1

    if max_enviado > MAX_TEXTO_IA:
        raise RuntimeError(
            f"Control de recursos incumplido: max_enviado={max_enviado} > {MAX_TEXTO_IA}."
        )

    entrada["control_recursos"] = {
        "umbral_texto_completo_caracteres": UMBRAL_TEXTO_COMPLETO,
        "max_texto_ia_caracteres": MAX_TEXTO_IA,
        "candidatos_recortados": recortados,
        "max_original_caracteres": max_original,
        "max_enviado_caracteres": max_enviado,
        "modifica_texto_bd": False,
    }
    return entrada


def payload_v4(pdf: Path, db: Path, modelo: str, entrada: dict, propuesta: dict, evaluaciones: dict) -> dict:
    return {
        "version": 4,
        "naturaleza": "PROPUESTA_CONTENIDO_PSEUDONORMA_IA_V2",
        "modo": "PILOTO_SOLO_LECTURA",
        "pdf": str(pdf),
        "db": str(db),
        "modelo": modelo,
        "entrada": entrada,
        "evaluaciones_candidatos": evaluaciones,
        "propuesta_ia": propuesta,
        "reglas": {
            "consulta_temario_csv": False,
            "consulta_temario_referencias": False,
            "texto_literal_candidatos_locales": True,
            "extractos_largos_identificados_explicitamente": True,
            "max_texto_candidato_ia_caracteres": MAX_TEXTO_IA,
            "evaluacion_ia_candidato_individual": True,
            "sintesis_ia_por_unidad": True,
            "aceptacion_automatica": False,
            "urls_externas_generadas_por_ia": False,
            "requiere_verificacion_fuentes": True,
            "requiere_revision_humana_final": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_bancos": False,
        },
    }


def indice_dudosos(evaluaciones: dict) -> dict[str, list[dict]]:
    salida: dict[str, list[dict]] = {}
    for codigo, filas in evaluaciones.items():
        salida[codigo] = [
            {
                "articulo_fuente_id": int(e["articulo_fuente_id"]),
                "motivo": str(e["motivo"]),
                "estado": "DUDOSO",
            }
            for e in filas
            if e.get("decision") == "DUDOSO"
        ]
    return salida


def fusionar_tema(item: dict, data_v5: dict) -> None:
    propuesta = data_v5["propuesta_ia"]
    evaluaciones = data_v5["evaluaciones_candidatos"]
    dudosos_por_unidad = indice_dudosos(evaluaciones)

    pseudo = []
    dudosos_tema = []
    hay_fuentes_pendientes = False

    for unidad in propuesta["unidades"]:
        codigo = str(unidad["codigo"])
        dudosos = dudosos_por_unidad.get(codigo, [])
        externas = list(unidad.get("fuentes_externas_a_buscar") or [])
        prioritarios = list(unidad.get("candidatos_locales_prioritarios") or [])
        if externas or prioritarios:
            hay_fuentes_pendientes = True

        pseudo.append({
            "codigo": codigo,
            "texto_temario": unidad["texto_temario"],
            "tipo_contenido": unidad["tipo_contenido_propuesto"],
            "proposiciones": list(unidad.get("proposiciones") or []),
            "candidatos_locales_prioritarios": prioritarios,
            "fuentes_externas_a_buscar": externas,
            "dudosos": dudosos,
            "lagunas": list(unidad.get("lagunas") or []),
            "incidencias": list(unidad.get("incidencias") or []),
            "fuentes": [],
            "evidencia": [],
            "estado": "PENDIENTE_VERIFICACION_FUENTES",
        })
        for d in dudosos:
            dudosos_tema.append({"pseudoarticulo": codigo, **d})

    item["pseudoarticulos"] = pseudo
    item["dudosos"] = dudosos_tema
    item["requiere_revision_humana"] = False
    item["estado_v2"] = "PENDIENTE_FUENTE" if hay_fuentes_pendientes else "PENDIENTE_REVISION"
    item["accion_siguiente"] = (
        "VERIFICAR_RESPALDOS_PSEUDONORMA"
        if hay_fuentes_pendientes
        else "AUDITAR_COBERTURA_PSEUDONORMA"
    )
    item.setdefault("trazabilidad", []).append({
        "tipo": "PROPUESTA_PSEUDONORMATIVA_IA_V4_NORMALIZADA_V5",
        "aceptacion_automatica": False,
        "dudosos_promocionados": False,
        "totales_normalizacion": data_v5["normalizacion_local"]["totales"],
        "control_recursos": data_v5.get("entrada", {}).get("control_recursos", {}),
    })


def recalcular_resumen(manifiesto: dict) -> None:
    por_estado: dict[str, int] = {}
    pendientes_humanos = 0
    pseudo_total = 0
    dudosos_total = 0
    for item in manifiesto["temas"]:
        estado = str(item.get("estado_v2") or "SIN_ESTADO")
        por_estado[estado] = por_estado.get(estado, 0) + 1
        if item.get("requiere_revision_humana"):
            pendientes_humanos += 1
        pseudo_total += len(item.get("pseudoarticulos") or [])
        dudosos_total += len(item.get("dudosos") or [])

    resumen = manifiesto.setdefault("resumen", {})
    resumen["por_estado_v2"] = dict(sorted(por_estado.items()))
    resumen["requieren_revision_humana"] = pendientes_humanos
    resumen["pseudoarticulos"] = pseudo_total
    resumen["dudosos"] = dudosos_total

    if pendientes_humanos:
        manifiesto["estado"] = "PENDIENTE_REVISION_HUMANA"
    elif any(x.get("estado_v2") == "PENDIENTE_FUENTE" for x in manifiesto["temas"]):
        manifiesto["estado"] = "PENDIENTE_FUENTE"
    else:
        manifiesto["estado"] = "ANALISIS_COMPLETADO"


def preparar_salida(manifiesto: dict) -> None:
    manifiesto["version"] = max(int(manifiesto.get("version", 2)), 3)
    manifiesto["naturaleza"] = "MANIFIESTO_CONSTRUCTOR_TEMARIO_V2"
    manifiesto["modo"] = "SOLO_LECTURA"
    manifiesto.setdefault("reglas", {}).update({
        "aceptacion_juridica_automatica": False,
        "dudosos_promocion_automatica": False,
        "temario_referencias_consultado": False,
        "modifica_bd": False,
        "modifica_corpus": False,
        "modifica_temario_csv": False,
        "modifica_bancos": False,
    })
    recalcular_resumen(manifiesto)


def guardar_checkpoint(path: Path, manifiesto: dict) -> None:
    preparar_salida(manifiesto)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Completa temas doctrinales del manifiesto V2 con propuesta IA normalizada, sin persistencia."
    )
    ap.add_argument("--manifiesto", required=True)
    ap.add_argument("--salida-json", required=True)
    ap.add_argument("--parte")
    ap.add_argument("--tema", type=int)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--modelo", default="gpt-5.4-mini")
    args = ap.parse_args()

    base_ia.bloquear_sesion_mantenimiento()

    entrada_path = Path(args.manifiesto).expanduser().resolve()
    salida_path = Path(args.salida_json).expanduser().resolve()
    if not entrada_path.is_file():
        raise SystemExit(f"ERROR: no existe el manifiesto: {entrada_path}")
    if entrada_path == salida_path:
        raise SystemExit("ERROR: la salida debe ser distinta del manifiesto de entrada.")

    original = cargar_manifiesto(entrada_path)
    manifiesto = copy.deepcopy(original)
    pdf = Path(str(manifiesto["pdf"])).expanduser().resolve()
    db = Path(str(manifiesto["db"])).expanduser().resolve()
    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF del manifiesto: {pdf}")
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD del manifiesto: {db}")

    seleccionados = seleccionar_temas(manifiesto, args.parte, args.tema)
    if not seleccionados:
        guardar_checkpoint(salida_path, manifiesto)
        print("No hay temas doctrinales pendientes de propuesta IA.")
        print(f"Salida............................ {salida_path}")
        return 0

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - COMPLETAR MANIFIESTO PSEUDONORMATIVO")
    print("=" * 78)
    print(f"Manifiesto........................ {entrada_path}")
    print(f"Temas a procesar.................. {len(seleccionados)}")
    print("Modo.............................. SOLO ARTEFACTOS / SIN PERSISTENCIA")
    print(f"Máx. texto por candidato IA....... {MAX_TEXTO_IA} caracteres")
    print()

    guardar_checkpoint(salida_path, manifiesto)

    for indice, item in enumerate(seleccionados, 1):
        parte = str(item["parte"])
        numero = int(item["numero_tema"])
        print(f"[{indice}/{len(seleccionados)}] {parte} {numero}")

        entrada = base_ia.construir_entrada(pdf, db, parte, numero, max(1, args.top))
        limitar_entrada_ia(entrada)
        control = entrada["control_recursos"]
        print(
            "  recursos........................ "
            f"recortados={control['candidatos_recortados']} | "
            f"máx.original={control['max_original_caracteres']} | "
            f"máx.enviado={control['max_enviado_caracteres']}"
        )

        propuesta, evaluaciones = ia_v4.construir_propuesta(entrada, args.modelo)
        data_v5 = normalizador_v5.normalizar(
            payload_v4(pdf, db, args.modelo, entrada, propuesta, evaluaciones)
        )
        fusionar_tema(item, data_v5)
        guardar_checkpoint(salida_path, manifiesto)
        print(f"  checkpoint...................... {salida_path}")

    preparar_salida(manifiesto)
    print()
    print(f"Salida............................ {salida_path}")
    print(f"Pseudoartículos................... {manifiesto['resumen']['pseudoarticulos']}")
    print(f"Dudosos........................... {manifiesto['resumen']['dudosos']}")
    print("Ninguna relación ha sido aceptada automáticamente.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("Los bancos NO han sido modificados.")
    print("temario.csv NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
