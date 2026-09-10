"""Contrato común de publicación de contenidos V2.

No modifica SQLite ni Supabase. Determina si un snapshot es legado (16 tablas)
o V2 completo (22 tablas). Un esquema V2 parcial se considera inválido.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

TABLAS_BASE = (
    "convocatorias",
    "normas",
    "articulos_fuente",
    "documentos_corpus",
    "convocatoria_partes",
    "temarios",
    "convocatoria_documentos_corpus",
    "temario_temas",
    "convocatoria_parte_reglas",
    "convocatoria_modelo_bloques",
    "temario_referencias",
    "equivalencias_temas_no_juridicos",
    "equivalencias_normas",
    "lote_preguntas",
    "banco_preguntas",
    "banco_preguntas_temas",
)

TABLAS_V2 = (
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "fuentes_conocimiento",
    "temario_pseudoarticulo_respaldos",
    "pregunta_contextos_v2",
)

# Orden de inserción compatible con las FK V2. Las tablas de banco quedan al
# final para conservar el orden consolidado de publicación.
TABLAS_ORDEN_V2 = (
    "convocatorias",
    "normas",
    "articulos_fuente",
    "documentos_corpus",
    "convocatoria_partes",
    "temarios",
    "convocatoria_documentos_corpus",
    "temario_temas",
    "convocatoria_parte_reglas",
    "convocatoria_modelo_bloques",
    "temario_referencias",
    "equivalencias_temas_no_juridicos",
    "equivalencias_normas",
    "lote_preguntas",
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "fuentes_conocimiento",
    "temario_pseudoarticulo_respaldos",
    "pregunta_contextos_v2",
    "banco_preguntas",
    "banco_preguntas_temas",
)

DEPENDENCIAS_V2 = {
    "temario_pseudonormas": {"temario_temas"},
    "temario_pseudoarticulos": {"temario_pseudonormas"},
    "temario_pseudoarticulo_proposiciones": {"temario_pseudoarticulos"},
    "temario_pseudoarticulo_respaldos": {
        "temario_pseudoarticulos",
        "temario_pseudoarticulo_proposiciones",
        "articulos_fuente",
        "fuentes_conocimiento",
    },
    "pregunta_contextos_v2": {
        "lote_preguntas",
        "temario_temas",
        "temario_pseudoarticulos",
        "temario_referencias",
    },
}


def nombres_tablas_sqlite(con: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def resolver_desde_nombres(existentes: Iterable[str]) -> tuple[list[str], bool]:
    existentes = set(existentes)
    faltan_base = [t for t in TABLAS_BASE if t not in existentes]
    if faltan_base:
        raise RuntimeError(
            "Faltan tablas consolidadas obligatorias: " + ", ".join(faltan_base)
        )

    presentes_v2 = [t for t in TABLAS_V2 if t in existentes]
    if not presentes_v2:
        return list(TABLAS_BASE), False

    faltan_v2 = [t for t in TABLAS_V2 if t not in existentes]
    if faltan_v2:
        raise RuntimeError(
            "Esquema Constructor V2 parcial. Faltan tablas: " + ", ".join(faltan_v2)
        )

    return list(TABLAS_ORDEN_V2), True


def resolver_tablas_publicables(con: sqlite3.Connection) -> tuple[list[str], bool]:
    return resolver_desde_nombres(nombres_tablas_sqlite(con))


def validar_orden_v2() -> None:
    posiciones = {tabla: i for i, tabla in enumerate(TABLAS_ORDEN_V2)}
    if len(posiciones) != len(TABLAS_ORDEN_V2):
        raise RuntimeError("TABLAS_ORDEN_V2 contiene tablas duplicadas.")
    if set(TABLAS_ORDEN_V2) != set(TABLAS_BASE) | set(TABLAS_V2):
        raise RuntimeError("TABLAS_ORDEN_V2 no coincide con el contrato base + V2.")
    for hija, padres in DEPENDENCIAS_V2.items():
        for padre in padres:
            if posiciones[padre] >= posiciones[hija]:
                raise RuntimeError(
                    f"Orden FK inválido: {padre} debe preceder a {hija}."
                )
