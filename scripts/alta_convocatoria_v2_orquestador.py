"""Orquestador aditivo para alta de convocatoria con Constructor Temario V2.

Contrato:
- si el JSON NO contiene `constructor_v2`, delega íntegramente al orquestador
  consolidado de alta;
- si contiene `constructor_v2`, valida primero PDF, selección de proceso y
  análisis V2 en SOLO LECTURA;
- sólo si esa prevalidación supera todos los controles delega el alta real al
  orquestador consolidado;
- tras un alta correcta genera el manifiesto V2 identificado con la nueva
  convocatoria, sin modificar ninguna tabla adicional.

No sustituye ni reimplementa la lógica de alta existente.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import analizar_convocatoria_v2 as analizador
import extraer_estructura_temario_v2 as estructura
import seleccionar_proceso_selectivo_v2 as procesos_v2

RAIZ = Path(__file__).resolve().parent.parent
ALTA_LEGADO = RAIZ / "scripts" / "alta_convocatoria_orquestador.py"
GESTOR_V2 = RAIZ / "scripts" / "gestionar_constructor_v2_convocatoria.py"
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def resolver_ruta(valor: str, base: Path) -> Path:
    ruta = Path(valor).expanduser()
    if not ruta.is_absolute():
        ruta = base / ruta
    return ruta.resolve()


def cargar_config(ruta: Path) -> dict[str, Any]:
    with ruta.open("r", encoding="utf-8-sig") as f:
        datos = json.load(f)
    if not isinstance(datos, dict):
        raise ValueError("La configuración debe ser un objeto JSON.")
    return datos


def validar_bloque_v2(config: Path, db: Path) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    datos = cargar_config(config)
    bloque = datos.get("constructor_v2")
    if bloque is None:
        return None, None, None
    if not isinstance(bloque, dict):
        raise ValueError("constructor_v2 debe ser un objeto JSON.")

    pdf_valor = bloque.get("pdf")
    if not isinstance(pdf_valor, str) or not pdf_valor.strip():
        raise ValueError("constructor_v2.pdf es obligatorio.")
    pdf = resolver_ruta(pdf_valor, config.parent)
    if not pdf.is_file():
        raise FileNotFoundError(f"No existe constructor_v2.pdf: {pdf}")

    proceso = bloque.get("proceso_selectivo")
    if proceso is not None and (not isinstance(proceso, str) or not proceso.strip()):
        raise ValueError("constructor_v2.proceso_selectivo debe ser texto no vacío o null.")
    proceso = proceso.strip() if isinstance(proceso, str) else None

    salida_valor = bloque.get("salida_json")
    if salida_valor is not None and (not isinstance(salida_valor, str) or not salida_valor.strip()):
        raise ValueError("constructor_v2.salida_json debe ser texto no vacío o null.")

    # Prevalidación V2 completa en solo lectura, todavía sin exigir que exista
    # la identidad de la nueva convocatoria.
    texto = estructura.extraer_texto_pdf(pdf)
    texto_filtrado, proceso_elegido, procesos_detectados = procesos_v2.seleccionar_texto(
        texto, proceso
    )
    extractor_original = analizador.estructura.extraer_texto_pdf
    analizador.estructura.extraer_texto_pdf = lambda _ruta: texto_filtrado
    try:
        manifiesto = analizador.construir_manifiesto(pdf, db)
    finally:
        analizador.estructura.extraer_texto_pdf = extractor_original

    if int((manifiesto.get("resumen") or {}).get("temas_total") or 0) <= 0:
        raise RuntimeError("La prevalidación V2 no ha extraído ningún tema.")

    bloque_validado = dict(bloque)
    bloque_validado["proceso_selectivo_resuelto"] = proceso_elegido
    bloque_validado["procesos_detectados"] = procesos_detectados
    return bloque_validado, pdf, proceso_elegido


def ejecutar(comando: list[str]) -> int:
    return subprocess.run(comando, cwd=RAIZ, check=False).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--solo-validar", action="store_true")
    ap.add_argument("--actualizar-existente", action="store_true")
    args = ap.parse_args()

    config = Path(args.config).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    if not config.is_file():
        raise FileNotFoundError(f"No existe la configuración: {config}")
    if not db.is_file():
        raise FileNotFoundError(f"No existe la BD: {db}")

    datos = cargar_config(config)
    convocatoria = datos.get("convocatoria") or {}
    codigo = convocatoria.get("codigo") if isinstance(convocatoria, dict) else None
    if not isinstance(codigo, str) or not codigo.strip():
        raise ValueError("convocatoria.codigo es obligatorio.")
    codigo = codigo.strip()

    bloque_v2, pdf, proceso = validar_bloque_v2(config, db)

    comando_alta = [sys.executable, str(ALTA_LEGADO), "--config", str(config), "--db", str(db)]
    if args.solo_validar:
        comando_alta.append("--solo-validar")
    if args.actualizar_existente:
        comando_alta.append("--actualizar-existente")

    rc = ejecutar(comando_alta)
    if rc != 0:
        return rc

    if bloque_v2 is None:
        print("Constructor V2................... NO CONFIGURADO; flujo legado intacto")
        return 0

    print("Constructor V2 prevalidación..... OK")
    print(f"PDF V2........................... {pdf}")
    print(f"Proceso selectivo................ {proceso or 'NO APLICA / ÚNICO'}")

    if args.solo_validar:
        print("Manifiesto V2.................... NO GENERADO (solo validación)")
        print("BD............................... SIN CAMBIOS POR V2")
        return 0

    salida_valor = bloque_v2.get("salida_json")
    if isinstance(salida_valor, str) and salida_valor.strip():
        salida = resolver_ruta(salida_valor, config.parent)
    else:
        salida = RAIZ / "auditorias" / "constructor_v2" / f"{codigo}_manifiesto_v2.json"

    comando_v2 = [
        sys.executable, str(GESTOR_V2),
        "--codigo", codigo,
        "--pdf", str(pdf),
        "--db", str(db),
        "--salida-json", str(salida),
    ]
    if proceso:
        comando_v2.extend(["--proceso-selectivo", proceso])

    rc = ejecutar(comando_v2)
    if rc != 0:
        raise RuntimeError(
            "El alta consolidada terminó correctamente, pero falló la materialización "
            "del manifiesto V2 pese a haber superado la prevalidación."
        )

    print("Alta consolidada.................. OK")
    print("Manifiesto Constructor V2......... OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
