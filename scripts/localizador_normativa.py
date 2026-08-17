"""
OpoCoach - Localizador especializado de normas e índices del BOE.

Ubicación:
    scripts/localizador_normativa.py

Objetivo:
- localizar con seguridad una norma;
- descargar su índice consolidado del BOE;
- convertir Título/Capítulo/Sección en artículos;
- funcionar con boe_api.py y con respaldo HTML para el índice;
- no modificar la base de datos.

Ejemplos:
    python scripts/localizador_normativa.py "Ley 39/2015" --indice
    python scripts/localizador_normativa.py "Ley 39/2015" --alcance "Título III"
    python scripts/localizador_normativa.py ^
        "Estatuto de Autonomía de la Comunitat Valenciana" ^
        --alcance "Título III, capítulo II"
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup, Tag


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
RUTA_BOE_API = Path(__file__).resolve().parent / "boe_api.py"
CACHE_DIR = RAIZ_PROYECTO / "cache_localizador_normativa"
TIMEOUT = 40

URL_FICHA = "https://www.boe.es/buscar/act.php"
URL_BUSCADOR = "https://www.boe.es/buscar/legislacion.php"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36 "
    "OpoCoach/1.0"
)

# Alias verificados. Son referencias cuyo nombre usual no contiene
# necesariamente tipo, número y año.
ALIASES_BOE = {
    "constitucion espanola": (
        "BOE-A-1978-31229",
        "Constitución Española",
    ),
    "constitucion espanola de 1978": (
        "BOE-A-1978-31229",
        "Constitución Española",
    ),
    "constitucion de 1978": (
        "BOE-A-1978-31229",
        "Constitución Española",
    ),
    "estatuto de autonomia de la comunitat valenciana": (
        "BOE-A-1982-17235",
        "Ley Orgánica 5/1982, de 1 de julio, de Estatuto de "
        "Autonomía de la Comunidad Valenciana",
    ),
    "estatuto de autonomia de la comunidad valenciana": (
        "BOE-A-1982-17235",
        "Ley Orgánica 5/1982, de 1 de julio, de Estatuto de "
        "Autonomía de la Comunidad Valenciana",
    ),
}

TIPOS_NIVEL = {
    "libro": 1,
    "titulo": 2,
    "capitulo": 3,
    "seccion": 4,
    "subseccion": 5,
}

ORDINALES = {
    "primero": "1", "primera": "1",
    "segundo": "2", "segunda": "2",
    "tercero": "3", "tercera": "3",
    "cuarto": "4", "cuarta": "4",
    "quinto": "5", "quinta": "5",
    "sexto": "6", "sexta": "6",
    "septimo": "7", "septima": "7",
    "octavo": "8", "octava": "8",
    "noveno": "9", "novena": "9",
    "decimo": "10", "decima": "10",
}

PATRON_ID_BOE = re.compile(r"\bBOE-A-\d{4}-\d+\b", re.I)

PATRON_UNIDAD = re.compile(
    r"^\s*(libro|t[ií]tulo|cap[ií]tulo|secci[oó]n|subsecci[oó]n)"
    r"\s+(preliminar|[ivxlcdm]+|\d+(?:[.ºª])?|"
    r"primero|primera|segundo|segunda|tercero|tercera|"
    r"cuarto|cuarta|quinto|quinta|sexto|sexta|"
    r"s[eé]ptimo|s[eé]ptima|octavo|octava|noveno|novena|"
    r"d[eé]cimo|d[eé]cima|[uú]nico|[uú]nica)\b",
    re.I,
)

PATRON_ARTICULO = re.compile(
    r"^\s*art[ií]culo\s+("
    r"\d+(?:\.\d+)*(?:\s+(?:bis|ter|quater|quinquies|sexies|"
    r"septies|octies|nonies|decies))?"
    r"|[uú]nico"
    r")\b",
    re.I,
)

PATRON_ARTICULOS_ALCANCE = re.compile(
    r"\bart[ií]culos?\s+"
    r"((?:\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quinquies|"
    r"sexies|septies|octies|nonies|decies))?)"
    r"(?:\s*(?:a|al|-|y|,)\s*"
    r"\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quinquies|"
    r"sexies|septies|octies|nonies|decies))?)*)",
    re.I,
)


class LocalizadorError(RuntimeError):
    """Error controlado. Nunca implica seleccionar una norma dudosa."""


@dataclass(frozen=True)
class NormaLocalizada:
    referencia_solicitada: str
    id_boe: str
    titulo: str
    departamento: str
    metodo: str
    url_indice: str


@dataclass(frozen=True)
class EntradaIndice:
    articulo: str
    titulo_articulo: str
    bloque: str
    ruta: dict[str, str]


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def normalizar(texto: str | None) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip(" .,:;")


def romano_a_entero(valor: str) -> int | None:
    valores = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    v = normalizar(valor)
    if not v or any(c not in valores for c in v):
        return None
    total = 0
    anterior = 0
    for c in reversed(v):
        actual = valores[c]
        if actual < anterior:
            total -= actual
        else:
            total += actual
            anterior = actual
    return total


def normalizar_valor_unidad(valor: str) -> str:
    v = normalizar(valor).replace("º", "").replace("ª", "").replace(".", "")
    if v == "preliminar":
        return "preliminar"
    if v in {"unico", "unica"}:
        return "unico"
    if v in ORDINALES:
        return ORDINALES[v]
    if v.isdigit():
        return str(int(v))
    romano = romano_a_entero(v)
    return str(romano) if romano is not None else v


def normalizar_tipo_unidad(tipo: str) -> str:
    return normalizar(tipo)


def normalizar_articulo(articulo: str) -> str:
    valor = normalizar(articulo).replace(",", ".")
    valor = re.sub(r"^(articulo|art\.?)\s+", "", valor)
    return valor.strip(" .ºª")


def cargar_boe_api():
    if not RUTA_BOE_API.is_file():
        raise LocalizadorError(f"No existe el módulo: {RUTA_BOE_API}")

    spec = importlib.util.spec_from_file_location(
        "opocoach_boe_api_localizador",
        RUTA_BOE_API,
    )
    if spec is None or spec.loader is None:
        raise LocalizadorError(f"No se pudo cargar {RUTA_BOE_API}")

    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def url_indice(id_boe: str) -> str:
    return f"{URL_FICHA}?id={id_boe}&tn=2"


def localizar_por_alias(referencia: str) -> NormaLocalizada | None:
    clave = normalizar(referencia)
    dato = ALIASES_BOE.get(clave)
    if dato is None:
        return None

    id_boe, titulo = dato
    return NormaLocalizada(
        referencia_solicitada=referencia,
        id_boe=id_boe,
        titulo=titulo,
        departamento="",
        metodo="alias_verificado",
        url_indice=url_indice(id_boe),
    )


def localizar_por_id(referencia: str) -> NormaLocalizada | None:
    coincidencia = PATRON_ID_BOE.search(referencia)
    if not coincidencia:
        return None

    id_boe = coincidencia.group(0).upper()
    return NormaLocalizada(
        referencia_solicitada=referencia,
        id_boe=id_boe,
        titulo=referencia,
        departamento="",
        metodo="id_directo",
        url_indice=url_indice(id_boe),
    )


def localizar_con_boe_api(referencia: str) -> NormaLocalizada:
    boe_api = cargar_boe_api()
    try:
        norma = boe_api.buscar_norma(referencia)
    except Exception as exc:
        raise LocalizadorError(str(exc)) from exc

    return NormaLocalizada(
        referencia_solicitada=referencia,
        id_boe=norma.id_boe,
        titulo=norma.titulo,
        departamento=getattr(norma, "departamento", "") or "",
        metodo="boe_api",
        url_indice=url_indice(norma.id_boe),
    )


def extraer_resultados_busqueda_html(html: str) -> list[tuple[str, str]]:
    """
    Respaldo conservador para resultados HTML oficiales del BOE.

    Solo devuelve enlaces que contienen un identificador BOE-A exacto.
    La selección final exige que quede un único candidato compatible.
    """
    sopa = BeautifulSoup(html, "html.parser")
    resultados: dict[str, str] = {}

    for enlace in sopa.find_all("a", href=True):
        href = str(enlace.get("href", ""))
        texto = limpiar(enlace.get_text(" ", strip=True))
        coincidencia = PATRON_ID_BOE.search(href + " " + texto)
        if not coincidencia:
            continue
        id_boe = coincidencia.group(0).upper()
        titulo = texto or limpiar(enlace.parent.get_text(" ", strip=True))
        resultados.setdefault(id_boe, titulo)

    return list(resultados.items())


def referencia_tipo_numero_anio(texto: str) -> tuple[str, str, str] | None:
    t = normalizar(texto)
    patrones = (
        ("real decreto legislativo", r"real decreto legislativo"),
        ("real decreto-ley", r"real decreto[\s-]+ley"),
        ("decreto legislativo", r"decreto legislativo"),
        ("decreto-ley", r"decreto[\s-]+ley"),
        ("ley organica", r"ley organica"),
        ("real decreto", r"real decreto"),
        ("ley", r"ley"),
        ("decreto", r"decreto"),
        ("orden", r"orden"),
    )
    for tipo, patron in patrones:
        m = re.search(rf"\b{patron}\s+(\d+)\s*/\s*(\d{{4}})\b", t)
        if m:
            return tipo, str(int(m.group(1))), m.group(2)
    return None


def localizar_con_busqueda_html(referencia: str) -> NormaLocalizada:
    """
    Respaldo sin API. Utiliza exclusivamente el buscador oficial del BOE.

    No acepta semejanza libre: título, número y año deben coincidir.
    """
    cita = referencia_tipo_numero_anio(referencia)
    if cita is None:
        raise LocalizadorError(
            "La búsqueda HTML requiere tipo, número y año exactos."
        )

    parametros = {
        "campo[0]": "TIT",
        "dato[0]": referencia,
        "operador[0]": "and",
        "page_hits": "50",
    }
    try:
        respuesta = requests.get(
            URL_BUSCADOR,
            params=parametros,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise LocalizadorError(
            "Falló también la búsqueda HTML oficial del BOE."
        ) from exc

    candidatos = []
    for id_boe, titulo in extraer_resultados_busqueda_html(respuesta.text):
        if referencia_tipo_numero_anio(titulo) == cita:
            candidatos.append((id_boe, titulo))

    unicos = {id_boe: titulo for id_boe, titulo in candidatos}
    if len(unicos) != 1:
        raise LocalizadorError(
            "La búsqueda HTML no produjo una única coincidencia exacta "
            f"para {referencia}. Candidatos exactos: {len(unicos)}."
        )

    id_boe, titulo = next(iter(unicos.items()))
    return NormaLocalizada(
        referencia_solicitada=referencia,
        id_boe=id_boe,
        titulo=titulo,
        departamento="",
        metodo="busqueda_html_boe",
        url_indice=url_indice(id_boe),
    )


def localizar_norma(referencia: str) -> NormaLocalizada:
    referencia = limpiar(referencia)
    if not referencia:
        raise ValueError("Debe indicarse una referencia normativa.")

    directa = localizar_por_id(referencia)
    if directa is not None:
        return directa

    alias = localizar_por_alias(referencia)
    if alias is not None:
        return alias

    error_api: Exception | None = None
    try:
        return localizar_con_boe_api(referencia)
    except Exception as exc:
        error_api = exc

    try:
        return localizar_con_busqueda_html(referencia)
    except Exception as error_html:
        raise LocalizadorError(
            f"No se pudo localizar con seguridad: {referencia}\n"
            f"API: {error_api}\n"
            f"HTML: {error_html}"
        ) from error_html


def ruta_cache_indice(id_boe: str) -> Path:
    return CACHE_DIR / id_boe / "indice.html"


def descargar_indice(norma: NormaLocalizada, refrescar: bool = False) -> str:
    ruta = ruta_cache_indice(norma.id_boe)

    if ruta.is_file() and not refrescar:
        try:
            contenido = ruta.read_text(encoding="utf-8")
            if contenido.strip():
                return contenido
        except OSError:
            pass

    try:
        respuesta = requests.get(
            norma.url_indice,
            timeout=TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            allow_redirects=True,
        )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise LocalizadorError(
            f"No se pudo descargar el índice: {norma.url_indice}"
        ) from exc

    html = respuesta.text
    if not html.strip():
        raise LocalizadorError("El BOE devolvió un índice vacío.")

    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".html.tmp")
    temporal.write_text(html, encoding="utf-8")
    temporal.replace(ruta)
    return html


def unidad_desde_texto(texto: str) -> tuple[str, str] | None:
    m = PATRON_UNIDAD.match(limpiar(texto))
    if not m:
        return None
    return (
        normalizar_tipo_unidad(m.group(1)),
        normalizar_valor_unidad(m.group(2)),
    )


def articulo_desde_texto(texto: str) -> str:
    m = PATRON_ARTICULO.match(limpiar(texto))
    return normalizar_articulo(m.group(1)) if m else ""


def bloque_desde_href(href: str) -> str:
    if "#" not in href:
        return ""
    return href.rsplit("#", 1)[-1].strip()


def ruta_por_ancestros(enlace: Tag) -> dict[str, str]:
    """
    Recupera jerarquía cuando el índice está representado mediante listas
    anidadas. Se recorren los <li> antecesores, del más externo al interno.
    """
    unidades: list[tuple[str, str]] = []
    ancestros = list(enlace.parents)
    ancestros.reverse()

    for ancestro in ancestros:
        if not isinstance(ancestro, Tag) or ancestro.name != "li":
            continue

        texto_directo = ""
        for hijo in ancestro.children:
            if isinstance(hijo, str):
                texto_directo += " " + hijo
            elif isinstance(hijo, Tag) and hijo.name not in {"ul", "ol"}:
                texto_directo += " " + hijo.get_text(" ", strip=True)

        unidad = unidad_desde_texto(texto_directo)
        if unidad:
            unidades.append(unidad)

    return {tipo: valor for tipo, valor in unidades}


def es_enlace_articulo(enlace: Tag) -> bool:
    texto = limpiar(enlace.get_text(" ", strip=True))
    if articulo_desde_texto(texto):
        return True

    bloque = bloque_desde_href(str(enlace.get("href", "")))
    return bool(re.fullmatch(r"a\d+(?:-\d+)?", bloque, flags=re.I))


def parsear_indice_listas(sopa: BeautifulSoup) -> list[EntradaIndice]:
    entradas: list[EntradaIndice] = []

    for enlace in sopa.find_all("a", href=True):
        if not es_enlace_articulo(enlace):
            continue

        texto = limpiar(enlace.get_text(" ", strip=True))
        articulo = articulo_desde_texto(texto)
        if not articulo:
            bloque = bloque_desde_href(str(enlace.get("href", "")))
            m = re.fullmatch(r"a(\d+(?:-\d+)?)", bloque, flags=re.I)
            if m:
                articulo = m.group(1).replace("-", ".")

        if not articulo:
            continue

        entradas.append(
            EntradaIndice(
                articulo=articulo,
                titulo_articulo=texto or f"Artículo {articulo}",
                bloque=bloque_desde_href(str(enlace.get("href", ""))),
                ruta=ruta_por_ancestros(enlace),
            )
        )

    return entradas


def elementos_indice_en_orden(sopa: BeautifulSoup) -> Iterable[Tag]:
    contenedores = [
        sopa.select_one("#indice"),
        sopa.select_one(".indice"),
        sopa.select_one("#textoxslt"),
        sopa.select_one("main"),
    ]
    contenedor = next((x for x in contenedores if x is not None), sopa)

    for etiqueta in contenedor.find_all(
        ["h2", "h3", "h4", "h5", "h6", "p", "div", "li", "a"],
    ):
        if isinstance(etiqueta, Tag):
            yield etiqueta


def parsear_indice_secuencial(sopa: BeautifulSoup) -> list[EntradaIndice]:
    """
    Respaldo para índices planos.

    Mantiene la última unidad de cada nivel encontrada en el orden del HTML.
    Al entrar en un nivel se eliminan los niveles inferiores anteriores.
    """
    ruta: dict[str, str] = {}
    entradas: list[EntradaIndice] = []
    vistos_elementos: set[int] = set()

    for etiqueta in elementos_indice_en_orden(sopa):
        identidad = id(etiqueta)
        if identidad in vistos_elementos:
            continue
        vistos_elementos.add(identidad)

        texto = limpiar(etiqueta.get_text(" ", strip=True))
        if not texto or len(texto) > 300:
            continue

        unidad = unidad_desde_texto(texto)
        if unidad:
            tipo, valor = unidad
            nivel = TIPOS_NIVEL[tipo]
            ruta = {
                t: v
                for t, v in ruta.items()
                if TIPOS_NIVEL[t] < nivel
            }
            ruta[tipo] = valor
            continue

        if etiqueta.name != "a":
            continue

        articulo = articulo_desde_texto(texto)
        if not articulo:
            continue

        entradas.append(
            EntradaIndice(
                articulo=articulo,
                titulo_articulo=texto,
                bloque=bloque_desde_href(str(etiqueta.get("href", ""))),
                ruta=dict(ruta),
            )
        )

    return entradas


def combinar_entradas(
    anidadas: list[EntradaIndice],
    secuenciales: list[EntradaIndice],
) -> list[EntradaIndice]:
    """
    Combina ambos métodos. Para un mismo artículo conserva la ruta más rica.
    """
    por_articulo: dict[str, EntradaIndice] = {}

    for entrada in [*anidadas, *secuenciales]:
        anterior = por_articulo.get(entrada.articulo)
        if anterior is None or len(entrada.ruta) > len(anterior.ruta):
            por_articulo[entrada.articulo] = entrada

    return list(por_articulo.values())


def clave_orden_articulo(articulo: str) -> tuple[int, ...]:
    numeros = [int(x) for x in re.findall(r"\d+", articulo)]
    if not numeros:
        return (10**9,)
    sufijo = normalizar(articulo)
    orden_sufijo = {
        "bis": 1, "ter": 2, "quater": 3, "quinquies": 4,
        "sexies": 5, "septies": 6, "octies": 7,
        "nonies": 8, "decies": 9,
    }
    extra = 0
    for nombre, valor in orden_sufijo.items():
        if nombre in sufijo:
            extra = valor
            break
    return (*numeros, extra)


def parsear_indice(html: str) -> list[EntradaIndice]:
    sopa = BeautifulSoup(html, "html.parser")
    entradas = combinar_entradas(
        parsear_indice_listas(sopa),
        parsear_indice_secuencial(sopa),
    )

    if not entradas:
        raise LocalizadorError(
            "El BOE no devolvió un índice interpretable: "
            "no se encontraron enlaces de artículos."
        )

    entradas.sort(key=lambda e: clave_orden_articulo(e.articulo))
    return entradas


def expandir_articulos(expresion: str) -> list[str]:
    expresion = normalizar(expresion).replace(",", ".")

    m = re.fullmatch(r"(\d+)\s*(?:a|al|-)\s*(\d+)", expresion)
    if m:
        inicio, fin = int(m.group(1)), int(m.group(2))
        if inicio <= fin:
            return [str(n) for n in range(inicio, fin + 1)]

    encontrados = re.findall(
        r"\d+(?:\.\d+)?(?:\s+(?:bis|ter|quater|quinquies|"
        r"sexies|septies|octies|nonies|decies))?",
        expresion,
        flags=re.I,
    )
    return [normalizar_articulo(x) for x in encontrados]


def articulos_explicitos(alcance: str) -> list[str]:
    resultado: list[str] = []
    for m in PATRON_ARTICULOS_ALCANCE.finditer(alcance):
        contexto = normalizar(alcance[max(0, m.start() - 25):m.start()])
        if "excepto" in contexto or "salvo" in contexto:
            continue
        resultado.extend(expandir_articulos(m.group(1)))
    return list(dict.fromkeys(resultado))


def articulos_excluidos(alcance: str) -> set[str]:
    m = re.search(
        r"(?:excepto|salvo)\s+(?:los\s+)?art[ií]culos?\s+([^.;)]+)",
        alcance,
        flags=re.I,
    )
    return set(expandir_articulos(m.group(1))) if m else set()


def unidades_alcance(alcance: str) -> dict[str, str]:
    """
    Devuelve la ruta jerárquica citada. Si aparecen Título y Capítulo,
    se exigirán ambos.
    """
    ruta: dict[str, str] = {}
    for m in re.finditer(PATRON_UNIDAD, alcance):
        tipo = normalizar_tipo_unidad(m.group(1))
        valor = normalizar_valor_unidad(m.group(2))
        ruta[tipo] = valor
    return ruta


def resolver_alcance(
    indice: list[EntradaIndice],
    alcance: str,
) -> list[str]:
    alcance = limpiar(alcance)
    excluir = articulos_excluidos(alcance)

    explicitos = articulos_explicitos(alcance)
    if explicitos:
        existentes = {e.articulo for e in indice}
        seleccion = [a for a in explicitos if a in existentes]
        faltantes = [a for a in explicitos if a not in existentes]
        if faltantes:
            raise LocalizadorError(
                "Artículos explícitos no encontrados en el índice: "
                + ", ".join(faltantes)
            )
        return [a for a in seleccion if a not in excluir]

    ruta = unidades_alcance(alcance)

    # Sin una unidad estructural ni artículos explícitos se interpreta
    # como norma completa.
    if not ruta:
        return [
            entrada.articulo
            for entrada in indice
            if entrada.articulo not in excluir
        ]

    seleccionados = [
        entrada.articulo
        for entrada in indice
        if all(entrada.ruta.get(tipo) == valor for tipo, valor in ruta.items())
        and entrada.articulo not in excluir
    ]

    if not seleccionados:
        descripcion = ", ".join(f"{k}={v}" for k, v in ruta.items())
        raise LocalizadorError(
            f"El alcance no produjo artículos en el índice ({descripcion})."
        )

    return seleccionados


def obtener_indice(
    referencia: str,
    refrescar: bool = False,
) -> tuple[NormaLocalizada, list[EntradaIndice]]:
    norma = localizar_norma(referencia)
    html = descargar_indice(norma, refrescar=refrescar)
    return norma, parsear_indice(html)


def imprimir_resumen_indice(
    norma: NormaLocalizada,
    indice: list[EntradaIndice],
) -> None:
    print(f"Norma:  {norma.titulo}")
    print(f"BOE:    {norma.id_boe}")
    print(f"Método: {norma.metodo}")
    print(f"URL:    {norma.url_indice}")
    print(f"Artículos detectados: {len(indice)}")
    print()

    for entrada in indice:
        ruta = " > ".join(
            f"{tipo.title()} {valor}"
            for tipo, valor in entrada.ruta.items()
        )
        print(
            f"{entrada.articulo:>10}  "
            f"{ruta or '(sin jerarquía)'}"
        )


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Localiza una norma y resuelve títulos, capítulos, secciones "
            "o artículos mediante el índice oficial del BOE."
        )
    )
    parser.add_argument("norma", help="Nombre de la norma o ID BOE-A-...")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "--localizar",
        action="store_true",
        help="Solo localizar la norma.",
    )
    grupo.add_argument(
        "--indice",
        action="store_true",
        help="Mostrar el índice interpretado.",
    )
    grupo.add_argument(
        "--alcance",
        help='Resolver un alcance, por ejemplo "Título III, capítulo II".',
    )
    parser.add_argument(
        "--refrescar",
        action="store_true",
        help="Ignorar la caché HTML y descargar de nuevo.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emitir la salida en JSON.",
    )
    return parser


def main() -> int:
    args = crear_parser().parse_args()

    if args.localizar:
        norma = localizar_norma(args.norma)
        if args.json:
            print(json.dumps(asdict(norma), ensure_ascii=False, indent=2))
        else:
            print(f"Norma:        {norma.titulo}")
            print(f"BOE:          {norma.id_boe}")
            print(f"Departamento: {norma.departamento or '(no consultado)'}")
            print(f"Método:       {norma.metodo}")
            print(f"Índice:       {norma.url_indice}")
        return 0

    norma, indice = obtener_indice(
        args.norma,
        refrescar=args.refrescar,
    )

    if args.indice:
        if args.json:
            print(
                json.dumps(
                    {
                        "norma": asdict(norma),
                        "indice": [asdict(x) for x in indice],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            imprimir_resumen_indice(norma, indice)
        return 0

    articulos = resolver_alcance(indice, args.alcance)

    if args.json:
        print(
            json.dumps(
                {
                    "norma": asdict(norma),
                    "alcance": args.alcance,
                    "articulos": articulos,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Norma:    {norma.titulo}")
        print(f"BOE:      {norma.id_boe}")
        print(f"Alcance:  {args.alcance}")
        print(f"Artículos ({len(articulos)}):")
        print(", ".join(articulos))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            f"ERROR: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)
