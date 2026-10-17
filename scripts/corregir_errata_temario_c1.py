"""Corrige la errata "Ley 53_1984. de 26 de diciembre1" del temario C1 (tema 10 especial).

- BD: 19 filas de temario_referencias (artículos 1-19, tema 782) tienen el nombre mal escrito.
  Apuntan bien a la norma 51 y a sus artículos, pero el generador v5 no reconoce "53_1984" como
  número de norma y se saltaba la comprobación de "norma explícita" en esos artículos.
  Se corrige el texto a "Ley 53/1984, de 26 de diciembre" (igual que el artículo 20).
- CSV (data_convocatorias/CONV_C1-01_58_26/temario.csv): se borran las 19 líneas repetidas con
  errata; las líneas correctas de los artículos 1-20 ya están en el fichero.

Uso (desde TuCoach-Mantenimiento):
    python scripts/corregir_errata_temario_c1.py            -> muestra lo que haría
    python scripts/corregir_errata_temario_c1.py --aplicar  -> copias de seguridad + cambios
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "db" / "oposiciones.sqlite3"
CSV = RAIZ / "data_convocatorias" / "CONV_C1-01_58_26" / "temario.csv"
ERRATA = "Ley 53_1984. de 26 de diciembre1"
BUENO_CSV = "Ley 53/1984, de 26 de diciembre"
BUENO_NORM = "ley 53/1984 de 26 de diciembre"


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    con = sqlite3.connect(DB)
    filas = con.execute(
        "SELECT id, tema_id, norma_id, articulo_solicitado FROM temario_referencias WHERE nombre_norma_csv=?",
        (ERRATA,)).fetchall()
    print(f"BD: {len(filas)} referencias con errata (temas {sorted({f[1] for f in filas})}, normas {sorted({f[2] for f in filas})})")

    lineas = CSV.read_bytes().split(b"\r\n")
    errata_b = ERRATA.encode("cp1252")
    quedan = [l for l in lineas if errata_b not in l]
    borrar = len(lineas) - len(quedan)
    buenas = sum(1 for l in lineas if b"Ley 53/1984, de 26 de diciembre" in l)
    print(f"CSV: {borrar} líneas con errata a borrar · {buenas} líneas correctas de la Ley 53/1984 que se conservan")

    if len(filas) != 19 or borrar != 19 or buenas < 19:
        print("Los recuentos no son los esperados (19/19/>=19). ¿Ya corregido? No se hace nada.")
        return 1
    if not aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0
    con.close()

    sello = f"{datetime.now():%Y%m%d_%H%M%S}"
    copia = DB.with_name(f"oposiciones_backup_antes_errata_c1_{sello}.sqlite3")
    o = sqlite3.connect(DB); d = sqlite3.connect(copia); o.backup(d); d.close(); o.close()
    shutil.copy2(CSV, CSV.with_name(f"temario_antes_errata_{sello}.csv.bak"))
    print(f"Copias de seguridad: {copia.name} y temario_antes_errata_{sello}.csv.bak")

    con = sqlite3.connect(DB)
    with con:
        n = con.execute(
            "UPDATE temario_referencias SET nombre_norma_csv=?, nombre_norma_normalizada=?, "
            "updated_at=datetime('now') WHERE nombre_norma_csv=?",
            (BUENO_CSV, BUENO_NORM, ERRATA)).rowcount
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    CSV.write_bytes(b"\r\n".join(quedan))
    print(f"BD: {n} referencias corregidas · integridad: {integridad}")
    print(f"CSV: {borrar} líneas borradas")
    print("RESULTADO:", "OK" if integridad == "ok" and n == 19 else "REVISAR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
