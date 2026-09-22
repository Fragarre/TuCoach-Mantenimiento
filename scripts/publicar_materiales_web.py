"""Publica resúmenes validados de Mantenimiento en TuCoach-Web."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "materiales_estudio" / "resumenes"
SOURCE_CATALOG = ROOT / "materiales_estudio" / "catalogo_materiales.json"
WEB_DIR = ROOT.parent / "TuCoach-Web" / "backend" / "materiales" / "resumenes"


def _validar_pdf(ruta: Path) -> None:
    lector = PdfReader(ruta)
    texto = "".join(p.extract_text() or "" for p in lector.pages)
    if not lector.pages or "opocoach" in texto.casefold():
        raise RuntimeError(f"PDF no publicable: {ruta.name}")


def _catalogo_web(origen: list[dict]) -> list[dict]:
    salida = []
    for item in origen:
        archivo = str(item.get("archivo") or "").strip()
        if not archivo:
            continue
        salida.append({
            "norma_id": int(item["norma_id"]),
            "norma": str(item.get("norma") or ""),
            "fuente": str(item.get("fuente_canonica_actual") or item.get("fuente_canonica_inicial") or ""),
            "archivo": archivo,
            "estado": "APROBADO",
        })
    return sorted(salida, key=lambda x: x["norma_id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origen", default=str(SOURCE_DIR))
    parser.add_argument("--catalogo-origen", default=str(SOURCE_CATALOG))
    parser.add_argument("--destino", default=str(WEB_DIR))
    parser.add_argument(
        "--solo-revisados", action="store_true",
        help="Publica solo PDFs sin la marca antigua y mantiene el catálogo web actual.",
    )
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()

    origen = Path(args.origen).resolve()
    catalogo_origen = Path(args.catalogo_origen).resolve()
    destino = Path(args.destino).resolve()
    datos = json.loads(catalogo_origen.read_text(encoding="utf-8"))
    catalogo = _catalogo_web(datos)
    pdfs = [origen / item["archivo"] for item in catalogo]
    faltantes = [pdf.name for pdf in pdfs if not pdf.is_file()]
    if faltantes:
        raise RuntimeError("Faltan PDFs: " + ", ".join(faltantes))
    if args.solo_revisados:
        publicables = []
        pendientes = []
        for pdf in pdfs:
            try:
                _validar_pdf(pdf)
                publicables.append(pdf)
            except RuntimeError:
                pendientes.append(pdf.name)
        pdfs = publicables
        print("Pendientes de regeneración: " + ", ".join(pendientes or ["ninguno"]))
    else:
        for pdf in pdfs:
            _validar_pdf(pdf)

    print(f"PDF validados para publicar: {len(pdfs)}")
    print(f"Destino: {destino}")
    if not args.aplicar:
        print("SOLO PLAN: no se ha publicado ningún material.")
        return 0

    destino.mkdir(parents=True, exist_ok=True)
    backup = destino / "backups_publicacion" / datetime.now().strftime("%Y%m%d_%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        destino_pdf = destino / pdf.name
        if destino_pdf.exists():
            shutil.copy2(destino_pdf, backup / pdf.name)
        with tempfile.NamedTemporaryFile(suffix=".pdf", dir=destino, delete=False) as tmp:
            temporal = Path(tmp.name)
        try:
            shutil.copy2(pdf, temporal)
            _validar_pdf(temporal)
            temporal.replace(destino_pdf)
        finally:
            temporal.unlink(missing_ok=True)

    if args.solo_revisados:
        print("Catálogo web sin cambios (publicación parcial).")
        print(f"PUBLICADO: {len(pdfs)} PDF(s)")
        print(f"Copia de seguridad: {backup}")
        return 0

    destino_catalogo = destino / "catalogo_resumenes.json"
    if destino_catalogo.exists():
        shutil.copy2(destino_catalogo, backup / destino_catalogo.name)
    temporal_catalogo = destino_catalogo.with_suffix(".tmp")
    temporal_catalogo.write_text(
        json.dumps(catalogo, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporal_catalogo.replace(destino_catalogo)
    print(f"PUBLICADO: {len(pdfs)} PDF(s)")
    print(f"Copia de seguridad: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
