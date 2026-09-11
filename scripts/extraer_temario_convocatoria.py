"""
NetReto / OpoCoach - Extracción de temario explícita/implícita.

Genera un temario.csv desde el PDF oficial con este contrato:
    parte,tema,titulo,LEY,articulo,tipo

Reglas:
- Detecta temas GENERAL/ESPECIAL y conserva un título corto:
  texto hasta el primer punto; si se repite dentro de la misma parte,
  amplía lo mínimo necesario con las frases siguientes.
- Resuelve referencias normativas explícitas (norma, artículos,
  títulos/capítulos/secciones) usando el localizador normativo existente.
- Aplica un catálogo pequeño y auditable de equivalencias implícitas
  únicamente cuando el texto identifica de forma suficientemente inequívoca
  una norma.
- Una misma combinación LEY + artículo nunca puede quedar asignada a dos
  puntos diferentes del temario.
- Si un punto jurídico no produce ninguna referencia segura, genera
  exactamente una fila:
      LEY = "No determinada"
      articulo = "No determinados"
      tipo = "JURIDICO"
- No modifica la base de datos. La salida es determinista para el mismo PDF,
  las mismas fuentes normativas y estas reglas.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
RUTA_LOCALIZADOR = RAIZ_PROYECTO / "scripts" / "localizador_normativa.py"
RUTA_PDF_PREDETERMINADA = (
    RAIZ_PROYECTO
    / "data_convocatorias"
    / "CONV_C2-01_70_26"
    / "temario_70_26.pdf"
)
RUTA_SALIDA_PREDETERMINADA = (
    RAIZ_PROYECTO
    / "data_convocatorias"
    / "CONV_C2-01_70_26"
    / "temario.csv"
)

COLUMNAS = ["parte", "tema", "titulo", "LEY", "articulo", "tipo"]
ENCODING_SALIDA = "utf-8-sig"

PALABRAS_ORDINALES = {
    "primero": 1, "primera": 1,
    "segundo": 2, "segunda": 2,
    "tercero": 3, "tercera": 3,
    "cuarto": 4, "cuarta": 4,
    "quinto": 5, "quinta": 5,
    "sexto": 6, "sexta": 6,
    "septimo": 7, "septima": 7,
    "octavo": 8, "octava": 8,
    "noveno": 9, "novena": 9,
    "decimo": 10, "decima": 10,
}

TIPOS_JERARQUIA = {
    "libro": 1,
    "titulo": 2,
    "capitulo": 3,
    "seccion": 4,
    "subseccion": 5,
}

PATRON_UNIDAD = re.compile(
    r"\b(libro|t[ií]tulo|cap[ií]tulo|secci[oó]n|subsecci[oó]n)\s+"
    r"(preliminar|[ivxlcdm]+|\d+\s*[.ºª]*|"
    r"primero|primera|segundo|segunda|tercero|tercera|cuarto|cuarta|"
    r"quinto|quinta|sexto|sexta|s[eé]ptimo|s[eé]ptima|octavo|octava|"
    r"noveno|novena|d[eé]cimo|d[eé]cima)\b",
    re.IGNORECASE,
)

PATRON_ARTICULOS = re.compile(
    r"\bart(?:[íi]culo)?s?\.?\s*"
    r"((?:\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quáter|quinquies|sexies|"
    r"septies|octies|nonies|decies))?)"
    r"(?:\s*(?:a|al|-|y|,)\s*"
    r"\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quáter|quinquies|sexies|"
    r"septies|octies|nonies|decies))?)*)",
    re.IGNORECASE,
)

PATRON_NORMA = re.compile(
    r"\b(?:la\s+|el\s+)?("
    r"Constituci[oó]n Espa[nñ]ola(?:\s+de\s+1978)?"
    r"|Estatuto de Autonom[ií]a de la Comunitat Valenciana"
    r"|Ley Org[aá]nica\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Real Decreto Legislativo\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Real Decreto(?:-ley|\s+ley)?\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Decreto Legislativo\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Decreto-ley\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Decreto\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Orden\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Ley\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r")",
    re.IGNORECASE,
)

NOMBRES_ALIAS = {
    "constitucion espanola": "Constitución Española de 1978",
    "constitucion espanola de 1978": "Constitución Española de 1978",
    "estatuto de autonomia de la comunitat valenciana":
        "Ley Orgánica 5/1982, de Estatuto de Autonomía de la Comunitat Valenciana",
}


class ExtraccionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tema:
    parte: str
    numero: int
    texto: str
    orden: int


@dataclass(frozen=True)
class ReferenciaNorma:
    nombre_detectado: str
    alcance: str
    explicita: bool = True


@dataclass(frozen=True)
class Selector:
    ruta: dict[str, str]
    articulos_explicitos: tuple[str, ...] = ()
    excluir: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReglaImplicita:
    patron: str
    norma: str
    alcance: str = ""


@dataclass
class Candidato:
    parte: str
    tema: int
    titulo: str
    ley: str
    articulo: str
    tipo: str
    explicita: bool
    precision: int
    orden_tema: int
    amplitud_norma_tema: int = 0

    def fila(self) -> dict[str, str]:
        return {
            "parte": self.parte,
            "tema": str(self.tema),
            "titulo": self.titulo,
            "LEY": self.ley,
            "articulo": self.articulo,
            "tipo": self.tipo,
        }


# Solo equivalencias con una relación suficientemente inequívoca.
# No se pretende convertir temas doctrinales amplios en normas por semejanza.
REGLAS_IMPLICITAS = (
    ReglaImplicita(
        r"\bjurisdiccion contencioso[- ]administrativa\b",
        "Ley 29/1998, reguladora de la Jurisdicción Contencioso-administrativa",
    ),
    ReglaImplicita(
        r"\btribunal constitucional\b",
        "Ley Orgánica 2/1979, del Tribunal Constitucional",
    ),
    ReglaImplicita(
        r"\bcontratos? del sector publico\b",
        "Ley 9/2017, de Contratos del Sector Público",
    ),
    ReglaImplicita(
        r"\bexpropiacion forzosa\b",
        "Ley de 16 de diciembre de 1954 sobre expropiación forzosa",
    ),
    ReglaImplicita(
        r"\bsistema de la seguridad social\b",
        "Real Decreto Legislativo 8/2015, texto refundido de la Ley General de la Seguridad Social",
    ),
    ReglaImplicita(
        r"\bpatrimonio de las administraciones publicas\b",
        "Ley 33/2003, del Patrimonio de las Administraciones Públicas",
    ),
    ReglaImplicita(
        r"\bley de patrimonio de la generalitat\b",
        "Ley 14/2003, de Patrimonio de la Generalitat Valenciana",
    ),
    ReglaImplicita(
        r"\bley de tasas de la generalitat\b",
        "Ley 20/2017, de tasas",
    ),
)


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip(" .,:;")


def canonicalizar_ley(nombre: str) -> str:
    n = normalizar(nombre)
    if n.startswith("constitucion espanola"):
        return "constitucion espanola 1978"
    if "estatuto de autonomia de la comunitat valenciana" in n:
        return "ley organica 5/1982"
    m = re.search(
        r"\b(ley organica|ley|real decreto legislativo|real decreto ley|"
        r"real decreto-ley|real decreto|decreto legislativo|decreto-ley|"
        r"decreto|orden)\s+(\d+/\d{4})\b",
        n,
    )
    if m:
        tipo = m.group(1).replace("-", " ")
        return f"{tipo} {m.group(2)}"
    return n


def nombre_ley_csv(nombre: str) -> str:
    clave = normalizar(nombre)
    if clave in NOMBRES_ALIAS:
        return NOMBRES_ALIAS[clave]
    return limpiar(nombre)


def cargar_localizador():
    if not RUTA_LOCALIZADOR.exists():
        raise ExtraccionError(
            f"No existe el módulo especializado: {RUTA_LOCALIZADOR}"
        )
    spec = importlib.util.spec_from_file_location(
        "opocoach_localizador_normativa", RUTA_LOCALIZADOR
    )
    if spec is None or spec.loader is None:
        raise ExtraccionError(f"No se pudo cargar {RUTA_LOCALIZADOR}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def extraer_texto_pdf(ruta_pdf: Path) -> str:
    if not ruta_pdf.is_file():
        raise ExtraccionError(f"No existe el PDF: {ruta_pdf}")

    try:
        import fitz  # type: ignore
        with fitz.open(ruta_pdf) as doc:
            texto = "\n".join(p.get_text("text") for p in doc)
        if texto.strip():
            return texto
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore
        lector = PdfReader(str(ruta_pdf))
        texto = "\n".join(p.extract_text() or "" for p in lector.pages)
        if texto.strip():
            return texto
    except Exception:
        pass

    try:
        proceso = subprocess.run(
            ["pdftotext", "-layout", str(ruta_pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proceso.stdout.strip():
            return proceso.stdout
    except (OSError, subprocess.CalledProcessError):
        pass

    raise ExtraccionError(
        "No se pudo extraer texto del PDF. Instale PyMuPDF o pypdf, "
        "o tenga pdftotext disponible."
    )


def limpiar_texto_dogv(texto: str) -> str:
    lineas: list[str] = []
    for linea in texto.splitlines():
        l = limpiar(linea)
        if not l:
            continue
        if re.match(r"^N[uú]m\.\s+\d+\s*/", l, re.I):
            continue
        if re.fullmatch(r"\d+\s*/\s*\d+", l):
            continue
        if l.startswith("CVE:") or "https://dogv.gva.es" in l:
            continue
        if re.match(r"^Anexo\s+[IVXLCDM]+$", l, re.I):
            continue
        if re.match(r"^Convocatoria\s+\d+/\d+$", l, re.I):
            continue
        lineas.append(l)
    return limpiar(" ".join(lineas))


def extraer_temas(texto_pdf: str) -> list[Tema]:
    texto = limpiar_texto_dogv(texto_pdf)
    marcador = re.compile(
        r"TEMARIO PARTE (GENERAL|ESPECIAL)|(?<!\d)(\d{1,2})\.\s+",
        re.IGNORECASE,
    )
    coincidencias = list(marcador.finditer(texto))
    parte_actual = ""
    temas: list[Tema] = []
    orden = 0

    for i, m in enumerate(coincidencias):
        if m.group(1):
            parte_actual = m.group(1).upper()
            continue
        if not parte_actual:
            continue

        numero = int(m.group(2))
        inicio = m.end()
        fin = len(texto)
        for siguiente in coincidencias[i + 1:]:
            if siguiente.group(1) or siguiente.group(2):
                fin = siguiente.start()
                break

        contenido = limpiar(texto[inicio:fin])
        contenido = re.sub(
            r"^[A-Z]\.\s+[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ\s-]+(?=\s)",
            "",
            contenido,
        )
        if contenido:
            orden += 1
            temas.append(Tema(parte_actual, numero, contenido, orden))

    unicos: dict[tuple[str, int], Tema] = {}
    for tema in temas:
        unicos.setdefault((tema.parte, tema.numero), tema)
    resultado = list(unicos.values())
    if not resultado:
        raise ExtraccionError("No se detectó ningún tema en el PDF.")
    return resultado


def frases_tema(texto: str) -> list[str]:
    partes = [limpiar(x) for x in re.split(r"\.\s+", limpiar(texto)) if limpiar(x)]
    return partes or [limpiar(texto)]


def construir_titulos(temas: list[Tema]) -> dict[tuple[str, int], str]:
    frases = {(t.parte, t.numero): frases_tema(t.texto) for t in temas}
    profundidad = {clave: 1 for clave in frases}

    while True:
        actuales = {
            clave: ". ".join(vals[:profundidad[clave]]).rstrip(".")
            for clave, vals in frases.items()
        }
        grupos: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
        for clave, titulo in actuales.items():
            grupos[(clave[0], normalizar(titulo))].append(clave)

        repetidos = [claves for claves in grupos.values() if len(claves) > 1]
        if not repetidos:
            return actuales

        cambio = False
        for claves in repetidos:
            for clave in claves:
                if profundidad[clave] < len(frases[clave]):
                    profundidad[clave] += 1
                    cambio = True
        if not cambio:
            return actuales


def detectar_referencias_explicitas(texto: str) -> list[ReferenciaNorma]:
    encontrados = list(PATRON_NORMA.finditer(texto))
    referencias: list[ReferenciaNorma] = []
    for i, m in enumerate(encontrados):
        inicio = m.end()
        fin = encontrados[i + 1].start() if i + 1 < len(encontrados) else len(texto)
        alcance = limpiar(texto[inicio:fin]).lstrip(" ,:")
        referencias.append(
            ReferenciaNorma(
                nombre_detectado=limpiar(m.group(1)),
                alcance=alcance,
                explicita=True,
            )
        )
    return referencias


def detectar_referencias_implicitas(
    texto: str,
    explicitas: list[ReferenciaNorma],
) -> list[ReferenciaNorma]:
    texto_n = normalizar(texto)
    explicitas_canon = {
        canonicalizar_ley(r.nombre_detectado)
        for r in explicitas
    }
    resultado: list[ReferenciaNorma] = []

    for regla in REGLAS_IMPLICITAS:
        if not re.search(regla.patron, texto_n, re.I):
            continue
        if canonicalizar_ley(regla.norma) in explicitas_canon:
            continue
        resultado.append(
            ReferenciaNorma(
                nombre_detectado=regla.norma,
                alcance=regla.alcance,
                explicita=False,
            )
        )
    return resultado


def valor_unidad(valor: str) -> str:
    v = normalizar(valor)
    if v == "preliminar":
        return "preliminar"
    v = v.replace("º", "").replace("ª", "").replace(".", "").strip()
    if v in PALABRAS_ORDINALES:
        return str(PALABRAS_ORDINALES[v])
    if v.isdigit():
        return str(int(v))

    romanos = {
        "i": 1, "v": 5, "x": 10, "l": 50,
        "c": 100, "d": 500, "m": 1000,
    }
    if v and all(c in romanos for c in v):
        total = 0
        anterior = 0
        for c in reversed(v):
            actual = romanos[c]
            if actual < anterior:
                total -= actual
            else:
                total += actual
                anterior = actual
        return str(total)
    return v


def normalizar_articulo(valor: str) -> str:
    return limpiar(valor).lower().replace(",", ".").replace("quater", "quáter")


def expandir_articulos(expresion: str) -> list[str]:
    expresion = normalizar_articulo(expresion)
    m = re.fullmatch(r"(\d+)\s*(?:a|al|-)\s*(\d+)", expresion)
    if m:
        ini, fin = int(m.group(1)), int(m.group(2))
        if ini <= fin:
            return [str(n) for n in range(ini, fin + 1)]

    valores = re.findall(
        r"\d+(?:\.\d+)?(?:\s+(?:bis|ter|quáter|quinquies|sexies|"
        r"septies|octies|nonies|decies))?",
        expresion,
        flags=re.I,
    )
    return [normalizar_articulo(v) for v in valores]


def exclusiones_de_alcance(alcance: str) -> tuple[str, ...]:
    m = re.search(
        r"excepto\s+los\s+art[íi]culos?\s+([^);.]+)",
        alcance,
        re.I,
    )
    return tuple(expandir_articulos(m.group(1))) if m else ()


def crear_selectores(alcance: str) -> tuple[list[Selector], int]:
    """
    Devuelve selectores y nivel de precisión:
      3 = artículos explícitos
      2 = unidad estructural (título/capítulo/sección...)
      1 = norma completa
      0 = alcance no resoluble
    """
    alcance = limpiar(alcance)
    excluir = exclusiones_de_alcance(alcance)

    explicitos: list[str] = []
    for m in PATRON_ARTICULOS.finditer(alcance):
        previo = normalizar(alcance[max(0, m.start() - 20):m.start()])
        if "excepto" in previo:
            continue
        explicitos.extend(expandir_articulos(m.group(1)))
    if explicitos:
        return [Selector({}, tuple(dict.fromkeys(explicitos)), excluir)], 3

    fragmentos = [
        limpiar(x)
        for x in re.split(r";|\.(?=\s+[A-ZÁÉÍÓÚ])", alcance)
        if limpiar(x)
    ]
    contexto: dict[str, str] = {}
    selectores: list[Selector] = []

    for fragmento in fragmentos:
        unidades = list(PATRON_UNIDAD.finditer(fragmento))
        if not unidades:
            continue
        for unidad in unidades:
            tipo = normalizar(unidad.group(1))
            valor = valor_unidad(unidad.group(2))
            nivel = TIPOS_JERARQUIA[tipo]
            for existente in list(contexto):
                if TIPOS_JERARQUIA[existente] >= nivel:
                    contexto.pop(existente, None)
            contexto[tipo] = valor

        tipo_hoja = normalizar(unidades[-1].group(1))
        ruta = {
            tipo: valor
            for tipo, valor in contexto.items()
            if TIPOS_JERARQUIA[tipo] <= TIPOS_JERARQUIA[tipo_hoja]
        }
        selectores.append(Selector(ruta, (), excluir))

    if selectores:
        unicos: list[Selector] = []
        vistos: set[tuple[tuple[str, str], ...]] = set()
        for selector in selectores:
            clave = tuple(sorted(selector.ruta.items()))
            if clave not in vistos:
                vistos.add(clave)
                unicos.append(selector)
        return unicos, 2

    if not alcance or ":" not in alcance:
        return [Selector({}, (), excluir)], 1

    return [], 0


def seleccionar_articulos(indice, selectores: list[Selector]) -> list[str]:
    resultado: list[str] = []
    for selector in selectores:
        if selector.articulos_explicitos:
            candidatos = list(selector.articulos_explicitos)
        else:
            candidatos = [
                nodo.articulo
                for nodo in indice
                if all(nodo.ruta.get(k) == v for k, v in selector.ruta.items())
            ]
        excluidos = set(selector.excluir)
        resultado.extend(a for a in candidatos if a not in excluidos)
    return list(dict.fromkeys(resultado))


def tema_es_no_juridico(tema: Tema, referencias: list[ReferenciaNorma]) -> bool:
    if referencias:
        return False
    texto_n = normalizar(tema.texto)
    indicadores = (
        "windows", "word", "excel", "outlook", "teams", "onedrive",
        "navegador", "informatica", "inteligencia artificial",
        "seguridad digital",
    )
    return any(x in texto_n for x in indicadores)


def procesar_tema(
    localizador,
    tema: Tema,
    titulo: str,
    cache_indices: dict[str, tuple[object, list[object]]],
    incidencias: list[str],
) -> list[Candidato]:
    explicitas = detectar_referencias_explicitas(tema.texto)
    implicitas = detectar_referencias_implicitas(tema.texto, explicitas)
    referencias = explicitas + implicitas

    if tema_es_no_juridico(tema, referencias):
        return [
            Candidato(
                tema.parte, tema.numero, titulo,
                "No aplicable", "No aplicable", "NO_JURIDICO",
                True, 999, tema.orden,
            )
        ]

    candidatos: list[Candidato] = []

    for referencia in referencias:
        ley_csv = nombre_ley_csv(referencia.nombre_detectado)
        try:
            clave_cache = canonicalizar_ley(referencia.nombre_detectado)
            dato_cache = cache_indices.get(clave_cache)
            if dato_cache is None:
                dato_cache = localizador.obtener_indice(
                    referencia.nombre_detectado
                )
                cache_indices[clave_cache] = dato_cache
            _, indice = dato_cache

            selectores, precision = crear_selectores(referencia.alcance)
            if not selectores:
                incidencias.append(
                    f"{tema.parte} {tema.numero}: alcance no resoluble para "
                    f"{ley_csv}: {referencia.alcance!r}"
                )
                continue

            articulos = seleccionar_articulos(indice, selectores)
            if not articulos:
                incidencias.append(
                    f"{tema.parte} {tema.numero}: {ley_csv} no produjo artículos."
                )
                continue

            score = precision * 10 + (5 if referencia.explicita else 0)
            for articulo in articulos:
                candidatos.append(
                    Candidato(
                        tema.parte,
                        tema.numero,
                        titulo,
                        ley_csv,
                        normalizar_articulo(articulo),
                        "JURIDICO",
                        referencia.explicita,
                        score,
                        tema.orden,
                    )
                )
        except Exception as exc:
            incidencias.append(
                f"{tema.parte} {tema.numero}: no se pudo resolver "
                f"{ley_csv}: {exc}"
            )

    unicos: dict[tuple[str, str], Candidato] = {}
    for cand in candidatos:
        clave = (canonicalizar_ley(cand.ley), cand.articulo)
        anterior = unicos.get(clave)
        if anterior is None or cand.precision > anterior.precision:
            unicos[clave] = cand
    return list(unicos.values())


def resolver_colisiones_globales(
    candidatos: list[Candidato],
    incidencias: list[str],
) -> list[Candidato]:
    juridicos = [c for c in candidatos if c.tipo == "JURIDICO"]
    otros = [c for c in candidatos if c.tipo != "JURIDICO"]

    amplitud: Counter[tuple[str, int, str]] = Counter(
        (c.parte, c.tema, canonicalizar_ley(c.ley))
        for c in juridicos
    )
    for c in juridicos:
        c.amplitud_norma_tema = amplitud[
            (c.parte, c.tema, canonicalizar_ley(c.ley))
        ]

    grupos: dict[tuple[str, str], list[Candidato]] = defaultdict(list)
    for cand in juridicos:
        grupos[(canonicalizar_ley(cand.ley), cand.articulo)].append(cand)

    elegidos: list[Candidato] = []
    for _, grupo in grupos.items():
        temas = {(c.parte, c.tema) for c in grupo}
        if len(temas) == 1:
            elegidos.append(max(grupo, key=lambda c: c.precision))
            continue

        ganador = sorted(
            grupo,
            key=lambda c: (
                -c.precision,
                c.amplitud_norma_tema,
                c.orden_tema,
            ),
        )[0]
        elegidos.append(ganador)
        perdedores = sorted(
            {(c.parte, c.tema) for c in grupo if c is not ganador}
        )
        incidencias.append(
            "COLISIÓN resuelta "
            f"{ganador.ley} art. {ganador.articulo}: "
            f"asignado a {ganador.parte} {ganador.tema}; "
            f"descartado en {perdedores}."
        )

    return elegidos + otros


def completar_no_determinados(
    temas: list[Tema],
    titulos: dict[tuple[str, int], str],
    candidatos: list[Candidato],
) -> list[Candidato]:
    con_filas = {(c.parte, c.tema) for c in candidatos}
    resultado = list(candidatos)
    for tema in temas:
        clave = (tema.parte, tema.numero)
        if clave in con_filas:
            continue
        resultado.append(
            Candidato(
                tema.parte,
                tema.numero,
                titulos[clave],
                "No determinada",
                "No determinados",
                "JURIDICO",
                False,
                0,
                tema.orden,
            )
        )
    return resultado


def validar_invariantes(
    temas: list[Tema],
    candidatos: list[Candidato],
) -> None:
    esperados = {(t.parte, t.numero) for t in temas}
    presentes = {(c.parte, c.tema) for c in candidatos}
    if presentes != esperados:
        faltan = sorted(esperados - presentes)
        sobran = sorted(presentes - esperados)
        raise ExtraccionError(
            f"Cobertura inválida. Faltan={faltan}; sobran={sobran}"
        )

    asignaciones: dict[tuple[str, str], set[tuple[str, int]]] = defaultdict(set)
    for c in candidatos:
        if c.tipo != "JURIDICO" or c.ley == "No determinada":
            continue
        asignaciones[(canonicalizar_ley(c.ley), c.articulo)].add(
            (c.parte, c.tema)
        )
    colisiones = {
        clave: temas
        for clave, temas in asignaciones.items()
        if len(temas) > 1
    }
    if colisiones:
        muestra = list(colisiones.items())[:10]
        raise ExtraccionError(
            "Persisten combinaciones LEY+artículo en varios temas: "
            f"{muestra}"
        )

    for tema in temas:
        filas = [
            c for c in candidatos
            if c.parte == tema.parte and c.tema == tema.numero
        ]
        no_det = [c for c in filas if c.ley == "No determinada"]
        if no_det and len(filas) != 1:
            raise ExtraccionError(
                f"{tema.parte} {tema.numero}: 'No determinada' debe ser "
                "la única fila del punto."
            )


def ordenar_filas(candidatos: Iterable[Candidato]) -> list[dict[str, str]]:
    orden_parte = {"GENERAL": 0, "ESPECIAL": 1}

    def clave_articulo(articulo: str):
        m = re.match(
            r"(\d+)(?:\.(\d+))?(?:\s+(bis|ter|quáter|quinquies|.*))?$",
            articulo,
        )
        if not m:
            return (10**9, 0, articulo)
        return (
            int(m.group(1)),
            int(m.group(2) or 0),
            m.group(3) or "",
        )

    ordenados = sorted(
        candidatos,
        key=lambda c: (
            orden_parte.get(c.parte, 9),
            c.tema,
            c.ley,
            clave_articulo(c.articulo),
            c.tipo,
        ),
    )
    return [c.fila() for c in ordenados]


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    with temporal.open("w", encoding=ENCODING_SALIDA, newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(filas)
    temporal.replace(ruta)


def escribir_auditoria(
    ruta_csv: Path,
    temas: list[Tema],
    filas: list[dict[str, str]],
    incidencias: list[str],
) -> Path:
    ruta = ruta_csv.with_suffix(".auditoria.txt")
    juridicas = sum(
        f["tipo"] == "JURIDICO" and f["LEY"] != "No determinada"
        for f in filas
    )
    no_determinados = [
        (f["parte"], f["tema"])
        for f in filas
        if f["LEY"] == "No determinada"
    ]
    no_juridicos = sum(f["tipo"] == "NO_JURIDICO" for f in filas)
    colisiones = sum(x.startswith("COLISIÓN") for x in incidencias)

    lineas = [
        "EXTRACCIÓN TEMARIO EXPLÍCITA/IMPLÍCITA",
        f"Temas detectados: {len(temas)}",
        f"Filas totales: {len(filas)}",
        f"Filas jurídicas determinadas: {juridicas}",
        f"Temas no determinados: {len(no_determinados)}",
        f"Temas no jurídicos: {no_juridicos}",
        f"Colisiones LEY+artículo resueltas: {colisiones}",
        "Colisiones LEY+artículo restantes: 0",
        "",
        "PUNTOS NO DETERMINADOS:",
    ]
    lineas.extend(
        f"- {parte} tema {tema}"
        for parte, tema in no_determinados
    )
    lineas += ["", "INCIDENCIAS / DECISIONES:"]
    lineas.extend(f"- {x}" for x in incidencias)
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def ejecutar(ruta_pdf: Path, ruta_salida: Path) -> None:
    localizador = cargar_localizador()
    temas = extraer_temas(extraer_texto_pdf(ruta_pdf))
    titulos = construir_titulos(temas)

    print(f"Temas detectados: {len(temas)}")
    cache_indices: dict[str, tuple[object, list[object]]] = {}
    incidencias: list[str] = []
    candidatos: list[Candidato] = []

    for tema in temas:
        print(f"Procesando {tema.parte} tema {tema.numero}...")
        candidatos.extend(
            procesar_tema(
                localizador,
                tema,
                titulos[(tema.parte, tema.numero)],
                cache_indices,
                incidencias,
            )
        )

    candidatos = resolver_colisiones_globales(candidatos, incidencias)
    candidatos = completar_no_determinados(temas, titulos, candidatos)
    validar_invariantes(temas, candidatos)

    filas = ordenar_filas(candidatos)
    escribir_csv(ruta_salida, filas)
    ruta_auditoria = escribir_auditoria(
        ruta_salida, temas, filas, incidencias
    )

    juridicas = sum(
        f["tipo"] == "JURIDICO" and f["LEY"] != "No determinada"
        for f in filas
    )
    no_determinados = sum(f["LEY"] == "No determinada" for f in filas)
    no_juridicas = sum(f["tipo"] == "NO_JURIDICO" for f in filas)

    print()
    print(f"CSV generado: {ruta_salida}")
    print(f"Auditoría: {ruta_auditoria}")
    print(f"Filas jurídicas determinadas: {juridicas}")
    print(f"Temas no determinados: {no_determinados}")
    print(f"Temas no jurídicos: {no_juridicas}")
    print("Duplicados LEY+artículo entre temas: 0")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extracción de temario explícita/implícita desde PDF a temario.csv, "
            "sin duplicar una combinación LEY+artículo entre temas."
        )
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=RUTA_PDF_PREDETERMINADA,
        help=f"PDF de entrada. Predeterminado: {RUTA_PDF_PREDETERMINADA}",
    )
    parser.add_argument(
        "--salida",
        type=Path,
        default=RUTA_SALIDA_PREDETERMINADA,
        help=f"CSV de salida. Predeterminado: {RUTA_SALIDA_PREDETERMINADA}",
    )
    args = parser.parse_args()

    try:
        ejecutar(args.pdf.resolve(), args.salida.resolve())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
