"""
Constructor Temario V2 - preparación de incorporación de una fuente BOE nueva.

FASE DE PREPARACIÓN: NO MODIFICA LA BASE DE DATOS.

A partir de un plan generado por planificar_alta_fuente_boe_v2.py:
- exige una única norma canónica candidata cuando falta norma_fuentes;
- recupera desde BOE el texto vigente de cada artículo objetivo;
- usa la fecha_actualizacion del índice cuando existe;
- cuando el índice no aporta fecha_actualizacion, aplica un respaldo conservador:
  * una única versión del bloque -> se acepta esa versión;
  * varias versiones -> se exige una única fecha_publicacion máxima;
  * si persiste ambigüedad -> se bloquea;
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
import re
from pathlib import Path

import comun
import ampliar_corpus_chat as ampliar


def limpiar(valor) -> str:
    return " ".join(str(valor or "").split()).strip()


def fecha_version(version) -> str:
    return re.sub(
        r"\D",
        "",
        limpiar(version.attrib.get("fecha_publicacion", "")),
    )


def seleccionar_version_sin_fecha_indice(raiz_bloque):
    """
    Respaldo conservador para bloques cuyo índice BOE no informa
    fecha_actualizacion.

    Nunca elige arbitrariamente:
    - 1 versión: se usa;
    - varias: sólo se usa la de fecha_publicacion máxima si es única;
    - fechas ausentes o empate en la máxima: error controlado.
    """
    versiones = ampliar._versiones_bloque(raiz_bloque)
    if not versiones:
        raise ampliar.BOEError("El bloque BOE no contiene ninguna versión.")

    if len(versiones) == 1:
        return versiones[0], "UNICA_VERSION_SIN_FECHA_INDICE"

    con_fecha = [(fecha_version(v), v) for v in versiones]
    if any(not fecha for fecha, _ in con_fecha):
        raise ampliar.BOEError(
            "El índice no aporta fecha_actualizacion y alguna versión del bloque "
            "tampoco aporta fecha_publicacion; no se selecciona arbitrariamente."
        )

    fecha_maxima = max(fecha for fecha, _ in con_fecha)
    recientes = [v for fecha, v in con_fecha if fecha == fecha_maxima]
    if len(recientes) != 1:
        raise ampliar.BOEError(
            "El índice no aporta fecha_actualizacion y hay varias versiones "
            f"igualmente recientes ({fecha_maxima}); no se selecciona arbitrariamente."
        )

    return recientes[0], "VERSION_MAXIMA_UNICA_SIN_FECHA_INDICE"


def construir_articulo(
    id_boe: str,
    articulo: str,
    id_bloque: str,
    titulo: str,
    fecha_actualizacion: str,
) -> dict:
    raiz = ampliar.obtener_bloque_texto(id_boe, id_bloque)
    fecha_actualizacion = re.sub(r"\D", "", limpiar(fecha_actualizacion))

    if fecha_actualizacion:
        version = ampliar._seleccionar_version_actualizada(
            raiz,
            fecha_actualizacion,
        )
        criterio_version = "FECHA_ACTUALIZACION_INDICE"
    else:
        version, criterio_version = seleccionar_version_sin_fecha_indice(raiz)

    texto = limpiar(ampliar._texto_version(version))
    if not ampliar.texto_articulo_suficiente(texto, titulo):
        raise RuntimeError(f"{id_boe} art. {articulo}: texto insuficiente")

    numero_real = ampliar.extraer_numero_encabezado(texto)
    if (
        numero_real
        and ampliar.normalizar_articulo(numero_real)
        != ampliar.normalizar_articulo(articulo)
    ):
        raise RuntimeError(
            f"{id_boe}/{id_bloque}: encabezado {numero_real!r} "
            f"no corresponde al art. {articulo}"
        )

    hash_texto = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    return {
        "id_boe": id_boe,
        "id_bloque": id_bloque,
        "articulo_boe": articulo,
        "titulo_bloque": titulo,
        "departamento": "",
        "texto": texto,
        "hash_texto": hash_texto,
        "fecha_actualizacion_indice": fecha_actualizacion,
        "fecha_publicacion_version": fecha_version(version),
        "criterio_version": criterio_version,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Prepara, sin aplicar, el alta atómica de una fuente BOE nueva."
    )
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
                "Se requiere exactamente una norma canónica candidata; "
                f"encontradas: {len(candidatos)}"
            )
        norma_id = int(candidatos[0]["norma_id"])
        crear_enlace = True

    objetivos = plan.get("indice_boe", {}).get("articulos_objetivo") or []
    if not objetivos:
        raise SystemExit("El plan no contiene artículos objetivo.")

    no_localizados = [
        x.get("articulo")
        for x in objetivos
        if x.get("estado") != "LOCALIZADO_EN_INDICE"
    ]
    if no_localizados:
        raise SystemExit(f"Hay artículos no localizados en BOE: {no_localizados}")

    # Reconstruimos el índice actual para recuperar fecha_actualizacion por bloque.
    indice_fechas = {}
    raiz_indice = ampliar.obtener_indice_texto(id_boe)
    for elemento in raiz_indice.iter():
        datos = ampliar._datos_bloque_indice(elemento)
        if not datos:
            continue
        id_bloque, _, fecha_actualizacion = datos
        indice_fechas[id_bloque] = fecha_actualizacion

    print("=" * 78)
    print(
        "CONSTRUCTOR TEMARIO V2 - PREPARAR INCORPORACION FUENTE BOE - "
        "SIN ESCRITURAS BD"
    )
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
        id_bloque = limpiar(obj.get("id_bloque"))
        try:
            item = construir_articulo(
                id_boe,
                art,
                id_bloque,
                limpiar(obj.get("titulo")),
                indice_fechas.get(id_bloque, ""),
            )
            preparados.append(item)
            print(
                f"  OK    | art. {art} | bloque={item['id_bloque']} "
                f"| chars={len(item['texto'])} "
                f"| criterio={item['criterio_version']}"
            )
        except Exception as exc:
            errores.append(
                {"articulo": art, "error": f"{type(exc).__name__}: {exc}"}
            )
            print(f"  ERROR | art. {art} | {type(exc).__name__}: {exc}")

    estado = (
        "LISTO_PARA_REVISION"
        if len(preparados) == len(objetivos) and not errores
        else "BLOQUEADO"
    )

    payload = {
        "version": 2,
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
            "tablas": (
                ["norma_fuentes", "articulos_fuente"]
                if crear_enlace
                else ["articulos_fuente"]
            ),
            "aplicada": False,
        },
        "reglas": {
            "bd_modificada": False,
            "temarios_modificados": False,
            "bancos_modificados": False,
            "aceptacion_automatica": False,
            "seleccion_version_sin_fecha_indice_no_arbitraria": True,
        },
    }

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print(f"Estado............................ {estado}")
    print(f"Artículos preparados.............. {len(preparados)}")
    print(f"Errores........................... {len(errores)}")
    print(f"JSON.............................. {salida}")
    print("La base de datos NO ha sido modificada.")
    return 0 if estado == "LISTO_PARA_REVISION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
