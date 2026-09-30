"""Genera una muestra HTML, sin modificar la base, de preguntas `tests`.

La propuesta aplica solo reglas locales conservadoras: elimina referencias
identificativas de examen cuando son inequívocas, suaviza fórmulas genéricas
del enunciado y permuta las opciones conservando su texto y su respuesta.
"""
from __future__ import annotations

import argparse
import html
import random
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
SALIDA_DEFECTO = ROOT / "informes" / "muestra_reformulacion_tests.html"

# Una fecha aislada no basta: se exige además una marca inequívoca de origen.
PATRON_ORIGEN = re.compile(
    r"\s*[\(\[][^\(\)\[\]]*"
    r"(?:ayuntamiento|diputaci[oó]n|mancomunidad|bolsa\s+(?:de|para)|"
    r"convocatoria|oposiciones?|examen(?:\s+tipo)?|proceso\s+selectivo|"
    r"prueba\s+selectiva)"
    r"[^\(\)\[\]]*[\)\]]",
    flags=re.IGNORECASE,
)


def limpiar_origen(enunciado: str) -> tuple[str, bool]:
    resultado, reemplazos = PATRON_ORIGEN.subn("", enunciado)
    resultado = re.sub(r"\s{2,}", " ", resultado).strip(" ;,.-")
    return resultado, bool(reemplazos)


def reformular_formula(enunciado: str) -> tuple[str, bool]:
    """Cambia únicamente fórmulas de examen sin tocar referencias jurídicas."""
    texto = enunciado.strip()

    # Patrones completos frecuentes. La norma o el artículo se capturan y se
    # reinsertan literalmente: solo cambia la forma de preguntar.
    completos = (
        (
            re.compile(r"^Las normas dictadas por el Estado:?$", re.IGNORECASE),
            "Respecto de las normas dictadas por el Estado, señale la opción correcta.",
        ),
        (
            re.compile(r"^¿Cuál de los siguientes principios no está recogido en (.+?)\?$", re.IGNORECASE),
            lambda m: f"Indique el principio que no figura en {m.group(1)}.",
        ),
        (
            re.compile(r"^¿Cuál es la especialidad en relación con (.+?) según (.+?)\?$", re.IGNORECASE),
            lambda m: f"Según {m.group(2)}, ¿qué particularidad presenta {m.group(1)}?",
        ),
        (
            re.compile(r"^Señale la respuesta incorrecta, de conformidad con (.+?):$", re.IGNORECASE),
            lambda m: f"De conformidad con {m.group(1)}, identifique la afirmación incorrecta:",
        ),
        (
            re.compile(
                r"^Por lo que respecta a (.+?) y tal y como dispone (.+?), (.+?) indique cuál será éste:$",
                re.IGNORECASE,
            ),
            lambda m: (
                f"De acuerdo con {m.group(2)}, {m.group(3)} "
                "¿Cuál es el límite aplicable?"
            ),
        ),
    )
    for patron, sustitucion in completos:
        coincidencia = patron.match(texto)
        if coincidencia:
            return (
                sustitucion(coincidencia)
                if callable(sustitucion)
                else sustitucion,
                True,
            )

    reglas = (
        (
            re.compile(r"\bconforme\s+la\s+", re.IGNORECASE),
            "Según la ",
        ),
        (
            re.compile(r"\bconforme\s+el\s+", re.IGNORECASE),
            "Según el ",
        ),
        (
            re.compile(r"\bconforme\s+(?:a|al|a\s+la)\s+", re.IGNORECASE),
            "Según ",
        ),
        (
            re.compile(r"\bindique\s+(?:cu[aá]l\s+es\s+)?la\s+respuesta\s+correcta\b", re.IGNORECASE),
            "señale la afirmación correcta",
        ),
        (
            re.compile(r"\bindique\s+la\s+opci[oó]n\s+correcta\b", re.IGNORECASE),
            "señale la opción correcta",
        ),
        (
            re.compile(r"\bseñale\s+la\s+respuesta\s+correcta\b", re.IGNORECASE),
            "señale la afirmación correcta",
        ),
    )
    for patron, sustitucion in reglas:
        cambiado, cantidad = patron.subn(sustitucion, texto, count=1)
        if cantidad:
            return cambiado, True
    return enunciado, False


