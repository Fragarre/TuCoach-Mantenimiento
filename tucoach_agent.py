from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


RAIZ = Path(__file__).resolve().parent
API_BASE = os.environ.get(
    "TUCOACH_AGENT_API",
    "https://opocoach-web-staging-backend.onrender.com/api/v1/agent",
).rstrip("/")
TOKEN = os.environ.get("TUCOACH_AGENT_TOKEN", "").strip()
VERSION = "0.1.2"
INTERVALO_SEGUNDOS = 15

# Allowlist cerrada. El servidor nunca puede enviar un comando de shell.
OPERACIONES: dict[str, list[str]] = {
    "VALIDACION_COMPLETA": [
        sys.executable,
        str(RAIZ / "scripts" / "validacion_completa.py"),
    ],
}


def peticion(
    ruta: str,
    *,
    metodo: str = "POST",
    datos: dict[str, Any] | None = None,
) -> Any:
    cuerpo = json.dumps(datos or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{ruta}",
        data=cuerpo,
        method=metodo,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": f"TuCoach-Agent/{VERSION}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as respuesta:
        contenido = respuesta.read()
        return json.loads(contenido.decode("utf-8")) if contenido else None


def heartbeat() -> None:
    peticion(
        "/heartbeat",
        datos={
            "version": VERSION,
            "metadata": {
                "python": sys.version.split()[0],
                "plataforma": sys.platform,
            },
        },
    )


def actualizar_estado(
    job_id: str,
    estado: str,
    *,
    resultado: dict[str, Any] | None = None,
    error_texto: str | None = None,
) -> None:
    peticion(
        f"/jobs/{job_id}/estado",
        datos={
            "estado": estado,
            "resultado": resultado,
            "error_texto": error_texto,
        },
    )


def ejecutar_job(job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    tipo = str(job.get("tipo", "")).strip().upper()
    parametros = job.get("parametros") or {}

    comando = OPERACIONES.get(tipo)
    if comando is None:
        actualizar_estado(
            job_id,
            "ERROR",
            error_texto=f"Tipo de trabajo no permitido por el agente: {tipo}",
        )
        return

    if tipo == "VALIDACION_COMPLETA" and parametros:
        actualizar_estado(
            job_id,
            "ERROR",
            error_texto="VALIDACION_COMPLETA no admite parámetros.",
        )
        return

    actualizar_estado(job_id, "EJECUTANDO")

    try:
        proceso = subprocess.run(
            comando,
            cwd=RAIZ,
            shell=False,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60 * 60,
        )
        salida_bytes = proceso.stdout or b""
        try:
            salida = salida_bytes.decode("utf-8")
        except UnicodeDecodeError:
            salida = salida_bytes.decode("cp1252", errors="replace")

        # El resultado administrativo conserva un resumen razonable. El log
        # completo de validacion_completa permanece en el repositorio local.
        salida_resumida = salida[-20000:]
        resultado = {
            "returncode": proceso.returncode,
            "salida": salida_resumida,
        }

        if proceso.returncode == 0:
            estado_final = "COMPLETADO"
            error_final = None
            mensaje = f"Trabajo completado: {job_id} | CORRECTO"
        else:
            estado_final = "ERROR"
            error_final = f"VALIDACION_COMPLETA terminó con código {proceso.returncode}."
            mensaje = f"Trabajo finalizado con error: {job_id} | código {proceso.returncode}"
    except subprocess.TimeoutExpired:
        estado_final = "ERROR"
        resultado = None
        error_final = "VALIDACION_COMPLETA superó el límite de 60 minutos."
        mensaje = f"Trabajo finalizado con error: {job_id} | timeout"
    except Exception as exc:
        estado_final = "ERROR"
        resultado = None
        error_final = f"{type(exc).__name__}: {exc}"
        mensaje = f"Trabajo finalizado con error local: {job_id} | {error_final}"

    # La ejecución local ya ha terminado. Un fallo de red al comunicar el
    # resultado no debe reinterpretarse como un fallo del proceso ni provocar
    # una segunda ejecución.
    try:
        actualizar_estado(
            job_id,
            estado_final,
            resultado=resultado,
            error_texto=error_final,
        )
        print(mensaje)
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        print(
            f"Resultado local obtenido para {job_id}, pero no se pudo confirmar "
            f"el estado remoto: {exc}"
        )
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", errors="replace")
        print(
            f"Resultado local obtenido para {job_id}, pero el servidor rechazó "
            f"la actualización: HTTP {exc.code}: {detalle}"
        )
    except Exception as exc:
        print(
            f"Resultado local obtenido para {job_id}, pero falló su comunicación: "
            f"{type(exc).__name__}: {exc}"
        )


def ciclo() -> None:
    if not TOKEN:
        raise SystemExit(
            "Falta TUCOACH_AGENT_TOKEN. Configure la variable de entorno del usuario."
        )

    print(f"TuCoach Agent {VERSION}")
    print(f"Repositorio: {RAIZ}")
    print(f"API: {API_BASE}")
    print("Operaciones permitidas: " + ", ".join(sorted(OPERACIONES)))
    print("Ctrl+C para detener.")

    while True:
        try:
            heartbeat()
            job = peticion("/jobs/claim")
            if job:
                print(f"Trabajo recibido: {job['id']} | {job['tipo']}")
                ejecutar_job(job)
            time.sleep(INTERVALO_SEGUNDOS)
        except KeyboardInterrupt:
            print("\nAgente detenido.")
            return
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", errors="replace")
            print(f"HTTP {exc.code}: {detalle}")
            time.sleep(INTERVALO_SEGUNDOS)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            print(f"Conexión no disponible: {exc}")
            time.sleep(INTERVALO_SEGUNDOS)
        except Exception as exc:
            print(f"Error del agente: {type(exc).__name__}: {exc}")
            time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    ciclo()
