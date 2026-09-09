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


# Señales fuertes de teoría general o de alcance transversal. Deben describir
# una estructura abierta del tema; no basta una palabra genérica aislada.
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
        r"\beficacia\s+temporal\s+de\s+las\s+normas\b",
        r"\bderecho\s+transitorio\b",
        r"\bprincipio\s+de\s+irretroactividad\b",
        r"\badministracion\s+publica\s+como\s+persona\s+juridica\b",
        r"\bactividad\s+discrecional\b",
        r"\bformas?\s+de\s+actividad\s+administrativa\b",
        r"\bactividad\s+de\s+limitacion\s+o\s+policia\b",
        r"\bfinanciacion\s+de\s+las\s+comunidades\s+autonomas\b",
        r"\bplanificacion\s+estrategica\b",
        r"\bfases?\s+de\s+la\s+planificacion\b",
        # Fórmula típica de materias competenciales amplias: exige conocer
        # varias normas sectoriales, sin que el temario delimite una sola.
        r"\bcompetencias?\s+de\s+la\s+generalitat\s+en\s+materia\s+de\b",
        r"\bprincipales\s+normas\s+reguladoras\s*:\s*aspectos\s+generales\b",
        # Marcos normativos claramente multicapas y abiertos. No se utiliza la
        # sola expresión 'marco europeo y estatal': puede describir una materia
        # estrecha y jurídicamente delimitable.
        r"\bnormativa\s+basica\s+estatal\s+y\s+autonomica\b",
        r"\bnormativa\s+europea,?\s+basica\s+estatal\s+y\s+autonomica\b",
        r"\bnormativa\s+(?:europea,?\s+estatal\s+y\s+autonomica|europea,?\s+estatal\s+o\s+autonomica)\b",
    )
)