def propuesta(fila: sqlite3.Row) -> dict[str, object]:
    original = str(fila["enunciado"])
    limpio, borro_origen = limpiar_origen(original)
    enunciado, reformulado = reformular_formula(limpio)

    opciones_originales = [str(fila[f"opcion_{letra}"]) for letra in "abcd"]
    orden = list(range(4))
    random.Random(int(fila["id"])).shuffle(orden)
    if orden == [0, 1, 2, 3]:
        orden = [1, 2, 3, 0]
    correcta_original = str(fila["respuesta_correcta"]).strip().upper()
    indice_correcta = "ABCD".index(correcta_original)
    correcta_nueva = "ABCD"[orden.index(indice_correcta)]

    acciones = []
    if borro_origen:
        acciones.append("referencia identificativa eliminada")
    if reformulado:
        acciones.append("fórmula genérica reformulada")
    acciones.append("opciones permutadas; texto de opciones conservado")
    return {
        "id": int(fila["id"]),
        "enunciado_original": original,
        "opciones_originales": opciones_originales,
        "correcta_original": correcta_original,
        "enunciado_propuesto": enunciado,
        "opciones_propuestas": [opciones_originales[i] for i in orden],
        "correcta_propuesta": correcta_nueva,
        "acciones": acciones,
    }


def lista_opciones(opciones: list[str], correcta: str) -> str:
    return "".join(
        "<li{}><b>{})</b> {}</li>".format(
            " class=\"correcta\"" if letra == correcta else "",
            letra,
            html.escape(opcion),
        )
        for letra, opcion in zip("ABCD", opciones)
    )


def crear_html(muestras: list[dict[str, object]], salida: Path) -> None:
    tarjetas = []
    for muestra in muestras:
        tarjetas.append(
            f"""<article>
<h2>ID {muestra['id']}</h2>
<p class=\"acciones\"><b>Acciones:</b> {html.escape('; '.join(muestra['acciones']))}</p>
<section><h3>Original</h3><p>{html.escape(str(muestra['enunciado_original']))}</p>
<ol>{lista_opciones(muestra['opciones_originales'], str(muestra['correcta_original']))}</ol>
<p><b>Respuesta correcta:</b> {html.escape(str(muestra['correcta_original']))}</p></section>
<section><h3>Propuesta</h3><p>{html.escape(str(muestra['enunciado_propuesto']))}</p>
<ol>{lista_opciones(muestra['opciones_propuestas'], str(muestra['correcta_propuesta']))}</ol>
<p><b>Respuesta correcta:</b> {html.escape(str(muestra['correcta_propuesta']))}</p></section>
</article>"""
        )
    documento = f"""<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">
<title>Muestra de reformulación · tests</title><style>
body{{font-family:system-ui,sans-serif;background:#f3f6fa;color:#152033;margin:0}}
main{{max-width:1100px;margin:auto;padding:28px}}article{{background:white;border-radius:12px;padding:20px;margin:20px 0;box-shadow:0 1px 5px #0002}}
section{{border:1px solid #d6dee9;border-radius:8px;padding:14px;margin-top:12px}}h1,h2,h3{{color:#123f75}}.acciones{{color:#48566a}}
li{{margin:7px 0;padding:3px 6px}}.correcta{{background:#e2f5e7;border-radius:4px}}
</style></head><body><main><h1>Muestra de reformulación de preguntas tests</h1>
<p>Documento de revisión: no se ha modificado ninguna pregunta ni metadato de la base.</p>
<p>Las opciones verdes señalan la respuesta correcta en cada versión.</p>{''.join(tarjetas)}</main></body></html>"""
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(documento, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_DEFECTO)
    parser.add_argument("--cantidad", type=int, default=8)
    parser.add_argument(
        "--ids",
        help="IDs separados por coma para repetir una muestra concreta.",
    )
    parser.add_argument("--salida", type=Path, default=SALIDA_DEFECTO)
    args = parser.parse_args()
    if args.cantidad < 1:
        raise RuntimeError("cantidad debe ser positiva.")
    with sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        if args.ids:
            ids = [int(valor.strip()) for valor in args.ids.split(",") if valor.strip()]
            marcadores = ",".join("?" for _ in ids)
            filas = con.execute(
                f"SELECT * FROM lote_preguntas WHERE id IN ({marcadores}) "
                "AND LOWER(TRIM(tipo_fuente))='tests' ORDER BY id",
                ids,
            ).fetchall()
        else:
            filas = con.execute(
                "SELECT * FROM lote_preguntas WHERE LOWER(TRIM(tipo_fuente))='tests' ORDER BY RANDOM() LIMIT ?",
                (args.cantidad,),
            ).fetchall()
    crear_html([propuesta(fila) for fila in filas], args.salida.resolve())
    print(f"Muestra creada: {args.salida.resolve()} ({len(filas)} preguntas; base sin cambios).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
