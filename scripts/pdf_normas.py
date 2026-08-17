"""
OpoCoach - Proveedor local de artículos normativos desde PDF.

Se utiliza únicamente para normas incluidas explícitamente en MAPA_PDFS.
No usa OCR, no consulta Internet y no selecciona normas por semejanza.

Los PDF deben estar en:
    fuentes_normativas/

Interfaz:
    buscar_norma(nombre_norma) -> NormaBOE
    obtener_articulo(nombre_norma, articulo) -> ArticuloBOE
"""

from __future__ import annotations

import re
import unicodedata
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


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    valor = unicodedata.normalize("NFKD", texto)
    valor = "".join(
        caracter
        for caracter in valor
        if not unicodedata.combining(caracter)
    )
    valor = valor.lower()
    valor = re.sub(r"\s+", " ", valor)
    return valor.strip()


def limpiar_texto(texto: str) -> str:
    lineas = []
    for linea in texto.replace("\r", "\n").splitlines():
        linea = re.sub(r"\s+", " ", linea).strip()
        if not linea:
            continue
        if re.fullmatch(r"Núm\.\s+\d+\s*/\s*\d{2}\.\d{2}\.\d{4}.*", linea):
            continue
        if linea.startswith("CVE: ") or linea.startswith("https://dogv.gva.es"):
            continue
        if re.fullmatch(
            r"(?:Decreto|Orden)\s+\d+/\d{4}.*\d+\s+de\s+\d+",
            linea,
            flags=re.I,
        ):
            continue
        lineas.append(linea)
    return "\n".join(lineas).strip()


def extraer_clave(nombre_norma: str) -> str:
    texto = normalizar(nombre_norma)
    patrones = (
        (r"\bdecreto\s+(\d+)\s*[/_-]\s*(\d{4})\b", "decreto"),
        (r"\borden\s+(\d+)\s*[/_-]\s*(\d{4})\b", "orden"),
    )
    coincidencias = []
    for patron, tipo in patrones:
        for numero, anio in re.findall(patron, texto):
            coincidencias.append(f"{tipo}|{int(numero)}|{anio}")
    coincidencias = sorted(set(coincidencias))
    if len(coincidencias) != 1:
        raise BOEError(
            "No se pudo identificar de forma exacta una norma PDF local "
            f"admitida en: {nombre_norma}"
        )
    clave = coincidencias[0]
    if clave not in MAPA_PDFS:
        raise BOEError(f"No existe PDF local configurado para: {nombre_norma}")
    return clave


def ruta_pdf(clave: str) -> Path:
    ruta = DIRECTORIO_PDFS / MAPA_PDFS[clave]["archivo"]
    if not ruta.exists():
        raise BOEError(f"No existe el PDF normativo local: {ruta}")
    return ruta


@lru_cache(maxsize=None)
def extraer_texto_pdf(clave: str) -> str:
    ruta = ruta_pdf(clave)
    try:
        with fitz.open(ruta) as documento:
            paginas = [pagina.get_text("text") for pagina in documento]
    except Exception as exc:
        raise BOEError(f"No se pudo leer el PDF local: {ruta}") from exc
    texto = "\n".join(paginas)
    if not texto.strip():
        raise BOEError(f"El PDF local no contiene texto extraíble: {ruta}")
    return texto.replace("\r\n", "\n").replace("\r", "\n")


def normalizar_articulo(articulo: str) -> str:
    valor = str(articulo or "").strip().replace(",", ".")
    if valor.upper() == "ANEXO":
        return "ANEXO"
    if not re.fullmatch(r"(\d+)(?:\.(\d+))?", valor):
        raise BOEError(f"Artículo PDF no válido: {articulo!r}")
    return valor


def buscar_encabezados(texto: str) -> list[tuple[int, int, str, str]]:
    patron = re.compile(
        r"(?im)^[ \t]*art[ií]culo[ \t]+"
        r"(\d+(?:\.\d+)?)[ \t]*(?:\.|(?=[A-ZÁÉÍÓÚ]))[ \t]*([^\n]*)"
    )
    return [
        (
            coincidencia.start(),
            coincidencia.end(),
            coincidencia.group(1),
            coincidencia.group(2).strip(),
        )
        for coincidencia in patron.finditer(texto)
    ]


def obtener_bloque_articulo(
    clave: str,
    articulo_solicitado: str,
) -> tuple[str, str]:
    numero = normalizar_articulo(articulo_solicitado)
    texto = extraer_texto_pdf(clave)

    if numero == "ANEXO":
        coincidencias = list(
            re.finditer(r"(?im)^[ \\t]*ANEXO(?:[ \\t].*)?$", texto)
        )
        if not coincidencias:
            raise BOEError(
                f"No se encontró el ANEXO en {MAPA_PDFS[clave]['archivo']}."
            )
        inicio = coincidencias[-1].start()
        bloque = limpiar_texto(texto[inicio:])
        if not bloque:
            raise BOEError("El ANEXO se localizó, pero quedó sin contenido.")
        return "ANEXO", bloque

    encabezados = buscar_encabezados(texto)
    candidatos = [h for h in encabezados if h[2] == numero]
    if not candidatos:
        raise BOEError(
            f"No se encontró el artículo {numero} en "
            f"{MAPA_PDFS[clave]['archivo']}."
        )

    inicio, _, _, titulo = candidatos[-1]
    siguientes = [h for h in encabezados if h[0] > inicio]
    fin = siguientes[0][0] if siguientes else len(texto)
    bloque = limpiar_texto(texto[inicio:fin])

    if not bloque:
        raise BOEError(
            f"El artículo {numero} se localizó, pero quedó sin contenido."
        )

    primera_linea = bloque.splitlines()[0]
    if not re.match(
        rf"(?i)^art[ií]culo\s+{re.escape(numero)}(?:\s*\.|\s+)",
        primera_linea,
    ):
        raise BOEError(
            f"No se pudo validar el encabezado del artículo {numero}."
        )

    titulo_bloque = f"Artículo {numero}"
    if titulo:
        titulo_bloque += ". " + re.sub(r"\s+", " ", titulo).strip()

    return titulo_bloque, bloque


def buscar_norma(nombre_norma: str) -> NormaBOE:
    clave = extraer_clave(nombre_norma)
    datos = MAPA_PDFS[clave]
    ruta_pdf(clave)
    return NormaBOE(
        nombre_buscado=nombre_norma,
        id_boe=datos["id_fuente"],
        titulo=datos["titulo"],
        departamento=datos["departamento"],
    )


def obtener_articulo(nombre_norma: str, articulo: str) -> ArticuloBOE:
    clave = extraer_clave(nombre_norma)
    norma = buscar_norma(nombre_norma)
    numero = normalizar_articulo(articulo)
    titulo_bloque, texto = obtener_bloque_articulo(clave, numero)
    return ArticuloBOE(
        nombre_norma=norma.titulo,
        id_boe=norma.id_boe,
        departamento=norma.departamento,
        articulo=numero,
        id_bloque=(
            "ANEXO"
            if numero == "ANEXO"
            else f"ART-{numero.replace('.', '-')}"
        ),
        titulo_bloque=titulo_bloque,
        texto=texto,
    )


def tiene_pdf_local(nombre_norma: str) -> bool:
    try:
        extraer_clave(nombre_norma)
        return True
    except BOEError:
        return False
