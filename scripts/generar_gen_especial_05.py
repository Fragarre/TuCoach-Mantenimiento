"""
Version 2 - generación revisable del GEN para A1 / ESPECIAL 5.

FASE: GENERACIÓN FUERA DE BD.

Garantías:
- NO importa ni abre SQLite;
- NO modifica el CSV del temario;
- obtiene únicamente artículos numerados desde legislación consolidada oficial del BOE;
- NO usa preámbulos ni disposiciones adicionales, transitorias, derogatorias o finales;
- inserta los textos normativos literalmente, fuera de la salida de IA;
- los artículos reales ya ocupados estructuralmente pueden alimentar el GEN;
- la identidad estructural posterior será GEN-norma + GEN-artículo;
- GPT-5.4-nano redacta solo explicación doctrinal desde un paquete controlado;
- valida que todos los textos normativos exigidos estén íntegros en el resultado;
- guarda JSON/Markdown únicamente en gen_revision;
- no sobreescribe una generación existente salvo --forzar.

Uso:
    python scripts/generar_gen_especial_05.py --solo-fuentes
    python scripts/generar_gen_especial_05.py --generar

No existe ninguna opción de escritura en BD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import boe_api
from boe_api import ArticuloBOE, obtener_articulo
from openai_api import seleccionar_fragmento_json

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM"
SALIDA = CARPETA / "gen_revision"
ID_GEN = "GEN-A1-ESPECIAL-05"
NOMBRE_GEN = "GEN - La eficacia temporal de las normas"
MODELO = "gpt-5.4-nano"

CODIGO_CIVIL_NOMBRE = "Real Decreto de 24 de julio de 1889 por el que se publica el Código Civil"
CODIGO_CIVIL_ID = "BOE-A-1889-4763"


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


ESPECIFICACIONES = (
    EspecificacionArticulo(
        1,
        "Eficacia temporal de las normas y sucesión normativa",
        ("CC-2", "CE-9"),
        "Explicar qué significa la eficacia temporal de las normas y cómo se ordena la sucesión de normas en el tiempo, distinguiendo vigencia, derogación y retroactividad sin introducir fuentes ajenas al paquete.",
    ),
    EspecificacionArticulo(
        2,
        "Entrada en vigor y fin de la vigencia",
        ("CC-2",),
        "Explicar la regla de entrada en vigor y, respecto del fin de vigencia, limitarse a lo que afirma el artículo 2 del Código Civil: las leyes solo se derogan por otras posteriores. No convertir esa frase en una teoría general y exhaustiva sobre todas las posibles causas de pérdida de vigencia de cualquier norma.",
    ),
    EspecificacionArticulo(
        3,
        "Derogación expresa y derogación tácita",
        ("CC-2",),
        "Explicar la derogación expresa y la derogación tácita por incompatibilidad entre la norma nueva y la anterior sobre la misma materia, así como la regla de no reviviscencia contenida en el artículo 2 del Código Civil. Si se menciona el artículo 2.3, identificarlo únicamente como regla general legal del Código Civil sobre retroactividad, nunca como garantía constitucional.",
    ),
    EspecificacionArticulo(
        4,
        "Derecho transitorio",
        ("CC-2", "CE-9"),
        "Explicar doctrinalmente la función del Derecho transitorio ante una sucesión normativa usando únicamente los principios contenidos en los artículos suministrados. No citar ni reproducir disposiciones transitorias ni otras partes no articuladas de normas.",
    ),
    EspecificacionArticulo(
        5,
        "Retroactividad e irretroactividad",
        ("CC-2", "CE-9"),
        "Explicar la regla general del Código Civil sobre retroactividad y la garantía constitucional específica de irretroactividad, diferenciando correctamente sus respectivos ámbitos.",
    ),
    EspecificacionArticulo(
        6,
        "Derechos adquiridos y límites a la retroactividad",
        ("CC-2", "CE-9"),
        "Explicar con prudencia doctrinal la relación entre sucesión normativa, situaciones jurídicas consolidadas, derechos adquiridos y límites a la retroactividad. No identificar automáticamente los derechos adquiridos con los derechos individuales del artículo 9.3 de la Constitución ni inventar una protección absoluta no contenida en las fuentes.",
    ),
)

NORMAS_BOE = {
    "CC": CODIGO_CIVIL_NOMBRE,
    "CE": "Constitución Española de 1978",
}

ARTICULOS_BOE = {
    "CC": (2,),
    "CE": (9,),
}


def limpiar(v: object | None) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def normalizar_para_validar(texto: str) -> str:
    return limpiar(texto).casefold()


def preparar_alias_codigo_civil() -> None:
    clave = boe_api.normalizar(CODIGO_CIVIL_NOMBRE)
    existente = boe_api.NORMAS_ESPECIALES.get(clave)
    if existente not in (None, CODIGO_CIVIL_ID):
        raise RuntimeError(
            f"Alias Código Civil en conflicto: {existente} != {CODIGO_CIVIL_ID}"
        )
    boe_api.NORMAS_ESPECIALES[clave] = CODIGO_CIVIL_ID


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
    preparar_alias_codigo_civil()
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


def fuentes_para_prompt(
    e: EspecificacionArticulo, bloques: dict[str, BloqueOficial]
) -> list[dict]:
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


def construir_prompt(
    e: EspecificacionArticulo, bloques: dict[str, BloqueOficial]
) -> str:
    paquete = {
        "articulo_gen": e.numero,
        "titulo": e.titulo,
        "objetivo": e.objetivo,
        "fuentes_normativas_oficiales": fuentes_para_prompt(e, bloques),
    }
    regla_constitucional = (
        "Distingue la regla general legal del artículo 2.3 del Código Civil de la garantía constitucional específica del artículo 9.3 CE. Nunca llames constitucional a la regla del Código Civil."
        if "CE-9" in e.claves_bloques
        else "El paquete no contiene la Constitución. No introduzcas referencias, garantías ni calificaciones constitucionales. Si mencionas el artículo 2.3 CC, identifícalo solo como regla general legal del Código Civil."
    )
    return (
        "Redacta EXCLUSIVAMENTE la explicación doctrinal de un artículo de un corpus para oposiciones jurídicas. "
        "No reproduzcas ni reescribas los textos normativos: el programa los añadirá literalmente después.\n\n"
        "REGLAS OBLIGATORIAS:\n"
        "1. Usa solo la información del paquete suministrado.\n"
        "2. No inventes normas, artículos, sentencias, fechas ni efectos jurídicos.\n"
        "3. No cites ni uses preámbulos, exposiciones de motivos, disposiciones adicionales, transitorias, derogatorias o finales.\n"
        "4. No introduzcas citas normativas concretas que no aparezcan en el paquete.\n"
        "5. Explica con precisión suficiente para derivar preguntas de oposición, sin complejidad innecesaria.\n"
        f"6. {regla_constitucional}\n"
        "7. La frase del artículo 2.2 CC 'las leyes sólo se derogan por otras posteriores' debe explicarse sin inferir que la derogación sea una teoría general exhaustiva de todas las causas de pérdida de vigencia de cualquier norma.\n"
        "8. Al tratar derechos adquiridos, no los identifiques automáticamente con los derechos individuales del artículo 9.3 CE.\n"
        "9. Al explicar el artículo 2.3 CC, conserva exactamente su alcance: regla general de irretroactividad salvo que la ley disponga lo contrario. No añadas que la retroactividad deba establecerse 'expresamente', 'de forma expresa' o mediante una 'previsión expresa', porque ese requisito adicional no figura en el artículo suministrado.\n"
        "10. Devuelve JSON con exactamente dos claves: explicacion, puntos_clave.\n"
        "11. explicacion: entre 350 y 700 palabras.\n"
        "12. puntos_clave: lista de 5 a 10 frases breves.\n\n"
        "PAQUETE CONTROLADO:\n"
        + json.dumps(paquete, ensure_ascii=False, indent=2)
    )


def generar_explicacion(
    e: EspecificacionArticulo, bloques: dict[str, BloqueOficial]
) -> dict:
    r = seleccionar_fragmento_json(
        prompt=construir_prompt(e, bloques),
        modelo=MODELO,
        operacion=f"gen-especial-05-art-{e.numero:02d}",
        max_output_tokens=4200,
    )
    if not isinstance(r, dict):
        raise RuntimeError(f"Artículo GEN {e.numero}: respuesta IA no es JSON objeto.")
    explicacion = limpiar(r.get("explicacion"))
    puntos = [limpiar(x) for x in (r.get("puntos_clave") or []) if limpiar(x)]
    palabras = len(explicacion.split())
    if palabras < 280 or palabras > 950:
        raise RuntimeError(
            f"Artículo GEN {e.numero}: explicación fuera de rango de seguridad ({palabras} palabras)."
        )
    if not 5 <= len(puntos) <= 10:
        raise RuntimeError(
            f"Artículo GEN {e.numero}: puntos_clave inválidos ({len(puntos)})."
        )
    return {"explicacion": explicacion, "puntos_clave": puntos}


def validar_precision_juridica(e: EspecificacionArticulo, ia: dict) -> None:
    texto = normalizar_para_validar(
        ia["explicacion"] + " " + " ".join(ia["puntos_clave"])
    )

    # En artículos sin CE-9 se bloquean solo atribuciones constitucionales
    # concretas; la mera palabra "constitucional" era un criterio demasiado amplio.
    if "CE-9" not in e.claves_bloques:
        expresiones_constitucionales_no_admitidas = (
            "garantía constitucional",
            "garantia constitucional",
            "principio constitucional",
            "regla constitucional",
            "artículo 9.3",
            "articulo 9.3",
            "9.3 ce",
            "constitución española",
            "constitucion española",
        )
        for expresion in expresiones_constitucionales_no_admitidas:
            if expresion in texto:
                raise RuntimeError(
                    f"Artículo GEN {e.numero}: referencia constitucional sin CE-9 en las fuentes."
                )

    expresiones_retroactividad_no_admitidas = (
        "retroactividad requiere una previsión expresa",
        "retroactividad requiere una prevision expresa",
        "retroactividad exige una previsión expresa",
        "retroactividad exige una prevision expresa",
        "retroactividad requiere previsión expresa",
        "retroactividad requiere prevision expresa",
        "retroactividad exige previsión expresa",
        "retroactividad exige prevision expresa",
        "solo habrá retroactividad si la ley lo dispone expresamente",
        "solo habra retroactividad si la ley lo dispone expresamente",
        "solo hay retroactividad si la ley nueva dispone lo contrario expresamente",
        "para admitir retroactividad, exista una previsión expresa",
        "para admitir retroactividad, exista una prevision expresa",
        "previsión expresa en la propia ley",
        "prevision expresa en la propia ley",
        "previsión expresa en la ley nueva",
        "prevision expresa en la ley nueva",
    )
    for expresion in expresiones_retroactividad_no_admitidas:
        if expresion in texto:
            raise RuntimeError(
                f"Artículo GEN {e.numero}: añade un requisito de retroactividad expresa no contenido en CC 2.3."
            )

    if e.numero == 2:
        expresiones_no_admitidas = (
            "no se produce por instrumentos distintos",
            "única causa de pérdida de vigencia",
            "unica causa de perdida de vigencia",
        )
        for expresion in expresiones_no_admitidas:
            if expresion in texto:
                raise RuntimeError(
                    "Artículo GEN 2: generalización no sustentada sobre pérdida de vigencia."
                )


def renderizar_articulo(
    e: EspecificacionArticulo, bloques: dict[str, BloqueOficial], ia: dict
) -> str:
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


def validar_integridad(
    e: EspecificacionArticulo, bloques: dict[str, BloqueOficial], final: str
) -> None:
    destino = normalizar_para_validar(final)
    for clave in e.claves_bloques:
        esperado = normalizar_para_validar(bloques[clave].texto)
        if not esperado or esperado not in destino:
            raise RuntimeError(
                f"Artículo GEN {e.numero}: falta o se alteró el texto oficial completo de {clave}."
            )


def guardar_json(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Genera GEN A1 ESPECIAL 5 fuera de la BD."
    )
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument(
        "--solo-fuentes",
        action="store_true",
        help="Obtiene y valida fuentes; 0 llamadas IA.",
    )
    modo.add_argument(
        "--generar", action="store_true", help="Genera 6 artículos y guarda revisión."
    )
    p.add_argument(
        "--forzar",
        action="store_true",
        help="Permite reemplazar una revisión ya existente.",
    )
    return p


def main() -> int:
    args = parser().parse_args()
    if not CARPETA.is_dir():
        raise FileNotFoundError(f"No existe la carpeta A1: {CARPETA}")

    print("=" * 78)
    print("GEN A1 / ESPECIAL 5 - PREPARACION DE FUENTES")
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
        validar_precision_juridica(e, ia)
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
                "hash_texto_final": hashlib.sha256(
                    final.encode("utf-8")
                ).hexdigest(),
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

    markdown = [f"# {NOMBRE_GEN}", ""]
    for art in articulos:
        markdown.append(art["texto_final"].rstrip())
        markdown.append("")
    ruta_md.write_text("\n".join(markdown).rstrip() + "\n", encoding="utf-8")

    print()
    print("VALIDACION FINAL: OK")
    print(f"JSON revisión: {ruta_json}")
    print(f"Markdown revisión: {ruta_md}")
    print("BD abierta: NO")
    print("BD modificada: NO")
    print("CSV modificado: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
