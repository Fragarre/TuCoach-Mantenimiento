"""Compatibilidad de resolución BOE para el piloto GEN A1 / ESPECIAL 1.

El BOE identifica la Ley Orgánica 5/1982 con el título histórico
"Estatuto de Autonomía de la Comunidad Valenciana". El epígrafe/proyecto usa
la denominación actual "Comunitat Valenciana".

Además, el índice XML consolidado del BOE no está siendo resuelto correctamente
por boe_api.py para el artículo 45 de esta norma, aunque el artículo sí existe
en el texto consolidado vigente. Este lanzador aplica un fallback deliberadamente
estrecho SOLO para EACV art. 45: descarga la página consolidada oficial del BOE,
comprueba que contiene literalmente el texto vigente esperado y construye el
ArticuloBOE únicamente si esa verificación exacta supera el control.

No modifica boe_api.py y no añade ninguna opción de escritura en BD.
"""
from __future__ import annotations

import html
import re

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
        return boe_api.obtener_articulo(nombre_norma, articulo)
    except BOEError:
        es_eacv = (
            boe_api.normalizar(nombre_norma)
            == boe_api.normalizar(EACV_NOMBRE_PROYECTO)
        )
        if es_eacv and str(articulo).strip() == "45":
            return _eacv_45_verificado(nombre_norma)
        raise


def main() -> int:
    preparar_alias_eacv()
    # generar_gen_especial_01 importó obtener_articulo como símbolo local.
    # Se sustituye solo durante este lanzador para mantener el cambio aislado.
    piloto.obtener_articulo = obtener_articulo_piloto
    return piloto.main()


if __name__ == "__main__":
    raise SystemExit(main())
