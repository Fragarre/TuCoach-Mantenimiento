"""
Constructor Temario V2 - completar temas JURIDICO_DELIMITABLE.

SOLO LECTURA respecto de BD, corpus, bancos y temario.csv.

Este adaptador es deliberadamente aditivo: no modifica el flujo doctrinal ya
validado. Reutiliza su evaluación V4, normalizador V5 y control de recursos.

Para cada tema delimitable:
- conserva cualquier articulo_reales ya resuelto con seguridad por el manifiesto;
- usa los pseudoarticulos de trabajo segmentados desde el texto oficial;
- busca candidatos oficiales en el corpus local sin consultar temario_referencias;
- evalúa cada candidato con IA V4;
- normaliza DUDOSO de forma determinista;
- deja los candidatos plausibles como PROPUESTAS PENDIENTES DE VERIFICACION;
- mantiene pseudoarticulos para completar el contenido no cubierto.

Nunca convierte por sí mismo un candidato en artículo real asignado.
"""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import asdict
from pathlib import Path

import comun
import completar_manifiesto_pseudonormas_v2 as orq
import normalizar_propuesta_pseudonormas_ia_v4 as normalizador_v5
import proponer_contenido_pseudonormas_ia_v2 as base_ia
import proponer_contenido_pseudonormas_ia_v4 as ia_v4
import proponer_respaldos_pseudonormas_v2 as respaldos_mod


CLASE = "JURIDICO_DELIMITABLE"


def seleccionar_temas(manifiesto: dict, parte: str | None, tema: int | None) -> list[dict]:
    salida = []
    for item in manifiesto["temas"]:
        if item.get("clasificacion_v2") != CLASE:
            continue
        if parte is not None and str(item.get("parte") or "").upper() != parte.upper():
            continue
        if tema is not None and int(item.get("numero_tema")) != int(tema):
            continue
        pseudo = item.get("pseudoarticulos") or []
        if not pseudo or any(
            str(p.get("estado") or "") == "PENDIENTE_PROPUESTA_IA"
            for p in pseudo
        ):
            salida.append(item)
    return salida


def construir_entrada_desde_item(item: dict, db: Path, top: int) -> dict:
    pseudo = item.get("pseudoarticulos") or []
    if not pseudo:
        raise RuntimeError(
            f"{item.get('parte')} {item.get('numero_tema')}: no contiene pseudoarticulos de trabajo."
        )

    with comun.conectar_sqlite_solo_lectura(db) as con:
        articulos = respaldos_mod.cargar_articulos(con)
    idf = respaldos_mod.construir_idf(articulos)
    articulo_por_id = {a.articulo_fuente_id: a for a in articulos}

    unidades = []
    for unidad in pseudo:
        texto_unidad = str(unidad.get("texto_temario") or "").strip()
        codigo = str(unidad.get("codigo") or "").strip()
        if not codigo or not texto_unidad:
            raise RuntimeError("Pseudoarticulo de trabajo sin codigo o texto_temario.")

        candidatos = respaldos_mod.proponer(
            texto_unidad,
            articulos,
            idf,
            max(1, top),
        )
        enriquecidos = []
        for candidato in candidatos:
            datos = asdict(candidato)
            articulo = articulo_por_id.get(candidato.articulo_fuente_id)
            if articulo is None:
                raise RuntimeError(
                    f"No se localizo articulo_fuente_id={candidato.articulo_fuente_id}."
                )
            datos["texto_articulo"] = articulo.texto
            enriquecidos.append(datos)

        unidades.append({
            "codigo": codigo,
            "texto_temario": texto_unidad,
            "candidatos_locales": enriquecidos,
        })

    return {
        "identidad": f"{item.get('parte')}:{item.get('numero_tema')}:DELIMITABLE",
        "parte": item.get("parte"),
        "numero_tema": int(item.get("numero_tema")),
        "texto_tema_oficial": item.get("texto_oficial"),
        "clasificacion": CLASE,
        "evidencia_clasificacion": item.get("evidencia_alcance"),
        "articulos_reales_preexistentes": copy.deepcopy(item.get("articulos_reales") or []),
        "unidades": unidades,
    }


