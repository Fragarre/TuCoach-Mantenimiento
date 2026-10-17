"""Corrige 4 preguntas revisadas a mano contra el texto vigente de su artículo (17/10/2026).

Origen: 17 preguntas en las que tres modelos de IA coincidían en una respuesta
distinta de la guardada. Revisadas una a una con el texto del artículo:
  - 11 tenían la clave bien (fallaban los modelos) y 2 ya estaban corregidas;
  - 4027 ya está en REVISION y 16806 no está en ningún banco (sin texto para verificar);
  - estas 4 necesitan corrección:

  17726  Ley 1/2014 CES, art. 10: el mandato es de CUATRO años (la D dice cinco).
         La correcta es la A (literal del art. 10.2). Clave D -> A.
  11897  Ley 40/2015, art. 7: la B dice "deberá"; la norma dice "podrá".
         La correcta es la A. Clave B -> A, y la A pasa a decir "orgánica y funcional" (literal).
  13321  Ley 4/2021, art. 78.7: errata en el enunciado, "emitir" -> "eximir". Clave D correcta.
  11540  Ley 1/2015, art. 171.1: la opción B estaba corrupta ("por respecto de competencia
         de crédito", "al inicio"), lo que la convertía también en falsa. Se restaura el
         texto literal. Clave C correcta (las exoneraciones no son "en todo caso").

Uso (desde TuCoach-Mantenimiento):
    python scripts/corregir_claves_revision.py            -> muestra lo que haría
    python scripts/corregir_claves_revision.py --aplicar  -> copia de seguridad + cambios + niveles
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"

B_11540 = (
    "No obstante lo anterior, no podrá realizarse el pago de la subvención en tanto la persona "
    "beneficiaria no se halle al corriente en el cumplimiento de sus obligaciones tributarias y frente "
    "a la Seguridad Social o tenga la condición de sujeto deudor por resolución de procedencia de "
    "reintegro. Sin perjuicio de lo anterior, podrá realizarse el pago de la subvención desde el momento "
    "en que, por órgano competente de la Administración de la Generalitat, se haya notificado al sujeto "
    "deudor del reintegro el inicio del correspondiente expediente de compensación de deudas, en orden "
    "a su completa extinción."
)

# (id, campo, valor_esperado_actual (o fragmento con 'LIKE:'), valor_nuevo)
CAMBIOS = [
    (17726, "respuesta_correcta", "D", "A"),
    (11897, "respuesta_correcta", "B", "A"),
    (11897, "opcion_a", "LIKE:%autonomía orgánica o funcional con respecto a la Administración activa%", None),
    (13321, "enunciado", "LIKE:%deberá emitir, previa solicitud expresa%", None),
    (11540, "opcion_b", "LIKE:%por respecto de competencia de crédito%", B_11540),
]


def nuevo_valor(pid, campo, actual, nuevo):
    if nuevo is not None:
        return nuevo
    if pid == 11897:
        return actual.replace("autonomía orgánica o funcional", "autonomía orgánica y funcional")
    if pid == 13321:
        return actual.replace("deberá emitir, previa solicitud expresa", "deberá eximir, previa solicitud expresa")
    raise ValueError


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    con = sqlite3.connect(DB)
    plan = []
    for pid, campo, esperado, nuevo in CAMBIOS:
        fila = con.execute(f"SELECT {campo} FROM lote_preguntas WHERE id=?", (pid,)).fetchone()
        if fila is None:
            print(f"{pid}: no existe. No se hace nada."); return 1
        actual = fila[0] or ""
        if esperado.startswith("LIKE:"):
            frag = esperado[5:].strip("%")
            ok = frag in actual
        else:
            ok = actual.strip().upper() == esperado
        if not ok:
            print(f"{pid}.{campo}: el valor actual no es el esperado (¿ya corregido?). No se hace nada.")
            return 1
        valor = nuevo_valor(pid, campo, actual, nuevo)
        plan.append((pid, campo, actual, valor))
        print(f"{pid}.{campo}:\n   antes:   {actual[:140]}\n   después: {valor[:140]}")
    if not aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0
    con.close()
    copia = DB.with_name(f"oposiciones_backup_antes_corregir_claves_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    o = sqlite3.connect(DB); d = sqlite3.connect(copia); o.backup(d); d.close(); o.close()
    print(f"\nCopia de seguridad: {copia}")
    con = sqlite3.connect(DB)
    with con:
        for pid, campo, _, valor in plan:
            con.execute(f"UPDATE lote_preguntas SET {campo}=? WHERE id=?", (valor, pid))
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from calcular_nivel_dificultad import aplicar_niveles
    aplicar_niveles(DB)
    print(f"Cambios aplicados: {len(plan)} · integridad: {integridad}")
    print("RESULTADO:", "OK" if integridad == "ok" else "REVISAR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
