import re
import unicodedata


EQUIVALENCIAS_NORMAS = {
    "constitucion espanola.": "constitucion espanola",
    "decreto 1/2019": "decreto legislativo 1/2019",
    "decreto 1/2021": "decreto legislativo 1/2021",
    "decreto del consell 176/2014, de 10 de octubre, por el que se regula los convenios que suscriba la generalitat y su registro": "decreto 176/2014",
    "decreto del consell 176/2014, de 10 de octubre, por el que se regulan los convenios que suscriba la generalitat y su registro": "decreto 176/2014",
    "estatut d'autonomia de la comunitat valenciana": "ley organica 5/1982",
    "estatuto de autonomia": "ley organica 5/1982",
    "estatuto de autonomia de la comunitat valenciana": "ley organica 5/1982",
    "estatuto de los trabajadores": "real decreto legislativo 2/2015",
    "ley 3/2018": "ley organica 3/2018",
    "ley de 16 de diciembre de 1954 sobre expropiacion forzosa": "ley de expropiacion forzosa de 1954",
    "ley de 16 de diciembre de 1954, sobre expropiacion forzosa": "ley de expropiacion forzosa de 1954",
    "ley de expropiacion forzosa": "ley de expropiacion forzosa de 1954",
    "ley de la generalitat valenciana 1/1987, de 31 de marzo, electoral valenciana": "ley 1/1987",
    "ley organica del poder judicial": "ley organica 6/1985",
    "ley organica del regimen electoral general": "ley organica 5/1985",
    "real decreto 2/2015": "real decreto legislativo 2/2015",
    "real decreto 5/2015": "real decreto legislativo 5/2015",
    "reglamento de las corts valencianes": "reglamento de les corts valencianes",
    "reglamento de les corts": "reglamento de les corts valencianes",
    "texto refundido de la ley del estatuto basico del empleado publico": "real decreto legislativo 5/2015",
    "texto refundido de la ley del estatuto basico del empleado publico (trebep)": "real decreto legislativo 5/2015",
    "texto refundido de la ley general de la seguridad social": "real decreto legislativo 8/2015",
    "tratado de funcionamiento de la union europea (tfue)": "tratado de funcionamiento de la union europea",
    "tratado de la union europea (tue)": "tratado de la union europea",
}


def aplicar_equivalencia(clave: str) -> str:
    return EQUIVALENCIAS_NORMAS.get(clave, clave)


def normalizar_norma(texto: str) -> str:
    if not texto:
        return ""

    texto = texto.strip().lower().replace("_", " ")
    texto = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caracter) != "Mn"
    )
    texto = re.sub(r"\s+", " ", texto)

    if texto == "tfue":
        return aplicar_equivalencia("tratado de funcionamiento de la union europea")
    if texto == "tue":
        return aplicar_equivalencia("tratado de la union europea")
    if texto == "constitucion espanola de 1978":
        return aplicar_equivalencia("constitucion espanola")

    # Buscar TODAS las identidades estructuradas y escoger la primera que aparece
    # en el texto. Esto evita que una norma citada dentro del título gane por la
    # prioridad artificial del tipo normativo (p. ej. RD 635/2014 ... LO 2/2012).
    patrones = [
        (r"(ley organica)\s+(\d+)\s*/?\s*(\d{4})", lambda g: f"{g[0]} {g[1]}/{g[2]}"),
        (r"(real decreto legislativo)\s+(\d+)\s*/?\s*(\d{4})", lambda g: f"{g[0]} {g[1]}/{g[2]}"),
        (r"(real decreto)\s+(\d+)\s*/?\s*(\d{4})", lambda g: f"{g[0]} {g[1]}/{g[2]}"),
        (r"(decreto legislativo)\s+(\d+)\s*/?\s*(\d{4})", lambda g: f"{g[0]} {g[1]}/{g[2]}"),
        (r"(decreto[-\s]+ley)\s+(\d+)\s*/?\s*(\d{4})", lambda g: f"decreto ley {g[1]}/{g[2]}"),
        (r"(decreto)\s+(\d+)\s*/?\s*(\d{4})", lambda g: f"{g[0]} {g[1]}/{g[2]}"),
        (r"(ley)\s+(\d+)\s*/?\s*(\d{4})", lambda g: f"{g[0]} {g[1]}/{g[2]}"),
        (r"(directiva(?:\s*\(\s*ue\s*\)|\s+ue)?)\s+(\d{4})\s*/?\s*(\d+)", lambda g: f"directiva ue {g[1]}/{g[2]}"),
        (r"(reglamento\s*\(\s*ue\s*,\s*euratom\s*\))\s+(\d{4})\s*/?\s*(\d+)", lambda g: f"reglamento ue euratom {g[1]}/{g[2]}"),
        (r"(reglamento(?:\s*\(\s*ue\s*\)|\s+ue)?)\s+(\d{4})\s*/?\s*(\d+)", lambda g: f"reglamento ue {g[1]}/{g[2]}"),
    ]

    hallados = []
    for patron, construir in patrones:
        m = re.search(patron, texto)
        if m:
            hallados.append((m.start(), construir(m.groups())))
    if hallados:
        _pos, clave = min(hallados, key=lambda x: x[0])

        # La fecha de disposici?n forma parte de la identidad normativa cuando
        # est? expresamente incluida en la denominaci?n. Esto evita colisiones
        # entre normas distintas con el mismo tipo, n?mero y a?o.
        meses = (
            "enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
            "septiembre|setiembre|octubre|noviembre|diciembre"
        )
        m_fecha = re.search(
            rf"\bde\s+(\d{{1,2}})\s+de\s+({meses})"
            rf"(?:\s+de\s+(\d{{4}}))?\b",
            texto[_pos:],
        )
        if m_fecha:
            dia = str(int(m_fecha.group(1)))
            mes = "septiembre" if m_fecha.group(2) == "setiembre" else m_fecha.group(2)
            anio_fecha = m_fecha.group(3)

            fecha = f"de {dia} de {mes}"
            if anio_fecha:
                fecha += f" de {anio_fecha}"

            clave = f"{clave} {fecha}"

        return aplicar_equivalencia(clave)

    return aplicar_equivalencia(texto)

def identidad_sin_fecha(clave: str) -> str:
    """Retira ?nicamente la fecha final de una identidad normativa normalizada."""
    clave = (clave or "").strip()
    if not clave:
        return ""

    # Solo las identidades estructuradas con n?mero/a?o participan
    # en esta equivalencia de base.
    if not re.search(r"\b\d+/\d{4}\b", clave):
        return clave

    meses = (
        "enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        "septiembre|octubre|noviembre|diciembre"
    )
    return re.sub(
        rf"\s+de\s+\d{{1,2}}\s+de\s+(?:{meses})(?:\s+de\s+\d{{4}})?$",
        "",
        clave,
    ).strip()
