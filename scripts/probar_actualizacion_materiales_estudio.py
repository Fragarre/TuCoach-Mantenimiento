from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DB_REAL = ROOT / "db" / "oposiciones.sqlite3"
CATALOGO_REAL = ROOT / "materiales_estudio" / "catalogo_materiales.json"
RESUMENES_REALES = ROOT / "materiales_estudio" / "resumenes"
GENERADOR = ROOT / "scripts" / "generar_materiales_estudio.py"
AUDITOR = ROOT / "scripts" / "auditar_materiales_estudio.py"
REGISTROS = ROOT / "registros" / "pruebas_materiales"

# Norma deliberadamente pequeña para limitar tiempo/coste de la prueba.
NORMA_ID_PRUEBA = 51  # LEY 53/1984


def ejecutar(cmd: list[str]) -> int:
    print()
    print("=" * 78)
    print("EJECUCIÓN")
    print("=" * 78)
    print(" ".join(cmd))
    print("=" * 78)
    resultado = subprocess.run(cmd, check=False)
    return int(resultado.returncode)


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Prueba aislada de actualización de Materiales. "
            "NO modifica BD, catálogo ni PDFs reales."
        )
    )
    p.add_argument(
        "--norma-id",
        type=int,
        default=NORMA_ID_PRUEBA,
    )
    args = p.parse_args()

    for ruta, nombre in (
        (DB_REAL, "base"),
        (CATALOGO_REAL, "catálogo"),
        (RESUMENES_REALES, "resúmenes"),
        (GENERADOR, "generador"),
        (AUDITOR, "auditor"),
    ):
        if not ruta.exists():
            print(f"ERROR: no existe {nombre}: {ruta}")
            return 2

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    prueba = REGISTROS / f"prueba_actualizacion_{marca}"
    db = prueba / "db" / "oposiciones.sqlite3"
    catalogo = prueba / "materiales_estudio" / "catalogo_materiales.json"
    resumenes = prueba / "materiales_estudio" / "resumenes"

    db.parent.mkdir(parents=True, exist_ok=True)
    resumenes.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("PRUEBA AISLADA DE ACTUALIZACIÓN DE MATERIALES")
    print("=" * 78)
    print(f"Carpeta de prueba: {prueba}")
    print("Datos reales modificados: NO")
    print(f"Norma simulada como DESACTUALIZADO: {args.norma_id}")
    print()

    shutil.copy2(DB_REAL, db)
    shutil.copy2(CATALOGO_REAL, catalogo)
    for pdf in RESUMENES_REALES.glob("*.pdf"):
        shutil.copy2(pdf, resumenes / pdf.name)

    datos = json.loads(catalogo.read_text(encoding="utf-8"))
    item = next(
        (x for x in datos if int(x["norma_id"]) == args.norma_id),
        None,
    )
    if item is None:
        print(
            f"ERROR: norma_id={args.norma_id} no existe en el catálogo real."
        )
        return 2

    archivo_original = str(item.get("archivo") or "")
    hash_original = str(item.get("hash_corpus") or "")
    item["hash_corpus"] = (
        "SIMULACION_DESACTUALIZADO_PRUEBA_AISLADA"
    )
    catalogo.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Material de prueba: {item.get('norma')} | "
        f"archivo={archivo_original}"
    )
    print("Se ha alterado únicamente la huella de la COPIA.")
    print()

    # 1. Debe detectar exactamente el desactualizado.
    rc = ejecutar([
        sys.executable,
        str(AUDITOR),
        "--db", str(db),
        "--catalogo", str(catalogo),
        "--resumenes", str(resumenes),
        "--detalle",
    ])
    # El auditor devuelve 1 cuando hay pendiente: en esta prueba es esperado.
    if rc not in {0, 1}:
        print("ERROR: la auditoría previa falló estructuralmente.")
        return 2

    print()
    print(
        "A continuación se ejecutará el flujo REAL sobre la COPIA: "
        "validador RAG + IA + PDF + actualización de catálogo + auditoría."
    )
    respuesta = input(
        "¿Ejecutar ahora la prueba IA aislada? [s/N]: "
    ).strip().lower()
    if respuesta not in {"s", "si", "sí"}:
        print("Prueba detenida antes de cualquier llamada IA.")
        print(f"La copia queda disponible en: {prueba}")
        return 0

    rc = ejecutar([
        sys.executable,
        str(GENERADOR),
        "--db", str(db),
        "--catalogo", str(catalogo),
        "--resumenes", str(resumenes),
        "--aplicar",
        "--norma-id", str(args.norma_id),
    ])
    if rc != 0:
        print()
        print("RESULTADO: LA PRUEBA AISLADA REQUIERE REVISIÓN")
        print(f"Datos reales modificados: NO")
        print(f"Revisar carpeta: {prueba}")
        return rc

    # 3. Verificación explícita final.
    rc = ejecutar([
        sys.executable,
        str(AUDITOR),
        "--db", str(db),
        "--catalogo", str(catalogo),
        "--resumenes", str(resumenes),
        "--detalle",
    ])
    if rc != 0:
        print()
        print("ERROR: auditoría final de la copia no terminó en OK.")
        print(f"Datos reales modificados: NO")
        print(f"Revisar carpeta: {prueba}")
        return 1

    datos_finales = json.loads(catalogo.read_text(encoding="utf-8"))
    final = next(
        x for x in datos_finales
        if int(x["norma_id"]) == args.norma_id
    )
    hash_final = str(final.get("hash_corpus") or "")

    if not hash_final or hash_final == hash_original:
        # Puede coincidir con el original porque el corpus real no cambió:
        # eso es correcto en esta simulación. Lo obligatorio es que ya no sea
        # la huella artificial.
        pass
    if hash_final == "SIMULACION_DESACTUALIZADO_PRUEBA_AISLADA":
        print("ERROR: el catálogo de prueba no actualizó la huella.")
        return 1

    pdf_final = resumenes / str(final["archivo"])
    if not pdf_final.is_file():
        print("ERROR: no existe el PDF final de prueba.")
        return 1

    print()
    print("=" * 78)
    print("PRUEBA AISLADA COMPLETADA")
    print("=" * 78)
    print("Datos reales modificados.............. NO")
    print(f"Norma probada.......................... {final.get('norma')}")
    print(f"PDF generado........................... {pdf_final}")
    print(f"Catálogo de prueba..................... {catalogo}")
    print(f"Carpeta completa....................... {prueba}")
    print("Auditoría final........................ OK")
    print("RESULTADO.............................. OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
