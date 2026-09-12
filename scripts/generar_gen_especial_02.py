"""
Version 2 - generación revisable del GEN para A1 / ESPECIAL 2.

FASE: GENERACIÓN FUERA DE BD.

Garantías:
- NO importa ni abre SQLite;
- NO modifica el CSV del temario;
- obtiene los artículos desde legislación consolidada oficial del BOE;
- inserta los textos normativos literalmente, fuera de la salida de IA;
- los artículos reales ya ocupados estructuralmente pueden alimentar el GEN;
- la identidad estructural posterior será GEN-norma + GEN-artículo;
- GPT-5.4-nano redacta solo explicación doctrinal desde un paquete controlado;
- valida que todos los textos normativos exigidos estén íntegros en el resultado;
- guarda JSON/Markdown únicamente en gen_revision;
- no sobreescribe una generación existente salvo --forzar.

Uso:
    python scripts/generar_gen_especial_02.py --solo-fuentes
    python scripts/generar_gen_especial_02.py --generar

No existe ninguna opción de escritura en BD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from boe_api import ArticuloBOE, obtener_articulo
from openai_api import seleccionar_fragmento_json


RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM"
SALIDA = CARPETA / "gen_revision"
ID_GEN = "GEN-A1-ESPECIAL-02"
NOMBRE_GEN = "GEN - Las fuentes del derecho administrativo (II)"
MODELO = "gpt-5.4-nano"


@dataclass(frozen=True)
class BloqueOficial:
    clave: str
    norma: str
    articulo: str
    titulo: str
    texto: str
    fuente_id: str
    fuente_url: str

    @property
    def hash_texto(self) -> str:
        return hashlib.sha256(self.texto.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EspecificacionArticulo:
    numero: int
    titulo: str
    claves_bloques: tuple[str, ...]
    objetivo: str


# Los bloques incluyen tanto referencias que podrán ser directas en el CSV como
# referencias reales ya ocupadas por temas anteriores. Estas últimas NO se
# reasignan: su texto se encapsula aquí bajo la identidad GEN + artículo GEN.
ESPECIFICACIONES = (
    EspecificacionArticulo(
        1,
        "El reglamento: concepto, naturaleza y clases",
        ("L39-128", "L50-24", "L5-32", "L5-33", "L5-34", "L5-36", "L5-37"),
        "Explicar el reglamento como norma jurídica administrativa subordinada a la ley, su naturaleza y sus principales clases, distinguiendo las formas reglamentarias estatales y valencianas sin confundir forma con rango legal.",
    ),
    EspecificacionArticulo(
        2,
        "Fundamento constitucional de la potestad reglamentaria",
        ("CE-9", "CE-97", "CE-106", "L39-128"),
        "Explicar el fundamento constitucional de la potestad reglamentaria, sus titulares y el sometimiento pleno de la Administración y de los reglamentos a la Constitución, la ley y el control judicial.",
    ),
    EspecificacionArticulo(
        3,
        "Potestad reglamentaria del Gobierno de la Nación: ejercicio, formas y jerarquía",
        ("L50-22", "L50-24", "L39-128", "L39-129"),
        "Explicar el ejercicio de la potestad reglamentaria estatal, las formas de las disposiciones del Gobierno y sus miembros, la jerarquía y los principios de buena regulación relevantes como límites del ejercicio reglamentario.",
    ),
    EspecificacionArticulo(
        4,
        "Potestad reglamentaria de la Generalitat Valenciana: titularidad, formas y jerarquía",
        ("L5-31", "L5-32", "L5-33", "L5-34", "L5-35", "L5-36", "L5-37"),
        "Explicar la potestad reglamentaria del Consell y las formas y jerarquía de sus disposiciones, utilizando íntegramente la regulación valenciana incorporada. Estas referencias reales pueden estar ocupadas estructuralmente por temas anteriores y aquí quedan vinculadas mediante el artículo GEN.",
    ),
    EspecificacionArticulo(
        5,
        "Límites de la potestad reglamentaria e inderogabilidad singular",
        ("CE-9", "L39-37", "L39-47", "L39-128", "L39-129"),
        "Explicar límites por legalidad, jerarquía, competencia y reserva de ley; nulidad de disposiciones contrarias al ordenamiento e inderogabilidad singular de las disposiciones generales.",
    ),
    EspecificacionArticulo(
        6,
        "Control de la potestad reglamentaria",
        ("CE-106", "L39-47", "LJCA-25", "LJCA-26", "LJCA-27"),
        "Explicar el control jurisdiccional de la potestad reglamentaria, la impugnación directa e indirecta de disposiciones generales, la cuestión de ilegalidad y los efectos pertinentes, sin inventar vías o efectos no contenidos en las fuentes.",
    ),
)

NORMAS_BOE = {
    "CE": "Constitución Española de 1978",
    "L39": "Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas",
    "L50": "Ley 50/1997, de 27 de noviembre, del Gobierno",
    "L5": "Ley 5/1983, de 30 de diciembre, de Gobierno Valenciano",
    "LJCA": "Ley 29/1998, de 13 de julio, reguladora de la Jurisdicción Contencioso-administrativa",
}

ARTICULOS_BOE = {
    "CE": (9, 97, 106),
    "L39": (37, 47, 128, 129),
    "L50": (22, 24),
    "L5": (31, 32, 33, 34, 35, 36, 37),
    "LJCA": (25, 26, 27),
}


def limpiar(v: object | None) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def normalizar_para_validar(texto: str) -> str:
    return limpiar(texto).casefold()


def bloque_boe(prefijo: str, articulo: ArticuloBOE) -> BloqueOficial:
    numero = articulo.articulo.split(".", 1)[0]
    texto = limpiar(articulo.texto)
    if not texto:
        raise RuntimeError(f"{prefijo}-{numero}: texto BOE vacío.")
    return BloqueOficial(
        clave=f"{prefijo}-{numero}",
        norma=articulo.nombre_norma,
        articulo=numero,
        titulo=articulo.titulo_bloque,
        texto=texto,
        fuente_id=articulo.id_boe,
        fuente_url=f"https://www.boe.es/buscar/act.php?id={articulo.id_boe}",
    )


def obtener_fuentes() -> dict[str, BloqueOficial]:
    bloques: dict[str, BloqueOficial] = {}
    for prefijo, numeros in ARTICULOS_BOE.items():
        norma = NORMAS_BOE[prefijo]
        for numero in numeros:
            bloque = bloque_boe(prefijo, obtener_articulo(norma, str(numero)))
            if bloque.clave in bloques:
                raise RuntimeError(f"Clave BOE duplicada: {bloque.clave}")
            bloques[bloque.clave] = bloque

    requeridas = {clave for e in ESPECIFICACIONES for clave in e.claves_bloques}
    faltan = sorted(requeridas - set(bloques))
    if faltan:
        raise RuntimeError("Faltan fuentes del GEN: " + ", ".join(faltan))
    return bloques


def fuentes_para_prompt(e: EspecificacionArticulo, bloques: dict[str, BloqueOficial]) -> list[dict]:
    return [
        {
            "clave": bloques[c].clave,
            "norma": bloques[c].norma,
            "articulo": bloques[c].articulo,
            "titulo": bloques[c].titulo,
            "texto_oficial": bloques[c].texto,
            "fuente_id": bloques[c].fuente_id,
            "url": bloques[c].fuente_url,
        }
        for c in e.claves_bloques
    ]


def construir_prompt(e: EspecificacionArticulo, bloques: dict[str, BloqueOficial]) -> str:
    paquete = {
        "articulo_gen": e.numero,
        "titulo": e.titulo,
        "objetivo": e.objetivo,
        "fuentes_normativas_oficiales": fuentes_para_prompt(e, bloques),
    }
    return (
        "Redacta EXCLUSIVAMENTE la explicación doctrinal de un artículo de un corpus para oposiciones jurídicas. "
        "No reproduzcas ni reescribas los textos normativos: el programa los añadirá literalmente después.\n\n"
        "REGLAS OBLIGATORIAS:\n"
        "1. Usa solo la información del paquete suministrado.\n"
        "2. No inventes normas, artículos, sentencias, fechas ni efectos jurídicos.\n"
        "3. No introduzcas citas normativas concretas que no aparezcan en el paquete.\n"
        "4. Explica con precisión suficiente para derivar preguntas de oposición.\n"
        "5. Distingue norma con rango de ley, reglamento, disposición general y acto administrativo cuando proceda.\n"
        "6. Distingue jerarquía, competencia, legalidad y reserva de ley cuando proceda.\n"
        "7. Devuelve JSON con exactamente dos claves: explicacion, puntos_clave.\n"
        "8. explicacion: entre 450 y 900 palabras.\n"
        "9. puntos_clave: lista de 6 a 12 frases breves.\n\n"
        "PAQUETE CONTROLADO:\n" + json.dumps(paquete, ensure_ascii=False, indent=2)
    )


def generar_explicacion(e: EspecificacionArticulo, bloques: dict[str, BloqueOficial]) -> dict:
    r = seleccionar_fragmento_json(
        prompt=construir_prompt(e, bloques),
        modelo=MODELO,
        operacion=f"gen-especial-02-art-{e.numero:02d}",
        max_output_tokens=5000,
    )
    if not isinstance(r, dict):
        raise RuntimeError(f"Artículo GEN {e.numero}: respuesta IA no es JSON objeto.")
    explicacion = limpiar(r.get("explicacion"))
    puntos = [limpiar(x) for x in (r.get("puntos_clave") or []) if limpiar(x)]
    palabras = len(explicacion.split())
    if palabras < 350 or palabras > 1200:
        raise RuntimeError(
            f"Artículo GEN {e.numero}: explicación fuera de rango de seguridad ({palabras} palabras)."
        )
    if not 6 <= len(puntos) <= 12:
        raise RuntimeError(f"Artículo GEN {e.numero}: puntos_clave inválidos ({len(puntos)}).")
    return {"explicacion": explicacion, "puntos_clave": puntos}


def renderizar_articulo(e: EspecificacionArticulo, bloques: dict[str, BloqueOficial], ia: dict) -> str:
    partes = [
        f"Artículo {e.numero}. {e.titulo}",
        "",
        ia["explicacion"],
        "",
        "Puntos clave:",
        *[f"- {p}" for p in ia["puntos_clave"]],
        "",
        "Textos oficiales incorporados íntegramente:",
        "",
    ]
    for clave in e.claves_bloques:
        b = bloques[clave]
        partes.extend(
            [
                f"[{b.norma} — {b.titulo}]",
                f"Fuente: {b.fuente_id} | {b.fuente_url}",
                b.texto,
                "",
            ]
        )
    return "\n".join(partes).strip() + "\n"


def validar_integridad(e: EspecificacionArticulo, bloques: dict[str, BloqueOficial], final: str) -> None:
    destino = normalizar_para_validar(final)
    for clave in e.claves_bloques:
        esperado = normalizar_para_validar(bloques[clave].texto)
        if not esperado or esperado not in destino:
            raise RuntimeError(
                f"Artículo GEN {e.numero}: falta o se alteró el texto oficial completo de {clave}."
            )


def guardar_json(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Genera GEN A1 ESPECIAL 2 fuera de la BD.")
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument(
        "--solo-fuentes", action="store_true", help="Obtiene y valida fuentes; 0 llamadas IA."
    )
    modo.add_argument(
        "--generar", action="store_true", help="Genera 6 artículos y guarda revisión."
    )
    p.add_argument(
        "--forzar", action="store_true", help="Permite reemplazar una revisión ya existente."
    )
    return p


def main() -> int:
    args = parser().parse_args()
    if not CARPETA.is_dir():
        raise FileNotFoundError(f"No existe la carpeta A1: {CARPETA}")

    print("=" * 78)
    print("GEN A1 / ESPECIAL 2 - PREPARACION DE FUENTES")
    print("=" * 78)
    bloques = obtener_fuentes()
    print(f"Bloques oficiales obtenidos: {len(bloques)}")

    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta_fuentes = SALIDA / f"{ID_GEN}_fuentes.json"
    guardar_json(
        ruta_fuentes,
        {
            "id_gen": ID_GEN,
            "nombre_gen": NOMBRE_GEN,
            "modelo_previsto": MODELO,
            "fuentes": {
                k: {**asdict(v), "hash_texto": v.hash_texto}
                for k, v in sorted(bloques.items())
            },
        },
    )
    print(f"Snapshot de fuentes: {ruta_fuentes}")

    if args.solo_fuentes:
        print("SOLO FUENTES: 0 llamadas a OpenAI; 0 escrituras en BD; 0 cambios CSV.")
        return 0

    ruta_json = SALIDA / f"{ID_GEN}.json"
    ruta_md = SALIDA / f"{ID_GEN}.md"
    if (ruta_json.exists() or ruta_md.exists()) and not args.forzar:
        raise RuntimeError(
            "Ya existe una generación de revisión. No se sobreescribe; usa --forzar solo si quieres regenerarla."
        )

    articulos = []
    print()
    print("GENERACION GPT-5.4-NANO")
    total = len(ESPECIFICACIONES)
    for e in ESPECIFICACIONES:
        print(f"Artículo {e.numero}/{total}: {e.titulo}")
        ia = generar_explicacion(e, bloques)
        final = renderizar_articulo(e, bloques, ia)
        validar_integridad(e, bloques, final)
        articulos.append(
            {
                "numero": e.numero,
                "titulo": e.titulo,
                "id_bloque": f"{ID_GEN}-ART-{e.numero:03d}",
                "claves_fuente": list(e.claves_bloques),
                "explicacion": ia["explicacion"],
                "puntos_clave": ia["puntos_clave"],
                "texto_final": final,
                "hash_texto_final": hashlib.sha256(final.encode("utf-8")).hexdigest(),
            }
        )

    guardar_json(
        ruta_json,
        {
            "id_gen": ID_GEN,
            "nombre_gen": NOMBRE_GEN,
            "modelo": MODELO,
            "estado": "REVISION_HUMANA_PENDIENTE",
            "escrituras_bd": 0,
            "articulos": articulos,
        },
    )
    ruta_md.write_text(
        "# " + NOMBRE_GEN + "\n\n" + "\n\n".join(a["texto_final"] for a in articulos),
        encoding="utf-8",
    )

    recargado = json.loads(ruta_json.read_text(encoding="utf-8"))
    if len(recargado.get("articulos") or []) != total:
        raise RuntimeError(f"La salida guardada no contiene exactamente {total} artículos GEN.")
    for e, guardado in zip(ESPECIFICACIONES, recargado["articulos"], strict=True):
        validar_integridad(e, bloques, str(guardado.get("texto_final") or ""))

    print()
    print("VALIDACION FINAL: OK")
    print(f"JSON revisión: {ruta_json}")
    print(f"Markdown:      {ruta_md}")
    print("Estado: REVISION_HUMANA_PENDIENTE")
    print("BD modificada: NO")
    print("CSV modificado: NO")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.", file=sys.stderr)
        raise SystemExit(130)
