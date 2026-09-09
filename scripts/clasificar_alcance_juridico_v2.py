"""
Constructor Temario V2 - clasificación del alcance jurídico.

SOLO LECTURA.

No resuelve normas ni artículos y no consulta temario_referencias. Su objetivo
es separar, antes de cualquier resolución de artículos, tres situaciones:

- JURIDICO_EXPLICITO: existe una referencia normativa literal suficientemente
  concreta en el texto oficial.
- JURIDICO_DELIMITABLE: no hay referencia literal suficiente, pero la materia
  está formulada como un ámbito jurídico acotado que permite buscar normas y
  artículos candidatos con revisión.
- JURIDICO_DOCTRINAL_TRANSVERSAL: el punto formula teoría general, principios,
  fuentes o una materia transversal cuyo contenido no debe forzarse a una lista
  exhaustiva automática de artículos.

Los puntos NO_JURIDICO se conservan. Los casos sin evidencia suficiente quedan
REVISION.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter
from pathlib import Path

import extraer_estructura_temario_v2 as estructura
import resolver_normas_temario_v2 as resolver


def _norm(texto: str) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", x).strip()


# Señales de teoría general/transversal. Se exigen señales fuertes; no basta con
# palabras genéricas como "principios" o "ley" aisladas.
PATRONES_DOCTRINALES_FUERTES = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bfuentes?\s+(?:del|de)\s+(?:derecho|ordenamiento)\b",
        r"\bordenamiento\s+juridico\b",
        r"\bjerarquia\s+normativa\b",
        r"\breserva\s+de\s+ley\b",
        r"\bprincipios\s+generales\s+del\s+derecho\b",
        r"\bcostumbre\b",
        r"\bjurisprudencia\b",
        r"\bdoctrina\b",
        r"\baplicacion\s+e\s+interpretacion\s+de\s+las\s+normas\b",
    )
)

# Familias de materias jurídicas normalmente acotables. No identifican una
# norma concreta; únicamente permiten distinguir una materia delimitable de una
# formulación doctrinal abierta.
GRUPOS_DELIMITABLES = (
    ("ORGANIZACION_ADMINISTRATIVA", (
        r"\borganos?\s+de\s+las?\s+administraciones?\s+publicas?\b",
        r"\borganos?\s+colegiados?\b",
        r"\bcompetencia\b",
        r"\bdelegacion\s+de\s+competencias?\b",
        r"\bencomienda\s+de\s+gestion\b",
        r"\bavocacion\b",
    )),
    ("EMPLEO_PUBLICO", (
        r"\bpersonal\s+al\s+servicio\s+de\s+las?\s+administraciones?\s+publicas?\b",
        r"\bempleo\s+publico\b",
        r"\bpersonal\s+empleado\s+publico\b",
        r"\bsituaciones\s+administrativas\b",
        r"\bregimen\s+disciplinario\b",
        r"\bincompatibilidades\b",
        r"\bprovision\s+de\s+puestos\b",
        r"\bpromocion\s+profesional\b",
    )),
    ("PRESUPUESTO", (
        r"\bpresupuesto\s+de\s+la\s+generalitat\b",
        r"\bgestion\s+presupuestaria\b",
        r"\bcreditos?\s+presupuestarios?\b",
        r"\banticipos?\s+de\s+caja\s+fija\b",
        r"\bpagos?\s+a\s+justificar\b",
    )),
    ("CONTROL_ECONOMICO", (
        r"\bcontrol\s+interno\s+de\s+la\s+gestion\s+economico",
        r"\bfuncion\s+interventora\b",
        r"\bcontrol\s+financiero\b",
        r"\bauditoria\s+publica\b",
        r"\btribunal\s+de\s+cuentas\b",
        r"\bsindicatura\s+de\s+comptes\b",
    )),
)


def _senales_doctrinales(texto: str) -> list[str]:
    n = _norm(texto)
    return [p.pattern for p in PATRONES_DOCTRINALES_FUERTES if p.search(n)]


def _grupo_delimitable(texto: str) -> tuple[str | None, int]:
    n = _norm(texto)
    mejor_nombre = None
    mejor_n = 0
    for nombre, patrones in GRUPOS_DELIMITABLES:
        encontrados = sum(1 for p in patrones if re.search(p, n, re.I))
        if encontrados > mejor_n:
            mejor_nombre, mejor_n = nombre, encontrados
    return mejor_nombre, mejor_n


def clasificar(tema: estructura.TemaExtraido) -> tuple[str, str]:
    if tema.clasificacion == "NO_JURIDICO":
        return "NO_JURIDICO", tema.evidencia_clasificacion

    texto = f"{tema.bloque_titulo} {tema.texto}".strip()
    doctrina = _senales_doctrinales(texto)
    if doctrina:
        return (
            "JURIDICO_DOCTRINAL_TRANSVERSAL",
            "SENAL_DOCTRINAL:" + ",".join(doctrina[:3]),
        )

    citas = resolver.detectar_citas(tema.texto)
    if citas:
        nombres = ", ".join(c.texto for c in citas[:3])
        return "JURIDICO_EXPLICITO", f"CITA_LITERAL:{nombres}"

    grupo, n_senales = _grupo_delimitable(texto)
    # Se exigen al menos dos señales de una misma familia para no transformar
    # una coincidencia casual en una clasificación jurídica afirmativa.
    if grupo and n_senales >= 2:
        return "JURIDICO_DELIMITABLE", f"MATERIA_ACOTADA:{grupo};senales={n_senales}"

    # Si el extractor ya lo consideró jurídico por una referencia literal que
    # el resolvedor nominal todavía no sabe identificar, se mantiene en revisión
    # de alcance en vez de degradarlo o inventar una norma.
    return "REVISION", "ALCANCE_NO_DETERMINADO"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Clasifica el alcance jurídico del temario sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    resultados = []
    for tema in temas:
        clase, evidencia = clasificar(tema)
        resultados.append((tema, clase, evidencia))

    conteo = Counter(clase for _, clase, _ in resultados)
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - CLASIFICACION DE ALCANCE JURIDICO - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"Temas............................ {len(temas)}")
    print()
    for clase in (
        "JURIDICO_EXPLICITO",
        "JURIDICO_DELIMITABLE",
        "JURIDICO_DOCTRINAL_TRANSVERSAL",
        "NO_JURIDICO",
        "REVISION",
    ):
        print(f"{clase:<34} {conteo.get(clase, 0)}")
    print()

    for tema, clase, evidencia in resultados:
        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | {clase}")
        print(f"  evidencia: {evidencia}")
        if clase in {"JURIDICO_DELIMITABLE", "JURIDICO_DOCTRINAL_TRANSVERSAL", "REVISION"}:
            print(f"  texto: {tema.texto}")
        print()

    print("No se ha consultado ni modificado temario_referencias.")
    print("No se han aceptado normas ni resuelto artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
