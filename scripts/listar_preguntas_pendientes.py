"""
Genera un listado legible de las preguntas de lote_preguntas
cuya clasificación sea PENDIENTE.

No modifica la base de datos.

Salidas predeterminadas:
    registros/preguntas_pendientes.html
    registros/preguntas_pendientes.csv

Uso:
    python scripts/listar_preguntas_pendientes.py

Opciones:
    --db RUTA
    --salida-html RUTA
    --salida-csv RUTA
"""

from __future__ import annotations

import argparse
import csv
import html
import sqlite3
from pathlib import Path
from typing import Any


RAIZ = Path(__file__).resolve().parent.parent
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
HTML_PREDETERMINADO = RAIZ / "registros" / "preguntas_pendientes.html"
CSV_PREDETERMINADO = RAIZ / "registros" / "preguntas_pendientes.csv"


CAMPOS = [
    "id",
    "enunciado",
    "opcion_a",
    "opcion_b",
    "opcion_c",
    "opcion_d",
    "respuesta_correcta",
    "tipo_clasificacion",
    "tipo_norma",
    "nombre_norma",
    "articulo",
    "tema_no_juridico",
    "origen_oposicion",
    "tipo_fuente",
    "importacion_fichero_id",
    "pagina_origen",
    "norma_id_normalizada",
    "articulo_normalizado",
    "teorica_practica",
    "tipo_norma_normalizado",
    "nombre_norma_normalizado",
]


def obtener_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Genera un listado HTML y CSV de preguntas "
            "clasificadas como PENDIENTE."
        )
    )
    parser.add_argument(
        "--db",
        default=str(DB_PREDETERMINADA),
        help="Ruta de la base de datos SQLite.",
    )
    parser.add_argument(
        "--salida-html",
        default=str(HTML_PREDETERMINADO),
        help="Ruta del fichero HTML de salida.",
    )
    parser.add_argument(
        "--salida-csv",
        default=str(CSV_PREDETERMINADO),
        help="Ruta del fichero CSV de salida.",
    )
    return parser.parse_args()


def texto(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor)


def texto_html(valor: Any) -> str:
    valor_texto = texto(valor).strip()
    if not valor_texto:
        return '<span class="vacio">—</span>'
    return html.escape(valor_texto).replace("\n", "<br>")


def leer_pendientes(db: Path) -> list[sqlite3.Row]:
    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")

    columnas = ", ".join(CAMPOS)
    consulta = f"""
        SELECT {columnas}
        FROM lote_preguntas
        WHERE UPPER(TRIM(tipo_clasificacion)) = 'PENDIENTE'
        ORDER BY id
    """

    with sqlite3.connect(db) as conexion:
        conexion.row_factory = sqlite3.Row
        return list(conexion.execute(consulta))


def escribir_csv(filas: list[sqlite3.Row], salida: Path) -> None:
    salida.parent.mkdir(parents=True, exist_ok=True)

    with salida.open("w", encoding="utf-8-sig", newline="") as fichero:
        escritor = csv.writer(fichero, delimiter=";")
        escritor.writerow(CAMPOS)

        for fila in filas:
            escritor.writerow([texto(fila[campo]) for campo in CAMPOS])


def bloque_dato(etiqueta: str, valor: Any) -> str:
    return (
        '<div class="dato">'
        f'<div class="etiqueta">{html.escape(etiqueta)}</div>'
        f'<div class="valor">{texto_html(valor)}</div>'
        "</div>"
    )


def tarjeta_pregunta(fila: sqlite3.Row) -> str:
    opciones = "".join(
        [
            bloque_dato("A", fila["opcion_a"]),
            bloque_dato("B", fila["opcion_b"]),
            bloque_dato("C", fila["opcion_c"]),
            bloque_dato("D", fila["opcion_d"]),
        ]
    )

    clasificacion = "".join(
        [
            bloque_dato("Clasificación", fila["tipo_clasificacion"]),
            bloque_dato("Respuesta correcta", fila["respuesta_correcta"]),
            bloque_dato("Teórica / práctica", fila["teorica_practica"]),
            bloque_dato("Tema no jurídico", fila["tema_no_juridico"]),
        ]
    )

    norma_original = "".join(
        [
            bloque_dato("Tipo de norma", fila["tipo_norma"]),
            bloque_dato("Nombre de norma", fila["nombre_norma"]),
            bloque_dato("Artículo", fila["articulo"]),
        ]
    )

    norma_normalizada = "".join(
        [
            bloque_dato(
                "Tipo de norma normalizado",
                fila["tipo_norma_normalizado"],
            ),
            bloque_dato(
                "Nombre de norma normalizado",
                fila["nombre_norma_normalizado"],
            ),
            bloque_dato(
                "Norma ID normalizada",
                fila["norma_id_normalizada"],
            ),
            bloque_dato(
                "Artículo normalizado",
                fila["articulo_normalizado"],
            ),
        ]
    )

    origen = "".join(
        [
            bloque_dato("Origen oposición", fila["origen_oposicion"]),
            bloque_dato("Tipo de fuente", fila["tipo_fuente"]),
            bloque_dato(
                "Importación fichero ID",
                fila["importacion_fichero_id"],
            ),
            bloque_dato("Página de origen", fila["pagina_origen"]),
        ]
    )

    return f"""
    <article class="pregunta" id="pregunta-{fila['id']}">
        <div class="cabecera">
            <h2>Pregunta ID {fila['id']}</h2>
            <a href="#pregunta-{fila['id']}">Enlace directo</a>
        </div>

        <section>
            <h3>Enunciado</h3>
            <div class="enunciado">{texto_html(fila['enunciado'])}</div>
        </section>

        <section>
            <h3>Opciones</h3>
            <div class="rejilla opciones">{opciones}</div>
        </section>

        <section>
            <h3>Clasificación actual</h3>
            <div class="rejilla">{clasificacion}</div>
        </section>

        <section>
            <h3>Datos originales</h3>
            <div class="rejilla">{norma_original}</div>
        </section>

        <section>
            <h3>Datos normalizados</h3>
            <div class="rejilla">{norma_normalizada}</div>
        </section>

        <section>
            <h3>Procedencia</h3>
            <div class="rejilla">{origen}</div>
        </section>
    </article>
    """


