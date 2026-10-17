"""Elimina la pregunta A2-141/21 n.º 10 importada en la fase 4 (opciones cortadas en el PDF).

Sus opciones A, B y C quedaron truncadas en la extracción, así que A y B son idénticas
y la validación completa da "Opción correcta duplicada: 1". La pregunta es nueva
(importada hoy, aún no publicada en la web), por lo que se borra del lote y de los bancos.

Uso (desde TuCoach-Mantenimiento):
    python scripts/corregir_fase4_pregunta_rota.py            -> muestra lo que haría
    python scripts/corregir_fase4_pregunta_rota.py --aplicar  -> copia de seguridad + borrado
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    con = sqlite3.connect(DB)
    filas = con.execute(
        """SELECT lp.id, lp.importacion_fichero_id, lp.opcion_a, lp.opcion_b
           FROM lote_preguntas lp JOIN importaciones_ficheros i ON i.id = lp.importacion_fichero_id
           WHERE i.nombre_fichero = 'GVA-fase4_A2-141_21.pdf'
             AND lp.enunciado LIKE '%artículo 8 de la Ley 5/1983%president de la Generalitat puede cesar por%'
             AND lp.opcion_a = lp.opcion_b""").fetchall()
    if len(filas) != 1:
        print(f"Se esperaba 1 pregunta y hay {len(filas)}. No se hace nada.")
        return 1
    pid, imp_id, a, _ = filas[0]
    bancos = [r[0] for r in con.execute("SELECT id FROM banco_preguntas WHERE pregunta_id=?", (pid,))]
    print(f"Pregunta {pid} · opciones A y B: «{a[:70]}…»")
    print(f"Vínculos en bancos: {len(bancos)}")
    if not aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0
    con.close()
    copia = DB.with_name(f"oposiciones_backup_antes_corregir_fase4_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    o = sqlite3.connect(DB); d = sqlite3.connect(copia); o.backup(d); d.close(); o.close()
    print(f"Copia de seguridad: {copia}")
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    with con:
        marcas = ",".join("?" * len(bancos))
        if bancos:
            con.execute(f"DELETE FROM banco_preguntas_temas WHERE banco_pregunta_id IN ({marcas})", bancos)
            con.execute(f"DELETE FROM banco_preguntas WHERE id IN ({marcas})", bancos)
        con.execute("DELETE FROM lote_preguntas WHERE id=?", (pid,))
        con.execute("""UPDATE importaciones_ficheros SET paginas_insertadas = paginas_insertadas - 1,
                       paginas_omitidas = paginas_omitidas + 1 WHERE id=?""", (imp_id,))
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    print(f"Borrada la pregunta {pid} y {len(bancos)} vínculos · integridad: {integridad} · errores FK: {len(fk)}")
    print("RESULTADO:", "OK" if integridad == "ok" and not fk else "REVISAR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
