"""
Sincronización común de todos los bancos de OpoCoach.

No contiene reglas propias de selección. La única fuente de verdad es
scripts/mantener_banco_preguntas.py.

Contrato entre procesos:
- mantener_banco_preguntas.py genera auditorias/mantener_banco/resumen.json.
- este sincronizador lee ese JSON y NO interpreta textos de consola.

Proceso:
1. Ejecuta el constructor en SOLO REVISIÓN para todas las convocatorias activas.
2. Si alguna revisión falla o presenta bloqueos/incidencias, no modifica ningún banco.
3. Con --aplicar, ejecuta --guardar solo para convocatorias con novedades.
4. Ejecuta validacion_completa.py al terminar.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import comun

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
CONSTRUCTOR = SCRIPTS / "mantener_banco_preguntas.py"
VALIDADOR = SCRIPTS / "validacion_completa.py"
AUDITORIAS = RAIZ / "auditorias"


@dataclass(frozen=True)
class RevisionBanco:
    convocatoria_id: int
    codigo: str
    total_nuevas: int
    resumen: dict[str, Any]


# Migrado a scripts/comun.py: misma logica (UTF-8 forzado en el hijo,
# fallback de decodificacion con la codificacion preferida del sistema).
def _ejecutar(comando: list[str]) -> tuple[int, str]:
    return comun.ejecutar_subproceso(comando, cwd=RAIZ)


def _convocatorias(db: Path) -> list[tuple[int, str]]:
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        columnas = {
            str(r[1])
            for r in con.execute("PRAGMA table_info(convocatorias)").fetchall()
        }
        if "activa" in columnas:
            filas = con.execute(
                "SELECT id, codigo FROM convocatorias WHERE activa = 1 ORDER BY id"
            ).fetchall()
        else:
            filas = con.execute(
                "SELECT id, codigo FROM convocatorias ORDER BY id"
            ).fetchall()
    return [(int(i), str(c)) for i, c in filas]


def _estado_resumenes() -> dict[Path, tuple[int, int]]:
    """Devuelve tamaño y mtime_ns de cada resumen existente."""
    estado: dict[Path, tuple[int, int]] = {}
    if not AUDITORIAS.is_dir():
        return estado
    for ruta in AUDITORIAS.glob("mantener_banco/resumen.json"):
        try:
            st = ruta.stat()
        except OSError:
            continue
        estado[ruta.resolve()] = (st.st_size, st.st_mtime_ns)
    return estado


def _resumen_modificado(
    antes: dict[Path, tuple[int, int]],
    *,
    convocatoria_id: int,
    codigo: str,
    db: Path,
    modo_esperado: str,
) -> dict[str, Any]:
    candidatos: list[tuple[int, Path, dict[str, Any]]] = []
    despues = _estado_resumenes()

    for ruta, firma in despues.items():
        if antes.get(ruta) == firma:
            continue
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        try:
            cid = int(datos.get("convocatoria_id"))
        except (TypeError, ValueError):
            continue

        if cid != convocatoria_id:
            continue
        if str(datos.get("convocatoria_codigo") or "") != codigo:
            continue
        if str(datos.get("modo") or "") != modo_esperado:
            continue

        base_json = Path(str(datos.get("base_datos") or "")).resolve()
        if base_json != db.resolve():
            continue

        candidatos.append((firma[1], ruta, datos))

    if not candidatos:
        raise RuntimeError(
            f"{codigo}: el constructor terminó, pero no se encontró un resumen.json "
            f"nuevo o actualizado para modo {modo_esperado}."
        )

    candidatos.sort(key=lambda item: item[0], reverse=True)
    return candidatos[0][2]


def _ejecutar_constructor(
    db: Path,
    convocatoria_id: int,
    codigo: str,
    *,
    guardar: bool,
) -> tuple[dict[str, Any], str]:
    antes = _estado_resumenes()
    comando = [
        sys.executable,
        str(CONSTRUCTOR),
        "--db",
        str(db),
        "--convocatoria-id",
        str(convocatoria_id),
    ]
    if guardar:
        comando.append("--guardar")

    rc, salida = _ejecutar(comando)
    if rc != 0:
        if salida.strip():
            print(salida)
        raise RuntimeError(
            f"{codigo}: el constructor terminó con código {rc}."
        )

    modo = "GUARDAR" if guardar else "SOLO_REVISION"
    resumen = _resumen_modificado(
        antes,
        convocatoria_id=convocatoria_id,
        codigo=codigo,
        db=db,
        modo_esperado=modo,
    )
    return resumen, salida


def _validar_resumen_revision(
    convocatoria_id: int,
    codigo: str,
    resumen: dict[str, Any],
) -> RevisionBanco:
    try:
        bloqueos = int(resumen["bloqueos"])
        total_nuevas = int(resumen["total_nuevas"])
        incidencias_finales = int(resumen["incidencias_finales"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{codigo}: resumen.json incompleto o inválido: {exc}"
        ) from exc

    if bloqueos != 0:
        raise RuntimeError(
            f"{codigo}: revisión con {bloqueos} bloqueo(s). "
            "Revise la auditoría creada por mantener_banco_preguntas.py."
        )
    if incidencias_finales != 0:
        raise RuntimeError(
            f"{codigo}: la revisión detectó {incidencias_finales} incidencia(s) finales."
        )

    return RevisionBanco(
        convocatoria_id=convocatoria_id,
        codigo=codigo,
        total_nuevas=total_nuevas,
        resumen=resumen,
    )


def sincronizar_todos_bancos(
    db: Path = DB_DEFECTO,
    *,
    aplicar: bool,
    validar_final: bool = True,
) -> bool:
    db = Path(db).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")
    if not CONSTRUCTOR.is_file():
        raise FileNotFoundError(f"No existe el constructor: {CONSTRUCTOR}")
    if aplicar and validar_final and not VALIDADOR.is_file():
        raise FileNotFoundError(f"No existe el validador: {VALIDADOR}")

    convocatorias = _convocatorias(db)
    if not convocatorias:
        raise RuntimeError("No hay convocatorias configuradas.")

    print("=" * 78)
    print("SINCRONIZACIÓN COMÚN DE BANCOS")
    print("=" * 78)
    print(f"Base: {db}")
    print("Fase 1: revisión de TODAS las convocatorias activas")
    print()

    revisiones: list[RevisionBanco] = []
    for cid, codigo in convocatorias:
        resumen, _ = _ejecutar_constructor(
            db, cid, codigo, guardar=False
        )
        revision = _validar_resumen_revision(cid, codigo, resumen)
        revisiones.append(revision)
        print(
            f"  {cid} | {codigo:<24} OK | "
            f"nuevas={revision.total_nuevas}"
        )

    total = sum(r.total_nuevas for r in revisiones)
    print(f"\nTotal de vinculaciones nuevas previstas: {total}")

    if not aplicar:
        print("\nSOLO REVISIÓN: no se ha modificado ningún banco.")
        return True

    print("\nFase 2: guardado")
    for revision in revisiones:
        if revision.total_nuevas == 0:
            print(
                f"  {revision.codigo}: sin cambios; "
                "no se crea backup innecesario."
            )
            continue

        resumen_guardado, salida = _ejecutar_constructor(
            db,
            revision.convocatoria_id,
            revision.codigo,
            guardar=True,
        )

        try:
            bloqueos = int(resumen_guardado["bloqueos"])
            incidencias = int(resumen_guardado["incidencias_finales"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{revision.codigo}: resumen de guardado inválido: {exc}"
            ) from exc

        if bloqueos != 0 or incidencias != 0:
            if salida.strip():
                print(salida)
            raise RuntimeError(
                f"{revision.codigo}: el guardado terminó con "
                f"bloqueos={bloqueos}, incidencias_finales={incidencias}."
            )

        print(
            f"  {revision.codigo}: guardado correcto | "
            f"nuevas={revision.total_nuevas}"
        )

    if validar_final:
        if db != DB_DEFECTO.resolve():
            raise RuntimeError(
                "La validación completa vigente trabaja sobre la base predeterminada. "
                "No se valida automáticamente una --db alternativa."
            )
        print("\nFase 3: validación completa")
        rc, salida = _ejecutar([sys.executable, str(VALIDADOR)])
        print(salida)
        if rc != 0:
            raise RuntimeError(
                "La sincronización terminó, pero validacion_completa.py detectó incidencias."
            )

    print("\nSINCRONIZACIÓN DE BANCOS: CORRECTA")
    return True


def crear_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Revisa o sincroniza todos los bancos usando el constructor vigente."
        )
    )
    p.add_argument("--db", type=Path, default=DB_DEFECTO)
    p.add_argument("--aplicar", action="store_true")
    return p


def main() -> int:
    args = crear_parser().parse_args()
    try:
        sincronizar_todos_bancos(args.db, aplicar=args.aplicar)
        return 0
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
