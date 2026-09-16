"""
Auditoría IA de simulacros frente al PDF oficial del temario.

SOLO LECTURA respecto de la base de datos y de los PDF. Extrae texto de los
PDF, compara cada pregunta con el texto literal del temario oficial y genera
informes JSON y HTML en auditorias/. No usa temario.csv ni la base de datos.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
AUDITORIAS = ROOT / "auditorias"

# Esta auditoría no forma parte de una sesión que pueda acumular coste en BD.
os.environ.pop("OPOCOACH_MANTENIMIENTO_SESION_ID", None)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from openai_api import seleccionar_fragmento_json  # noqa: E402


ESTADOS = {"OK", "DUDOSA", "FUERA_TEMARIO"}


def texto_pdf(ruta: Path) -> str:
    with fitz.open(ruta) as doc:
        paginas = [pagina.get_text("text") for pagina in doc]
    texto = "\n".join(paginas)
    texto = texto.replace("\u00ad", "")
    return re.sub(r"[ \t]+", " ", texto)


def extraer_preguntas(texto: str) -> list[dict[str, object]]:
    """Extrae bloques que comienzan por 'N. ' y conserva su texto completo."""
    patron = re.compile(r"(?m)^\s*(\d{1,3})\.\s+(?=\S)")
    marcas = list(patron.finditer(texto))
    preguntas: list[dict[str, object]] = []
    for i, marca in enumerate(marcas):
        numero = int(marca.group(1))
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        bloque = texto[marca.start():fin].strip()
        # Los simulacros de la aplicación llevan cuatro opciones. Esta condición
        # evita confundir numeraciones internas del texto con preguntas.
        if not all(re.search(rf"(?m)^\s*{letra}\)\s+", bloque) for letra in "ABCD"):
            continue
        bloque = re.split(r"(?m)^\s*Seguridad en la respuesta:", bloque, maxsplit=1)[0].strip()
        preguntas.append({"numero": numero, "texto": bloque})
    return preguntas


def partir(lista: list[dict[str, object]], n: int) -> list[list[dict[str, object]]]:
    return [lista[i:i + n] for i in range(0, len(lista), n)]


def auditar_lote(temario: str, preguntas: list[dict[str, object]], nombre: str) -> list[dict[str, object]]:
    bloque_preguntas = "\n\n".join(str(p["texto"]) for p in preguntas)
    prompt = f"""
Eres un auditor de fidelidad de preguntas de oposición al TEMARIO OFICIAL.
Tu única fuente para decidir el ámbito material es el texto del TEMARIO OFICIAL
incluido abajo. No uses temario.csv, bases de datos ni amplíes el temario con
conocimientos externos.

Clasifica TODAS las preguntas recibidas:
- OK: el contenido preguntado encaja razonablemente en un epígrafe del temario.
- DUDOSA: el epígrafe es amplio o existe una duda real de inclusión. Ante duda,
  usa DUDOSA y nunca FUERA_TEMARIO.
- FUERA_TEMARIO: la exclusión es clara. Ejemplo conceptual: se pregunta por un
  producto o materia distinta de la que el temario delimita expresamente.

No exijas que una norma o artículo aparezca literalmente si el epígrafe oficial
abarca claramente la materia. Tampoco conviertas una coincidencia de palabras
en OK si la materia concreta está fuera del alcance descrito.

Devuelve exclusivamente JSON con esta forma:
{{"resultados":[{{"pregunta":1,"estado":"OK|DUDOSA|FUERA_TEMARIO",
"parte_oficial":"...","tema_oficial":"...","epigrafe_justificativo":"...",
"confianza":0.0,"motivo":"..."}}]}}

La confianza debe estar entre 0 y 1. El epígrafe justificativo debe ser una
cita breve o paráfrasis fiel del texto oficial; no inventes numeraciones.

TEMARIO OFICIAL:
---
{temario}
---

SIMULACRO: {nombre}
PREGUNTAS A AUDITAR:
---
{bloque_preguntas}
---
""".strip()
    datos = seleccionar_fragmento_json(
        prompt,
        modelo="gpt-5.4-nano",
        operacion="auditar_simulacro_temario_oficial",
        max_output_tokens=7000,
    )
    resultados = datos.get("resultados") if isinstance(datos, dict) else None
    if not isinstance(resultados, list):
        raise RuntimeError("La IA no devolvió la lista 'resultados'.")

    esperadas = {int(p["numero"]) for p in preguntas}
    recibidas: set[int] = set()
    salida: list[dict[str, object]] = []
    for r in resultados:
        if not isinstance(r, dict):
            raise RuntimeError("Resultado IA con formato inválido.")
        numero = int(r.get("pregunta", -1))
        estado = str(r.get("estado", "")).upper().strip()
        if numero not in esperadas or numero in recibidas or estado not in ESTADOS:
            raise RuntimeError(f"Resultado IA inválido para pregunta {numero}: {estado!r}")
        recibidas.add(numero)
        try:
            confianza = float(r.get("confianza", 0))
        except (TypeError, ValueError):
            confianza = 0.0
        r["pregunta"] = numero
        r["estado"] = estado
        r["confianza"] = max(0.0, min(1.0, confianza))
        salida.append(r)
    faltan = esperadas - recibidas
    if faltan:
        raise RuntimeError(f"La IA omitió preguntas: {sorted(faltan)}")
    return sorted(salida, key=lambda x: int(x["pregunta"]))


def generar_html(informe: dict[str, object], ruta: Path) -> None:
    filas = []
    for sim in informe["simulacros"]:
        nombre = html.escape(str(sim["archivo"]))
        for r in sim["resultados"]:
            filas.append(
                "<tr>"
                f"<td>{nombre}</td><td>{int(r['pregunta'])}</td>"
                f"<td><strong>{html.escape(str(r['estado']))}</strong></td>"
                f"<td>{float(r['confianza']):.2f}</td>"
                f"<td>{html.escape(str(r.get('parte_oficial','')))}</td>"
                f"<td>{html.escape(str(r.get('tema_oficial','')))}</td>"
                f"<td>{html.escape(str(r.get('epigrafe_justificativo','')))}</td>"
                f"<td>{html.escape(str(r.get('motivo','')))}</td>"
                "</tr>"
            )
    doc = f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<title>Auditoría simulacros ↔ temario oficial</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #bbb;padding:6px;vertical-align:top}}th{{background:#eee}}.resumen{{margin:16px 0}}</style>
</head><body><h1>Auditoría simulacros ↔ temario oficial</h1>
<p>Temario: {html.escape(str(informe['temario']))}</p>
<p class='resumen'>Total: {informe['totales']['total']} · OK: {informe['totales']['OK']} · DUDOSA: {informe['totales']['DUDOSA']} · FUERA_TEMARIO: {informe['totales']['FUERA_TEMARIO']}</p>
<table><thead><tr><th>Simulacro</th><th>Pregunta</th><th>Estado</th><th>Conf.</th><th>Parte</th><th>Tema</th><th>Epígrafe</th><th>Motivo</th></tr></thead><tbody>
{''.join(filas)}</tbody></table></body></html>"""
    ruta.write_text(doc, encoding="utf-8")


