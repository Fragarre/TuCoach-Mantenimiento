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

    texto = texto.strip().lower()
    texto = texto.replace("_", " ")

    texto = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caracter) != "Mn"
    )

    if texto == "tfue":
        return aplicar_equivalencia(
            "tratado de funcionamiento de la union europea"
        )

    if texto == "tue":
        return aplicar_equivalencia("tratado de la union europea")

    texto = re.sub(r"\s+", " ", texto)

    if texto == "constitucion espanola de 1978":
        return aplicar_equivalencia("constitucion espanola")

    patrones = [
        r"(ley organica)\s+(\d+)\s*/?\s*(\d{4})",
        r"(real decreto legislativo)\s+(\d+)\s*/?\s*(\d{4})",
        r"(real decreto)\s+(\d+)\s*/?\s*(\d{4})",
        r"(decreto legislativo)\s+(\d+)\s*/?\s*(\d{4})",
        r"(decreto ley)\s+(\d+)\s*/?\s*(\d{4})",
        r"(decreto)\s+(\d+)\s*/?\s*(\d{4})",
        r"(ley)\s+(\d+)\s*/?\s*(\d{4})",
    ]

    for patron in patrones:
        coincidencia = re.search(patron, texto)
        if coincidencia:
            tipo, numero, anio = coincidencia.groups()
            return aplicar_equivalencia(f"{tipo} {numero}/{anio}")

    return aplicar_equivalencia(texto)
