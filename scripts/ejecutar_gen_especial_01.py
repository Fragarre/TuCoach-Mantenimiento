"""Compatibilidad de resolución BOE para el piloto GEN A1 / ESPECIAL 1.

El BOE identifica la Ley Orgánica 5/1982 con el título histórico
"Estatuto de Autonomía de la Comunidad Valenciana". El epígrafe/proyecto usa
la denominación actual "Comunitat Valenciana". Este lanzador registra de forma
explícita el identificador BOE verificado y delega toda la ejecución en
`generar_gen_especial_01.py`.

No añade ninguna opción de escritura en BD.
"""
from __future__ import annotations

import boe_api
import generar_gen_especial_01 as piloto


EACV_NOMBRE_PROYECTO = (
    "Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía "
    "de la Comunitat Valenciana"
)
EACV_ID_BOE = "BOE-A-1982-17235"


def preparar_alias_eacv() -> None:
    clave = boe_api.normalizar(EACV_NOMBRE_PROYECTO)
    existente = boe_api.NORMAS_ESPECIALES.get(clave)
    if existente not in (None, EACV_ID_BOE):
        raise RuntimeError(
            f"Alias EACV en conflicto: {existente} != {EACV_ID_BOE}"
        )
    boe_api.NORMAS_ESPECIALES[clave] = EACV_ID_BOE


def main() -> int:
    preparar_alias_eacv()
    return piloto.main()


if __name__ == "__main__":
    raise SystemExit(main())
