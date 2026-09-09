"""
Constructor de Temarios V2 - extracción estructural desde PDF oficial.

SOLO LECTURA respecto de la base de datos.

Esta primera fase NO resuelve normas ni artículos. Únicamente:
- extrae partes, bloques y temas del PDF oficial;
- conserva el texto literal extraído de cada tema;
- propone una clasificación conservadora:
    JURIDICO / NO_JURIDICO / REVISION.

La clasificación no consulta temarios ya consolidados ni usa la BD como respuesta.
Los casos sin evidencia literal suficiente se dejan en REVISION.

Uso:
    python scripts/extraer_estructura_temario_v2.py --pdf RUTA_PDF

Opcional:
    python scripts/extraer_estructura_temario_v2.py --pdf RUTA_PDF \
        --salida-json auditorias/constructor_temario_v2/estructura_C1.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


class ExtraccionError(RuntimeError):
    pass


@dataclass
class TemaExtraido:
    parte: str
    bloque_numero: str
    bloque_titulo: str
    numero_tema: int
    texto: str
    clasificacion: str = "REVISION"
    evidencia_clasificacion: str = "SIN_EVIDENCIA_LITERAL_SUFFICIENTE"


PATRON_PARTE = re.compile(
    r"^TEMARIO\s+PARTE\s+(GENERAL|ESPECIAL)\s*$",
    re.IGNORECASE,
)
PATRON_BLOQUE = re.compile(r"^([IVXLCDM]+)\.\s+(.+)$")
PATRON_TEMA = re.compile(r"^(\d{1,2})\.\s+(.+)$")

PATRONES_CABECERA = (
    re.compile(r"^N[uú]m\.\s+\d+\s*/", re.IGNORECASE),
    re.compile(r"^\d+\s*/\s*\d+$"),
    re.compile(r"^CVE:", re.IGNORECASE),
    re.compile(r"^https://dogv\.gva\.es/?$", re.IGNORECASE),
    re.compile(r"^Anexo\s+[IVXLCDM]+$", re.IGNORECASE),
    re.compile(r"^Convocatoria\s+\d+/\d+$", re.IGNORECASE),
    re.compile(r"^[A-Z]\d?-\d+\.\s+Cuerpo\b", re.IGNORECASE),
)

# Evidencia jurídica que puede reconocerse literalmente sin decidir todavía
# qué norma o qué artículos corresponden al tema.
PATRONES_JURIDICOS_EXPLICITOS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bconstituci[oó]n\b",
        r"\bestatuto de autonom[ií]a\b",
        r"\bley\b",
        r"\bley org[aá]nica\b",
        r"\breal decreto legislativo\b",
        r"\breal decreto\b",
        r"\bdecreto legislativo\b",
        r"\bdecreto\b",
        r"\borden\b",
        r"\btratado de la uni[oó]n europea\b",
        r"\btratado de funcionamiento de la uni[oó]n europea\b",
    )
)

# Señales inequívocas de contenido técnico/ofimático. No se usan para decidir
# por simple presencia de una palabra aislada: se exige combinación de varias
# señales o un bloque explícitamente técnico.
PATRON_BLOQUE_NO_JURIDICO = re.compile(
    r"\binform[aá]tica\b|\bofim[aá]tica\b",
    re.IGNORECASE,
)

PATRONES_TECNICOS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwindows\s*11\b",
        r"\bmicrosoft\s+365\b",
        r"\boutlook\b",
        r"\bteams\b",
        r"\bonedrive\b",
        r"\bword\b",
        r"\bexcel\b",
        r"\bnavegador(?:es)?\s+web\b",
        r"\barchivos?\s+y\s+carpetas?\b",
        r"\bhojas?\s+de\s+c[aá]lculo\b",
        r"\bprocesador\s+de\s+textos?\b",
        r"\binteligencia\s+artificial\b",
        r"\bherramientas?\s+basadas?\s+en\s+inteligencia\s+artificial\b",
        r"\bchat\s+y\s+llamadas\b",
        r"\btrabajo\s+colaborativo\b",
        r"\bdescarga\s+de\s+documentos\b",
        r"\bmarcadores\b",
        r"\bhistorial\b",
    )
)


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def normalizar(texto: str | None) -> str:
    valor = unicodedata.normalize("NFKD", texto or "")
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    valor = valor.lower()
    return re.sub(r"\s+", " ", valor).strip()


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
        "No se pudo extraer texto del PDF. Se requiere PyMuPDF, pypdf "
        "o pdftotext."
    )


def es_cabecera_dogv(linea: str) -> bool:
    if "https://dogv.gva.es/" in linea:
        return True
    return any(patron.search(linea) for patron in PATRONES_CABECERA)


def extraer_temas(texto_pdf: str) -> list[TemaExtraido]:
    temas: list[TemaExtraido] = []
    parte_actual = ""
    bloque_numero = ""
    bloque_titulo = ""
    tema_actual: TemaExtraido | None = None
    lineas_tema: list[str] = []

    def cerrar_tema() -> None:
        nonlocal tema_actual, lineas_tema
        if tema_actual is None:
            return
        tema_actual.texto = limpiar(" ".join(lineas_tema))
        if tema_actual.texto:
            temas.append(tema_actual)
        tema_actual = None
        lineas_tema = []

    for linea_original in texto_pdf.splitlines():
        linea = limpiar(linea_original)
        if not linea or es_cabecera_dogv(linea):
            continue

        m_parte = PATRON_PARTE.match(linea)
        if m_parte:
            cerrar_tema()
            parte_actual = m_parte.group(1).upper()
            bloque_numero = ""
            bloque_titulo = ""
            continue

        m_bloque = PATRON_BLOQUE.match(linea)
        if m_bloque and parte_actual:
            cerrar_tema()
            bloque_numero = m_bloque.group(1).upper()
            bloque_titulo = limpiar(m_bloque.group(2))
            continue

        m_tema = PATRON_TEMA.match(linea)
        if m_tema and parte_actual:
            cerrar_tema()
            tema_actual = TemaExtraido(
                parte=parte_actual,
                bloque_numero=bloque_numero,
                bloque_titulo=bloque_titulo,
                numero_tema=int(m_tema.group(1)),
                texto="",
            )
            lineas_tema = [limpiar(m_tema.group(2))]
            continue

        if tema_actual is not None:
            lineas_tema.append(linea)

    cerrar_tema()

    claves = [(t.parte, t.numero_tema) for t in temas]
    repetidas = [clave for clave, n in Counter(claves).items() if n > 1]
    if repetidas:
        raise ExtraccionError(
            "Se han detectado temas duplicados: "
            + ", ".join(f"{p}-{n}" for p, n in repetidas)
        )

    return temas


def evidencia_tecnica_suficiente(texto: str) -> bool:
    coincidencias = sum(1 for patron in PATRONES_TECNICOS if patron.search(texto))
    return coincidencias >= 2


def clasificar_tema(tema: TemaExtraido) -> None:
    bloque_normalizado = normalizar(tema.bloque_titulo)
    texto_con_bloque = f"{tema.bloque_titulo} {tema.texto}"

    # Una referencia normativa literal tiene prioridad absoluta: un tema nunca
    # se convierte en NO_JURIDICO por contener además herramientas técnicas.
    if any(patron.search(texto_con_bloque) for patron in PATRONES_JURIDICOS_EXPLICITOS):
        tema.clasificacion = "JURIDICO"
        tema.evidencia_clasificacion = "REFERENCIA_JURIDICA_LITERAL"
        return

    if PATRON_BLOQUE_NO_JURIDICO.search(bloque_normalizado):
        tema.clasificacion = "NO_JURIDICO"
        tema.evidencia_clasificacion = "BLOQUE_TECNICO_EXPLICITO"
        return

    if evidencia_tecnica_suficiente(tema.texto):
        tema.clasificacion = "NO_JURIDICO"
        tema.evidencia_clasificacion = "CONTENIDO_TECNICO_INEQUIVOCO"
        return

    tema.clasificacion = "REVISION"
    tema.evidencia_clasificacion = "SIN_EVIDENCIA_LITERAL_SUFFICIENTE"


def imprimir_resultado(ruta_pdf: Path, temas: list[TemaExtraido]) -> None:
    conteo_partes = Counter(t.parte for t in temas)
    conteo_clases = Counter(t.clasificacion for t in temas)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - EXTRACCIÓN ESTRUCTURAL - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {ruta_pdf}")
    print(f"Temas extraídos.................. {len(temas)}")
    for parte in sorted(conteo_partes):
        print(f"  {parte:<28} {conteo_partes[parte]}")
    print()
    print("Clasificación propuesta:")
    for estado in ("JURIDICO", "NO_JURIDICO", "REVISION"):
        print(f"  {estado:<28} {conteo_clases.get(estado, 0)}")
    print()

    for tema in temas:
        bloque = (
            f"{tema.bloque_numero}. {tema.bloque_titulo}"
            if tema.bloque_titulo
            else "SIN BLOQUE"
        )
        print(
            f"{tema.parte:<8} | {tema.numero_tema:>2} | "
            f"{tema.clasificacion:<12} | {bloque}"
        )
        if tema.clasificacion == "REVISION":
            print(f"    {tema.texto}")

    print()
    print("No se ha consultado ni modificado la base de datos.")
    print("No se han resuelto normas ni artículos.")


def guardar_json(ruta: Path, pdf: Path, temas: list[TemaExtraido]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = {
        "modo": "SOLO_LECTURA",
        "pdf": str(pdf),
        "temas_total": len(temas),
        "clasificacion": dict(Counter(t.clasificacion for t in temas)),
        "temas": [asdict(t) for t in temas],
    }
    ruta.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extrae la estructura de un temario oficial y propone una "
            "clasificación conservadora sin consultar ni modificar la BD."
        )
    )
    parser.add_argument("--pdf", required=True, help="Ruta al PDF oficial")
    parser.add_argument(
        "--salida-json",
        help="Ruta opcional del informe JSON; sólo escribe este fichero de auditoría",
    )
    args = parser.parse_args()

    ruta_pdf = Path(args.pdf).expanduser().resolve()
    temas = extraer_temas(extraer_texto_pdf(ruta_pdf))
    for tema in temas:
        clasificar_tema(tema)

    imprimir_resultado(ruta_pdf, temas)

    if args.salida_json:
        ruta_json = Path(args.salida_json).expanduser().resolve()
        guardar_json(ruta_json, ruta_pdf, temas)
        print(f"Informe JSON..................... {ruta_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