# Familias de materias jurídicas normalmente acotables. No identifican una
# norma concreta; únicamente permiten distinguir una materia delimitable de una
# formulación doctrinal abierta. Se exigen al menos dos señales de la misma
# familia para evitar clasificaciones por coincidencias aisladas.
GRUPOS_DELIMITABLES = (
    ("ORGANIZACION_ADMINISTRATIVA", (
        r"\borganos?\s+de\s+las?\s+administraciones?\s+publicas?\b",
        r"\borganos?\s+colegiados?\b",
        r"\bcompetencia\b",
        r"\bdelegacion\s+de\s+competencias?\b",
        r"\bencomienda\s+de\s+gestion\b",
        r"\bavocacion\b",
    )),
    ("CONSTITUCIONAL_ESTADO", (
        r"\bgobierno\s+y\s+administracion\b",
        r"\bconsejo\s+de\s+ministros\b",
        r"\brelaciones\s+entre\s+el\s+gobierno\s+y\s+las\s+cortes\s+generales\b",
        r"\bprincipios\s+constitucionales\b",
        r"\badministracion\s+general\s+del\s+estado\b",
        r"\bpoder\s+judicial\b",
        r"\bconsejo\s+general\s+del\s+poder\s+judicial\b",
        r"\btribunal\s+constitucional\b",
        r"\bdefensor\s+del\s+pueblo\b",
        r"\borganizacion\s+territorial\s+del\s+estado\b",
        r"\badministracion\s+local\b",
        r"\bcomunidades\s+autonomas\b",
    )),
    ("INSTITUCIONES_VALENCIANAS", (
        r"\bprocedimiento\s+legislativo\b",
        r"\bsistema\s+electoral\s+valenciano\b",
        r"\bsindic(?:a|o)\s+de\s+greuges\b",
        r"\bsindicatura\s+de\s+comptes\b",
        r"\bconsell\s+juridic\s+consultiu\b",
        r"\bconsell\s+valencia\s+de\s+cultura\b",
        r"\bacademia\s+valenciana\s+de\s+la\s+llengua\b",
        r"\bcomite\s+economic\s+i\s+social\b",
    )),
    ("UNION_EUROPEA", (
        r"\bparlamento\s+europeo\b",
        r"\bconsejo\s+europeo\b",
        r"\bcomision\s+europea\b",
        r"\btribunal\s+de\s+justicia\s+de\s+la\s+union\s+europea\b",
        r"\bbanco\s+central\s+europeo\b",
        r"\bcompetencias?\s+de\s+la\s+union\s+europea\b",
        r"\bmiembros?\s+de\s+la\s+union\s+europea\b",
        r"\bciudadania\s+de\s+la\s+union\b",
        r"\bmercado\s+interior\b",
        r"\blibre\s+circulacion\b",
        r"\bespacio\s+de\s+libertad,?\s+seguridad\s+y\s+justicia\b",
    )),
    ("ADMINISTRATIVO_GENERAL", (
        r"\bpotestades?\s+administrativas?\b",
        r"\bacto\s+administrativo\b",
        r"\bactos?\s+de\s+tramite\b",
        r"\biniciativa\s+legislativa\b",
        r"\bpotestad\s+para\s+dictar\s+reglamentos\b",
        r"\bproyectos?\s+normativos?\b",
    )),
    ("JUSTICIA_ADMINISTRATIVA", (
        r"\bjurisdiccion\s+contencioso-administrativa\b",
        r"\brecurso\s+contencioso-administrativo\b",
        r"\bejecucion\s+de\s+sentencias\b",
        r"\bprocedimientos?\s+de\s+declaracion\s+de\s+inconstitucionalidad\b",
        r"\brecurso\s+de\s+amparo\s+constitucional\b",
        r"\bconflictos?\s+constitucionales?\b",
    )),
    ("POTESTAD_SANCIONADORA", (
        r"\bpotestad\s+sancionadora\b",
        r"\bprincipios\s+de\s+la\s+potestad\s+sancionadora\b",
        r"\bprocedimientos?\s+sancionadores?\b",
    )),
    ("RESPONSABILIDAD_PATRIMONIAL", (
        r"\bresponsabilidad\s+patrimonial\b",
        r"\bresponsabilidad\s+de\s+las\s+autoridades\b",
        r"\bprocedimientos?\s+en\s+materia\s+de\s+responsabilidad\s+patrimonial\b",
    )),
    ("PATRIMONIO_PUBLICO", (
        r"\bpatrimonio\s+de\s+las\s+administraciones\s+publicas\b",
        r"\blegislacion\s+basica\s+del\s+estado\b",
        r"\bley\s+de\s+patrimonio\s+de\s+la\s+generalitat\b",
    )),
    ("CONTRATACION_PUBLICA", (
        r"\bcontratos?\s+del\s+sector\s+publico\b",
        r"\bcontratos?\s+de\s+las\s+administraciones\s+publicas\b",
        r"\borganizacion\s+administrativa\s+para\s+la\s+gestion\s+de\s+la\s+contratacion\b",
        r"\belementos\s+estructurales\s+de\s+los\s+contratos\b",
    )),
    ("EXPROPIACION", (
        r"\bexpropiacion\s+forzosa\b",
        r"\bprocedimiento\s+general\b",
        r"\bprocedimiento\s+de\s+urgencia\b",
    )),
    ("INFORMANTES_IRREGULARIDADES", (
        r"\bproteccion\s+de\s+las\s+personas\s+que\s+informen\s+sobre\s+irregularidades\b",
        r"\bsistema\s+interno\s+de\s+informacion\b",
        r"\bley\s+valenciana\s+de\s+funcion\s+publica\b",
    )),
    ("EMPLEO_PUBLICO", (
        r"\bpersonal\s+al\s+servicio\s+de\s+las?\s+administraciones?\s+publicas?\b",
        r"\bempleo\s+publico\b",
        r"\bpersonal\s+empleado\s+publico\b",
        r"\bestructuracion\s+del\s+empleo\s+publico\b",
        r"\bordenacion\s+de\s+los\s+puestos\s+de\s+trabajo\b",
        r"\bregistros?\s+de\s+personal\b",
        r"\bsituaciones\s+administrativas\b",
        r"\bregimen\s+disciplinario\b",
        r"\bincompatibilidades\b",
        r"\bprovision\s+de\s+puestos\b",
        r"\bpromocion\s+profesional\b",
    )),
    ("SEGURIDAD_SOCIAL", (
        r"\bsistema\s+de\s+la\s+seguridad\s+social\b",
        r"\bregimen\s+general\b",
        r"\bafiliacion\s+y\s+cotizacion\b",
        r"\baccion\s+protectora\b",
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
    ("TESORERIA_DEUDA", (
        r"\btesoreria\s+de\s+la\s+generalitat\b",
        r"\bgestion\s+de\s+la\s+tesoreria\b",
        r"\bdeuda\s+publica\b",
        r"\bregimen\s+de\s+avales\b",
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
    if grupo and n_senales >= 2:
        return "JURIDICO_DELIMITABLE", f"MATERIA_ACOTADA:{grupo};senales={n_senales}"

    # El bloque aporta contexto, pero no basta por sí solo para afirmar que una
    # materia sea delimitable. En ausencia de evidencia temática se conserva
    # REVISION para evitar falsa precisión.
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
