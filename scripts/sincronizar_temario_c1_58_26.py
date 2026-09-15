from __future__ import annotations

import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CSV_DEFECTO = RAIZ / "data_convocatorias" / "CONV_C1-01_58_26" / "temario.csv"
COLUMNAS = ["parte", "tema", "titulo", "LEY", "articulo", "tipo"]


def rango(a: int, b: int) -> list[str]:
    return [str(i) for i in range(a, b + 1)]


# Fuente determinista del temario jurídico C1-01_58_26 validado.
REGLAS: dict[tuple[str, int], list[tuple[str, list[str]]]] = {
    ("GENERAL", 1): [("Constitucion Española", rango(1, 55))],
    ("GENERAL", 2): [("Constitucion Española", rango(56, 92))],
    ("GENERAL", 3): [("Constitucion Española", rango(97, 116))],
    ("GENERAL", 4): [("Constitucion Española", rango(117, 127) + rango(159, 165))],
    ("GENERAL", 5): [("Constitucion Española", rango(137, 158))],
    ("GENERAL", 6): [("Ley Organica 5/1982, de 1 de julio", rango(1, 58))],
    ("GENERAL", 7): [("Ley 5/1983, de 30 de diciembre", rango(1, 26) + rango(31, 59))],
    ("GENERAL", 8): [("Ley 5/1983, de 30 de diciembre", rango(27, 30) + rango(60, 79))],
    ("GENERAL", 9): [("TUE", rango(1, 8)), ("TFUE", rango(288, 299))],
    ("GENERAL", 10): [
        ("Ley Organica 3/2007, de 22 de marzo", rango(1, 35)),
        ("Ley 9/2003, de 2 de abril", ["1", "2", "3", "4", "4 bis"] + rango(42, 49)),
        ("Ley 4/2023, de 28 de febrero", ["4"] + rango(11, 13)),
    ],
    ("GENERAL", 11): [("Ley Organica 1/2004, de 28 de diciembre", rango(1, 28))],
    ("GENERAL", 12): [
        ("Ley 19/2013, de 9 de diciembre", rango(1, 6) + ["6 bis"] + rango(7, 24)),
        ("Ley 1/2022, de 13 de abril", rango(1, 76)),
    ],
    ("ESPECIAL", 1): [("La Ley 39/2015, de 1 de octubre", rango(1, 33))],
    ("ESPECIAL", 2): [("La Ley 39/2015, de 1 de octubre", rango(34, 52))],
    ("ESPECIAL", 3): [("La Ley 39/2015, de 1 de octubre", rango(53, 126))],
    ("ESPECIAL", 4): [("Ley 40/2015, de 1 de octubre, ", rango(5, 24))],
    ("ESPECIAL", 5): [
        ("Ley 38/2003, de 17 de noviembre", rango(1, 35)),
        ("Ley 1/2015, de 6 de febrero", rango(159, 178)),
    ],
    ("ESPECIAL", 6): [
        ("Ley 9/2017, de 8 de noviembre", rango(1, 18) + rango(24, 27) + rango(36, 43) + rango(61, 130))
    ],
    ("ESPECIAL", 7): [
        ("Decreto 30/2025", rango(1, 46)),
        ("Decreto 54/2025, de 15 de abril", rango(8, 25)),
        (
            "Ley Orgánica 3/2018, de 5 de diciembre, de Protección de Datos Personales y garantía de los derechos digitales",
            rango(1, 97),
        ),
    ],
    ("ESPECIAL", 8): [
        ("Real Decreto Legislativo 5/2015", rango(1, 7)),
        ("Ley 4/2021, de 16 de abril, de la Función Pública Valenciana", rango(1, 15)),
    ],
    ("ESPECIAL", 9): [
        ("Ley 4/2021, de 16 de abril, de la Función Pública Valenciana", rango(16, 59))
    ],
    ("ESPECIAL", 10): [
        (" Ley 53/1984, de 26 de diciembre ", rango(1, 20)),
        ("Ley 4/2021, de 16 de abril, de la Función Pública Valenciana", rango(60, 181)),
    ],
    ("ESPECIAL", 11): [
        ("Ley 1/2015, de 6 de febrero", rango(24, 38)),
        ("Reglamento de Les Corts Valencianes", rango(132, 134)),
    ],
    ("ESPECIAL", 12): [
        ("Decreto 77_2019 ", rango(1, 41)),
        ("Decreto 76_2026 ", rango(1, 19)),
        ("Ley 1/2015, de 6 de febrero", rango(39, 51) + rango(56, 66)),
        ("Ley 4/2026, de 22 de julio", rango(5, 9) + rango(24, 29)),
    ],
    ("ESPECIAL", 13): [
        ("Ley 6/1985, de 11 de mayo", ["5"]),
        ("Ley Orgánica 2/1982", rango(9, 18)),
        (
            "Ley 1/2015, de 6 de febrero",
            rango(92, 123) + ["123 bis", "123 ter", "123 quáter", "123 quinquies"],
        ),
    ],
    ("ESPECIAL", 14): [("Ley 1/2015, de 6 de febrero", rango(124, 143))],
}


def detectar_encoding(ruta: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            ruta.read_text(encoding=enc)
            return enc
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"No se pudo detectar el encoding de {ruta}")


