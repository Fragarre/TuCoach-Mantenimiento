"""Integra el Constructor Temario V2 con una convocatoria existente.

Capa común para alta/mantenimiento. SOLO LECTURA respecto de SQLite.
Recibe una convocatoria ya dada de alta y el PDF oficial del temario, ejecuta
el analizador general V2 y genera un manifiesto identificado inequívocamente
con convocatoria y temario.

No modifica temario.csv, corpus, lote_preguntas ni bancos.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import analizar_convocatoria_v2 as analizador

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def cargar_identidad(db: Path, codigo: str) -> dict:
    uri = db.as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        conv = con.execute(
            "SELECT id,codigo,puesto,numero,anio FROM convocatorias WHERE codigo=?",
            (codigo,),
        ).fetchone()
        if conv is None:
            raise RuntimeError(f"No existe la convocatoria {codigo!r}.")
        temarios = con.execute(
            "SELECT id,nombre FROM temarios WHERE convocatoria_id=? ORDER BY id",
            (int(conv["id"]),),
        ).fetchall()
        if len(temarios) != 1:
            raise RuntimeError(
                "La convocatoria debe tener exactamente un temario para integrar V2; "
                f"encontrados={len(temarios)}."
            )
        return {
            "convocatoria_id": int(conv["id"]),
            "codigo": str(conv["codigo"]),
            "puesto": conv["puesto"],
            "numero": conv["numero"],
            "anio": conv["anio"],
            "temario_id": int(temarios[0]["id"]),
            "temario_nombre": temarios[0]["nombre"],
        }
    finally:
        con.close()


def construir(codigo: str, pdf: Path, db: Path) -> dict:
    identidad = cargar_identidad(db, codigo)
    manifiesto = analizador.construir_manifiesto(pdf, db)
    manifiesto["convocatoria"] = identidad
    manifiesto["naturaleza"] = "MANIFIESTO_CONVOCATORIA_CONSTRUCTOR_V2"
    manifiesto["reglas"]["convocatoria_identificada"] = True
    return manifiesto


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Analiza con Constructor V2 una convocatoria existente sin modificar SQLite."
    )
    ap.add_argument("--codigo", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    pdf = Path(args.pdf).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    if not db.is_file():
        raise RuntimeError(f"No existe la BD: {db}")
    if not pdf.is_file():
        raise RuntimeError(f"No existe el PDF oficial: {pdf}")

    antes = sha256(db)
    manifiesto = construir(args.codigo.strip(), pdf, db)
    despues = sha256(db)
    if antes != despues:
        raise RuntimeError("La BD cambió durante el análisis V2.")

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    resumen = manifiesto["resumen"]
    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - INTEGRACION POR CONVOCATORIA")
    print("=" * 78)
    print(f"Convocatoria..................... {manifiesto['convocatoria']['codigo']}")
    print(f"Convocatoria ID.................. {manifiesto['convocatoria']['convocatoria_id']}")
    print(f"Temario ID....................... {manifiesto['convocatoria']['temario_id']}")
    print(f"Temas analizados................. {resumen['temas_total']}")
    print(f"Revisión humana.................. {resumen['requieren_revision_humana']}")
    print(f"Bloqueantes...................... {resumen['bloqueantes']}")
    print(f"Estado V2........................ {manifiesto['estado']}")
    print(f"Manifiesto....................... {salida}")
    print("temario.csv...................... SIN CAMBIOS")
    print("Corpus........................... SIN CAMBIOS")
    print("lote_preguntas................... SIN CAMBIOS")
    print("Banco............................ SIN CAMBIOS")
    print("BD............................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
