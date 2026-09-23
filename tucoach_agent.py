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
VERSION = "0.8.0"
INTERVALO_SEGUNDOS = 15

# Allowlist cerrada. El servidor nunca puede enviar un comando de shell.
OPERACIONES_DIRECTAS: dict[str, list[str]] = {
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
    "BUSCAR_NORMA_RESPUESTA_CORRECTA": [
        sys.executable,
        str(RAIZ / "scripts" / "buscar_norma_por_respuesta_correcta.py"),
    ],
}

# Las operaciones con confirmación se declararán de forma separada y deberán
# definir dos comandos distintos: REVIEW (sin escritura) y APPLY (escritura).
# Mientras este mapa esté vacío, el agente rechazará cualquier job que solicite
# confirmación aunque el servidor lo marque por error.
OPERACIONES_CONFIRMABLES: dict[str, dict[str, list[str]]] = {
    "MANTENIMIENTO_TEMARIO": {
        "REVIEW": [
            sys.executable,
            str(RAIZ / "scripts" / "preparar_mantenimiento_temario.py"),
            "--fase",
            "review",
        ],
        "APPLY": [
            sys.executable,
            str(RAIZ / "scripts" / "aplicar_mantenimiento_temario.py"),
        ],
    },
}

OPERACIONES_CON_CONVOCATORIA = {
    "AUDITORIA_FUNCIONAL_BANCO",
    "AUDITORIA_CONSISTENCIA_GLOBAL",
    "MANTENIMIENTO_TEMARIO",
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


def confirmar_inicio_ejecucion(job_id: str) -> bool:
    # El subprocess solo puede arrancar después de que el backend confirme
    # inequívocamente EJECUTANDO. Si se pierde la respuesta, repetimos
    # únicamente el ACK; el backend acepta EJECUTANDO -> EJECUTANDO de forma
    # idempotente y no crea una segunda ejecución.
    for intento in range(1, 4):
        try:
            actualizar_estado(job_id, "EJECUTANDO")
            return True
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if intento == 3:
                print(
                    f"No se pudo confirmar el inicio remoto de {job_id} tras "
                    f"{intento} intentos. El proceso local NO se ejecutará: {exc}"
                )
                return False
            print(
                f"Confirmación de inicio pendiente para {job_id}; "
                f"reintento {intento + 1}/3."
            )
            time.sleep(5)
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", errors="replace")
            print(
                f"El servidor rechazó el inicio de {job_id}: "
                f"HTTP {exc.code}: {detalle}. El proceso local NO se ejecutará."
            )
            return False
        except Exception as exc:
            print(
                f"Falló la confirmación de inicio de {job_id}: "
                f"{type(exc).__name__}: {exc}. El proceso local NO se ejecutará."
            )
            return False
    return False


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
    requiere_confirmacion = bool(job.get("requiere_confirmacion"))
    confirmado_at = job.get("confirmado_at")
    resultado_revision = job.get("resultado")

    # Protocolo REVIEW/APPLY. Ninguna operación actual requiere confirmación,
    # por lo que este soporte queda inerte hasta que se añada explícitamente
    # una operación de escritura a la allowlist.
    if confirmado_at is not None:
        if not requiere_confirmacion:
            actualizar_estado(
                job_id,
                "ERROR",
                error_texto="Trabajo confirmado que no está marcado como requiere_confirmacion.",
            )
            return
        if not isinstance(resultado_revision, dict):
            actualizar_estado(
                job_id,
                "ERROR",
                error_texto="Falta el resultado de REVIEW necesario para ejecutar APPLY.",
            )
            return
        fase = "APPLY"
    elif requiere_confirmacion:
        fase = "REVIEW"
    else:
        fase = "DIRECTA"

    if fase == "DIRECTA":
        comando = OPERACIONES_DIRECTAS.get(tipo)
    else:
        fases_operacion = OPERACIONES_CONFIRMABLES.get(tipo)
        comando = fases_operacion.get(fase) if fases_operacion else None

    if comando is None:
        actualizar_estado(
            job_id,
            "ERROR",
            error_texto=f"Tipo/fase de trabajo no permitido por el agente: {tipo}/{fase}",
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
        if tipo == "MANTENIMIENTO_TEMARIO" and fase == "APPLY":
            review = resultado_revision.get("review")
            if not isinstance(review, dict):
                actualizar_estado(
                    job_id,
                    "ERROR",
                    error_texto="Falta resultado.review estructurado para ejecutar APPLY.",
                )
                return
            if review.get("convocatoria_id") != convocatoria_id:
                actualizar_estado(
                    job_id,
                    "ERROR",
                    error_texto="La convocatoria de REVIEW no coincide con la de APPLY.",
                )
                return
            csv_sha256 = review.get("csv_sha256")
            db_sha256 = review.get("db_sha256")
            if not isinstance(csv_sha256, str) or not isinstance(db_sha256, str):
                actualizar_estado(
                    job_id,
                    "ERROR",
                    error_texto="REVIEW no contiene los SHA-256 requeridos para APPLY.",
                )
                return
            comando = [
                *comando,
                "--csv-sha256-esperado",
                csv_sha256,
                "--db-sha256-esperado",
                db_sha256,
            ]
    elif tipo == "BUSCAR_NORMA_RESPUESTA_CORRECTA":
        if not isinstance(parametros, dict):
            actualizar_estado(
                job_id,
                "ERROR",
                error_texto="Los parámetros deben ser un objeto.",
            )
            return
        claves = set(parametros)
        if claves not in ({"pregunta_id"}, {"limite"}):
            actualizar_estado(
                job_id,
                "ERROR",
                error_texto=f"{tipo} requiere exactamente pregunta_id o limite.",
            )
            return
        clave = next(iter(claves))
        valor = parametros.get(clave)
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            actualizar_estado(
                job_id,
                "ERROR",
                error_texto=f"{clave} debe ser un entero positivo.",
            )
            return
        argumento = "--id" if clave == "pregunta_id" else "--limite"
        comando = [*comando, argumento, str(valor)]
    elif parametros:
        actualizar_estado(
            job_id,
            "ERROR",
            error_texto=f"{tipo} no admite parámetros.",
        )
        return

    if not confirmar_inicio_ejecucion(job_id):
        return

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

        if fase == "REVIEW" and proceso.returncode == 0:
            try:
                review = json.loads(salida)
            except json.JSONDecodeError as exc:
                estado_final = "ERROR"
                error_final = (
                    f"{tipo} REVIEW no devolvió JSON válido: "
                    f"{exc.msg} (línea {exc.lineno}, columna {exc.colno})."
                )
                mensaje = f"Trabajo finalizado con error: {job_id} | REVIEW sin JSON válido"
            else:
                if not isinstance(review, dict):
                    estado_final = "ERROR"
                    error_final = f"{tipo} REVIEW debe devolver un objeto JSON."
                    mensaje = f"Trabajo finalizado con error: {job_id} | REVIEW JSON inválido"
                else:
                    resultado["review"] = review
                    estado_final = "ESPERANDO_CONFIRMACION"
                    error_final = None
                    mensaje = f"Revisión completada: {job_id} | ESPERANDO CONFIRMACIÓN"
        elif proceso.returncode == 0:
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
    print("Operaciones directas permitidas: " + ", ".join(sorted(OPERACIONES_DIRECTAS)))
    if OPERACIONES_CONFIRMABLES:
        print("Operaciones confirmables: " + ", ".join(sorted(OPERACIONES_CONFIRMABLES)))
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
