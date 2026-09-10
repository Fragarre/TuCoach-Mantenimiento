"""
Smoke test aislado del cargador de persistencia V2.

Nunca escribe en la BD real. Trabaja sobre una copia temporal y un manifiesto
sintético derivado de un tema V2 ya completado, remapeado a un tema existente
sólo para validar la fontanería de persistencia.

Valida:
- modo revisión sin cambios;
- carga efectiva sólo en las cinco tablas V2 de la copia;
- segunda carga idempotente;
- tablas consolidadas de la copia sin cambios durante la carga;
- foreign_key_check OK;
- BD real intacta.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
DB_REAL = RAIZ / "db" / "oposiciones.sqlite3"
ORIGEN = RAIZ / "auditorias" / "constructor_v2" / "smoke_delimitable" / "A1_GENERAL_2_completado.json"
TMPDIR = RAIZ / "auditorias" / "constructor_v2" / "smoke_persistencia"
DB_TMP = TMPDIR / "oposiciones_smoke.sqlite3"
MAN_TMP = TMPDIR / "manifiesto_smoke.json"
CONVOCATORIA = "C1-01_58_26"
TABLAS_V2 = (
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "fuentes_conocimiento",
    "temario_pseudoarticulo_respaldos",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def ejecutar(args: list[str]) -> None:
    print("\n" + "=" * 78)
    print(" ".join(args))
    print("=" * 78)
    r = subprocess.run(args, cwd=RAIZ, check=False)
    if r.returncode:
        raise RuntimeError(f"Comando fallido ({r.returncode}): {args[1]}")


def huellas_tablas_consolidadas(db: Path) -> dict[str, str]:
    with sqlite3.connect(db) as con:
        tablas = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            if r[0] not in TABLAS_V2
        ]
        salida = {}
        for tabla in tablas:
            filas = con.execute(f'SELECT * FROM "{tabla}"').fetchall()
            payload = repr(filas).encode("utf-8")
            salida[tabla] = hashlib.sha256(payload).hexdigest()
        return salida


def preparar_manifiesto() -> tuple[str, int, int]:
    data = json.loads(ORIGEN.read_text(encoding="utf-8"))
    candidatos = [
        x for x in data.get("temas", [])
        if str(x.get("parte") or "").upper() == "GENERAL"
        and int(x.get("numero_tema")) == 2
        and x.get("pseudoarticulos")
    ]
    if len(candidatos) != 1:
        raise RuntimeError(f"Tema fuente GENERAL 2 localizados={len(candidatos)}")
    item = candidatos[0]

    with sqlite3.connect(DB_TMP) as con:
        fila = con.execute(
            """
            SELECT tt.parte, tt.numero_tema
            FROM temario_temas tt
            JOIN temarios t ON t.id=tt.temario_id
            JOIN convocatorias c ON c.id=t.convocatoria_id
            WHERE c.codigo=?
            ORDER BY tt.id
            LIMIT 1
            """,
            (CONVOCATORIA,),
        ).fetchone()
    if fila is None:
        raise RuntimeError(f"No hay temario_temas para {CONVOCATORIA} en la copia.")

    item = json.loads(json.dumps(item))
    item["parte"] = str(fila[0])
    item["numero_tema"] = int(fila[1])
    data["temas"] = [item]
    MAN_TMP.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(fila[0]), int(fila[1]), len(item.get("pseudoarticulos") or [])


def contar_v2(db: Path) -> dict[str, int]:
    with sqlite3.connect(db) as con:
        return {t: int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in TABLAS_V2}


def main() -> int:
    if not DB_REAL.is_file():
        raise RuntimeError(f"Falta BD real: {DB_REAL}")
    if not ORIGEN.is_file():
        raise RuntimeError(f"Falta manifiesto origen: {ORIGEN}")

    TMPDIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_REAL, DB_TMP)
    hash_real_antes = sha256(DB_REAL)
    parte, numero, pseudo_esperados = preparar_manifiesto()
    consolidadas_antes = huellas_tablas_consolidadas(DB_TMP)
    v2_antes = contar_v2(DB_TMP)

    cmd_base = [
        sys.executable,
        str(SCRIPTS / "cargar_persistencia_constructor_v2.py"),
        "--manifiesto", str(MAN_TMP),
        "--convocatoria", CONVOCATORIA,
        "--db", str(DB_TMP),
    ]

    ejecutar(cmd_base)
    if contar_v2(DB_TMP) != v2_antes:
        raise RuntimeError("El modo SOLO REVISION modificó tablas V2.")
    if huellas_tablas_consolidadas(DB_TMP) != consolidadas_antes:
        raise RuntimeError("El modo SOLO REVISION modificó tablas consolidadas.")

    ejecutar(cmd_base + ["--aplicar"])
    primera = contar_v2(DB_TMP)
    if primera["temario_pseudonormas"] != 1:
        raise RuntimeError(f"Pseudonormas esperadas=1, obtenidas={primera['temario_pseudonormas']}")
    if primera["temario_pseudoarticulos"] != pseudo_esperados:
        raise RuntimeError(
            f"Pseudoartículos esperados={pseudo_esperados}, obtenidos={primera['temario_pseudoarticulos']}"
        )
    if huellas_tablas_consolidadas(DB_TMP) != consolidadas_antes:
        raise RuntimeError("La carga modificó tablas consolidadas de la copia.")

    ejecutar(cmd_base + ["--aplicar"])
    segunda = contar_v2(DB_TMP)
    if segunda != primera:
        raise RuntimeError(f"Segunda carga no idempotente: primera={primera}, segunda={segunda}")
    if huellas_tablas_consolidadas(DB_TMP) != consolidadas_antes:
        raise RuntimeError("La segunda carga modificó tablas consolidadas de la copia.")

    with sqlite3.connect(DB_TMP) as con:
        con.execute("PRAGMA foreign_keys=ON")
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check: {len(fk)} incidencias")

    if sha256(DB_REAL) != hash_real_antes:
        raise RuntimeError("La BD real fue modificada durante el smoke test.")

    print("\n" + "=" * 78)
    print("SMOKE TEST CARGADOR PERSISTENCIA V2: OK")
    print("=" * 78)
    print(f"Tema temporal mapeado............. {CONVOCATORIA} {parte} {numero}")
    print(f"Pseudonormas...................... {segunda['temario_pseudonormas']}")
    print(f"Pseudoartículos................... {segunda['temario_pseudoarticulos']}")
    print(f"Proposiciones..................... {segunda['temario_pseudoarticulo_proposiciones']}")
    print(f"Respaldos locales................. {segunda['temario_pseudoarticulo_respaldos']}")
    print(f"Fuentes conocimiento.............. {segunda['fuentes_conocimiento']}")
    print("Modo revisión..................... OK")
    print("Segunda carga/idempotencia........ OK")
    print("Tablas consolidadas copia......... SIN CAMBIOS")
    print("foreign_key_check................. OK")
    print("BD real........................... SIN CAMBIOS")
    print(f"Copia temporal.................... {DB_TMP}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR SMOKE PERSISTENCIA V2: {exc}")
        raise SystemExit(1)
