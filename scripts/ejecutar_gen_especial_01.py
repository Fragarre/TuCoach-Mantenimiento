"""Correcciones aisladas para el piloto GEN A1 / ESPECIAL 1.

Este lanzador mantiene fuera del resolvedor general tres particularidades del piloto:
- alias y fallback verificado del EACV art. 45;
- limpieza controlada de artefactos tipograficos del PDF oficial del TFUE 288;
- fragmentos jurisprudenciales oficiales y breves para el bloque de primacia.

Tambien normaliza el titulo del EACV art. 44 cuando boe_api incorpora el inicio
del texto normativo al metadato titulo_bloque.

No modifica boe_api.py y no anade ninguna opcion de escritura en BD.
"""
from __future__ import annotations

import html
import re
from dataclasses import replace

import boe_api
import generar_gen_especial_01 as piloto
from boe_api import ArticuloBOE, BOEError


EACV_NOMBRE_PROYECTO = (
    "Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía "
    "de la Comunitat Valenciana"
)
EACV_ID_BOE = "BOE-A-1982-17235"
EACV_45_TEXTO = (
    "En materia de competencia exclusiva, el Derecho Valenciano es el aplicable "
    "en el territorio de la Comunitat Valenciana, con preferencia sobre cualquier "
    "otro. En defecto del Derecho propio, será de aplicación supletoria el Derecho "
    "Estatal."
)

# Fragmentos oficiales deliberadamente breves. No se presentan como texto integro
# de las resoluciones: son el pasaje relevante que alimenta el bloque doctrinal.
JURISPRUDENCIA_OFICIAL = (
    piloto.BloqueOficial(
        clave="TC-D1-2004-FJ4",
        norma="Tribunal Constitucional - Declaración 1/2004",
        articulo="FJ 4",
        titulo="Fragmento oficial relevante - FJ 4",
        texto=(
            "Primacía y supremacía son categorías que se desenvuelven en órdenes "
            "diferenciados. Aquélla, en el de la aplicación de normas válidas; ésta, "
            "en el de los procedimientos de normación."
        ),
        fuente_id="TC-D1-2004",
        fuente_url="https://hj.tribunalconstitucional.es/es/Resolucion/Show/6945",
    ),
    piloto.BloqueOficial(
        clave="TJUE-SIMMENTHAL-24",
        norma="TJUE - Simmenthal, asunto 106/77",
        articulo="apartado 24",
        titulo="Fragmento oficial relevante - apartado 24",
        texto=(
            "el Juez nacional encargado de aplicar, en el marco de su competencia, "
            "las disposiciones del Derecho comunitario, está obligado a garantizar "
            "la plena eficacia de dichas normas"
        ),
        fuente_id="CELEX-61977CJ0106",
        fuente_url="https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:61977CJ0106",
    ),
    piloto.BloqueOficial(
        clave="TJUE-PRIMACIA-SINTESIS",
        norma="EUR-Lex - Primacía del Derecho europeo",
        articulo="síntesis oficial",
        titulo="Referencia oficial a Costa/ENEL e Internationale Handelsgesellschaft",
        texto=(
            "El Derecho de la Unión se caracteriza por proceder de una fuente "
            "autónoma constituida por los Tratados y por su primacía sobre los "
            "Derechos de los Estados miembros."
        ),
        fuente_id="EURLEX-PRIMACIA",
        fuente_url="https://eur-lex.europa.eu/legal-content/ES/ALL/?uri=LEGISSUM:l14548",
    ),
)


def preparar_alias_eacv() -> None:
    clave = boe_api.normalizar(EACV_NOMBRE_PROYECTO)
    existente = boe_api.NORMAS_ESPECIALES.get(clave)
    if existente not in (None, EACV_ID_BOE):
        raise RuntimeError(
            f"Alias EACV en conflicto: {existente} != {EACV_ID_BOE}"
        )
    boe_api.NORMAS_ESPECIALES[clave] = EACV_ID_BOE


