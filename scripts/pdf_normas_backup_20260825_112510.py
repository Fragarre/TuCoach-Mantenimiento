"""
OpoCoach - Proveedor local de artículos normativos desde PDF.

Fallback conservador para fuentes_normativas/.

Principios:
- No usa OCR ni Internet.
- Primero conserva el mapa histórico de PDF conocidos.
- Si la norma no está en el mapa, busca de forma automática un único PDF
  compatible por identidad normativa/título.
- Nunca elige entre candidatos ambiguos.
- Los PDF se reutilizan tanto para resolver artículos del temario como para
  completar una norma entera para el RAG.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf as fitz
from boe_api import ArticuloBOE, BOEError, NormaBOE

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
DIRECTORIO_PDFS = RAIZ_PROYECTO / "fuentes_normativas"

MAPA_PDFS = {
    "decreto|30|2025": {
        "archivo": "Decreto 30_2025.pdf",
        "titulo": (
            "Decreto 30/2025, de 25 de febrero, del Consell, por el que "
            "se regula la atención a la ciudadanía y las oficinas de "
            "asistencia en materia de registro en la Administración y el "
            "sector público instrumental de la Generalitat."
        ),
        "departamento": "Generalitat Valenciana",
        "id_fuente": "LOCAL-DOGV-DECRETO-30-2025",
    },
    "decreto|42|2019": {
        "archivo": "Decreto 42_2019.pdf",
        "titulo": (
            "Decreto 42/2019, de 22 de marzo, del Consell, de regulación "
            "de las condiciones de trabajo del personal funcionario de "
            "la Administración de la Generalitat."
        ),
        "departamento": "Generalitat Valenciana",
        "id_fuente": "LOCAL-DOGV-DECRETO-42-2019",
    },
    "decreto|54|2025": {
        "archivo": "Decreto 54_2025.pdf",
        "titulo": (
            "Decreto 54/2025, de 15 de abril, del Consell, de "
            "simplificación administrativa y transformación digital."
        ),
        "departamento": "Generalitat Valenciana",
        "id_fuente": "LOCAL-DOGV-DECRETO-54-2025",
    },
    "orden|19|2013": {
        "archivo": "Orden 19_2013.pdf",
        "titulo": (
            "Orden 19/2013, de 3 de diciembre, de la Conselleria de "
            "Hacienda y Administración Pública."
        ),
        "departamento": "Generalitat Valenciana",
        "id_fuente": "LOCAL-DOGV-ORDEN-19-2013",
    },
}


@dataclass(frozen=True)
class PDFNorma:
    ruta: Path
    id_fuente: str
    titulo: str
    departamento: str


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    valor = unicodedata.normalize("NFKD", texto)
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    valor = valor.lower().replace("º", "o").replace("ª", "a")
    valor = re.sub(r"[^a-z0-9]+", " ", valor)
    return re.sub(r"\s+", " ", valor).strip()


def limpiar_texto(texto: str) -> str:
    lineas: list[str] = []
    for linea in texto.replace("\r", "\n").splitlines():
        linea = re.sub(r"\s+", " ", linea).strip()
        if not linea:
            continue
        if re.fullmatch(r"Núm\.\s+\d+\s*/\s*\d{2}\.\d{2}\.\d{4}.*", linea):
            continue
        if linea.startswith("CVE: ") or linea == "https://dogv.gva.es/":
            continue
        if re.fullmatch(r"Página\s+\d+", linea, flags=re.I):
            continue
        if linea in {"BOLETÍN OFICIAL DEL ESTADO", "LEGISLACIÓN CONSOLIDADA"}:
            continue
        lineas.append(linea)
    return "\n".join(lineas).strip()


def _extraer_clave_mapa(nombre_norma: str) -> str | None:
    texto = normalizar(nombre_norma)
    patrones = (
        (r"\bdecreto\s+(\d+)\s*[ /_-]\s*(\d{4})\b", "decreto"),
        (r"\borden\s+(\d+)\s*[ /_-]\s*(\d{4})\b", "orden"),
    )
    coincidencias: list[str] = []
    for patron, tipo in patrones:
        for numero, anio in re.findall(patron, texto):
            coincidencias.append(f"{tipo}|{int(numero)}|{anio}")
    coincidencias = sorted(set(coincidencias))
    if len(coincidencias) == 1 and coincidencias[0] in MAPA_PDFS:
        return coincidencias[0]
    return None


@lru_cache(maxsize=None)
def _texto_pdf(ruta_str: str) -> str:
    ruta = Path(ruta_str)
    try:
        with fitz.open(ruta) as documento:
            paginas = [pagina.get_text("text") for pagina in documento]
    except Exception as exc:
        raise BOEError(f"No se pudo leer el PDF local: {ruta}") from exc
    texto = "\n".join(paginas).replace("\r\n", "\n").replace("\r", "\n")
    if not texto.strip():
        raise BOEError(f"El PDF local no contiene texto extraíble: {ruta}")
    return texto


@lru_cache(maxsize=None)
def _cabecera_pdf(ruta_str: str) -> str:
    ruta = Path(ruta_str)
    try:
        with fitz.open(ruta) as documento:
            paginas = [documento[i].get_text("text") for i in range(min(8, len(documento)))]
    except Exception as exc:
        raise BOEError(f"No se pudo leer el PDF local: {ruta}") from exc
    return "\n".join(paginas)


def _titulo_desde_cabecera(cabecera: str, ruta: Path) -> str:
    nombre_archivo = normalizar(ruta.stem)
    if "tfue" in nombre_archivo:
        return "Tratado de Funcionamiento de la Unión Europea"
    if re.search(r"\btue\b", nombre_archivo):
        return "Tratado de la Unión Europea"
    lineas = [re.sub(r"\s+", " ", x).strip() for x in cabecera.splitlines() if x.strip()]
    # BOE: el título suele ser la primera línea normativa.
    for i, linea in enumerate(lineas[:80]):
        if re.match(r"(?i)^(ley organica|ley|real decreto legislativo|real decreto|decreto legislativo|decreto|orden)\b", linea):
            titulo = linea
            # Une una continuación claramente cortada, sin arrastrar metadatos.
            if len(titulo) < 90 and i + 1 < len(lineas):
                sig = lineas[i + 1]
                if not re.match(r"(?i)^(jefatura|boe|referencia|indice|num\.|cve:|i\.)\b", normalizar(sig)):
                    titulo = f"{titulo} {sig}"
            return titulo.strip()
    n = normalizar(cabecera)
    if "tratado de funcionamiento de la union europea" in n:
        return "Tratado de Funcionamiento de la Unión Europea"
    if "tratado de la union europea" in n:
        return "Tratado de la Unión Europea"
    return ruta.stem


def _id_desde_cabecera(cabecera: str, titulo: str, ruta: Path) -> str:
    m = re.search(r"(?im)^\s*Referencia:\s*(BOE-A-\d{4}-\d+)\s*$", cabecera)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?im)^\s*CVE:\s*(DOGV-(?:[A-Z]-)?\d{4}-\d+)\b", cabecera)
    if m:
        return m.group(1).upper()
    nombre_archivo = normalizar(ruta.stem)
    nt = normalizar(titulo + " " + cabecera[:1200])
    if "tfue" in nombre_archivo or "tratado de funcionamiento de la union europea" in nt:
        return "DOUE-C-2010-083-TFUE"
    if re.search(r"\btue\b", nombre_archivo) or "tratado de la union europea" in nt:
        return "DOUE-C-2010-083-TUE"
    # Identidad local estable, solo como último recurso para un PDF inequívoco.
    slug = re.sub(r"[^A-Z0-9]+", "-", normalizar(ruta.stem).upper()).strip("-")
    return f"LOCAL-PDF-{slug}"


def _departamento_desde_cabecera(cabecera: str, id_fuente: str) -> str:
    lineas = [re.sub(r"\s+", " ", x).strip() for x in cabecera.splitlines() if x.strip()]
    if id_fuente.startswith("BOE-A-"):
        for linea in lineas[:30]:
            if linea in {"Jefatura del Estado", "Ministerio de la Presidencia", "Cortes Generales"}:
                return linea
        return "Boletín Oficial del Estado"
    if id_fuente.startswith("DOGV-") or id_fuente.startswith("LOCAL-DOGV-"):
        return "Generalitat Valenciana"
    if id_fuente.startswith("DOUE-"):
        return "Unión Europea"
    return "Fuente normativa local"


@lru_cache(maxsize=1)
def inventario_pdfs() -> tuple[PDFNorma, ...]:
    if not DIRECTORIO_PDFS.is_dir():
        return tuple()
    resultado: list[PDFNorma] = []
    vistos: set[Path] = set()

    # Mapa histórico primero, manteniendo identidad existente.
    for datos in MAPA_PDFS.values():
        ruta = DIRECTORIO_PDFS / datos["archivo"]
        if ruta.is_file():
            resultado.append(PDFNorma(ruta, datos["id_fuente"], datos["titulo"], datos["departamento"]))
            vistos.add(ruta.resolve())

    for ruta in sorted(DIRECTORIO_PDFS.glob("*.pdf")):
        if ruta.resolve() in vistos:
            continue
        cabecera = _cabecera_pdf(str(ruta.resolve()))
        titulo = _titulo_desde_cabecera(cabecera, ruta)
        id_fuente = _id_desde_cabecera(cabecera, titulo, ruta)
        departamento = _departamento_desde_cabecera(cabecera, id_fuente)
        resultado.append(PDFNorma(ruta.resolve(), id_fuente, titulo, departamento))
    return tuple(resultado)


def _extraer_tipo_numero_anio(texto: str) -> tuple[str, str, str] | None:
    n = normalizar(texto)
    patrones = (
        ("ley organica", r"\bley organica\s+(\d+)\s+(\d{4})\b"),
        ("ley", r"\bley\s+(\d+)\s+(\d{4})\b"),
        ("decreto", r"\bdecreto\s+(\d+)\s+(\d{4})\b"),
        ("orden", r"\borden\s+(\d+)\s+(\d{4})\b"),
    )
    for tipo, patron in patrones:
        m = re.search(patron, n)
        if m:
            return tipo, str(int(m.group(1))), m.group(2)
    return None


STOP = {
    "de", "del", "la", "el", "los", "las", "y", "por", "para", "en", "un", "una",
    "ley", "organica", "decreto", "orden", "real", "texto", "consolidado", "version",
    "vigente", "articulo", "generalitat", "consell", "comunitat", "comunidad", "valenciana",
}


def _tokens_significativos(texto: str) -> set[str]:
    return {t for t in normalizar(texto).split() if len(t) >= 3 and t not in STOP}


def _compatibilidad(nombre_norma: str, pdf: PDFNorma) -> float:
    q = normalizar(nombre_norma)
    d = normalizar(pdf.titulo + " " + pdf.ruta.stem + " " + pdf.id_fuente)

    # Tratados: identidad explícita, no similitud genérica.
    if "tfue" in q or "tratado de funcionamiento de la union europea" in q:
        return 1.0 if ("tfue" in d or "tratado de funcionamiento de la union europea" in d) else 0.0
    if re.search(r"\btue\b", q) or "tratado de la union europea" in q:
        return 1.0 if (re.search(r"\btue\b", d) or "tratado de la union europea" in d) else 0.0

    cita_q = _extraer_tipo_numero_anio(nombre_norma)
    cita_d = _extraer_tipo_numero_anio(pdf.titulo + " " + pdf.ruta.stem)
    if cita_q is not None:
        if cita_d != cita_q:
            return 0.0
        return 1.0

    tq = _tokens_significativos(nombre_norma)
    td = _tokens_significativos(pdf.titulo + " " + pdf.ruta.stem)
    if not tq:
        return 0.0
    comunes = tq & td
    cobertura = len(comunes) / len(tq)
    # Para denominaciones descriptivas exigimos coincidencia sustantiva fuerte.
    if len(comunes) < min(2, len(tq)):
        return 0.0
    return cobertura


def buscar_norma_local(nombre_norma: str) -> PDFNorma:
    clave = _extraer_clave_mapa(nombre_norma)
    if clave is not None:
        datos = MAPA_PDFS[clave]
        ruta = DIRECTORIO_PDFS / datos["archivo"]
        if not ruta.is_file():
            raise BOEError(f"No existe el PDF normativo local: {ruta}")
        return PDFNorma(ruta.resolve(), datos["id_fuente"], datos["titulo"], datos["departamento"])

    candidatos: list[tuple[float, PDFNorma]] = []
    for pdf in inventario_pdfs():
        puntuacion = _compatibilidad(nombre_norma, pdf)
        if puntuacion >= 0.70:
            candidatos.append((puntuacion, pdf))
    candidatos.sort(key=lambda x: (-x[0], x[1].ruta.name.lower()))
    if not candidatos:
        raise BOEError(f"No existe PDF local inequívoco para: {nombre_norma}")
    mejor = candidatos[0][0]
    mejores = [pdf for score, pdf in candidatos if abs(score - mejor) < 1e-9]

    # Puede haber varias copias físicas de la misma norma con identificadores
    # distintos (por ejemplo una copia antigua LOCAL-PDF-* y otra con CVE DOGV).
    # La identidad jurídica se determina primero por tipo + número + año o,
    # para los Tratados, por su identidad TUE/TFUE. Si todos los mejores
    # candidatos representan la misma norma, no existe ambigüedad jurídica.
    def identidad_juridica(pdf: PDFNorma) -> tuple[str, ...] | None:
        d = normalizar(pdf.titulo + " " + pdf.ruta.stem + " " + pdf.id_fuente)
        if "tfue" in d or "tratado de funcionamiento de la union europea" in d:
            return ("tratado", "tfue")
        if re.search(r"\btue\b", d) or "tratado de la union europea" in d:
            return ("tratado", "tue")
        cita = _extraer_tipo_numero_anio(pdf.titulo + " " + pdf.ruta.stem)
        if cita is not None:
            return ("norma", *cita)
        return None

    identidades = {identidad_juridica(pdf) for pdf in mejores}
    if len(identidades) == 1 and None not in identidades:
        # Preferir identificadores oficiales (BOE/DOGV/DOUE) frente a LOCAL-*
        # y, a igualdad, elegir de forma determinista.
        return sorted(
            mejores,
            key=lambda pdf: (
                1 if pdf.id_fuente.upper().startswith("LOCAL-") else 0,
                len(pdf.ruta.name),
                pdf.ruta.name.lower(),
            ),
        )[0]

    fuentes = {pdf.id_fuente.upper() for pdf in mejores}
    if len(fuentes) == 1:
        return sorted(mejores, key=lambda pdf: (len(pdf.ruta.name), pdf.ruta.name.lower()))[0]
    if len(mejores) != 1:
        raise BOEError(
            "Hay varios PDF locales igualmente compatibles con la norma y "
            "corresponden a normas distintas: "
            f"{nombre_norma}. Candidatos: {', '.join(p.ruta.name for p in mejores)}"
        )
    return mejores[0]


def buscar_norma_por_id(id_fuente: str) -> PDFNorma:
    objetivo = str(id_fuente or "").strip().upper()
    candidatos = [p for p in inventario_pdfs() if p.id_fuente.upper() == objetivo]
    if not candidatos:
        raise BOEError(f"No existe PDF local para la fuente: {id_fuente}")
    # Varias copias del mismo id_fuente son duplicados físicos, no fuentes
    # ambiguas. Se elige una de forma determinista.
    return sorted(candidatos, key=lambda pdf: (len(pdf.ruta.name), pdf.ruta.name.lower()))[0]


def buscar_norma(nombre_norma: str) -> NormaBOE:
    pdf = buscar_norma_local(nombre_norma)
    return NormaBOE(
        nombre_buscado=nombre_norma,
        id_boe=pdf.id_fuente,
        titulo=pdf.titulo,
        departamento=pdf.departamento,
    )


def tiene_pdf_local(nombre_norma: str) -> bool:
    try:
        buscar_norma_local(nombre_norma)
        return True
    except BOEError:
        return False


UNIDADES = {"uno": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9}
ESPECIALES = {
    "primero": 1, "segundo": 2, "tercero": 3, "cuarto": 4, "quinto": 5, "sexto": 6,
    "septimo": 7, "octavo": 8, "noveno": 9, "diez": 10, "decimo": 10, "once": 11,
    "undecimo": 11, "doce": 12, "duodecimo": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "cien": 100, "ciento": 100,
}
DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}


def _numero_palabras(texto: str) -> str | None:
    n = normalizar(texto).strip()
    if n in ESPECIALES:
        return str(ESPECIALES[n])
    if n in UNIDADES:
        return str(UNIDADES[n])
    partes = n.split()
    total = 0
    if partes and partes[0] in {"cien", "ciento"}:
        total = 100
        partes = partes[1:]
        if not partes:
            return str(total)
    if partes and partes[0] in DECENAS:
        total += DECENAS[partes[0]]
        partes = partes[1:]
        if partes and partes[0] == "y":
            partes = partes[1:]
        if partes:
            if len(partes) == 1 and partes[0] in UNIDADES:
                total += UNIDADES[partes[0]]
                partes = []
    elif partes and len(partes) == 1 and partes[0] in ESPECIALES:
        total += ESPECIALES[partes[0]]
        partes = []
    elif partes and len(partes) == 1 and partes[0] in UNIDADES:
        total += UNIDADES[partes[0]]
        partes = []
    if partes or total <= 0:
        return None
    return str(total)


def normalizar_articulo(articulo: str) -> str:
    valor = str(articulo or "").strip().replace(",", ".")
    if valor.upper() == "ANEXO":
        return "ANEXO"
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?", valor)
    if not m:
        raise BOEError(f"Artículo PDF no válido: {articulo!r}")
    return valor


def _encabezados(texto: str) -> list[tuple[int, int, str, str]]:
    patron = re.compile(r"(?im)^[ \t]*art[ií]culo[ \t]+([^\n.]+(?:\.[0-9]+)?)[ \t]*\.?[ \t]*([^\n]*)$")
    salida: list[tuple[int, int, str, str]] = []
    for m in patron.finditer(texto):
        bruto = m.group(1).strip()
        # Numérico, eventualmente 4.3.
        num = None
        mn = re.match(r"^(\d+(?:\.\d+)?)\b", bruto)
        if mn:
            num = mn.group(1)
        else:
            # Solo palabras del número; evita absorber la rúbrica.
            palabras = []
            for token in normalizar(bruto).split():
                if token in ESPECIALES or token in DECENAS or token in UNIDADES or token == "y":
                    palabras.append(token)
                else:
                    break
            if palabras:
                num = _numero_palabras(" ".join(palabras))
        if not num:
            continue
        salida.append((m.start(), m.end(), num, m.group(2).strip()))
    return salida


def _bloque_articulo(pdf: PDFNorma, articulo_solicitado: str) -> tuple[str, str, str]:
    solicitado = normalizar_articulo(articulo_solicitado)
    if solicitado == "ANEXO":
        raise BOEError("La extracción automática de ANEXO completo no está habilitada para PDF genérico.")
    base = solicitado.split(".", 1)[0]
    texto = _texto_pdf(str(pdf.ruta.resolve()))
    encabezados = _encabezados(texto)
    candidatos = [h for h in encabezados if h[2] == base]
    if not candidatos:
        raise BOEError(f"No se encontró el artículo {base} en {pdf.ruta.name}.")
    # Puede aparecer en índice o en una remisión al inicio de línea. Elegimos
    # el bloque con mayor cuerpo real; el índice y las remisiones son mucho
    # más cortos que el artículo normativo.
    opciones: list[tuple[int, str, str]] = []
    for inicio, _, _, titulo in candidatos:
        siguientes = [h for h in encabezados if h[0] > inicio]
        fin = siguientes[0][0] if siguientes else len(texto)
        bloque = limpiar_texto(texto[inicio:fin])
        opciones.append((len(normalizar(bloque)), titulo, bloque))
    opciones.sort(key=lambda x: x[0], reverse=True)
    longitud, titulo, bloque = opciones[0]
    if longitud < 20:
        raise BOEError(f"El artículo {base} se localizó en {pdf.ruta.name}, pero quedó sin cuerpo suficiente.")
    titulo_bloque = f"Artículo {base}" + (f". {titulo}" if titulo else "")
    return base, titulo_bloque.strip(), bloque


def obtener_articulo(nombre_norma: str, articulo: str) -> ArticuloBOE:
    pdf = buscar_norma_local(nombre_norma)
    base, titulo, texto = _bloque_articulo(pdf, articulo)
    return ArticuloBOE(
        nombre_norma=pdf.titulo,
        id_boe=pdf.id_fuente,
        departamento=pdf.departamento,
        articulo=base,
        id_bloque=f"ART-{base.replace('.', '-')}",
        titulo_bloque=titulo,
        texto=texto,
    )


def obtener_articulo_por_id(id_fuente: str, articulo: str) -> ArticuloBOE:
    pdf = buscar_norma_por_id(id_fuente)
    base, titulo, texto = _bloque_articulo(pdf, articulo)
    return ArticuloBOE(
        nombre_norma=pdf.titulo,
        id_boe=pdf.id_fuente,
        departamento=pdf.departamento,
        articulo=base,
        id_bloque=f"ART-{base.replace('.', '-')}",
        titulo_bloque=titulo,
        texto=texto,
    )


def obtener_todos_articulos_por_id(id_fuente: str) -> list[ArticuloBOE]:
    pdf = buscar_norma_por_id(id_fuente)
    texto = _texto_pdf(str(pdf.ruta.resolve()))
    encabezados = _encabezados(texto)
    candidatos = {int(h[2]) for h in encabezados if h[2].isdigit()}
    # Las normas manejadas aquí numeran sus artículos correlativamente desde 1.
    # Esto excluye remisiones internas que aparecen al inicio de línea como si
    # fueran encabezados (p. ej. "Artículo 149...").
    ultimo = 0
    while ultimo + 1 in candidatos:
        ultimo += 1
    if ultimo == 0:
        raise BOEError(f"No se pudo determinar la secuencia de artículos de {pdf.ruta.name}.")
    numeros = [str(n) for n in range(1, ultimo + 1)]
    resultado: list[ArticuloBOE] = []
    for numero in numeros:
        try:
            resultado.append(obtener_articulo_por_id(id_fuente, numero))
        except BOEError:
            continue
    if not resultado:
        raise BOEError(f"No se pudieron extraer artículos del PDF local {pdf.ruta.name}.")
    return resultado
