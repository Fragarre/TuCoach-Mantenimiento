"""
==============================================================================
Proyecto : OpoCoach
Estado   : OK

Archivo : openai_api.py
Ruta    : scripts/openai_api.py

Objetivo:
    Centralizar las llamadas a la API de OpenAI y registrar su coste.

Entradas:
    - Prompt.
    - Modelo.
    - Parámetros de respuesta.

Salidas:
    - Respuesta estructurada y métricas de uso.

Modifica BD:
    - Solo cuando la llamada pertenece a una sesión de mantenimiento.

Tablas afectadas:
    - sesiones_coste_mantenimiento.

Utiliza:
    - Variable de entorno OPOCOACH_MANTENIMIENTO_SESION_ID.

Flujo:
    1. Carga credenciales.
    2. Ejecuta la solicitud.
    3. Si existe una sesión de mantenimiento activa, acumula el coste en BD.
    4. En cualquier otro caso, registra el coste en logs/costes_ia.csv.

==============================================================================
"""

import csv
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

clave = os.getenv("OPENAI_API_KEY_OPOCOACH")

cliente = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY_OPOCOACH")
)


PRECIOS = {
    "gpt-5-mini": {
        "input": 0.75,
        "output": 4.50,
    },
    "gpt-5.4": {
        "input": 2.50,
        "output": 15.00,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "output": 4.50,
    },
    "gpt-5.4-nano": {
        "input": 0.20,
        "cached_input": 0.02,
        "output": 1.25,
    },
}


ROOT = Path(__file__).resolve().parents[1]

LOG_COSTES = ROOT / "logs" / "costes_ia.csv"
RUTA_DB = ROOT / "db" / "oposiciones.sqlite3"
VARIABLE_SESION_MANTENIMIENTO = "OPOCOACH_MANTENIMIENTO_SESION_ID"


def _obtener_sesion_mantenimiento() -> int | None:
    valor = os.getenv(VARIABLE_SESION_MANTENIMIENTO, "").strip()

    if not valor:
        return None

    try:
        sesion_id = int(valor)
    except ValueError as exc:
        raise RuntimeError(
            f"La variable {VARIABLE_SESION_MANTENIMIENTO} "
            f"no contiene un identificador válido: {valor!r}."
        ) from exc

    if sesion_id <= 0:
        raise RuntimeError(
            f"El identificador de sesión debe ser positivo: {sesion_id}."
        )

    return sesion_id


def _registrar_coste_mantenimiento(
    sesion_id: int,
    coste: float,
) -> None:
    if not RUTA_DB.is_file():
        raise RuntimeError(
            f"No existe la base de datos de OpoCoach: {RUTA_DB}"
        )

    with sqlite3.connect(RUTA_DB, timeout=30) as conexion:
        cursor = conexion.execute(
            """
            UPDATE sesiones_coste_mantenimiento
            SET coste_total = coste_total + ?,
                numero_llamadas = numero_llamadas + 1,
                saldo_estimado = saldo_inicial - (coste_total + ?)
            WHERE id = ?
              AND estado = 'EN_CURSO'
            """,
            (coste, coste, sesion_id),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "No se pudo registrar el coste en la sesión de mantenimiento "
                f"{sesion_id}. La sesión no existe o no está EN_CURSO."
            )


def registrar_coste(
    modelo,
    operacion,
    tiempo,
    input_tokens,
    cached_tokens,
    output_tokens,
    coste,
):
    sesion_id = _obtener_sesion_mantenimiento()

    if sesion_id is not None:
        _registrar_coste_mantenimiento(
            sesion_id=sesion_id,
            coste=coste,
        )
        return

    LOG_COSTES.parent.mkdir(exist_ok=True)

    existe = LOG_COSTES.exists()

    with LOG_COSTES.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.writer(f)

        if not existe:
            writer.writerow([
                "fecha",
                "hora",
                "modelo",
                "operacion",
                "tiempo",
                "input",
                "cached_input",
                "output",
                "coste",
            ])

        ahora = datetime.now()

        writer.writerow([
            ahora.strftime("%Y-%m-%d"),
            ahora.strftime("%H:%M:%S"),
            modelo,
            operacion,
            f"{tiempo:.2f}",
            input_tokens,
            cached_tokens,
            output_tokens,
            f"{coste:.6f}",
        ])


def llamar_responses(
    input_api,
    modelo="gpt-5.4-nano",
    operacion="general",
    formato_texto=None,
    max_output_tokens=4096,
):
    if modelo not in PRECIOS:
        raise ValueError(
            f"No existen precios configurados "
            f"para el modelo {modelo!r}."
        )

    t0 = time.perf_counter()

    if not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
        raise ValueError("max_output_tokens debe ser un entero positivo.")

    parametros = {
        "model": modelo,
        "input": input_api,
        "max_output_tokens": max_output_tokens,
    }

    if formato_texto is not None:
        parametros["text"] = {
            "format": formato_texto,
        }

    respuesta = cliente.responses.create(**parametros)

    tiempo = time.perf_counter() - t0

    uso = respuesta.usage

    entrada = uso.input_tokens
    salida = uso.output_tokens

    cached = 0

    try:
        cached = uso.input_tokens_details.cached_tokens
    except Exception:
        pass

    precio = PRECIOS[modelo]

    input_real = max(0, entrada - cached)

    precio_cached = precio.get("cached_input", precio["input"])

    coste = (
        input_real * precio["input"]
        + cached * precio_cached
        + salida * precio["output"]
    ) / 1_000_000

    registrar_coste(
        modelo=modelo,
        operacion=operacion,
        tiempo=tiempo,
        input_tokens=entrada,
        cached_tokens=cached,
        output_tokens=salida,
        coste=coste,
    )

    print()
    print("=" * 60)
    print("IA")
    print("=" * 60)
    print(f"Modelo............. {modelo}")
    print(f"Operación.......... {operacion}")
    print(f"Tiempo............. {tiempo:.2f} s")
    print(f"Input.............. {entrada}")
    print(f"Cached............. {cached}")
    print(f"Output............. {salida}")
    print(f"Coste.............. ${coste:.6f}")
    print()

    return respuesta, tiempo


def seleccionar_fragmento(
    prompt,
    modelo="gpt-5.4-nano",
    operacion="general",
):
    respuesta, _ = llamar_responses(
        input_api=prompt,
        modelo=modelo,
        operacion=operacion,
    )

    return respuesta.output_text


def seleccionar_fragmento_json(
    prompt,
    modelo="gpt-5.4-nano",
    operacion="general",
    max_output_tokens=4096,
):
    """
    Ejecuta una llamada en modo JSON y convierte la respuesta.
    """
    import json
    import re

    respuesta_api, _ = llamar_responses(
        input_api=prompt,
        modelo=modelo,
        operacion=operacion,
        formato_texto={
            "type": "json_object",
        },
        max_output_tokens=max_output_tokens,
    )

    texto = str(respuesta_api.output_text or "").strip()

    bloque = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        texto,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if bloque:
        texto = bloque.group(1).strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "La API estaba configurada en modo JSON, pero la respuesta "
            "no pudo analizarse. "
            f"Línea {exc.lineno}, columna {exc.colno}. "
            f"Respuesta recibida: {texto[:1000]}"
        ) from exc

def generar_explicacion_ia(
    prompt,
    modelo="gpt-5.4-nano",
):
    return seleccionar_fragmento_json(
        prompt=prompt,
        modelo=modelo,
        operacion="explicacion",
    )