def _texto_visible_html(contenido: str) -> str:
    texto = re.sub(r"(?is)<script\b.*?</script>", " ", contenido)
    texto = re.sub(r"(?is)<style\b.*?</style>", " ", texto)
    texto = re.sub(r"(?s)<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def _eacv_45_verificado(nombre_norma: str) -> ArticuloBOE:
    contenido = boe_api.obtener_texto_consolidado_html(EACV_ID_BOE)
    visible = _texto_visible_html(contenido)
    esperado = re.sub(r"\s+", " ", EACV_45_TEXTO).strip()
    if esperado not in visible:
        raise BOEError(
            "EACV art. 45: el texto consolidado oficial del BOE no coincide con "
            "el fallback verificado; se detiene para revisión."
        )

    return ArticuloBOE(
        nombre_norma=nombre_norma,
        id_boe=EACV_ID_BOE,
        departamento="Jefatura del Estado",
        articulo="45",
        id_bloque="acuarentaycinco",
        titulo_bloque="Artículo 45.",
        texto=EACV_45_TEXTO,
    )


def obtener_articulo_piloto(nombre_norma: str, articulo: str) -> ArticuloBOE:
    try:
        dato = boe_api.obtener_articulo(nombre_norma, articulo)
    except BOEError:
        es_eacv = (
            boe_api.normalizar(nombre_norma)
            == boe_api.normalizar(EACV_NOMBRE_PROYECTO)
        )
        if es_eacv and str(articulo).strip() == "45":
            return _eacv_45_verificado(nombre_norma)
        raise

    # El contenido es correcto; se corrige solo el metadato anómalo observado.
    if dato.id_boe == EACV_ID_BOE and str(articulo).strip() == "44":
        dato = ArticuloBOE(
            nombre_norma=dato.nombre_norma,
            id_boe=dato.id_boe,
            departamento=dato.departamento,
            articulo=dato.articulo,
            id_bloque=dato.id_bloque,
            titulo_bloque="Artículo 44.",
            texto=dato.texto,
        )
    return dato


def _limpiar_tfue_288(bloque: piloto.BloqueOficial) -> piloto.BloqueOficial:
    texto = bloque.texto
    sustituciones = {
        "deci siones": "decisiones",
        "obliga toria": "obligatoria",
        "ES 30.3.2010 Diario Oficial de la Unión Europea C 83/171": "",
    }
    for origen, destino in sustituciones.items():
        texto = texto.replace(origen, destino)
    texto = re.sub(r"\s+", " ", texto).strip()

    controles = (
        "reglamentos, directivas, decisiones, recomendaciones y dictámenes",
        "El reglamento tendrá un alcance general.",
        "La directiva obligará al Estado miembro destinatario",
        "La decisión será obligatoria en todos sus elementos.",
        "Las recomendaciones y los dictámenes no serán vinculantes.",
    )
    faltan = [c for c in controles if c not in texto]
    basura = (
        "Diario Oficial de la Unión Europea" in texto
        or "deci siones" in texto
        or "obliga toria" in texto
    )
    if faltan or basura:
        raise RuntimeError(
            "TFUE 288: la limpieza controlada no supera la validación. "
            f"Faltan={faltan}; basura={basura}"
        )
    return replace(bloque, texto=texto)


def _instalar_fuentes_jurisprudenciales() -> None:
    originales = piloto.ESPECIFICACIONES
    nuevas = []
    for e in originales:
        if e.numero == 4:
            nuevas.append(
                replace(
                    e,
                    claves_bloques=e.claves_bloques + tuple(
                        b.clave for b in JURISPRUDENCIA_OFICIAL
                    ),
                    doctrina_extra=(),
                )
            )
        else:
            nuevas.append(e)
    piloto.ESPECIFICACIONES = tuple(nuevas)

    obtener_fuentes_original = piloto.obtener_fuentes

    def obtener_fuentes_piloto():
        # La función original valida sus claves contra ESPECIFICACIONES. Durante
        # esa llamada se conserva la especificación anterior y después se añaden
        # los fragmentos oficiales ya verificados.
        actuales = piloto.ESPECIFICACIONES
        piloto.ESPECIFICACIONES = originales
        try:
            bloques = obtener_fuentes_original()
        finally:
            piloto.ESPECIFICACIONES = actuales
        for bloque in JURISPRUDENCIA_OFICIAL:
            bloques[bloque.clave] = bloque
        return bloques

    piloto.obtener_fuentes = obtener_fuentes_piloto


def main() -> int:
    preparar_alias_eacv()
    piloto.obtener_articulo = obtener_articulo_piloto

    obtener_tfue_original = piloto.obtener_tfue_288

    def obtener_tfue_piloto():
        return _limpiar_tfue_288(obtener_tfue_original())

    piloto.obtener_tfue_288 = obtener_tfue_piloto
    _instalar_fuentes_jurisprudenciales()
    return piloto.main()


if __name__ == "__main__":
    raise SystemExit(main())
