"""
Limpieza conservadora de temporales de OpoCoach-Mantenimiento.

Por defecto SOLO muestra qué haría. Para borrar/recortar se exige --aplicar.

No toca db/, data_*/, cache_boe_v2/, registros/coste_ia.csv ni
registros/preguntas_ia_ultima_ejecucion.html.
"""

from __future__ import annotations

import argparse
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AUDITORIAS = RAIZ / "auditorias"
LOGS = RAIZ / "logs"
REGISTROS = RAIZ / "registros"
MIB = 1024 * 1024

@dataclass
class Candidato:
    ruta: Path
    motivo: str
    bytes: int

def _tamano(ruta: Path) -> int:
    if ruta.is_file():
        try:
            return ruta.stat().st_size
        except OSError:
            return 0
    total = 0
    for f in ruta.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total

def candidatos_auditorias(dias: int = 60, conservar_ultimas: int = 20) -> list[Candidato]:
    if not AUDITORIAS.exists():
        return []
    entradas = list(AUDITORIAS.iterdir())
    entradas.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    protegidas = set(entradas[:conservar_ultimas])
    limite = time.time() - dias * 86400
    resultado = []
    for p in entradas:
        if p in protegidas:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < limite:
            resultado.append(Candidato(p, f"auditoría > {dias} días", _tamano(p)))
    return resultado

def candidatos_logs_rotados(dias: int = 90) -> list[Candidato]:
    if not LOGS.exists():
        return []
    limite = time.time() - dias * 86400
    resultado = []
    for p in LOGS.glob("*.log.*"):
        if p.is_file() and p.stat().st_mtime < limite:
            resultado.append(Candidato(p, f"log rotado > {dias} días", p.stat().st_size))
    return resultado

def logs_a_recortar(max_mib: int = 5, conservar_mib: int = 2) -> list[tuple[Path, int, int]]:
    if not LOGS.exists():
        return []
    resultado = []
    max_bytes = max_mib * MIB
    conservar_bytes = conservar_mib * MIB
    for p in LOGS.glob("*.log"):
        if p.is_file() and p.stat().st_size > max_bytes:
            resultado.append((p, p.stat().st_size, conservar_bytes))
    return resultado

def _borrar(ruta: Path) -> None:
    shutil.rmtree(ruta) if ruta.is_dir() else ruta.unlink()

def _recortar_log(ruta: Path, conservar_bytes: int) -> None:
    with ruta.open("rb") as f:
        f.seek(max(0, ruta.stat().st_size - conservar_bytes))
        datos = f.read()
    salto = datos.find(b"\n")
    if salto >= 0:
        datos = datos[salto + 1:]
    cabecera = (
        b"[OpoCoach] Log recortado por limpieza de mantenimiento; "
        b"se conserva la parte mas reciente.\n"
    )
    temporal = ruta.with_suffix(ruta.suffix + ".tmp_limpieza")
    temporal.write_bytes(cabecera + datos)
    temporal.replace(ruta)

def main() -> int:
    p = argparse.ArgumentParser(description="Vista previa o limpieza conservadora de temporales.")
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--dias-auditorias", type=int, default=60)
    p.add_argument("--conservar-auditorias", type=int, default=20)
    p.add_argument("--dias-logs-rotados", type=int, default=90)
    args = p.parse_args()

    candidatos = (
        candidatos_auditorias(args.dias_auditorias, args.conservar_auditorias)
        + candidatos_logs_rotados(args.dias_logs_rotados)
    )
    recortes = logs_a_recortar()

    print("=" * 78)
    print("LIMPIEZA CONSERVADORA DE TEMPORALES")
    print("=" * 78)
    print(f"Modo: {'APLICAR' if args.aplicar else 'SOLO VISTA PREVIA'}")
    print(f"Elementos a borrar: {len(candidatos)}")
    print(f"Logs a recortar:    {len(recortes)}")

    bytes_borrar = sum(c.bytes for c in candidatos)
    for c in candidatos:
        print(f"BORRAR  {c.ruta} | {c.motivo} | {c.bytes / MIB:.2f} MiB")
    for ruta, antes, conservar in recortes:
        print(
            f"RECORTAR {ruta} | {antes / MIB:.2f} MiB -> aprox. {conservar / MIB:.2f} MiB"
        )

    if REGISTROS.exists():
        print(
            f"\nregistros/ ocupa {_tamano(REGISTROS) / MIB:.2f} MiB. "
            "No se borra genéricamente por seguridad."
        )
    print("cache_boe_v2/: no se toca por defecto.")

    if not args.aplicar:
        print(f"\nEspacio potencial a liberar (borrados): {bytes_borrar / MIB:.2f} MiB")
        return 0

    for c in candidatos:
        _borrar(c.ruta)
    for ruta, _, conservar in recortes:
        _recortar_log(ruta, conservar)

    print("\nLimpieza aplicada correctamente.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