def escribir_html(
    filas: list[sqlite3.Row],
    salida: Path,
    db: Path,
) -> None:
    salida.parent.mkdir(parents=True, exist_ok=True)

    tarjetas = "\n".join(tarjeta_pregunta(fila) for fila in filas)

    documento = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preguntas pendientes - OpoCoach</title>
<style>
    * {{
        box-sizing: border-box;
    }}

    body {{
        margin: 0;
        font-family: Arial, Helvetica, sans-serif;
        background: #f3f4f6;
        color: #202124;
        line-height: 1.45;
    }}

    .contenedor {{
        width: min(1200px, 96%);
        margin: 24px auto 60px;
    }}

    .portada {{
        background: white;
        border: 1px solid #d9dde3;
        border-radius: 10px;
        padding: 22px;
        margin-bottom: 22px;
    }}

    .portada h1 {{
        margin: 0 0 10px;
        font-size: 28px;
    }}

    .resumen {{
        display: flex;
        flex-wrap: wrap;
        gap: 12px 24px;
        margin-top: 14px;
    }}

    .pregunta {{
        background: white;
        border: 1px solid #cfd4dc;
        border-radius: 10px;
        margin: 0 0 24px;
        overflow: hidden;
        page-break-inside: avoid;
    }}

    .cabecera {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        padding: 14px 18px;
        background: #e9edf3;
        border-bottom: 1px solid #cfd4dc;
    }}

    .cabecera h2 {{
        margin: 0;
        font-size: 21px;
    }}

    .cabecera a {{
        font-size: 14px;
    }}

    section {{
        padding: 16px 18px;
        border-bottom: 1px solid #e3e6eb;
    }}

    section:last-child {{
        border-bottom: 0;
    }}

    h3 {{
        margin: 0 0 10px;
        font-size: 16px;
    }}

    .enunciado {{
        font-size: 17px;
        font-weight: 600;
        white-space: normal;
    }}

    .rejilla {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
    }}

    .opciones {{
        grid-template-columns: 1fr;
    }}

    .dato {{
        border: 1px solid #d8dce2;
        border-radius: 7px;
        overflow: hidden;
    }}

    .etiqueta {{
        padding: 6px 9px;
        background: #f2f4f7;
        font-size: 12px;
        font-weight: bold;
        text-transform: uppercase;
    }}

    .valor {{
        padding: 9px;
        min-height: 38px;
        overflow-wrap: anywhere;
    }}

    .vacio {{
        color: #8a9099;
        font-style: italic;
    }}

    @media (max-width: 720px) {{
        .rejilla {{
            grid-template-columns: 1fr;
        }}

        .cabecera {{
            align-items: flex-start;
            flex-direction: column;
        }}
    }}

    @media print {{
        body {{
            background: white;
        }}

        .contenedor {{
            width: 100%;
            margin: 0;
        }}

        .portada,
        .pregunta {{
            border-color: #999;
            box-shadow: none;
        }}

        .cabecera a {{
            display: none;
        }}
    }}
</style>
</head>
<body>
<main class="contenedor">
    <header class="portada">
        <h1>Preguntas pendientes de lote_preguntas</h1>
        <div class="resumen">
            <div><strong>Total:</strong> {len(filas)}</div>
            <div><strong>Base:</strong> {html.escape(str(db))}</div>
        </div>
    </header>

    {tarjetas if tarjetas else '<p>No existen preguntas clasificadas como PENDIENTE.</p>'}
</main>
</body>
</html>
"""

    salida.write_text(documento, encoding="utf-8")


def main() -> int:
    argumentos = obtener_argumentos()

    db = Path(argumentos.db).expanduser().resolve()
    salida_html = Path(argumentos.salida_html).expanduser().resolve()
    salida_csv = Path(argumentos.salida_csv).expanduser().resolve()

    try:
        filas = leer_pendientes(db)
        escribir_html(filas, salida_html, db)
        escribir_csv(filas, salida_csv)

    except (OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}")
        return 1

    print(f"Preguntas PENDIENTE encontradas: {len(filas)}")
    print(f"HTML: {salida_html}")
    print(f"CSV:  {salida_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