def leer_csv(ruta: Path) -> tuple[list[dict[str, str]], str]:
    enc = detectar_encoding(ruta)
    with ruta.open("r", encoding=enc, newline="") as f:
        lector = csv.DictReader(f)
        if lector.fieldnames != COLUMNAS:
            raise RuntimeError(
                f"Cabecera inesperada. Esperada={COLUMNAS}; encontrada={lector.fieldnames}"
            )
        filas = [dict(r) for r in lector]
    return filas, enc


def clave(fila: dict[str, str]) -> tuple[str, int, str, str]:
    return (fila["parte"].strip().upper(), int(fila["tema"]), fila["LEY"], fila["articulo"])


def construir_esperado(filas: list[dict[str, str]]) -> list[dict[str, str]]:
    titulos: dict[tuple[str, int], str] = {}
    for fila in filas:
        k = (fila["parte"].strip().upper(), int(fila["tema"]))
        titulos.setdefault(k, fila["titulo"])

    faltan_temas = [k for k in REGLAS if k not in titulos]
    if faltan_temas:
        raise RuntimeError(f"Faltan temas necesarios en el CSV: {faltan_temas}")

    salida: list[dict[str, str]] = []

    for parte in ("GENERAL", "ESPECIAL"):
        temas = sorted({int(f["tema"]) for f in filas if f["parte"].strip().upper() == parte})
        for tema in temas:
            k = (parte, tema)
            if k in REGLAS:
                titulo = titulos[k]
                for ley, articulos in REGLAS[k]:
                    for articulo in articulos:
                        salida.append(
                            {
                                "parte": parte,
                                "tema": str(tema),
                                "titulo": titulo,
                                "LEY": ley,
                                "articulo": articulo,
                                "tipo": "JURIDICO",
                            }
                        )
            else:
                # Fuera del bloque jurídico validado (ESPECIAL 15-23): conservar exactamente.
                salida.extend(
                    dict(f)
                    for f in filas
                    if f["parte"].strip().upper() == parte and int(f["tema"]) == tema
                )

    return salida


def diferencias(actual: list[dict[str, str]], esperado: list[dict[str, str]]) -> tuple[list[tuple], list[tuple]]:
    a = {clave(f) for f in actual}
    e = {clave(f) for f in esperado}
    altas = sorted(e - a, key=lambda x: (0 if x[0] == "GENERAL" else 1, x[1], x[2], x[3]))
    bajas = sorted(a - e, key=lambda x: (0 if x[0] == "GENERAL" else 1, x[1], x[2], x[3]))
    return altas, bajas


def escribir_csv(ruta: Path, filas: list[dict[str, str]], encoding: str) -> None:
    with ruta.open("w", encoding=encoding, newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)


def copia_seguridad(ruta: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = ruta.with_name(f"{ruta.stem}_antes_sincronizar_C1_{marca}{ruta.suffix}")
    shutil.copy2(ruta, destino)
    return destino


def main() -> int:
    p = argparse.ArgumentParser(
        description="Sincroniza de forma determinista el temario C1-01_58_26 con el conjunto jurídico validado."
    )
    p.add_argument("--csv", default=str(CSV_DEFECTO))
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--sin-copia-seguridad", action="store_true")
    args = p.parse_args()

    ruta = Path(args.csv).expanduser().resolve()
    if not ruta.is_file():
        raise FileNotFoundError(ruta)

    actual, enc = leer_csv(ruta)
    esperado = construir_esperado(actual)
    altas, bajas = diferencias(actual, esperado)

    juridicas = sum(1 for f in esperado if f["tipo"].strip().upper() == "JURIDICO")
    no_juridicas = len(esperado) - juridicas

    print(f"CSV: {ruta}")
    print(f"Encoding: {enc}")
    print(f"Filas actuales: {len(actual)}")
    print(f"Filas esperadas: {len(esperado)}")
    print(f"  Jurídicas: {juridicas}")
    print(f"  No jurídicas: {no_juridicas}")
    print(f"ALTAS necesarias: {len(altas)}")
    print(f"BAJAS necesarias: {len(bajas)}")

    if altas:
        print("\nALTAS:")
        for x in altas:
            print(f"  + {x[0]} {x[1]:02d} | {x[2]} | art. {x[3]}")
    if bajas:
        print("\nBAJAS:")
        for x in bajas:
            print(f"  - {x[0]} {x[1]:02d} | {x[2]} | art. {x[3]}")

    if not args.aplicar:
        print("\nSOLO REVISIÓN. No se ha modificado el CSV.")
        return 0

    if not altas and not bajas:
        print("\nSin cambios. El temario ya coincide con el conjunto esperado.")
        return 0

    if not args.sin_copia_seguridad:
        backup = copia_seguridad(ruta)
        print(f"Copia de seguridad: {backup}")

    escribir_csv(ruta, esperado, enc)

    verificado, _ = leer_csv(ruta)
    altas2, bajas2 = diferencias(verificado, esperado)
    if altas2 or bajas2:
        raise RuntimeError(
            f"Fallo de verificación tras escritura: altas={len(altas2)} bajas={len(bajas2)}"
        )

    print("\nAplicación terminada.")
    print(f"Filas finales: {len(verificado)}")
    print("Segunda comprobación: 0 altas / 0 bajas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