def fusionar_delimitable(item: dict, data_v5: dict) -> None:
    # Reutiliza la fusión común, que mantiene candidatos como propuestas y
    # DUDOSO separado, pero nunca escribe articulos_reales.
    articulos_reales_antes = copy.deepcopy(item.get("articulos_reales") or [])
    orq.fusionar_tema(item, data_v5)
    if item.get("articulos_reales") != articulos_reales_antes:
        raise RuntimeError("El flujo delimitable altero articulos_reales preexistentes.")

    item["cobertura_hibrida"] = {
        "articulos_reales_conservados": len(articulos_reales_antes),
        "candidatos_reales_pendientes_verificacion": sum(
            len(p.get("candidatos_locales_prioritarios") or [])
            for p in item.get("pseudoarticulos") or []
        ),
        "pseudoarticulos": len(item.get("pseudoarticulos") or []),
        "promocion_automatica_a_articulo_real": False,
    }
    item["accion_siguiente"] = "VERIFICAR_COBERTURA_HIBRIDA_DELIMITABLE"
    item.setdefault("trazabilidad", []).append({
        "tipo": "COBERTURA_HIBRIDA_DELIMITABLE_V2",
        "articulos_reales_preexistentes_conservados": True,
        "candidatos_no_promovidos_automaticamente": True,
        "temario_referencias_consultado": False,
    })


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Completa temas delimitables del manifiesto V2 sin persistencia."
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

    original = orq.cargar_manifiesto(entrada_path)
    manifiesto = copy.deepcopy(original)
    db = Path(str(manifiesto["db"])).expanduser().resolve()
    pdf = Path(str(manifiesto["pdf"])).expanduser().resolve()
    if not db.is_file():
        raise SystemExit(f"ERROR: no existe la BD del manifiesto: {db}")
    if not pdf.is_file():
        raise SystemExit(f"ERROR: no existe el PDF del manifiesto: {pdf}")

    seleccionados = seleccionar_temas(manifiesto, args.parte, args.tema)
    if not seleccionados:
        orq.guardar_checkpoint(salida_path, manifiesto)
        print("No hay temas delimitables pendientes de propuesta IA.")
        print(f"Salida............................ {salida_path}")
        return 0

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - COBERTURA HIBRIDA DELIMITABLE - SOLO LECTURA")
    print("=" * 78)
    print(f"Manifiesto........................ {entrada_path}")
    print(f"Temas a procesar.................. {len(seleccionados)}")
    print(f"Max. texto por candidato IA....... {orq.MAX_TEXTO_IA} caracteres")
    print("Promocion automatica articulo..... NO")
    print()

    orq.guardar_checkpoint(salida_path, manifiesto)

    for indice, item in enumerate(seleccionados, 1):
        print(f"[{indice}/{len(seleccionados)}] {item['parte']} {item['numero_tema']}")
        entrada = construir_entrada_desde_item(item, db, args.top)
        orq.limitar_entrada_ia(entrada)
        control = entrada["control_recursos"]
        print(
            "  recursos........................ "
            f"recortados={control['candidatos_recortados']} | "
            f"max.original={control['max_original_caracteres']} | "
            f"max.enviado={control['max_enviado_caracteres']}"
        )

        propuesta, evaluaciones = ia_v4.construir_propuesta(entrada, args.modelo)
        data_v5 = normalizador_v5.normalizar(
            orq.payload_v4(pdf, db, args.modelo, entrada, propuesta, evaluaciones)
        )
        fusionar_delimitable(item, data_v5)
        orq.guardar_checkpoint(salida_path, manifiesto)
        print(f"  checkpoint...................... {salida_path}")

    orq.preparar_salida(manifiesto)
    print()
    print(f"Salida............................ {salida_path}")
    print("Ningun candidato ha sido promovido automaticamente a articulo real.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("Los bancos NO han sido modificados.")
    print("temario.csv NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
