"""
Constructor Temario V2 - validación asistida de controles pseudonormativos.

SOLO LECTURA respecto de la base de datos.

Objetivo limitado:
- comprobar los pilotos conocidos P02 y P05 contra respaldos previamente
  verificados y documentados;
- ejecutar esos controles sólo sobre unidades explícitamente marcadas como
  control piloto en la ficha de revisión;
- buscar por identificador oficial + artículo en el corpus local, sin depender
  del ranking léxico;
- declarar explícitamente qué respaldos faltan del corpus local;
- distinguir fuentes normativas locales de fuentes externas/jurisprudenciales;
- producir un JSON de auditoría, nunca aceptar una pseudonorma.

Este script NO generaliza P02/P05 a otros pseudoartículos y NO modifica BD,
temario.csv, corpus ni bancos.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import comun
import proponer_respaldos_pseudonormas_v2 as respaldos_mod

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def normalizar(texto: str) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", x)


def articulo_equivalente(real: str, esperado: str) -> bool:
    return normalizar(real) == normalizar(esperado)


CONTROLES = {
    "P02": {
        "tipo": "MIXTO_NORMATIVO_DOCTRINAL",
        "locales_esperados": [
            {"fuente": "BOE-A-1889-4763", "articulo": a, "descripcion": f"Código Civil art. {a}"}
            for a in ("2", "3", "4", "5", "6", "7")
        ] + [
            {"fuente": "BOE-A-1978-31229", "articulo": "9", "descripcion": "Constitución Española art. 9"},
        ],
        "externos_esperados": [],
        "criterio": (
            "P02 debe apoyarse principalmente en artículos reales del Código Civil "
            "y en el art. 9 CE; la pseudonorma aporta estructura y delimitación."
        ),
    },
    "P05": {
        "tipo": "JURISPRUDENCIAL_DOCTRINAL_CON_RESPALDO_OFICIAL",
        "locales_esperados": [
            {"fuente": "BOE-A-1978-31229", "articulo": "93", "descripcion": "Constitución Española art. 93"},
        ],
        "externos_esperados": [
            {
                "tipo": "FUENTE_OFICIAL_UE",
                "identificador": "DECLARACION_17_PRIMACIA",
                "descripcion": "Declaración n.º 17 relativa a la primacía",
            },
            {
                "tipo": "JURISPRUDENCIA_TJUE",
                "identificador": "ECLI:EU:C:1964:66",
                "descripcion": "TJUE, Costa/ENEL, asunto 6/64",
            },
            {
                "tipo": "JURISPRUDENCIA_TJUE",
                "identificador": "ECLI:EU:C:1978:49",
                "descripcion": "TJUE, Simmenthal, asunto 106/77",
            },
            {
                "tipo": "JURISPRUDENCIA_CONSTITUCIONAL",
                "identificador": "BOE-T-2005-111",
                "descripcion": "Tribunal Constitucional, Declaración 1/2004",
            },
        ],
        "criterio": (
            "P05 no puede resolverse sólo con norma+artículo. Requiere art. 93 CE, "
            "fuente oficial UE y jurisprudencia TJUE/TC."
        ),
    },
}


def indexar_articulos(articulos):
    indice = {}
    for art in articulos:
        clave_fuente = str(art.id_fuente or "").strip()
        indice.setdefault(clave_fuente, []).append(art)
    return indice


def buscar_local(indice, esperado):
    fuente = esperado["fuente"]
    articulo = esperado["articulo"]
    candidatos = indice.get(fuente, [])
    matches = [a for a in candidatos if articulo_equivalente(a.articulo, articulo)]
    if not matches:
        return {
            **esperado,
            "estado": "FALTA_EN_CORPUS_LOCAL",
            "coincidencias": [],
        }
    return {
        **esperado,
        "estado": "LOCALIZADO_EN_CORPUS_LOCAL",
        "coincidencias": [
            {
                "norma_id": a.norma_id,
                "norma": a.norma,
                "id_fuente": a.id_fuente,
                "articulo_fuente_id": a.articulo_fuente_id,
                "articulo": a.articulo,
                "titulo_bloque": a.titulo_bloque,
            }
            for a in matches
        ],
    }


def cargar_unidades_control(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    controles = []
    for ficha in data.get("fichas", []):
        for unidad in ficha.get("unidades", []):
            control = unidad.get("control_piloto") or {}
            codigo = str(unidad.get("codigo") or "")
            if control.get("es_control") and codigo in CONTROLES:
                controles.append({
                    "identidad": ficha.get("identidad"),
                    "parte": ficha.get("parte"),
                    "numero_tema": ficha.get("numero_tema"),
                    "codigo": codigo,
                    "ambito_control": control.get("ambito_control"),
                })
    return data, controles


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Valida controles P02/P05 marcados en las fichas sin modificar datos."
    )
    ap.add_argument("--revision-json", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    revision = Path(args.revision_json).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()

    _, unidades_control = cargar_unidades_control(revision)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        articulos = respaldos_mod.cargar_articulos(con)
    indice = indexar_articulos(articulos)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - VALIDACION CONTROLES P02/P05 - SOLO LECTURA")
    print("=" * 78)
    print(f"Revisión.......................... {revision}")
    print(f"BD............................... {db}")
    print(f"Artículos oficiales indexados.... {len(articulos)}")
    print(f"Unidades control explícitas...... {len(unidades_control)}")
    print()

    resultados = []
    total_localizados = 0
    total_faltantes = 0
    total_externos = 0

    if not unidades_control:
        print("No hay unidades marcadas explícitamente como control piloto.")
        print()

    for unidad_control in unidades_control:
        codigo = unidad_control["codigo"]
        control = CONTROLES[codigo]
        locales = [buscar_local(indice, x) for x in control["locales_esperados"]]
        localizados = sum(x["estado"] == "LOCALIZADO_EN_CORPUS_LOCAL" for x in locales)
        faltantes = len(locales) - localizados
        externos = [
            {**x, "estado": "REQUIERE_FUENTE_EXTERNA_VERIFICADA"}
            for x in control["externos_esperados"]
        ]

        total_localizados += localizados
        total_faltantes += faltantes
        total_externos += len(externos)

        print(
            f"{codigo} | {control['tipo']} | "
            f"{unidad_control.get('parte')} {unidad_control.get('numero_tema')}"
        )
        print(f"  Respaldos normativos esperados.. {len(locales)}")
        print(f"  Localizados en corpus local..... {localizados}")
        print(f"  Faltan en corpus local.......... {faltantes}")
        print(f"  Fuentes externas requeridas..... {len(externos)}")
        for item in locales:
            marca = "OK" if item["estado"] == "LOCALIZADO_EN_CORPUS_LOCAL" else "FALTA"
            print(f"    {marca:<5} | {item['descripcion']} | {item['fuente']} art.{item['articulo']}")
        for item in externos:
            print(f"    EXT   | {item['descripcion']} | {item['identificador']}")
        print()

        resultados.append({
            **unidad_control,
            "tipo": control["tipo"],
            "criterio": control["criterio"],
            "respaldos_normativos": locales,
            "fuentes_externas": externos,
            "estado": "PILOTO_AUDITADO_NO_ACEPTADO",
        })

    payload = {
        "version": 2,
        "naturaleza": "AUDITORIA_CONTROLES_JURIDICOS_PSEUDONORMAS",
        "estado": "REVISION",
        "revision_origen": str(revision),
        "db": str(db),
        "resultados": resultados,
        "reglas": {
            "solo_controles_marcados_en_ficha": True,
            "aceptacion_automatica": False,
            "fuentes_externas_no_inventadas": True,
            "bd_modificada": False,
        },
    }

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Resumen:")
    print(f"  Respaldos locales localizados... {total_localizados}")
    print(f"  Respaldos locales faltantes..... {total_faltantes}")
    print(f"  Fuentes externas requeridas..... {total_externos}")
    print(f"  Fichero auditoría............... {salida}")
    print()
    print("No se ha aceptado ninguna pseudonorma.")
    print("No se han descargado ni inventado fuentes externas.")
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
