"""
Limpieza consolidada de archivos auxiliares de NetReto/OpoCoach.

Por defecto SOLO muestra una propuesta de limpieza. Para ejecutar borrados o
recortes se exige --aplicar.

Principios:
- nunca toca ninguna base de datos ni las carpetas de entrada data_*;
- conserva los registros acumulativos de valor operativo (por ejemplo coste_ia.csv);
- conserva un pequeño conjunto de auditorías, backups y publicaciones recientes;
- elimina historiales de trabajo antiguos y artefactos regenerables;
- actúa también sobre las copias locales de NetReto y NetReto-Web cuando existen.

La finalidad es sustituir varias limpiezas parciales por una única revisión
controlada y trazable desde el menú de mantenimiento.
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIB = 1024 * 1024

PROTEGIDOS_REGISTROS = {
    "coste_ia.csv",
    "distribucion_temas.csv",
}

PATRONES_REGISTROS_TEMPORALES = (
    "preguntas_no_localizadas",
    "preguntas_modelo_fuera_banco",
    "preguntas_modelo_resueltas_ia",
    "preguntas_modelo_enriquecimiento_ia_boe",
    "resumen",
)

DIRECTORIOS_EXCLUIDOS_RECORRIDO = {
    ".git",
    ".venv",
    "venv",
    "env",
    "site-packages",
    "node_modules",
}

@dataclass(frozen=True)
class Candidato:
    ruta: Path
    motivo: str
    bytes: int


def _ahora() -> float:
    return time.time()


def _tamano(ruta: Path) -> int:
    if ruta.is_file():
        try:
            return ruta.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for f in ruta.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _mtime(ruta: Path) -> float | None:
    try:
        return ruta.stat().st_mtime
    except OSError:
        return None


def _familia_nombre(nombre: str) -> str:
    """Quita una marca temporal final YYYYMMDD_HHMMSS/HHMM para agrupar."""
    base = re.sub(r"[_-]\d{8}_\d{6}(?=\.|$)", "", nombre)
    base = re.sub(r"[_-]\d{8}(?=\.|$)", "", base)
    return base


def _seleccionar_antiguos_por_familia(
    carpeta: Path,
    dias: int,
    conservar_por_familia: int,
    incluir_directorios: bool = True,
) -> list[Candidato]:
    if not carpeta.is_dir():
        return []

    entradas = [p for p in carpeta.iterdir() if incluir_directorios or p.is_file()]
    entradas = [p for p in entradas if _mtime(p) is not None]
    entradas.sort(key=lambda p: _mtime(p) or 0, reverse=True)

    grupos: dict[str, list[Path]] = {}
    for p in entradas:
        grupos.setdefault(_familia_nombre(p.name), []).append(p)

    limite = _ahora() - dias * 86400
    resultado: list[Candidato] = []
    for grupo in grupos.values():
        for p in grupo[conservar_por_familia:]:
            m = _mtime(p)
            if m is not None and m < limite:
                resultado.append(
                    Candidato(
                        p,
                        f"histórico > {dias} días; se conservan {conservar_por_familia} por familia",
                        _tamano(p),
                    )
                )
    return resultado


def candidatos_auditorias(
    dias: int = 30,
    conservar_ultimas_por_familia: int = 3,
) -> list[Candidato]:
    return _seleccionar_antiguos_por_familia(
        RAIZ / "auditorias",
        dias,
        conservar_ultimas_por_familia,
        incluir_directorios=True,
    )


def candidatos_backups(
    dias: int = 14,
    conservar_ultimos_por_familia: int = 2,
) -> list[Candidato]:
    carpetas = [
        RAIZ / "db" / "copias_seguridad",
        RAIZ.parent / "NetReto" / "db" / "copias_seguridad",
        RAIZ.parent / "NetReto-Web" / "backend" / "data" / "copias_seguridad",
    ]
    resultado: list[Candidato] = []
    for carpeta in carpetas:
        resultado.extend(
            _seleccionar_antiguos_por_familia(
                carpeta,
                dias,
                conservar_ultimos_por_familia,
                incluir_directorios=False,
            )
        )
    return resultado


def candidatos_publicaciones_web(
    dias: int = 90,
    conservar_ultimas: int = 3,
) -> list[Candidato]:
    carpeta = RAIZ / "publicaciones_web"
    if not carpeta.is_dir():
        return []

    entradas = [p for p in carpeta.iterdir() if p.is_dir()]
    entradas.sort(key=lambda p: _mtime(p) or 0, reverse=True)
    limite = _ahora() - dias * 86400
    resultado: list[Candidato] = []
    for p in entradas[conservar_ultimas:]:
        m = _mtime(p)
        if m is not None and m < limite:
            resultado.append(
                Candidato(
                    p,
                    f"publicación web > {dias} días; se conservan {conservar_ultimas} últimas",
                    _tamano(p),
                )
            )
    return resultado


def candidatos_logs_rotados(dias: int = 30) -> list[Candidato]:
    carpeta = RAIZ / "logs"
    if not carpeta.is_dir():
        return []
    limite = _ahora() - dias * 86400
    resultado: list[Candidato] = []
    for p in carpeta.glob("*.log.*"):
        m = _mtime(p)
        if p.is_file() and m is not None and m < limite:
            resultado.append(Candidato(p, f"log rotado > {dias} días", _tamano(p)))
    return resultado


def candidatos_registros(dias: int = 60) -> list[Candidato]:
    carpeta = RAIZ / "registros"
    if not carpeta.is_dir():
        return []
    limite = _ahora() - dias * 86400
    resultado: list[Candidato] = []
    for p in carpeta.iterdir():
        if not p.is_file() or p.name in PROTEGIDOS_REGISTROS:
            continue
        nombre = p.stem.casefold()
        if not any(patron in nombre for patron in PATRONES_REGISTROS_TEMPORALES):
            continue
        m = _mtime(p)
        if m is not None and m < limite:
            resultado.append(
                Candidato(
                    p,
                    f"registro regenerable > {dias} días",
                    _tamano(p),
                )
            )
    return resultado


def candidatos_pycache() -> list[Candidato]:
    """Busca __pycache__ solo fuera de entornos/dependencias locales."""
    resultado: list[Candidato] = []
    bases = (RAIZ, RAIZ.parent / "NetReto", RAIZ.parent / "NetReto-Web")

    def directorio_excluido(path: Path) -> bool:
        try:
            partes = path.relative_to(RAIZ.parent).parts
        except ValueError:
            partes = path.parts
        return any(parte.casefold() in {x.casefold() for x in DIRECTORIOS_EXCLUIDOS_RECORRIDO} for parte in partes)

    for base in bases:
        if not base.exists():
            continue
        try:
            for p in base.rglob("__pycache__"):
                if p.is_dir() and not directorio_excluido(p):
                    resultado.append(Candidato(p, "artefacto Python regenerable (__pycache__)", _tamano(p)))
        except OSError:
            continue
    return resultado


def candidatos_temporales_rollback() -> list[Candidato]:
    resultado: list[Candidato] = []
    for base in (RAIZ, RAIZ.parent / "NetReto", RAIZ.parent / "NetReto-Web"):
        if not base.exists():
            continue
        try:
            for p in base.rglob("*.rollback"):
                if p.is_file():
                    resultado.append(Candidato(p, "artefacto temporal de rollback", _tamano(p)))
        except OSError:
            pass
    return resultado


def logs_a_recortar(
    max_mib: int = 10,
    conservar_mib: int = 3,
) -> list[tuple[Path, int, int]]:
    carpeta = RAIZ / "logs"
    if not carpeta.is_dir():
        return []
    resultado: list[tuple[Path, int, int]] = []
    max_bytes = max_mib * MIB
    conservar_bytes = conservar_mib * MIB
    for p in carpeta.glob("*.log"):
        try:
            tamano = p.stat().st_size
        except OSError:
            continue
        if p.is_file() and tamano > max_bytes:
            resultado.append((p, tamano, conservar_bytes))
    return resultado


def _borrar(ruta: Path) -> None:
    if ruta.is_dir():
        shutil.rmtree(ruta)
    else:
        ruta.unlink()


def _recortar_log(ruta: Path, conservar_bytes: int) -> None:
    with ruta.open("rb") as f:
        f.seek(max(0, ruta.stat().st_size - conservar_bytes))
        datos = f.read()
    salto = datos.find(b"\n")
    if salto >= 0:
        datos = datos[salto + 1 :]
    cabecera = (
        b"[NetReto] Log recortado por limpieza de mantenimiento; "
        b"se conserva la parte mas reciente.\n"
    )
    temporal = ruta.with_suffix(ruta.suffix + ".tmp_limpieza")
    temporal.write_bytes(cabecera + datos)
    temporal.replace(ruta)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Vista previa o limpieza consolidada de temporales y copias históricas."
    )
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--dias-auditorias", type=int, default=30)
    p.add_argument("--conservar-auditorias", type=int, default=3)
    p.add_argument("--dias-backups", type=int, default=14)
    p.add_argument("--conservar-backups", type=int, default=2)
    p.add_argument("--dias-publicaciones", type=int, default=90)
    p.add_argument("--conservar-publicaciones", type=int, default=3)
    p.add_argument("--dias-logs-rotados", type=int, default=30)
    p.add_argument("--dias-registros", type=int, default=60)
    p.add_argument("--max-log-mib", type=int, default=10)
    p.add_argument("--conservar-log-mib", type=int, default=3)
    args = p.parse_args()

    candidatos = (
        candidatos_auditorias(args.dias_auditorias, args.conservar_auditorias)
        + candidatos_backups(args.dias_backups, args.conservar_backups)
        + candidatos_publicaciones_web(args.dias_publicaciones, args.conservar_publicaciones)
        + candidatos_logs_rotados(args.dias_logs_rotados)
        + candidatos_registros(args.dias_registros)
        + candidatos_pycache()
        + candidatos_temporales_rollback()
    )
    recortes = logs_a_recortar(args.max_log_mib, args.conservar_log_mib)

    print("=" * 78)
    print("LIMPIEZA CONSOLIDADA DE AUXILIARES")
    print("=" * 78)
    print(f"Modo: {'APLICAR' if args.aplicar else 'SOLO VISTA PREVIA'}")
    print()
    print("Retención:")
    print(f"- auditorías:       {args.conservar_auditorias} por familia y {args.dias_auditorias} días")
    print(f"- backups:          {args.conservar_backups} por familia y {args.dias_backups} días")
    print(f"- publicaciones:    {args.conservar_publicaciones} últimas y {args.dias_publicaciones} días")
    print(f"- registros:        regenerables > {args.dias_registros} días")
    print(f"- logs rotados:     > {args.dias_logs_rotados} días")
    print(f"- log activo:       > {args.max_log_mib} MiB → conservar ~{args.conservar_log_mib} MiB")
    print("- __pycache__:      eliminar siempre (regenerable, excepto .venv/entornos)")
    print("- *.rollback:       eliminar siempre (temporal)")

    print()
    print(f"Elementos a borrar: {len(candidatos)}")
    print(f"Logs a recortar:    {len(recortes)}")

    bytes_borrar = sum(c.bytes for c in candidatos)
    for c in candidatos:
        print(f"BORRAR    {c.ruta} | {c.motivo} | {c.bytes / MIB:.2f} MiB")
    for ruta, antes, conservar in recortes:
        print(f"RECORTAR  {ruta} | {antes / MIB:.2f} MiB → aprox. {conservar / MIB:.2f} MiB")

    print()
    print("NO SE TOCA:")
    print("- ninguna SQLite/BD")
    print("- data_*/datos de entrada")
    print("- cache_boe_v2/")
    print("- registros acumulativos protegidos, incluido coste_ia.csv")
    print("- scripts/documentación/fuentes normativas")

    if not args.aplicar:
        print(f"\nEspacio potencial a liberar (borrados): {bytes_borrar / MIB:.2f} MiB")
        return 0

    errores = 0
    for c in candidatos:
        try:
            _borrar(c.ruta)
        except Exception as exc:
            errores += 1
            print(f"ERROR al borrar {c.ruta}: {exc}")

    for ruta, _, conservar in recortes:
        try:
            _recortar_log(ruta, conservar)
        except Exception as exc:
            errores += 1
            print(f"ERROR al recortar {ruta}: {exc}")

    if errores:
        print(f"\nLimpieza terminada con {errores} error(es).")
        return 1

    print("\nLimpieza aplicada correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
