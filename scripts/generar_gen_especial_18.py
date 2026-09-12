"""Generación revisable del GEN para A1 / ESPECIAL 18, fuera de BD."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from boe_api import ArticuloBOE, obtener_articulo
from openai_api import seleccionar_fragmento_json

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM"
SALIDA = CARPETA / "gen_revision"
ID_GEN = "GEN-A1-ESPECIAL-18"
NOMBRE_GEN = "GEN - Las formas de actividad administrativa"
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

ESPECIFICACIONES = (
    EspecificacionArticulo(1, "Las formas de actividad administrativa", ("L40-4", "CE-128"), "Explicar doctrinalmente la clasificación clásica de la actividad administrativa en limitación o policía, fomento y servicio público, apoyándose solo en los artículos suministrados y sin presentar esa clasificación doctrinal como enumeración literal de una ley."),
    EspecificacionArticulo(2, "Actividad de limitación o policía", ("L40-4",), "Explicar concepto, finalidad y límites de la actividad administrativa de limitación o policía, con especial atención a proporcionalidad, necesidad y elección de la medida menos restrictiva conforme al artículo suministrado."),
    EspecificacionArticulo(3, "Actividad de fomento", ("LGS-2",), "Explicar doctrinalmente la actividad de fomento y la subvención como una de sus manifestaciones características, respetando estrictamente el concepto legal de subvención del artículo suministrado."),
    EspecificacionArticulo(4, "El servicio público: concepto y clases", ("LRBRL-85", "CE-128"), "Explicar doctrinalmente el servicio público, su vinculación con competencias públicas y sus clases, distinguiéndolo de limitación y fomento. No generalizar automáticamente a todas las Administraciones las reglas que el artículo 85 establece específicamente para servicios públicos locales."),
    EspecificacionArticulo(5, "Formas de gestión de los servicios públicos", ("LRBRL-85",), "Explicar gestión directa e indirecta y las modalidades del artículo 85 para los servicios públicos locales. Mantener explícito el ámbito local del precepto y no convertir sus modalidades en una enumeración universal para toda Administración."),
    EspecificacionArticulo(6, "Gestión indirecta mediante concesión de servicios", ("LCSP-284",), "Explicar la concesión de servicios como forma de gestión indirecta dentro del ámbito regulado por el artículo 284, incluidos sus límites, sin extender sus reglas más allá del texto suministrado."),
)

NORMAS_BOE = {
    "L40": "Ley 40/2015, de 1 de octubre, de Régimen Jurídico del Sector Público",
    "CE": "Constitución Española de 1978",
    "LGS": "Ley 38/2003, de 17 de noviembre, General de Subvenciones",
    "LRBRL": "Ley 7/1985, de 2 de abril, Reguladora de las Bases del Régimen Local",
    "LCSP": "Ley 9/2017, de 8 de noviembre, de Contratos del Sector Público",
}
ARTICULOS_BOE = {"L40": (4,), "CE": (128,), "LGS": (2,), "LRBRL": (85,), "LCSP": (284,)}

def limpiar(v: object | None) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def normalizar(texto: str) -> str:
    return limpiar(texto).casefold()

def bloque_boe(prefijo: str, articulo: ArticuloBOE) -> BloqueOficial:
    numero = articulo.articulo.split(".", 1)[0]
    texto = limpiar(articulo.texto)
    if not texto:
        raise RuntimeError(f"{prefijo}-{numero}: texto BOE vacío.")
    return BloqueOficial(f"{prefijo}-{numero}", articulo.nombre_norma, numero, articulo.titulo_bloque, texto, articulo.id_boe, f"https://www.boe.es/buscar/act.php?id={articulo.id_boe}")

def obtener_fuentes() -> dict[str, BloqueOficial]:
    bloques = {}
    for prefijo, numeros in ARTICULOS_BOE.items():
        for numero in numeros:
            b = bloque_boe(prefijo, obtener_articulo(NORMAS_BOE[prefijo], str(numero)))
            if b.clave in bloques:
                raise RuntimeError(f"Clave BOE duplicada: {b.clave}")
            bloques[b.clave] = b
    requeridas = {c for e in ESPECIFICACIONES for c in e.claves_bloques}
    faltan = sorted(requeridas - set(bloques))
    if faltan:
        raise RuntimeError("Faltan fuentes: " + ", ".join(faltan))
    return bloques

def fuentes_prompt(e, bloques):
    return [{"clave": bloques[c].clave, "norma": bloques[c].norma, "articulo": bloques[c].articulo, "titulo": bloques[c].titulo, "texto_oficial": bloques[c].texto, "fuente_id": bloques[c].fuente_id, "url": bloques[c].fuente_url} for c in e.claves_bloques]

def construir_prompt(e, bloques) -> str:
    paquete = {"articulo_gen": e.numero, "titulo": e.titulo, "objetivo": e.objetivo, "fuentes_normativas_oficiales": fuentes_prompt(e, bloques)}
    return (
        "Redacta EXCLUSIVAMENTE la explicación doctrinal de un artículo de un corpus para oposiciones jurídicas. No reproduzcas los textos normativos: el programa los añadirá literalmente después.\n\n"
        "REGLAS OBLIGATORIAS:\n"
        "1. Usa solo la información del paquete suministrado.\n"
        "2. No inventes normas, artículos, sentencias, fechas, clasificaciones legales ni efectos jurídicos.\n"
        "3. No cites ni uses preámbulos, exposiciones de motivos ni disposiciones adicionales, transitorias, derogatorias o finales.\n"
        "4. No introduzcas citas normativas concretas ajenas al paquete.\n"
        "5. La clasificación limitación/policía, fomento y servicio público es una explicación doctrinal del epígrafe; no afirmes que una fuente suministrada contiene literalmente esa clasificación si no la contiene.\n"
        "6. Cuando uses el artículo 85 LRBRL, indica que regula servicios públicos LOCALES; no generalices sus modalidades como régimen universal de todas las Administraciones.\n"
        "7. Cuando uses el artículo 2 LGS, presenta la subvención como manifestación característica del fomento, no como definición exhaustiva de toda actividad de fomento.\n"
        "8. Cuando uses el artículo 284 LCSP, limita las afirmaciones a la concesión de servicios y al ámbito que resulta del propio artículo.\n"
        "9. Explica con precisión suficiente para preguntas de oposición, sin complejidad innecesaria.\n"
        "10. Devuelve JSON con exactamente dos claves: explicacion, puntos_clave. explicacion entre 350 y 700 palabras; puntos_clave lista de 5 a 10 frases.\n\nPAQUETE CONTROLADO:\n" + json.dumps(paquete, ensure_ascii=False, indent=2)
    )

def generar(e, bloques):
    r = seleccionar_fragmento_json(prompt=construir_prompt(e, bloques), modelo=MODELO, operacion=f"gen-especial-18-art-{e.numero:02d}", max_output_tokens=4200)
    if not isinstance(r, dict):
        raise RuntimeError(f"Artículo GEN {e.numero}: respuesta IA no es objeto JSON.")
    explicacion = limpiar(r.get("explicacion"))
    puntos = [limpiar(x) for x in (r.get("puntos_clave") or []) if limpiar(x)]
    if not 280 <= len(explicacion.split()) <= 950:
        raise RuntimeError(f"Artículo GEN {e.numero}: explicación fuera de rango.")
    if not 5 <= len(puntos) <= 10:
        raise RuntimeError(f"Artículo GEN {e.numero}: puntos_clave inválidos.")
    return {"explicacion": explicacion, "puntos_clave": puntos}

def validar_precision(e, ia):
    texto = normalizar(ia["explicacion"] + " " + " ".join(ia["puntos_clave"]))
    if "LRBRL-85" in e.claves_bloques:
        if "servicios públicos locales" not in texto and "servicio público local" not in texto:
            raise RuntimeError(f"Artículo GEN {e.numero}: falta delimitar el ámbito local del art. 85 LRBRL.")
    if e.numero == 3 and ("única forma de fomento" in texto or "unica forma de fomento" in texto):
        raise RuntimeError("Artículo GEN 3: generalización indebida de la subvención.")

def renderizar(e, bloques, ia):
    partes = [f"Artículo {e.numero}. {e.titulo}", "", ia["explicacion"], "", "Puntos clave:", *[f"- {p}" for p in ia["puntos_clave"]], "", "Textos oficiales incorporados íntegramente:", ""]
    for c in e.claves_bloques:
        b = bloques[c]
        partes += [f"[{b.norma} — {b.titulo}]", f"Fuente: {b.fuente_id} | {b.fuente_url}", b.texto, ""]
    return "\n".join(partes).strip() + "\n"

def validar_integridad(e, bloques, final):
    destino = normalizar(final)
    for c in e.claves_bloques:
        if normalizar(bloques[c].texto) not in destino:
            raise RuntimeError(f"Artículo GEN {e.numero}: falta o se alteró el texto completo de {c}.")

def guardar_json(ruta, datos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    p = argparse.ArgumentParser(description="Genera GEN A1 ESPECIAL 18 fuera de BD.")
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--solo-fuentes", action="store_true")
    m.add_argument("--generar", action="store_true")
    p.add_argument("--forzar", action="store_true")
    args = p.parse_args()
    if not CARPETA.is_dir():
        raise FileNotFoundError(CARPETA)
    print("="*78); print("GEN A1 / ESPECIAL 18 - PREPARACION DE FUENTES"); print("="*78)
    bloques = obtener_fuentes()
    print(f"Bloques oficiales obtenidos: {len(bloques)}")
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta_fuentes = SALIDA / f"{ID_GEN}_fuentes.json"
    guardar_json(ruta_fuentes, {"id_gen": ID_GEN, "nombre_gen": NOMBRE_GEN, "modelo_previsto": MODELO, "fuentes": {k: {**asdict(v), "hash_texto": v.hash_texto} for k,v in sorted(bloques.items())}})
    print(f"Snapshot de fuentes: {ruta_fuentes}")
    if args.solo_fuentes:
        print("SOLO FUENTES: 0 llamadas a OpenAI; 0 escrituras en BD; 0 cambios CSV."); return 0
    ruta_json, ruta_md = SALIDA / f"{ID_GEN}.json", SALIDA / f"{ID_GEN}.md"
    if (ruta_json.exists() or ruta_md.exists()) and not args.forzar:
        raise RuntimeError("Ya existe una generación de revisión; usa --forzar solo para regenerarla.")
    articulos=[]
    print("\nGENERACION GPT-5.4-NANO")
    for e in ESPECIFICACIONES:
        print(f"Artículo {e.numero}/6: {e.titulo}")
        ia=generar(e,bloques); validar_precision(e,ia); final=renderizar(e,bloques,ia); validar_integridad(e,bloques,final)
        articulos.append({"numero":e.numero,"titulo":e.titulo,"id_bloque":f"{ID_GEN}-ART-{e.numero:03d}","claves_fuente":list(e.claves_bloques),"explicacion":ia["explicacion"],"puntos_clave":ia["puntos_clave"],"texto_final":final,"hash_texto_final":hashlib.sha256(final.encode("utf-8")).hexdigest()})
    guardar_json(ruta_json,{"id_gen":ID_GEN,"nombre_gen":NOMBRE_GEN,"modelo":MODELO,"estado":"REVISION_HUMANA_PENDIENTE","escrituras_bd":0,"articulos":articulos})
    md=[f"# {NOMBRE_GEN}",""]
    for a in articulos: md += [a["texto_final"].rstrip(),""]
    ruta_md.write_text("\n".join(md).rstrip()+"\n",encoding="utf-8")
    print("\nVALIDACION FINAL: OK"); print(f"JSON revisión: {ruta_json}"); print(f"Markdown revisión: {ruta_md}"); print("BD abierta: NO"); print("BD modificada: NO"); print("CSV modificado: NO")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
