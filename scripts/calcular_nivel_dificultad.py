"""Calcula y guarda el nivel de dificultad de cada pregunta jurídica en lote_preguntas.

La web (backend/app/dificultad.py) usa esta columna para elegir las preguntas más
difíciles de cada parte o bloque, relajándose solo cuando no hay margen suficiente.

NIVELES (1 = más difícil; se elige primero el nivel 1)
  1  sin pistas (puntuación < 20) y: no generada por IA, o generada con el generador v5
  2  pistas 20-39 y no IA, o IA antigua sin pistas (< 20)
  3  pistas >= 40 y no IA, o IA (cualquiera) con pistas 20-39
  4  IA con pistas >= 40
Preguntas no jurídicas: NULL (la web las trata siempre como nivel 1).

Uso (desde TuCoach-Mantenimiento):
    python scripts/calcular_nivel_dificultad.py            -> SIMULACIÓN (no cambia nada)
    python scripts/calcular_nivel_dificultad.py --aplicar  -> copia de seguridad + guarda los niveles
    python scripts/calcular_nivel_dificultad.py --deshacer -> pone todos los niveles a NULL
                                                            (la web vuelve a calcularlos como antes)
La primera vez añade la columna lote_preguntas.nivel_dificultad (INTEGER).
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MARCA_V5 = "IA-v5%"
UMBRAL_SIN_PISTAS = 20
UMBRAL_MUCHAS_PISTAS = 40

# --- Puntuación de pistas (idéntica a backend/app/dificultad.py de TuCoach-Web) ---
_ABS = re.compile(
    r"\b(siempre|nunca|en ningún caso|en todo caso|exclusivamente|únicamente|solo|sólo|"
    r"cualquier|todos? los|jamás|sin excepción|con independencia)\b", re.I)
_TODAS = re.compile(
    r"todas (las )?(anteriores|opciones|respuestas)? ?(son )?correctas|todas las anteriores", re.I)


def _palabras(t):
    return {p for p in re.findall(r"\w+", (t or "").lower()) if len(p) > 5}


def puntuacion_pistas(enunciado, opciones, correcta):
    s = 0
    largos = {k: len(v or "") for k, v in opciones.items()}
    orden = sorted(largos.values(), reverse=True)
    if largos[correcta] == orden[0] and orden[0] > orden[1]:
        s += 25
        if largos[correcta] >= 1.3 * orden[1]:
            s += 10
    if not _ABS.search(opciones[correcta] or ""):
        n = sum(1 for k, v in opciones.items() if k != correcta and _ABS.search(v or ""))
        if n:
            s += min(30, 12 * n)
    if _TODAS.search(opciones[correcta] or ""):
        s += 15
    e = _palabras(enunciado)
    co = {k: len(e & _palabras(v)) for k, v in opciones.items()}
    if co[correcta] > 0 and co[correcta] == max(co.values()) and list(co.values()).count(co[correcta]) == 1:
        s += 15
    if len(enunciado or "") < 90:
        s += 10
    return min(s, 100)


def nivel(pistas: int, ia: bool, v5: bool) -> int:
    if ia and not v5:
        if pistas < UMBRAL_SIN_PISTAS:
            return 2
        return 3 if pistas < UMBRAL_MUCHAS_PISTAS else 4
    # no IA, o IA del generador v5 (pasa los mismos controles que las mejores)
    if pistas < UMBRAL_SIN_PISTAS:
        return 1
    if pistas < UMBRAL_MUCHAS_PISTAS:
        return 2 if not v5 else 3
    return 3 if not v5 else 4


def asegurar_columna(con) -> bool:
    cols = {r[1] for r in con.execute("PRAGMA table_info(lote_preguntas)")}
    if "nivel_dificultad" in cols:
        return False
    con.execute("ALTER TABLE lote_preguntas ADD COLUMN nivel_dificultad INTEGER")
    return True


def calcular(con) -> dict[int, int | None]:
    v5_ids = {r[0] for r in con.execute(
        "SELECT id FROM importaciones_ficheros WHERE nombre_fichero LIKE ?", (MARCA_V5,))}
    niveles = {}
    for (pid, enun, a, b, c, d, corr, fuente, clas, imp) in con.execute(
            """SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d, respuesta_correcta,
                      tipo_fuente, tipo_clasificacion, importacion_fichero_id FROM lote_preguntas"""):
        corr = str(corr or "").strip().upper()
        if clas != "JURIDICA" or corr not in ("A", "B", "C", "D"):
            niveles[pid] = None
            continue
        p = puntuacion_pistas(enun, {"A": a, "B": b, "C": c, "D": d}, corr)
        niveles[pid] = nivel(p, fuente == "ia_generada", imp in v5_ids)
    return niveles


def aplicar_niveles(db: Path, silencioso: bool = False) -> Counter:
    """Calcula y guarda los niveles (sin copia de seguridad; uso interno del generador v5)."""
    con = sqlite3.connect(db)
    with con:
        asegurar_columna(con)
        niveles = calcular(con)
        con.executemany("UPDATE lote_preguntas SET nivel_dificultad=? WHERE id=?",
                        [(n, i) for i, n in niveles.items()])
    con.close()
    resumen = Counter(n for n in niveles.values())
    if not silencioso:
        print("Niveles de dificultad actualizados:", dict(sorted(resumen.items(), key=lambda x: (x[0] is None, x[0]))))
    return resumen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(RAIZ / "db" / "oposiciones.sqlite3"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--aplicar", action="store_true")
    g.add_argument("--deshacer", action="store_true")
    a = ap.parse_args()
    db = Path(a.db).resolve()
    if not db.exists():
        print(f"No existe la base: {db}"); return 1

    con = sqlite3.connect(db)
    if a.deshacer:
        cols = {r[1] for r in con.execute("PRAGMA table_info(lote_preguntas)")}
        if "nivel_dificultad" not in cols:
            print("La columna no existe: no hay nada que deshacer."); return 0
        with con:
            con.execute("UPDATE lote_preguntas SET nivel_dificultad=NULL")
        print("Niveles puestos a NULL. La web vuelve a calcular la dificultad como antes.")
        return 0

    niveles = calcular(con)
    excluidas = {r[0] for r in con.execute("SELECT pregunta_id FROM preguntas_exclusiones")}
    fuentes = dict(con.execute("SELECT id, tipo_fuente FROM lote_preguntas"))
    activos = {i: n for i, n in niveles.items() if n is not None and i not in excluidas}
    print(f"Base de datos: {db}")
    print(f"Preguntas jurídicas activas: {len(activos)}")
    for n in (1, 2, 3, 4):
        ids = [i for i, x in activos.items() if x == n]
        ia = sum(1 for i in ids if fuentes[i] == "ia_generada")
        print(f"  Nivel {n}: {len(ids):6d}   (de ellas IA: {ia})")
    if not a.aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0
    con.close()
    copia = db.with_name(f"oposiciones_backup_antes_nivel_dificultad_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    origen = sqlite3.connect(db); destino = sqlite3.connect(copia)
    origen.backup(destino); destino.close(); origen.close()
    print(f"\nCopia de seguridad: {copia}")
    con = sqlite3.connect(db)
    with con:
        if asegurar_columna(con):
            print("Columna lote_preguntas.nivel_dificultad creada.")
        con.executemany("UPDATE lote_preguntas SET nivel_dificultad=? WHERE id=?",
                        [(n, i) for i, n in niveles.items()])
    guardados = con.execute("SELECT COUNT(*) FROM lote_preguntas WHERE nivel_dificultad IS NOT NULL").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM lote_preguntas").fetchone()[0]
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    print(f"Niveles guardados: {guardados} (jurídicas) · Preguntas en lote_preguntas: {total} (no cambia)")
    print(f"Integridad de la base: {integridad}")
    print("RESULTADO:", "OK" if integridad == "ok" else "REVISAR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