def resolver_pdf(valor: str) -> Path:
    p = Path(valor).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    if not p.is_file() or p.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"No existe un PDF válido: {p}")
    return p


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita simulacros contra el PDF oficial del temario.")
    parser.add_argument("--temario", help="PDF oficial del temario")
    parser.add_argument("--simulacro", action="append", help="PDF de simulacro; puede repetirse")
    parser.add_argument("--carpeta", help="Carpeta con PDF de simulacros")
    parser.add_argument("--lote", type=int, default=10, help="Preguntas por llamada IA (10)")
    args = parser.parse_args()

    if not args.temario:
        args.temario = input("Ruta del PDF oficial del temario: ").strip()
    temario_pdf = resolver_pdf(args.temario)

    simulacros: list[Path] = []
    for valor in args.simulacro or []:
        simulacros.append(resolver_pdf(valor))
    if args.carpeta:
        carpeta = Path(args.carpeta).expanduser()
        if not carpeta.is_absolute():
            carpeta = (ROOT / carpeta).resolve()
        if not carpeta.is_dir():
            raise FileNotFoundError(f"No existe la carpeta: {carpeta}")
        simulacros.extend(sorted(carpeta.glob("*.pdf")))
    if not simulacros:
        valor = input("Ruta de un simulacro PDF o carpeta con simulacros: ").strip()
        p = Path(valor).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        if p.is_dir():
            simulacros = sorted(p.glob("*.pdf"))
        else:
            simulacros = [resolver_pdf(valor)]
    simulacros = list(dict.fromkeys(p.resolve() for p in simulacros))
    if not simulacros:
        raise RuntimeError("No se encontraron simulacros PDF.")
    if args.lote < 1 or args.lote > 20:
        raise ValueError("--lote debe estar entre 1 y 20.")

    texto_temario = texto_pdf(temario_pdf).strip()
    if len(texto_temario) < 200:
        raise RuntimeError("No se pudo extraer suficiente texto del PDF oficial del temario.")

    print("\nAUDITORÍA SIMULACROS ↔ TEMARIO OFICIAL")
    print("SOLO LECTURA: no usa ni modifica temario.csv ni la base de datos.")
    print(f"Temario: {temario_pdf}")
    print(f"Simulacros: {len(simulacros)}")

    salida_simulacros = []
    totales = {"total": 0, "OK": 0, "DUDOSA": 0, "FUERA_TEMARIO": 0}
    for pdf in simulacros:
        preguntas = extraer_preguntas(texto_pdf(pdf))
        if not preguntas:
            raise RuntimeError(f"No se detectaron preguntas A/B/C/D en {pdf.name}")
        print(f"\n{pdf.name}: {len(preguntas)} preguntas detectadas")
        resultados: list[dict[str, object]] = []
        for n, lote in enumerate(partir(preguntas, args.lote), 1):
            print(f"  Lote {n}: preguntas {lote[0]['numero']}–{lote[-1]['numero']}")
            resultados.extend(auditar_lote(texto_temario, lote, pdf.name))
        for r in resultados:
            totales["total"] += 1
            totales[str(r["estado"])] += 1
        salida_simulacros.append({"archivo": str(pdf), "preguntas_detectadas": len(preguntas), "resultados": resultados})

    version = datetime.now().strftime("%Y%m%d_%H%M%S")
    AUDITORIAS.mkdir(parents=True, exist_ok=True)
    base = AUDITORIAS / f"auditoria_simulacros_temario_{version}"
    informe = {
        "version": version,
        "criterio": "Comparación IA directa contra PDF oficial; FUERA_TEMARIO solo ante exclusión clara; duda => DUDOSA.",
        "temario": str(temario_pdf),
        "simulacros": salida_simulacros,
        "totales": totales,
    }
    ruta_json = base.with_suffix(".json")
    ruta_html = base.with_suffix(".html")
    ruta_json.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    generar_html(informe, ruta_html)

    print("\nRESULTADO")
    print(f"Total............. {totales['total']}")
    print(f"OK................ {totales['OK']}")
    print(f"DUDOSA............ {totales['DUDOSA']}")
    print(f"FUERA_TEMARIO..... {totales['FUERA_TEMARIO']}")
    print(f"JSON: {ruta_json}")
    print(f"HTML: {ruta_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
