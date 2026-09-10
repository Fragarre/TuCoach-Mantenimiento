"""Separación segura de procesos selectivos dentro de un PDF de temario V2.

No modifica BD ni ficheros fuente. Trabaja únicamente sobre el texto ya extraído.
Si el documento contiene varios procesos selectivos exige una selección explícita.
"""
from __future__ import annotations

import re
import unicodedata

PATRON_PROCESO = re.compile(
    r"^Proceso\s+selectivo\s+(\d+)\s*:\s*(.+)$",
    re.IGNORECASE,
)


def limpiar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def normalizar(texto: str | None) -> str:
    valor = unicodedata.normalize("NFKD", texto or "")
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", valor.casefold()).strip()


def detectar_procesos(texto: str) -> list[str]:
    procesos: list[str] = []
    vistos: set[str] = set()
    for linea_original in texto.splitlines():
        linea = limpiar(linea_original)
        if not PATRON_PROCESO.match(linea):
            continue
        clave = normalizar(linea)
        if clave not in vistos:
            vistos.add(clave)
            procesos.append(linea)
    return procesos


def resolver_selector(procesos: list[str], selector: str | None) -> str | None:
    if not procesos:
        if selector:
            raise RuntimeError(
                "Se indicó --proceso-selectivo pero el PDF no contiene cabeceras "
                "'Proceso selectivo ...'."
            )
        return None

    if selector is None or not selector.strip():
        if len(procesos) == 1:
            return procesos[0]
        detalle = "\n  - " + "\n  - ".join(procesos)
        raise RuntimeError(
            "El PDF contiene varios procesos selectivos. Debe indicarse "
            "--proceso-selectivo de forma explícita:" + detalle
        )

    objetivo = normalizar(selector)
    exactos = [p for p in procesos if normalizar(p) == objetivo]
    if len(exactos) == 1:
        return exactos[0]

    parciales = [p for p in procesos if objetivo in normalizar(p)]
    if len(parciales) == 1:
        return parciales[0]
    if not parciales:
        detalle = "\n  - " + "\n  - ".join(procesos)
        raise RuntimeError(
            f"No existe un proceso selectivo que coincida con {selector!r}. "
            "Disponibles:" + detalle
        )
    detalle = "\n  - " + "\n  - ".join(parciales)
    raise RuntimeError(
        f"El selector {selector!r} es ambiguo. Coincidencias:" + detalle
    )


def seleccionar_texto(texto: str, selector: str | None) -> tuple[str, str | None, list[str]]:
    procesos = detectar_procesos(texto)
    elegido = resolver_selector(procesos, selector)
    if elegido is None:
        return texto, None, procesos

    elegido_norm = normalizar(elegido)
    salida: list[str] = []
    proceso_actual: str | None = None
    primer_proceso_visto = False

    for linea_original in texto.splitlines():
        linea = limpiar(linea_original)
        if PATRON_PROCESO.match(linea):
            primer_proceso_visto = True
            proceso_actual = linea
            continue

        # El contenido anterior al primer proceso es común al documento y se conserva.
        if not primer_proceso_visto:
            salida.append(linea_original)
            continue

        if proceso_actual is not None and normalizar(proceso_actual) == elegido_norm:
            salida.append(linea_original)

    filtrado = "\n".join(salida).strip()
    if not filtrado:
        raise RuntimeError("La selección del proceso produjo un texto vacío.")
    return filtrado, elegido, procesos
