from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
WEB = RAIZ.parent / "TuCoach-Web"

BD = "backend/data/oposiciones.sqlite3"
MATERIALES = "backend/materiales/resumenes"


def git(*args: str, capturar: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=WEB,
        text=True,
        capture_output=capturar,
        check=False,
    )


def estado() -> list[str]:
    r = git("status", "--porcelain", capturar=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "No se pudo consultar Git.")
    return [x for x in r.stdout.splitlines() if x.strip()]


def ruta_de_linea(linea: str) -> str:
    ruta = linea[3:].strip()
    if " -> " in ruta:
        ruta = ruta.split(" -> ", 1)[1].strip()
    return ruta


def es_bd(ruta: str) -> bool:
    return ruta == BD


def es_material(ruta: str) -> bool:
    return ruta == MATERIALES or ruta.startswith(MATERIALES + "/")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publicación controlada de artefactos preparados en TuCoach-Web."
    )
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()

    if not WEB.is_dir():
        print(f"ERROR: no existe TuCoach-Web: {WEB}")
        return 1

    rama = git("branch", "--show-current", capturar=True)
    if rama.returncode != 0:
        print(rama.stderr.strip())
        return 1

    if rama.stdout.strip() != "main":
        print(f"ERROR: se exige rama main. Rama actual: {rama.stdout.strip()!r}")
        return 1

    lineas = estado()

    bd = [x for x in lineas if es_bd(ruta_de_linea(x))]
    materiales = [x for x in lineas if es_material(ruta_de_linea(x))]
    otros = [
        x for x in lineas
        if not es_bd(ruta_de_linea(x))
        and not es_material(ruta_de_linea(x))
    ]

    print("=" * 78)
    print("PUBLICACIÓN CONTROLADA TUCOACH-WEB")
    print("=" * 78)
    print(f"Repositorio: {WEB}")
    print("Rama:        main")

    print("\nBASE DE DATOS")
    print("-" * 78)
    print("\n".join(bd) if bd else "Sin cambios.")

    print("\nMATERIALES")
    print("-" * 78)
    print("\n".join(materiales) if materiales else "Sin cambios.")

    print("\nOTROS CAMBIOS — QUEDARÁN FUERA")
    print("-" * 78)
    print("\n".join(otros) if otros else "Ninguno.")

    if not args.aplicar:
        print("\nMODO DIAGNÓSTICO: no se ha modificado Git ni ningún archivo.")
        return 0

    disponibles = []
    if bd:
        disponibles.append(("1", "Base de datos", [BD]))
    if materiales:
        disponibles.append(("2", "Materiales", [MATERIALES]))

    if not disponibles:
        print("\nNo hay artefactos preparados pendientes de publicación.")
        return 0

    print("\nSELECCIONAR PUBLICACIÓN")
    print("-" * 78)
    if bd:
        print("1. Base de datos")
    if materiales:
        print("2. Materiales")
    if bd and materiales:
        print("3. Base de datos + materiales")
    print("0. Cancelar")

    op = input("Opción: ").strip()

    if op == "0":
        print("Operación cancelada.")
        return 0

    rutas: list[str]
    descripcion: str

    if op == "1" and bd:
        rutas = [BD]
        descripcion = "Actualizar contenidos Web"
    elif op == "2" and materiales:
        rutas = [MATERIALES]
        descripcion = "Actualizar materiales de estudio"
    elif op == "3" and bd and materiales:
        rutas = [BD, MATERIALES]
        descripcion = "Actualizar contenidos y materiales Web"
    else:
        print("Opción no válida.")
        return 1

    # Añadir EXCLUSIVAMENTE las rutas seleccionadas.
    r = git("add", "--", *rutas, capturar=True)
    if r.returncode != 0:
        print(f"\nERROR en git add:\n{r.stderr.strip()}")
        return 1

    staged = git("diff", "--cached", "--name-status", capturar=True)
    if staged.returncode != 0:
        print(f"\nERROR comprobando staging:\n{staged.stderr.strip()}")
        return 1

    nombres_staged = [
        linea.split("\t")[-1].strip()
        for linea in staged.stdout.splitlines()
        if linea.strip()
    ]

    permitidas = all(
        nombre == BD
        or nombre == MATERIALES
        or nombre.startswith(MATERIALES + "/")
        for nombre in nombres_staged
    )

    if not nombres_staged:
        print("\nNo hay cambios preparados para commit.")
        return 0

    if not permitidas:
        print("\nERROR: el staging contiene archivos no autorizados.")
        print(staged.stdout)
        print("No se realizará commit ni push.")
        return 1

    print("\nARCHIVOS QUE SE PUBLICARÁN")
    print("-" * 78)
    print(staged.stdout.rstrip())

    confirmar = input(
        "\n¿Crear commit y publicar en origin/main? [s/N]: "
    ).strip().lower()

    if confirmar not in {"s", "si", "sí"}:
        print("Publicación cancelada. No se ha creado commit ni push.")
        return 0

    r = git("commit", "-m", descripcion, capturar=True)
    if r.returncode != 0:
        print(f"\nERROR creando commit:\n{r.stdout}{r.stderr}")
        return 1

    print("\nCommit creado:")
    print(r.stdout.strip())

    r = git("push", "origin", "main", capturar=True)
    if r.returncode != 0:
        print(f"\nERROR en push:\n{r.stdout}{r.stderr}")
        print("El commit local existe, pero NO se ha confirmado su publicación remota.")
        return 1

    print("\nPush a origin/main completado.")

    local = git("rev-parse", "HEAD", capturar=True)
    remoto = git("rev-parse", "origin/main", capturar=True)

    if (
        local.returncode != 0
        or remoto.returncode != 0
        or local.stdout.strip() != remoto.stdout.strip()
    ):
        print("AVISO: no se ha podido verificar que HEAD y origin/main coincidan.")
        return 1

    print(f"Commit verificado en origin/main: {local.stdout.strip()}")
    print(
        "Render y Vercel tienen despliegue automático configurado; "
        "este script no fuerza ni da por finalizados esos despliegues."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
