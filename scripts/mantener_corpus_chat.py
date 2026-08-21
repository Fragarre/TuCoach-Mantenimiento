"""
OpoCoach-Mantenimiento
Mantenimiento conjunto del corpus normativo del Chat.

Coordina los proveedores ya validados:
- BOE-A-*  -> ampliar_corpus_chat_fase2.py (o equivalente con --aplicar)
- DOGV     -> ampliar_corpus_dogv.py
- DOUE     -> ampliar_corpus_doue.py

Por defecto ejecuta SOLO los planes.
Con --aplicar:
1. valida primero TODOS los planes;
2. sólo si los tres terminan correctamente, ejecuta las aplicaciones;
3. al final vuelve a ejecutar los tres planes para comprobar idempotencia.

No contiene lógica normativa propia: delega en los proveedores especializados.

Uso:
    python scripts/mantener_corpus_chat.py
    python scripts/mantener_corpus_chat.py --aplicar
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"


@dataclass(frozen=True)
class Proveedor:
    nombre: str
    script: str


def script_admite_aplicar(ruta: Path) -> bool:
    resultado = subprocess.run(
        [sys.executable, str(ruta), "--help"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    salida = (resultado.stdout or "") + "\n" + (resultado.stderr or "")
    return resultado.returncode == 0 and "--aplicar" in salida


def localizar_boe() -> str:
    candidatos = (
        "ampliar_corpus_chat_fase2.py",
        "ampliar_corpus_chat.py",
    )
    encontrados = []
    for nombre in candidatos:
        ruta = SCRIPTS / nombre
        if ruta.is_file() and script_admite_aplicar(ruta):
            encontrados.append(nombre)

    if not encontrados:
        raise FileNotFoundError(
            "No se encontró un ampliador BOE con opción --aplicar. "
            "Se esperaba ampliar_corpus_chat_fase2.py o ampliar_corpus_chat.py."
        )

    # Preferimos explícitamente la fase 2, que es el aplicador validado.
    if "ampliar_corpus_chat_fase2.py" in encontrados:
        return "ampliar_corpus_chat_fase2.py"

    return encontrados[0]


def proveedores() -> list[Proveedor]:
    lista = [
        Proveedor("BOE", localizar_boe()),
        Proveedor("DOGV", "ampliar_corpus_dogv.py"),
        Proveedor("DOUE", "ampliar_corpus_doue.py"),
    ]

    faltan = [
        p.script
        for p in lista
        if not (SCRIPTS / p.script).is_file()
    ]
    if faltan:
        raise FileNotFoundError(
            "Faltan scripts de mantenimiento del corpus: "
            + ", ".join(faltan)
        )

    return lista


def ejecutar(proveedor: Proveedor, aplicar: bool) -> int:
    ruta = SCRIPTS / proveedor.script
    comando = [sys.executable, str(ruta)]
    if aplicar:
        comando.append("--aplicar")

    print()
    print("=" * 78)
    print(
        f"{proveedor.nombre} - "
        f"{'APLICACIÓN' if aplicar else 'VALIDACIÓN / PLAN'}"
    )
    print("=" * 78)
    print(" ".join(comando))
    print()

    resultado = subprocess.run(
        comando,
        cwd=RAIZ,
        check=False,
    )

    print()
    if resultado.returncode == 0:
        print(f"{proveedor.nombre}: OK")
    else:
        print(
            f"{proveedor.nombre}: ERROR "
            f"(código {resultado.returncode})"
        )
    return resultado.returncode


def validar_todos(lista: list[Proveedor]) -> bool:
    for proveedor in lista:
        if ejecutar(proveedor, aplicar=False) != 0:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Valida y, opcionalmente, actualiza el corpus del Chat "
            "mediante los proveedores BOE, DOGV y DOUE."
        )
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help=(
            "Aplica sólo después de validar correctamente todos los proveedores."
        ),
    )
    args = parser.parse_args()

    lista = proveedores()

    print("=" * 78)
    print("MANTENIMIENTO DEL CORPUS NORMATIVO DEL CHAT")
    print("=" * 78)
    print("Proveedores: BOE + DOGV + DOUE")
    print(
        "Modo: "
        + ("VALIDAR → APLICAR → REVALIDAR" if args.aplicar
           else "SOLO VALIDACIÓN / PLAN")
    )

    print()
    print("=" * 78)
    print("FASE 1 - VALIDACIÓN GLOBAL")
    print("=" * 78)

    if not validar_todos(lista):
        print()
        print("RESULTADO GLOBAL: REQUIERE REVISIÓN")
        print("No se ha iniciado ninguna aplicación.")
        return 1

    print()
    print("VALIDACIÓN DE LOS TRES PROVEEDORES: OK")

    if not args.aplicar:
        print()
        print("La base de datos no ha sido modificada por este orquestador.")
        print("Para aplicar los planes, vuelva a ejecutar con --aplicar.")
        print("RESULTADO GLOBAL: OK")
        return 0

    print()
    print("=" * 78)
    print("FASE 2 - APLICACIÓN")
    print("=" * 78)

    for proveedor in lista:
        if ejecutar(proveedor, aplicar=True) != 0:
            print()
            print(
                "RESULTADO GLOBAL: APLICACIÓN INCOMPLETA. "
                "Revise el proveedor que ha fallado y sus copias de seguridad."
            )
            return 2

    print()
    print("=" * 78)
    print("FASE 3 - COMPROBACIÓN DE IDEMPOTENCIA")
    print("=" * 78)

    if not validar_todos(lista):
        print()
        print("RESULTADO GLOBAL: REVALIDACIÓN FALLIDA")
        return 3

    print()
    print("=" * 78)
    print("RESULTADO GLOBAL: OK")
    print("Los tres proveedores han quedado validados tras la aplicación.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
