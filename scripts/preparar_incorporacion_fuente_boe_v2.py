"""
Constructor Temario V2 - preparación de incorporación de una fuente BOE nueva.

FASE DE PREPARACIÓN: NO MODIFICA LA BASE DE DATOS.

A partir de un plan generado por planificar_alta_fuente_boe_v2.py:
- exige una única norma canónica candidata cuando falta norma_fuentes;
- recupera desde BOE el texto vigente de cada artículo objetivo;
- valida que el encabezado corresponde al artículo esperado;
- calcula el hash con la misma estructura usada por ampliar_corpus_chat.py;
- prepara una operación atómica futura: norma_fuentes + articulos_fuente;
- escribe únicamente un JSON de preparación.

No acepta pseudonormas, no modifica temarios, bancos ni C1/C2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import comun
import ampliar_corpus_chat as ampliar


def limpiar(valor) -> str:
    return " ".join(str(valor or "").split()).strip()


def construir_articulo(id_boe: str, articulo: str, id_bloque: str, titulo: str) -> dict:
    raiz = ampliar.obtener_bloque_texto(id_boe, id_bloque)
    # El índice ya ha seleccionado el bloque actual. La función consolidada
    # elige la versión vigente dentro de ese bloque.
    version = ampliar._seleccionar_version_actualizada(raiz, "")
    texto = ampliar._texto_version(version)
    texto = limpiar(texto)
    if not ampliar.texto_articulo_suficiente(texto):
        raise RuntimeError(f"{id_boe} art. {articulo}: texto insuficiente")

    numero_real = ampliar.extraer_numero_encabezado(texto)
    if numero_real and ampliar.normalizar_articulo(numero_real) != ampliar.normalizar_articulo(articulo):
        raise RuntimeError(
            f"{id_boe}/{id_bloque}: encabezado {numero_real!r} no corresponde al art. {articulo}"
        )

    departamento = ""
    hash_texto = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    return {
        "id_boe": id_boe,
        "id_bloque": id_bloque,
        "articulo_boe": articulo,
        "titulo_bloque": titulo,
        "departamento": departamento,
        "texto": texto,
        "hash_texto": hash_texto,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepara, sin aplicar, el alta atómica de una fuente BOE nueva.")
    ap.add_argument("--plan-json", required=True)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    plan_path = Path(args.plan_json).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if plan.get("naturaleza") != "PLAN_ALTA_NUEVA_FUENTE_BOE":
        raise SystemExit("El fichero no es un plan de alta de fuente BOE V2.")

    id_boe = limpiar(plan.get("id_boe")).upper()
    candidatos = plan.get("normas_canonicas_candidatas") or []
    enlaces = plan.get("enlaces_existentes") or []

    if enlaces:
        norma_ids = sorted({int(x["norma_id"]) for x in enlaces})
        if len(norma_ids) != 1:
            raise SystemExit(f"Fuente BOE enlazada a varias normas: {norma_ids}")
        norma_id = norma_ids[0]
        crear_enlace = False
    else:
        if len(candidatos) != 1:
            raise SystemExit(
                f"Se requiere exactamente una norma canónica candidata; encontradas: {len(candidatos)}"
            )
        norma_id = int(candidatos[0]["norma_id"])
        crear_enlace = True

    objetivos = plan.get("indice_boe", {}).get("articulos_objetivo") or []
    if not objetivos:
        raise SystemExit("El plan no contiene artículos objetivo.")
    no_localizados = [x.get("articulo") for x in objetivos if x.get("estado") != "LOCALIZADO_EN_INDICE"]
    if no_localizados:
        raise SystemExit(f"Hay artículos no localizados en BOE: {no_localizados}")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PREPARAR INCORPORACION FUENTE BOE - SIN ESCRITURAS BD")
    print("=" * 78)
    print(f"Plan.............................. {plan_path}")
    print(f"Fuente BOE....................... {id_boe}")
    print(f"Norma canónica................... {norma_id}")
    print(f"Crear enlace norma_fuentes....... {'SI' if crear_enlace else 'NO'}")
    print(f"Artículos objetivo............... {len(objetivos)}")
    print()

    preparados = []
    errores = []
    for obj in objetivos:
        art = limpiar(obj.get("articulo"))
        try:
            item = construir_articulo(id_boe, art, limpiar(obj.get("id_bloque")), limpiar(obj.get("titulo")))
            preparados.append(item)
            print(f"  OK    | art. {art} | bloque={item['id_bloque']} | chars={len(item['texto'])}")
        except Exception as exc:
            errores.append({"articulo": art, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  ERROR | art. {art} | {type(exc).__name__}: {exc}")

    estado = "LISTO_PARA_REVISION" if len(preparados) == len(objetivos) and not errores else "BLOQUEADO"
    payload = {
        "version": 1,
        "naturaleza": "PREPARACION_INCORPORACION_FUENTE_BOE",
        "estado": estado,
        "plan_origen": str(plan_path),
        "db": plan.get("db"),
        "id_boe": id_boe,
        "norma_id": norma_id,
        "crear_enlace_norma_fuentes": crear_enlace,
        "nombre_esperado": plan.get("nombre_esperado"),
        "articulos_preparados": preparados,
        "errores": errores,
        "operacion_futura": {
            "atomica": True,
            "tablas": ["norma_fuentes", "articulos_fuente"] if crear_enlace else ["articulos_fuente"],
            "aplicada": False,
        },
        "reglas": {
            "bd_modificada": False,
            "temarios_modificados": False,
            "bancos_modificados": False,
            "aceptacion_automatica": False,
        },
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"Estado............................ {estado}")
    print(f"Artículos preparados.............. {len(preparados)}")
    print(f"Errores........................... {len(errores)}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.")
    return 0 if estado == "LISTO_PARA_REVISION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
