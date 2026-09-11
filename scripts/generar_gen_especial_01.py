"""
Piloto Version 2 - generación revisable del GEN para A1 / ESPECIAL 1.

FASE ACTUAL: GENERACION FUERA DE BD.

Garantías:
- NO modifica SQLite;
- NO modifica el CSV del temario;
- obtiene los artículos estatales desde la legislación consolidada oficial del BOE;
- obtiene TFUE 288 desde el PDF oficial DOUE ya validado por el proyecto;
- inserta los textos normativos literalmente, fuera de la salida de IA;
- GPT-5.4-nano solo redacta explicación doctrinal a partir de un paquete controlado;
- valida que todos los textos normativos exigidos estén íntegros en el resultado;
- guarda JSON/Markdown únicamente en la carpeta gen_revision de la convocatoria;
- no sobreescribe una generación ya existente salvo --forzar.

Uso recomendado:
    python scripts/generar_gen_especial_01.py --solo-fuentes
    python scripts/generar_gen_especial_01.py --generar

No hay ninguna opción de escritura en BD en este script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import auditar_ampliacion_corpus_doue as auditor_doue
from boe_api import ArticuloBOE, obtener_articulo
from openai_api import seleccionar_fragmento_json


RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM"
SALIDA = CARPETA / "gen_revision"
ID_GEN = "GEN-A1-ESPECIAL-01"
NOMBRE_GEN = "GEN - Las fuentes del derecho administrativo (I)"
MODELO = "gpt-5.4-nano"

# Fuente oficial EUR-Lex. La declaración es breve, estable y se conserva aquí
# literalmente para que la IA nunca tenga que reconstruirla.
URL_DECLARACION_17 = (
    "https://eur-lex.europa.eu/legal-content/ES/TXT/"
    "?uri=CELEX:12007L/AFI/DCL/17"
)
DECLARACION_17 = (
    "La Conferencia recuerda que, con arreglo a una jurisprudencia reiterada "
    "del Tribunal de Justicia de la Unión Europea, los Tratados y el Derecho "
    "adoptado por la Unión sobre la base de los mismos priman sobre el Derecho "
    "de los Estados miembros, en las condiciones establecidas por la citada "
    "jurisprudencia."
)

# Doctrina oficial resumida y previamente contrastada. No se presenta como
# cita literal. Se usa solo como materia doctrinal controlada del artículo 4.
DOCTRINA_PRIMACIA = (
    {
        "fuente": "Tribunal Constitucional - Declaración 1/2004, FJ 4",
        "url": "https://hj.tribunalconstitucional.es/es/Resolucion/Show/6945",
        "contenido": (
            "La primacía no equivale a jerarquía. Opera como preferencia "
            "aplicativa en el ámbito propio del Derecho de la Unión. La "
            "supremacía de la Constitución es compatible con esa primacía, "
            "que la propia Constitución admite mediante su artículo 93."
        ),
    },
    {
        "fuente": "TJUE - Costa/ENEL, asunto 6/64",
        "url": "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:61964CJ0006",
        "contenido": (
            "La jurisprudencia del Tribunal de Justicia afirma la primacía "
            "del Derecho de la Unión frente al Derecho interno de los Estados "
            "miembros dentro del ámbito de aplicación del Derecho de la Unión."
        ),
    },
    {
        "fuente": "TJUE - Simmenthal, asunto 106/77",
        "url": "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:61977CJ0106",
        "contenido": (
            "El órgano jurisdiccional nacional debe garantizar la plena "
            "eficacia del Derecho de la Unión y dejar inaplicada, cuando sea "
            "necesario, la norma interna incompatible, sin esperar a su "
            "eliminación legislativa o constitucional."
        ),
    },
    {
        "fuente": "TJUE - Internationale Handelsgesellschaft, asunto 11/70",
        "url": "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:61970CJ0011",
        "contenido": (
            "La validez y eficacia del Derecho de la Unión se determinan "
            "conforme al propio ordenamiento de la Unión, sin quedar "
            "desplazadas por normas internas, incluso de rango constitucional."
        ),
    },
)


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
    doctrina_extra: tuple[dict, ...] = ()


ESPECIFICACIONES = (
    EspecificacionArticulo(
        1,
        "Concepto y sistema de fuentes del Derecho administrativo",
        ("CC-1", "CC-2"),
        (
            "Explicar el concepto de fuentes del Derecho administrativo y el "
            "sistema de fuentes, diferenciando producción normativa, rango, "
            "aplicación y relación entre fuentes."
        ),
    ),
    EspecificacionArticulo(
        2,
        "Aplicación, interpretación y eficacia de las normas",
        ("CC-3", "CC-4", "CC-5", "CC-6", "CC-7", "CE-9"),
        (
            "Explicar criterios de interpretación, equidad, analogía, cómputo "
            "de plazos, buena fe, fraude de ley, eficacia y sometimiento de "
            "ciudadanos y poderes públicos al ordenamiento."
        ),
    ),
    EspecificacionArticulo(
        3,
        "Derecho de la Unión Europea: sistema de fuentes y tipos de actos",
        ("TFUE-288",),
        (
            "Explicar Derecho originario y derivado y, con especial precisión, "
            "reglamentos, directivas, decisiones, recomendaciones y dictámenes."
        ),
    ),
    EspecificacionArticulo(
        4,
        "Primacía del Derecho de la Unión Europea y relación con la Constitución",
        ("DECL-17",),
        (
            "Explicar la primacía sin identificarla con supremacía o jerarquía; "
            "exponer sus consecuencias aplicativas en España y la función del "
            "artículo 93 CE, siguiendo la doctrina oficial facilitada."
        ),
        DOCTRINA_PRIMACIA,
    ),
    EspecificacionArticulo(
        5,
        "Tratados internacionales: incorporación, eficacia y aplicación",
        (
            "CE-93", "CE-94", "CE-95", "CE-96",
            "LEY25-23", "LEY25-28", "LEY25-29", "LEY25-30",
        ),
        (
            "Explicar celebración, autorización, incorporación al ordenamiento, "
            "publicación, eficacia, observancia y ejecución de los tratados, "
            "sin confundir su régimen con la primacía del Derecho de la Unión."
        ),
    ),
    EspecificacionArticulo(
        6,
        "La Constitución como fuente. La ley y sus clases",
        ("CE-81", "CE-82", "CE-83", "CE-84", "CE-85", "CE-86"),
        (
            "Explicar la Constitución como norma suprema y las principales "
            "clases de normas con rango de ley: ley orgánica, ley ordinaria, "
            "decretos legislativos y decretos-leyes."
        ),
    ),
    EspecificacionArticulo(
        7,
        "Principios de legalidad, reserva de ley, jerarquía normativa y competencia",
        ("CE-9", "CE-53", "CE-81", "CE-86"),
        (
            "Explicar legalidad, reserva de ley, jerarquía normativa y principio "
            "de competencia. No inventar una jerarquía donde la relación se "
            "resuelve por distribución constitucional de competencias."
        ),
    ),
    EspecificacionArticulo(
        8,
        "Estatutos de autonomía y leyes de las comunidades autónomas",
        ("CE-147", "CE-149", "CE-150", "EACV-44", "EACV-45"),
        (
            "Explicar posición de los estatutos de autonomía, potestad legislativa "
            "autonómica y articulación de competencias entre Estado y comunidades "
            "autónomas, con referencia específica a la Comunitat Valenciana."
        ),
    ),
    EspecificacionArticulo(
        9,
        "Costumbre y principios generales del Derecho",
        ("CC-1",),
        (
            "Explicar el carácter subsidiario de la costumbre y la función de "
            "los principios generales del Derecho, incluida su función informadora."
        ),
    ),
)


NORMAS_BOE = {
    "CC": "Real Decreto de 24 de julio de 1889 por el que se publica el Código Civil",
    "CE": "Constitución Española de 1978",
    "LEY25": "Ley 25/2014, de 27 de noviembre, de Tratados y otros Acuerdos Internacionales",
    "EACV": "Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía de la Comunitat Valenciana",
}

ARTICULOS_BOE = {
    "CC": (1, 2, 3, 4, 5, 6, 7),
    "CE": (9, 53, 81, 82, 83, 84, 85, 86, 93, 94, 95, 96, 147, 149, 150),
    "LEY25": (23, 28, 29, 30),
    "EACV": (44, 45),
}


def limpiar(v: object | None) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def normalizar_para_validar(texto: str) -> str:
    return limpiar(texto).casefold()


def bloque_boe(prefijo: str, articulo: ArticuloBOE) -> BloqueOficial:
    return BloqueOficial(
        clave=f"{prefijo}-{articulo.articulo.split('.', 1)[0]}",
        norma=articulo.nombre_norma,
        articulo=articulo.articulo.split(".", 1)[0],
        titulo=articulo.titulo_bloque,
        texto=limpiar(articulo.texto),
        fuente_id=articulo.id_boe,
        fuente_url=f"https://www.boe.es/buscar/act.php?id={articulo.id_boe}",
    )


def obtener_bloques_boe() -> dict[str, BloqueOficial]:
    bloques: dict[str, BloqueOficial] = {}
    for prefijo, numeros in ARTICULOS_BOE.items():
        norma = NORMAS_BOE[prefijo]
        for numero in numeros:
            art = obtener_articulo(norma, str(numero))
            bloque = bloque_boe(prefijo, art)
            if bloque.clave in bloques:
                raise RuntimeError(f"Clave BOE duplicada: {bloque.clave}")
            bloques[bloque.clave] = bloque
    return bloques


def obtener_tfue_288() -> BloqueOficial:
    fuente = next(
        f for f in auditor_doue.FUENTES_DOUE
        if f.id_fuente == "DOUE-C-2010-083-TFUE"
    )
    ruta = auditor_doue.FUENTES / fuente.archivo
    texto = auditor_doue.leer_pdf(ruta)
    aps = auditor_doue.apariciones(texto)
    nums = auditor_doue.inventario(aps)
    grupos = auditor_doue.agrupar(aps, nums)
    indice, _, _ = auditor_doue.modo_indice(grupos, nums)
    seleccion, errores1 = auditor_doue.seleccionar(grupos, nums, indice)
    bloques, errores2 = auditor_doue.segmentar(texto, seleccion, nums)
    errores = errores1 + errores2
    if errores:
        raise RuntimeError("TFUE: extracción no válida: " + "; ".join(errores))
    dato = bloques.get(288)
    if dato is None:
        raise RuntimeError("TFUE: no se pudo extraer el artículo 288.")
    cuerpo = limpiar(dato["texto"])
    if not cuerpo:
        raise RuntimeError("TFUE 288: texto vacío.")
    return BloqueOficial(
        clave="TFUE-288",
        norma="Tratado de Funcionamiento de la Unión Europea",
        articulo="288",
        titulo="Artículo 288",
        texto=cuerpo,
        fuente_id=fuente.id_fuente,
        fuente_url="https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:12012E288",
    )


def bloque_declaracion_17() -> BloqueOficial:
    return BloqueOficial(
        clave="DECL-17",
        norma="Declaración relativa a la primacía",
        articulo="17",
        titulo="17. Declaración relativa a la primacía",
        texto=DECLARACION_17,
        fuente_id="EURLEX-DECLARACION-17-PRIMACIA",
        fuente_url=URL_DECLARACION_17,
    )


def obtener_fuentes() -> dict[str, BloqueOficial]:
    bloques = obtener_bloques_boe()
    tfue = obtener_tfue_288()
    bloques[tfue.clave] = tfue
    decl = bloque_declaracion_17()
    bloques[decl.clave] = decl

    requeridas = {
        clave
        for especificacion in ESPECIFICACIONES
        for clave in especificacion.claves_bloques
    }
    faltan = sorted(requeridas - set(bloques))
    if faltan:
        raise RuntimeError("Faltan fuentes del GEN: " + ", ".join(faltan))
    return bloques


def fuentes_para_prompt(
    especificacion: EspecificacionArticulo,
    bloques: dict[str, BloqueOficial],
) -> list[dict]:
    fuentes = []
    for clave in especificacion.claves_bloques:
        b = bloques[clave]
        fuentes.append(
            {
                "clave": b.clave,
                "norma": b.norma,
                "articulo": b.articulo,
                "titulo": b.titulo,
                "texto_oficial": b.texto,
                "fuente_id": b.fuente_id,
                "url": b.fuente_url,
            }
        )
    return fuentes


def construir_prompt(
    especificacion: EspecificacionArticulo,
    bloques: dict[str, BloqueOficial],
) -> str:
    paquete = {
        "articulo_gen": especificacion.numero,
        "titulo": especificacion.titulo,
        "objetivo": especificacion.objetivo,
        "fuentes_normativas_oficiales": fuentes_para_prompt(especificacion, bloques),
        "doctrina_oficial_controlada": list(especificacion.doctrina_extra),
    }
    return (
        "Redacta EXCLUSIVAMENTE la explicación doctrinal de un artículo de un "
        "corpus para oposiciones jurídicas. No reproduzcas ni reescribas los "
        "textos normativos: el programa los añadirá literalmente después.\n\n"
        "Reglas obligatorias:\n"
        "1. Usa solo la información del paquete suministrado.\n"
        "2. No inventes normas, artículos, sentencias, fechas ni efectos jurídicos.\n"
        "3. No introduzcas citas normativas concretas que no aparezcan en el paquete.\n"
        "4. Explica con precisión suficiente para derivar preguntas de oposición.\n"
        "5. Distingue primacía, supremacía, jerarquía y competencia cuando proceda.\n"
        "6. En Derecho de la Unión, no afirmes sin matiz que la UE está 'por encima "
        "de la Constitución'; sigue la doctrina oficial suministrada.\n"
        "7. Devuelve JSON con exactamente estas claves: explicacion, puntos_clave.\n"
        "   explicacion: texto continuo de 700 a 1400 palabras.\n"
        "   puntos_clave: lista de 6 a 12 frases breves.\n\n"
        "PAQUETE CONTROLADO:\n"
        + json.dumps(paquete, ensure_ascii=False, indent=2)
    )


def generar_explicacion(
    especificacion: EspecificacionArticulo,
    bloques: dict[str, BloqueOficial],
) -> dict:
    resultado = seleccionar_fragmento_json(
        prompt=construir_prompt(especificacion, bloques),
        modelo=MODELO,
        operacion=f"gen-especial-01-art-{especificacion.numero:02d}",
        max_output_tokens=5000,
    )
    if not isinstance(resultado, dict):
        raise RuntimeError(
            f"Artículo GEN {especificacion.numero}: la IA no devolvió un objeto JSON."
        )
    explicacion = limpiar(resultado.get("explicacion"))
    puntos = resultado.get("puntos_clave")
    if len(explicacion) < 1200:
        raise RuntimeError(
            f"Artículo GEN {especificacion.numero}: explicación demasiado breve "
            f"({len(explicacion)} caracteres)."
        )
    if not isinstance(puntos, list) or not (6 <= len(puntos) <= 12):
        raise RuntimeError(
            f"Artículo GEN {especificacion.numero}: puntos_clave inválidos."
        )
    return {
        "explicacion": explicacion,
        "puntos_clave": [limpiar(x) for x in puntos if limpiar(x)],
    }


def renderizar_articulo(
    especificacion: EspecificacionArticulo,
    bloques: dict[str, BloqueOficial],
    ia: dict,
) -> str:
    partes = [
        f"Artículo {especificacion.numero}. {especificacion.titulo}",
        "",
        ia["explicacion"],
        "",
        "Puntos clave:",
    ]
    partes.extend(f"- {p}" for p in ia["puntos_clave"])
    partes.extend(["", "Textos oficiales incorporados íntegramente:", ""])

    for clave in especificacion.claves_bloques:
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


def validar_integridad_textos(
    especificacion: EspecificacionArticulo,
    bloques: dict[str, BloqueOficial],
    texto_final: str,
) -> None:
    destino = normalizar_para_validar(texto_final)
    for clave in especificacion.claves_bloques:
        esperado = normalizar_para_validar(bloques[clave].texto)
        if not esperado or esperado not in destino:
            raise RuntimeError(
                f"Artículo GEN {especificacion.numero}: falta o se alteró el "
                f"texto oficial completo de {clave}."
            )


def snapshot_fuentes(bloques: dict[str, BloqueOficial]) -> dict:
    return {
        clave: {
            **asdict(b),
            "hash_texto": b.hash_texto,
        }
        for clave, b in sorted(bloques.items())
    }


def guardar_json(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def guardar_markdown(ruta: Path, articulos: Iterable[dict]) -> None:
    partes = [f"# {NOMBRE_GEN}", ""]
    for art in articulos:
        partes.append(art["texto_final"])
        partes.append("")
    ruta.write_text("\n".join(partes).strip() + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Genera el piloto GEN A1 ESPECIAL 1 fuera de la BD."
    )
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument(
        "--solo-fuentes",
        action="store_true",
        help="Obtiene y valida las fuentes; no llama a OpenAI.",
    )
    modo.add_argument(
        "--generar",
        action="store_true",
        help="Genera los 9 artículos con GPT-5.4-nano y guarda revisión.",
    )
    p.add_argument(
        "--forzar",
        action="store_true",
        help="Permite reemplazar una generación de revisión ya existente.",
    )
    return p


def main() -> int:
    args = parser().parse_args()
    if not CARPETA.is_dir():
        raise FileNotFoundError(f"No existe la carpeta A1: {CARPETA}")

    print("=" * 78)
    print("GEN A1 / ESPECIAL 1 - PREPARACION DE FUENTES")
    print("=" * 78)
    bloques = obtener_fuentes()
    print(f"Fuentes/bloques oficiales obtenidos: {len(bloques)}")

    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta_fuentes = SALIDA / f"{ID_GEN}_fuentes.json"
    guardar_json(
        ruta_fuentes,
        {
            "id_gen": ID_GEN,
            "nombre_gen": NOMBRE_GEN,
            "modelo_previsto": MODELO,
            "fuentes": snapshot_fuentes(bloques),
        },
    )
    print(f"Snapshot de fuentes: {ruta_fuentes}")

    if args.solo_fuentes:
        print("SOLO FUENTES: 0 llamadas a OpenAI; 0 escrituras en BD.")
        return 0

    ruta_json = SALIDA / f"{ID_GEN}.json"
    ruta_md = SALIDA / f"{ID_GEN}.md"
    if (ruta_json.exists() or ruta_md.exists()) and not args.forzar:
        raise RuntimeError(
            "Ya existe una generación de revisión. No se sobreescribe. "
            "Revísala o usa --forzar conscientemente."
        )

    print()
    print("GENERACION GPT-5.4-NANO")
    articulos_salida = []
    for especificacion in ESPECIFICACIONES:
        print(f"Artículo {especificacion.numero}/9: {especificacion.titulo}")
        ia = generar_explicacion(especificacion, bloques)
        texto_final = renderizar_articulo(especificacion, bloques, ia)
        validar_integridad_textos(especificacion, bloques, texto_final)
        articulos_salida.append(
            {
                "numero": especificacion.numero,
                "titulo": especificacion.titulo,
                "id_bloque": f"{ID_GEN}-ART-{especificacion.numero:03d}",
                "claves_fuente": list(especificacion.claves_bloques),
                "explicacion": ia["explicacion"],
                "puntos_clave": ia["puntos_clave"],
                "texto_final": texto_final,
                "hash_texto_final": hashlib.sha256(
                    texto_final.encode("utf-8")
                ).hexdigest(),
            }
        )

    salida_json = {
        "id_gen": ID_GEN,
        "nombre_gen": NOMBRE_GEN,
        "modelo": MODELO,
        "estado": "REVISION_HUMANA_PENDIENTE",
        "escrituras_bd": 0,
        "articulos": articulos_salida,
    }
    guardar_json(ruta_json, salida_json)
    guardar_markdown(ruta_md, articulos_salida)

    # Validación final independiente sobre lo que realmente se guardó.
    recargado = json.loads(ruta_json.read_text(encoding="utf-8"))
    if len(recargado.get("articulos") or []) != len(ESPECIFICACIONES):
        raise RuntimeError("La salida guardada no contiene exactamente 9 artículos GEN.")
    for especificacion, guardado in zip(
        ESPECIFICACIONES,
        recargado["articulos"],
        strict=True,
    ):
        validar_integridad_textos(
            especificacion,
            bloques,
            str(guardado.get("texto_final") or ""),
        )

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
