"""
Dry-run de integración del piloto GEN A1 / ESPECIAL 2.

Garantías:
- NO importa sqlite3;
- NO abre ni modifica ninguna base de datos;
- NO modifica el CSV de entrada;
- NO modifica el JSON GEN;
- reutiliza exactamente canonical_ley() y articulo_raiz() del extractor V1;
- simula la sustitución del único marcador No determinado de ESPECIAL 2;
- aplica la exclusividad secuencial GENERAL 1 -> ESPECIAL 60;
- informa de las referencias posteriores que quedarían omitidas.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from extraer_temario_convocatoria import articulo_raiz, canonical_ley


RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM"
CSV_DEFECTO = CARPETA / "temario_A1_v1.csv"
GEN_DEFECTO = CARPETA / "gen_revision" / "GEN-A1-ESPECIAL-02.json"

ID_GEN = "GEN-A1-ESPECIAL-02"
NOMBRE_GEN = "GEN - Las fuentes del derecho administrativo (II)"

DIRECTAS = (
    (
        "Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas",
        ("37", "47", "128", "129"),
    ),
    (
        "Ley 29/1998, de 13 de julio, reguladora de la Jurisdicción Contencioso-administrativa",
        ("25", "26", "27"),
    ),
    (
        "Ley 7/1985, de 2 de abril, Reguladora de las Bases del Régimen Local",
        ("4",),
    ),
)

MARCADORES_NORMA = {
    "no determinado",
    "no determinada",
    "no determinados",
    "no determinadas",
}


def es_no_determinada(fila: dict[str, str]) -> bool:
    return (
        str(fila.get("LEY") or "").strip().casefold() in MARCADORES_NORMA
        or str(fila.get("articulo") or "").strip().casefold() in MARCADORES_NORMA
    )


def leer_csv(ruta: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el CSV V1: {ruta}")
    with ruta.open("r", encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        columnas = list(lector.fieldnames or [])
        requeridas = ["parte", "tema", "titulo", "LEY", "articulo", "tipo"]
        if columnas != requeridas:
            raise RuntimeError(
                f"Cabecera inesperada. Esperada: {requeridas}. Encontrada: {columnas}"
            )
        filas = [dict(x) for x in lector]
    return columnas, filas


def leer_y_validar_gen(ruta: Path) -> dict:
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el GEN revisado: {ruta}")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if datos.get("id_gen") != ID_GEN:
        raise RuntimeError(f"id_gen inesperado: {datos.get('id_gen')!r}")
    if datos.get("nombre_gen") != NOMBRE_GEN:
        raise RuntimeError(f"nombre_gen inesperado: {datos.get('nombre_gen')!r}")

    articulos = datos.get("articulos")
    if not isinstance(articulos, list) or len(articulos) != 6:
        raise RuntimeError("El GEN debe contener exactamente 6 artículos.")

    for esperado, art in enumerate(articulos, start=1):
        if int(art.get("numero", -1)) != esperado:
            raise RuntimeError(
                f"Secuencia GEN incorrecta: se esperaba artículo {esperado}."
            )
        id_bloque = f"{ID_GEN}-ART-{esperado:03d}"
        if art.get("id_bloque") != id_bloque:
            raise RuntimeError(
                f"Artículo GEN {esperado}: id_bloque inesperado {art.get('id_bloque')!r}."
            )
        texto = str(art.get("texto_final") or "")
        if not texto.strip():
            raise RuntimeError(f"Artículo GEN {esperado}: texto_final vacío.")
        hash_real = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        if art.get("hash_texto_final") != hash_real:
            raise RuntimeError(f"Artículo GEN {esperado}: hash_texto_final no coincide.")
    return datos


def filas_especial_2(titulo: str, gen: dict) -> list[dict[str, str]]:
    nuevas: list[dict[str, str]] = []
    for ley, articulos in DIRECTAS:
        for articulo in articulos:
            nuevas.append(
                {
                    "parte": "ESPECIAL",
                    "tema": "2",
                    "titulo": titulo,
                    "LEY": ley,
                    "articulo": articulo,
                    "tipo": "JURIDICO",
                }
            )
    for art in gen["articulos"]:
        nuevas.append(
            {
                "parte": "ESPECIAL",
                "tema": "2",
                "titulo": titulo,
                "LEY": NOMBRE_GEN,
                "articulo": str(art["numero"]),
                "tipo": "JURIDICO",
            }
        )
    return nuevas


def sustituir_virtualmente(filas: list[dict[str, str]], gen: dict) -> list[dict[str, str]]:
    objetivo = [
        f for f in filas if f["parte"] == "ESPECIAL" and f["tema"] == "2"
    ]
    if len(objetivo) != 1 or not es_no_determinada(objetivo[0]):
        raise RuntimeError(
            "El CSV V1 debe contener exactamente una fila No determinada en ESPECIAL 2."
        )

    titulo = objetivo[0]["titulo"]
    nuevas = filas_especial_2(titulo, gen)
    salida: list[dict[str, str]] = []
    sustituida = False
    for fila in filas:
        if (
            not sustituida
            and fila["parte"] == "ESPECIAL"
            and fila["tema"] == "2"
            and es_no_determinada(fila)
        ):
            salida.extend(nuevas)
            sustituida = True
        else:
            salida.append(dict(fila))
    if not sustituida:
        raise RuntimeError("No se pudo efectuar la sustitución virtual de ESPECIAL 2.")
    return salida


def clave_orden(fila: dict[str, str], posicion: int) -> tuple[int, int, int]:
    parte = str(fila["parte"]).strip().upper()
    orden_parte = 0 if parte == "GENERAL" else 1 if parte == "ESPECIAL" else 2
    return orden_parte, int(fila["tema"]), posicion


def aplicar_exclusividad(
    filas: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    ordenadas = [
        fila
        for _, fila in sorted(
            enumerate(filas), key=lambda par: clave_orden(par[1], par[0])
        )
    ]
    vistos: dict[tuple[str, str], tuple[str, str]] = {}
    resultado: list[dict[str, str]] = []
    omitidas: list[dict[str, object]] = []

    for fila in ordenadas:
        if es_no_determinada(fila):
            resultado.append(fila)
            continue
        clave = (canonical_ley(fila["LEY"]), articulo_raiz(fila["articulo"]))
        tema = (fila["parte"], fila["tema"])
        anterior = vistos.get(clave)
        if anterior is not None and anterior != tema:
            omitidas.append(
                {
                    "LEY": fila["LEY"],
                    "articulo": fila["articulo"],
                    "tema_omitido": f"{tema[0]} {tema[1]}",
                    "tema_prioritario": f"{anterior[0]} {anterior[1]}",
                    "articulo_raiz": clave[1],
                }
            )
            continue
        vistos.setdefault(clave, tema)
        resultado.append(fila)
    return resultado, omitidas


def validar_sin_colisiones(filas: list[dict[str, str]]) -> None:
    vistos: dict[tuple[str, str], tuple[str, str]] = {}
    for fila in filas:
        if es_no_determinada(fila):
            continue
        clave = (canonical_ley(fila["LEY"]), articulo_raiz(fila["articulo"]))
        tema = (fila["parte"], fila["tema"])
        anterior = vistos.get(clave)
        if anterior is not None and anterior != tema:
            raise RuntimeError(
                f"Colisión residual {fila['LEY']} art. {fila['articulo']}: {anterior} vs {tema}"
            )
        vistos.setdefault(clave, tema)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Simula, sin BD ni escritura de CSV, la integración GEN de A1 ESPECIAL 2."
    )
    p.add_argument("--csv", default=str(CSV_DEFECTO), help="CSV V1 de entrada.")
    p.add_argument("--gen", default=str(GEN_DEFECTO), help="JSON GEN revisado.")
    return p


def main() -> int:
    args = parser().parse_args()
    ruta_csv = Path(args.csv).resolve()
    ruta_gen = Path(args.gen).resolve()

    _, filas = leer_csv(ruta_csv)
    gen = leer_y_validar_gen(ruta_gen)

    nd_antes = [f for f in filas if es_no_determinada(f)]
    virtual = sustituir_virtualmente(filas, gen)
    final, omitidas = aplicar_exclusividad(virtual)
    validar_sin_colisiones(final)
    nd_despues = [f for f in final if es_no_determinada(f)]

    es2 = [f for f in final if f["parte"] == "ESPECIAL" and f["tema"] == "2"]
    gen_es2 = [f for f in es2 if f["LEY"] == NOMBRE_GEN]
    directas_es2 = [f for f in es2 if f["LEY"] != NOMBRE_GEN]

    print("=" * 78)
    print("DRY-RUN INTEGRACION GEN A1 / ESPECIAL 2")
    print("=" * 78)
    print(f"CSV entrada: {ruta_csv}")
    print(f"GEN entrada: {ruta_gen}")
    print(f"Filas CSV originales: {len(filas)}")
    print(f"No determinados antes: {len(nd_antes)}")
    print(f"Referencias directas ESPECIAL 2: {len(directas_es2)}")
    print(f"Artículos GEN ESPECIAL 2: {len(gen_es2)}")
    print(f"Referencias ESPECIAL 2 totales: {len(es2)}")
    print(f"No determinados después de resolver ESPECIAL 2: {len(nd_despues)}")
    print(f"Filas virtuales finales: {len(final)}")
    print(f"Referencias posteriores omitidas por exclusividad: {len(omitidas)}")

    if omitidas:
        print("\nOMISIONES SECUENCIALES")
        for x in omitidas:
            print(
                f"- {x['tema_omitido']}: {x['LEY']} art. {x['articulo']} "
                f"-> ya asignado en {x['tema_prioritario']} (raíz {x['articulo_raiz']})"
            )

    print("\nINTEGRACION GEN PREVISTA")
    print(f"Norma canónica: {NOMBRE_GEN}")
    print(f"id_fuente: {ID_GEN}")
    for art in gen["articulos"]:
        print(
            f"- art. {art['numero']} | {art['id_bloque']} | sha256={art['hash_texto_final']}"
        )

    print("\nVALIDACION: OK")
    print("BD abierta: NO")
    print("BD modificada: NO")
    print("CSV modificado: NO")
    print("Estado para integración real: BLOQUEADO mientras queden No determinados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
