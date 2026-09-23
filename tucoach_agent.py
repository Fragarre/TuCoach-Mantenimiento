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
VERSION = "0.4.0"
INTERVALO_SEGUNDOS = 15

# Allowlist cerrada. El servidor nunca puede enviar un comando de shell.
OPERACIONES: dict[str, list[str]] = {
    "VALIDACION_COMPLETA": [
        sys.executable,
        str(RAIZ / "scripts" / "validacion_completa.py"),
    ],
    "AUDITORIA_BD": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_bd.py"),
    ],
    "AUDITORIA_BANCOS_SELECCION": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_bancos_seleccion.py"),
        "--db",
        "db/oposiciones.sqlite3",
        "--constructor",
        "scripts/mantener_banco_preguntas.py",
    ],
    "AUDITORIA_ESTRUCTURA_BANCO": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_estructura_banco.py"),
    ],
    "AUDITORIA_MATERIALES_ESTUDIO": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_materiales_estudio.py"),
        "--detalle",
    ],
    "AUDITORIA_CORPUS_TEMARIO": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_corpus_temario.py"),
    ],
    "AUDITORIA_ESQUEMA_OBSOLETO": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_esquema_obsoleto.py"),
    ],
    "INVENTARIO_DENOMINACIONES_NORMAS": [
        sys.executable,
        str(RAIZ / "scripts" / "inventariar_denominaciones_normas.py"),
    ],
    "AUDITORIA_FUNCIONAL_BANCO": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_banco_preguntas.py"),
    ],
    "AUDITORIA_CONSISTENCIA_GLOBAL": [
        sys.executable,
        str(RAIZ / "scripts" / "auditar_consistencia_global.py"),
    ],
}

OPERACIONES_CON_CONVOCATORIA = {
    "AUDITORIA_FUNCIONAL_BANCO",
    "AUDITORIA_CONSISTENCIA_GLOBAL",
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


def confirmar_estado_final(
    job_id: str,
    estado: str,
    *,
    resultado: dict[str, Any] | None = None,
    error_texto: str | None = None,
) -> bool:
    # Reintenta únicamente la notificación del resultado. Nunca reejecuta
    # el proceso local. El backend acepta ACK repetido del mismo estado final.
    for intento in range(1, 4):
        try:
            actualizar_estado(
                job_id,
                estado,
                resultado=resultado,
                error_texto=error_texto,
            )
            return True
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if intento == 3:
                print(
                    f"Resultado local obtenido para {job_id}, pero no se pudo "
                    f"confirmar el estado remoto tras {intento} intentos: {exc}"
                )
                return False
            print(
                f"Comunicación del resultado pendiente para {job_id}; "
                f"reintento {intento + 1}/3."
            )
            time.sleep(5)
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", errors="replace")
            print(
                f"Resultado local obtenido para {job_id}, pero el servidor rechazó "
                f"la actualización: HTTP {exc.code}: {detalle}"
            )
            return False
        except Exception as exc:
            print(
                f"Resultado local obtenido para {job_id}, pero falló su comunicación: "
                f"{type(exc).__name__}: {exc}"
            )
            return False
    return False

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

    if tipo in OPERACIONES_CON_CONVOCATORIA:
        if set(parametros) != {"convocatoria_id"}:
            actualizar_estado(
                job_id,
                "ERROR",
                error_texto=f"{tipo} requiere únicamente convocatoria_id.",
            )
            return
        convocatoria_id = parametros.get("convocatoria_id")
        if isinstance(convocatoria_id, bool) or not isinstance(convocatoria_id, int) or convocatoria_id <= 0:
            actualizar_estado(
                job_id,
                "ERROR",
                error_texto="convocatoria_id debe ser un entero positivo.",
            )
            return
        comando = [*comando, "--convocatoria-id", str(convocatoria_id)]
    elif parametros:
        actualizar_estado(
            job_id,
            "ERROR",
            error_texto=f"{tipo} no admite parámetros.",
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
        elif tipo == "AUDITORIA_MATERIALES_ESTUDIO" and proceso.returncode == 1:
            # En esta auditoría, 1 significa que el diagnóstico encontró
            # materiales que requieren actualización/revisión. La ejecución
            # ha sido correcta; no es un fallo técnico del agente ni del script.
            resultado["requiere_revision"] = True
            estado_final = "COMPLETADO"
            error_final = None
            mensaje = f"Trabajo completado: {job_id} | REQUIERE REVISIÓN"
        else:
            estado_final = "ERROR"
            error_final = f"{tipo} terminó con código {proceso.returncode}."
            mensaje = f"Trabajo finalizado con error: {job_id} | código {proceso.returncode}"
    except subprocess.TimeoutExpired:
        estado_final = "ERROR"
        resultado = None
        error_final = f"{tipo} superó el límite de 60 minutos."
        mensaje = f"Trabajo finalizado con error: {job_id} | timeout"
    except Exception as exc:
        estado_final = "ERROR"
        resultado = None
        error_final = f"{type(exc).__name__}: {exc}"
        mensaje = f"Trabajo finalizado con error local: {job_id} | {error_final}"

    # La ejecución local ya ha terminado. Un fallo de red al comunicar el
    # resultado no debe reinterpretarse como un fallo del proceso ni provocar
    # una segunda ejecución.
    if confirmar_estado_final(
        job_id,
        estado_final,
        resultado=resultado,
        error_texto=error_final,
    ):
        print(mensaje)


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
