"""
OpoCoach - Extracción de un temario de convocatoria a temario.csv.

Ubicación prevista:
    scripts/extraer_temario_convocatoria.py

Uso normal:
    python scripts/extraer_temario_convocatoria.py

Uso con rutas explícitas:
    python scripts/extraer_temario_convocatoria.py ^
        --pdf data_convocatorias/CONV_C2-01_70_26/temario_70_26.pdf ^
        --salida data_convocatorias/CONV_C2-01_70_26/temario.csv

Salida:
    parte,tema,titulo,LEY,articulo,tipo

Criterios:
- Un tema no jurídico genera una fila NO_JURIDICO.
- Cada artículo jurídico genera una fila JURIDICO.
- Una referencia que no pueda resolverse de forma segura genera una fila
  PENDIENTE, sin inventar artículos.
- Los artículos se obtienen del índice consolidado del BOE.
- Títulos, capítulos, secciones y subsecciones se interpretan por límites
  estructurales del índice, no mediante rangos codificados manualmente.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
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
RUTA_BOE_API = RAIZ_PROYECTO / "scripts" / "boe_api.py"
RUTA_LOCALIZADOR = RAIZ_PROYECTO / "scripts" / "localizador_normativa.py"

TIMEOUT = 40
ENCODING_SALIDA = "cp1252"

COLUMNAS = ["parte", "tema", "titulo", "LEY", "articulo", "tipo"]

# Alias cuyo nombre no contiene una referencia tipo + número/año.
# Solo se incluyen identificadores verificados.
IDS_BOE_ALIAS = {
    "constitucion espanola": "BOE-A-1978-31229",
    "constitucion espanola de 1978": "BOE-A-1978-31229",
}

NOMBRES_LEY_ALIAS = {
    "constitucion espanola": "Constitucion Española",
    "constitucion espanola de 1978": "Constitucion Española",
}

TITULOS_TEMA_ALIAS = {
    "constitucion espanola": "Constitucion Española 1978",
    "constitucion espanola de 1978": "Constitucion Española 1978",
    "estatuto de autonomia de la comunitat valenciana":
        "Estatuto de autonomia de la Comunitat Valenciana",
}

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
    r"\bart[ií]culos?\s+"
    r"((?:\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quinquies|sexies|"
    r"septies|octies|nonies|decies))?)"
    r"(?:\s*(?:a|al|-|y|,)\s*"
    r"\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quinquies|sexies|"
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
    r"|Decreto\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Orden\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r"|Ley\s+\d+/\d{4}(?:,\s+de\s+\d{1,2}\s+de\s+\w+)?"
    r")",
    re.IGNORECASE,
)


class ExtraccionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tema:
    parte: str
    numero: int
    texto: str


@dataclass(frozen=True)
class ReferenciaNorma:
    nombre_detectado: str
    alcance: str


@dataclass(frozen=True)
class NodoIndice:
    articulo: str
    ruta: dict[str, str]


@dataclass(frozen=True)
class Selector:
    ruta: dict[str, str]
    articulos_explicitos: tuple[str, ...] = ()
    excluir: tuple[str, ...] = ()


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip(" .,:;")


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def cargar_boe_api():
    if not RUTA_BOE_API.exists():
        raise ExtraccionError(f"No existe el módulo de apoyo: {RUTA_BOE_API}")
    spec = importlib.util.spec_from_file_location("opocoach_boe_api", RUTA_BOE_API)
    if spec is None or spec.loader is None:
        raise ExtraccionError(f"No se pudo cargar {RUTA_BOE_API}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def cargar_localizador():
    if not RUTA_LOCALIZADOR.exists():
        raise ExtraccionError(
            f"No existe el módulo especializado: {RUTA_LOCALIZADOR}"
        )

    spec = importlib.util.spec_from_file_location(
        "opocoach_localizador_normativa",
        RUTA_LOCALIZADOR,
    )
    if spec is None or spec.loader is None:
        raise ExtraccionError(f"No se pudo cargar {RUTA_LOCALIZADOR}")

    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def extraer_texto_pdf(ruta_pdf: Path) -> str:
    """Extrae texto sin OCR. Prueba PyMuPDF, pypdf y pdftotext."""
    if not ruta_pdf.exists():
        raise ExtraccionError(f"No existe el PDF: {ruta_pdf}")

    try:
        import fitz  # type: ignore
        with fitz.open(ruta_pdf) as doc:
            texto = "\n".join(pagina.get_text("text") for pagina in doc)
        if texto.strip():
            return texto
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore
        lector = PdfReader(str(ruta_pdf))
        texto = "\n".join(pagina.extract_text() or "" for pagina in lector.pages)
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
        if l.startswith("CVE:"):
            continue
        if "https://dogv.gva.es" in l:
            continue
        if re.match(r"^Anexo\s+[IVXLCDM]+$", l, re.I):
            continue
        if re.match(r"^Convocatoria\s+\d+/\d+$", l, re.I):
            continue
        if re.match(r"^[A-Z]\d?-\d+\.\s+Cuerpo\b", l, re.I):
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
    temas: list[Tema] = []
    parte_actual = ""

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
            temas.append(Tema(parte_actual, numero, contenido))

    # Deduplicación defensiva.
    unicos: dict[tuple[str, int], Tema] = {}
    for tema in temas:
        unicos[(tema.parte, tema.numero)] = tema
    return list(unicos.values())


def detectar_referencias_normativas(texto: str) -> list[ReferenciaNorma]:
    encontrados = list(PATRON_NORMA.finditer(texto))
    referencias: list[ReferenciaNorma] = []
    for i, m in enumerate(encontrados):
        inicio_alcance = m.end()
        fin_alcance = encontrados[i + 1].start() if i + 1 < len(encontrados) else len(texto)
        alcance = limpiar(texto[inicio_alcance:fin_alcance]).lstrip(" ,:")
        referencias.append(
            ReferenciaNorma(
                nombre_detectado=limpiar(m.group(1)),
                alcance=alcance,
            )
        )
    return referencias


def nombre_ley_csv(nombre_detectado: str) -> str:
    clave = normalizar(nombre_detectado)
    if clave in NOMBRES_LEY_ALIAS:
        return NOMBRES_LEY_ALIAS[clave]

    m = re.match(
        r"(Ley Org[aá]nica|Ley|Real Decreto Legislativo|Real Decreto(?:-ley|\s+ley)?|"
        r"Decreto Legislativo|Decreto|Orden)\s+(\d+/\d{4})"
        r"(?:,\s+de\s+(\d{1,2}\s+de\s+\w+))?",
        nombre_detectado,
        re.I,
    )
    if not m:
        return limpiar(nombre_detectado)

    tipo = limpiar(m.group(1))
    tipo = re.sub(r"Org[aá]nica", "Organica", tipo, flags=re.I)
    resultado = f"{tipo} {m.group(2)}"
    if m.group(3):
        resultado += f", de {m.group(3)}"
    return resultado


def titulo_tema_csv(tema: Tema, referencias: list[ReferenciaNorma]) -> str:
    texto = limpiar(tema.texto)
    texto_n = normalizar(texto)
    texto_sin_articulo = re.sub(r"^(?:la|el)\s+", "", texto_n)

    for clave, titulo in TITULOS_TEMA_ALIAS.items():
        if texto_n.startswith(clave) or texto_sin_articulo.startswith(clave):
            return titulo

    if referencias:
        primera = referencias[0].nombre_detectado
        posicion = texto.lower().find(primera.lower())
        prefijo = limpiar(texto[:posicion]).strip(" .:-") if posicion > 0 else ""
        prefijo = re.sub(r"\s+(?:La|El)$", "", prefijo).strip(" .:-")
        if prefijo and len(prefijo) >= 12:
            return prefijo
        fin = texto.find(":", posicion + len(primera))
        if fin != -1:
            return limpiar(texto[:fin]).strip(" .")
        return limpiar(primera)

    return texto


def valor_unidad(valor: str) -> str:
    v = normalizar(valor)
    if v == "preliminar":
        return "preliminar"
    v = v.replace("º", "").replace("ª", "").replace(".", "").strip()
    if v in PALABRAS_ORDINALES:
        return str(PALABRAS_ORDINALES[v])
    if v.isdigit():
        return str(int(v))

    romanos = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
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


def tipo_unidad(tipo: str) -> str:
    return normalizar(tipo)


def normalizar_articulo(valor: str) -> str:
    return limpiar(valor).lower().replace(",", ".")


def expandir_articulos(expresion: str) -> list[str]:
    expresion = normalizar_articulo(expresion)
    # Rangos simples N a M / N-M / N al M.
    m = re.fullmatch(r"(\d+)\s*(?:a|al|-)\s*(\d+)", expresion)
    if m:
        ini, fin = int(m.group(1)), int(m.group(2))
        if ini <= fin:
            return [str(n) for n in range(ini, fin + 1)]

    valores = re.findall(
        r"\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quinquies|sexies|"
        r"septies|octies|nonies|decies))?",
        expresion,
        flags=re.I,
    )
    return [normalizar_articulo(v) for v in valores]


def exclusiones_de_alcance(alcance: str) -> tuple[str, ...]:
    m = re.search(
        r"excepto\s+los\s+art[ií]culos?\s+([^);.]+)",
        alcance,
        re.I,
    )
    if not m:
        return ()
    return tuple(expandir_articulos(m.group(1)))


def crear_selectores(alcance: str) -> list[Selector]:
    """
    Convierte una cita jerárquica en selectores.

    Se divide por punto y coma. Cada fragmento hereda la jerarquía anterior.
    Se selecciona la unidad más profunda del fragmento:
      Título III: Capítulo I  -> Capítulo I dentro del Título III
      Capítulo II            -> Capítulo II dentro del mismo Título III
    """
    alcance = limpiar(alcance)
    excluir = exclusiones_de_alcance(alcance)

    explicitos: list[str] = []
    for m in PATRON_ARTICULOS.finditer(alcance):
        if "excepto" in normalizar(alcance[max(0, m.start() - 20):m.start()]):
            continue
        explicitos.extend(expandir_articulos(m.group(1)))
    if explicitos:
        return [Selector({}, tuple(dict.fromkeys(explicitos)), excluir)]

    fragmentos = [limpiar(x) for x in re.split(r";|\.(?=\s+[A-ZÁÉÍÓÚ])", alcance) if limpiar(x)]
    contexto: dict[str, str] = {}
    selectores: list[Selector] = []

    for fragmento in fragmentos:
        unidades = list(PATRON_UNIDAD.finditer(fragmento))
        if not unidades:
            continue

        for unidad in unidades:
            tipo = tipo_unidad(unidad.group(1))
            valor = valor_unidad(unidad.group(2))
            nivel = TIPOS_JERARQUIA[tipo]
            # Al cambiar una unidad, se eliminan niveles inferiores.
            for existente in list(contexto):
                if TIPOS_JERARQUIA[existente] >= nivel:
                    contexto.pop(existente, None)
            contexto[tipo] = valor

        tipo_hoja = tipo_unidad(unidades[-1].group(1))
        ruta = {
            tipo: valor
            for tipo, valor in contexto.items()
            if TIPOS_JERARQUIA[tipo] <= TIPOS_JERARQUIA[tipo_hoja]
        }
        selectores.append(Selector(ruta, (), excluir))

    if not selectores and ":" not in alcance:
        # La norma se cita sin limitarla por título/capítulo/sección: se toma
        # el articulado completo, como en el CSV de referencia.
        return [Selector({}, (), excluir)]

    # Deduplicación manteniendo orden.
    resultado: list[Selector] = []
    vistos: set[tuple[tuple[str, str], ...]] = set()
    for selector in selectores:
        clave = tuple(sorted(selector.ruta.items()))
        if clave not in vistos:
            resultado.append(selector)
            vistos.add(clave)
    return resultado


def resolver_norma(boe_api, nombre_detectado: str) -> tuple[str, str]:
    clave = normalizar(nombre_detectado)
    id_alias = IDS_BOE_ALIAS.get(clave)
    if id_alias:
        return id_alias, nombre_ley_csv(nombre_detectado)

    norma = boe_api.buscar_norma(nombre_detectado)
    return norma.id_boe, nombre_ley_csv(nombre_detectado)


def descargar_indice_boe(id_boe: str) -> str:
    respuesta = requests.get(
        "https://www.boe.es/buscar/act.php",
        params={"id": id_boe, "tn": "2"},
        timeout=TIMEOUT,
        headers={"User-Agent": "OpoCoach/2.0"},
    )
    respuesta.raise_for_status()
    return respuesta.text


def es_texto_articulo(texto: str) -> str | None:
    m = re.fullmatch(
        r"Art[ií]culo\s+(\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quinquies|"
        r"sexies|septies|octies|nonies|decies))?)\.?",
        limpiar(texto),
        re.I,
    )
    return normalizar_articulo(m.group(1)) if m else None


def interpretar_encabezado(texto: str) -> tuple[str, str] | None:
    m = PATRON_UNIDAD.search(limpiar(texto))
    if not m or m.start() != 0:
        return None
    return tipo_unidad(m.group(1)), valor_unidad(m.group(2))


def construir_indice(html: str) -> list[NodoIndice]:
    soup = BeautifulSoup(html, "html.parser")
    ruta: dict[str, str] = {}
    articulos: list[NodoIndice] = []

    for enlace in soup.find_all("a"):
        texto = limpiar(enlace.get_text(" ", strip=True))
        if not texto:
            continue

        articulo = es_texto_articulo(texto)
        if articulo:
            articulos.append(NodoIndice(articulo, dict(ruta)))
            continue

        encabezado = interpretar_encabezado(texto)
        if not encabezado:
            continue

        tipo, valor = encabezado
        nivel = TIPOS_JERARQUIA[tipo]
        for existente in list(ruta):
            if TIPOS_JERARQUIA[existente] >= nivel:
                ruta.pop(existente, None)
        ruta[tipo] = valor

    if not articulos:
        raise ExtraccionError("El BOE no devolvió un índice interpretable.")
    return articulos


def coincide_ruta(ruta_articulo: dict[str, str], ruta_selector: dict[str, str]) -> bool:
    return all(ruta_articulo.get(k) == v for k, v in ruta_selector.items())


def seleccionar_articulos(
    indice: list[NodoIndice],
    selectores: list[Selector],
) -> list[str]:
    if not selectores:
        return []

    resultado: list[str] = []
    for selector in selectores:
        if selector.articulos_explicitos:
            candidatos = list(selector.articulos_explicitos)
        else:
            candidatos = [
                nodo.articulo
                for nodo in indice
                if coincide_ruta(nodo.ruta, selector.ruta)
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


def fila_pendiente(
    tema: Tema,
    titulo: str,
    ley: str = "",
    motivo: str = "",
) -> dict[str, str]:
    # El motivo se imprime en consola; no se añade una columna distinta,
    # para conservar exactamente el formato del CSV importable.
    if motivo:
        print(
            f"[PENDIENTE] {tema.parte} tema {tema.numero}: {motivo}",
            file=sys.stderr,
        )
    return {
        "parte": tema.parte,
        "tema": str(tema.numero),
        "titulo": titulo,
        "LEY": ley,
        "articulo": "",
        "tipo": "PENDIENTE",
    }


def procesar_tema(
    localizador,
    tema: Tema,
    cache_indices: dict[str, tuple[object, list[object]]],
) -> list[dict[str, str]]:
    referencias = detectar_referencias_normativas(tema.texto)
    titulo = titulo_tema_csv(tema, referencias)

    if tema_es_no_juridico(tema, referencias):
        return [{
            "parte": tema.parte,
            "tema": str(tema.numero),
            "titulo": titulo,
            "LEY": "",
            "articulo": "",
            "tipo": "NO_JURIDICO",
        }]

    if not referencias:
        return [fila_pendiente(
            tema, titulo, motivo="no se detectó una norma jurídica inequívoca"
        )]

    filas: list[dict[str, str]] = []

    for referencia in referencias:
        ley_csv = nombre_ley_csv(referencia.nombre_detectado)

        try:
            clave_cache = normalizar(referencia.nombre_detectado)
            dato_cache = cache_indices.get(clave_cache)

            if dato_cache is None:
                norma, indice = localizador.obtener_indice(
                    referencia.nombre_detectado
                )
                dato_cache = (norma, indice)
                cache_indices[clave_cache] = dato_cache
            else:
                norma, indice = dato_cache

            selectores = crear_selectores(referencia.alcance)
            articulos = seleccionar_articulos(indice, selectores)

            if not articulos:
                filas.append(fila_pendiente(
                    tema,
                    titulo,
                    ley_csv,
                    "la norma se localizó, pero el alcance no produjo artículos",
                ))
                continue

            for articulo in articulos:
                filas.append({
                    "parte": tema.parte,
                    "tema": str(tema.numero),
                    "titulo": titulo,
                    "LEY": ley_csv,
                    "articulo": articulo,
                    "tipo": "JURIDICO",
                })

        except Exception as exc:
            filas.append(fila_pendiente(
                tema,
                titulo,
                ley_csv,
                f"no se pudo resolver {referencia.nombre_detectado}: {exc}",
            ))

    unicas: dict[tuple[str, str, str, str, str, str], dict[str, str]] = {}
    for fila in filas:
        clave = tuple(fila[c] for c in COLUMNAS)
        unicas[clave] = fila

    return list(unicas.values())

def ordenar_filas(filas: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    orden_parte = {"GENERAL": 0, "ESPECIAL": 1}

    def clave(fila: dict[str, str]):
        art = fila["articulo"]
        m = re.match(r"(\d+)(?:\.(\d+))?(?:\s+(.*))?$", art)
        if m:
            articulo_orden = (
                int(m.group(1)),
                int(m.group(2) or 0),
                m.group(3) or "",
            )
        else:
            articulo_orden = (10**9, 0, art)
        return (
            orden_parte.get(fila["parte"], 9),
            int(fila["tema"]),
            fila["LEY"],
            articulo_orden,
            fila["tipo"],
        )

    return sorted(filas, key=clave)


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    with temporal.open("w", encoding=ENCODING_SALIDA, newline="", errors="replace") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(filas)
    temporal.replace(ruta)


def ejecutar(ruta_pdf: Path, ruta_salida: Path) -> None:
    localizador = cargar_localizador()
    temas = extraer_temas(extraer_texto_pdf(ruta_pdf))
    if not temas:
        raise ExtraccionError("No se detectó ningún tema en el PDF.")

    print(f"Temas detectados: {len(temas)}")
    cache_indices: dict[str, tuple[object, list[object]]] = {}
    filas: list[dict[str, str]] = []

    for tema in temas:
        print(f"Procesando {tema.parte} tema {tema.numero}...")
        filas.extend(procesar_tema(localizador, tema, cache_indices))

    filas = ordenar_filas(filas)
    escribir_csv(ruta_salida, filas)

    juridicas = sum(f["tipo"] == "JURIDICO" for f in filas)
    no_juridicas = sum(f["tipo"] == "NO_JURIDICO" for f in filas)
    pendientes = sum(f["tipo"] == "PENDIENTE" for f in filas)

    print()
    print(f"CSV generado: {ruta_salida}")
    print(f"Filas jurídicas: {juridicas}")
    print(f"Filas no jurídicas: {no_juridicas}")
    print(f"Filas pendientes: {pendientes}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrae el temario de una convocatoria y genera temario.csv."
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
