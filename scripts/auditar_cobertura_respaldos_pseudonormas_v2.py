"""
Constructor Temario V2 - auditoría de cobertura de respaldos pseudonormativos.

SOLO LECTURA.

No intenta decidir qué artículo es jurídicamente correcto. Mide si la búsqueda
textual sobre el corpus normativo oficial puede resolver razonablemente cada
unidad o si hace falta una capa adicional de fuentes/doctrina/jurisprudencia.

Reglas conservadoras:
- COBERTURA_FUERTE: existe una propuesta con al menos 3 términos relevantes y
  cobertura >= 0.70.
- COBERTURA_PARCIAL: existe una propuesta con al menos 2 términos y cobertura
  >= 0.45.
- INSUFICIENTE: el resto.

Estas etiquetas son diagnósticas; no aceptan ningún respaldo.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance
import generar_borradores_pseudonormas_v2 as borradores_mod
import proponer_respaldos_pseudonormas_v2 as respaldos_mod

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


def diagnosticar(propuestas):
    if not propuestas:
        return "INSUFICIENTE", "sin coincidencias"

    mejor = propuestas[0]
    n_terminos = len(mejor.terminos)
    if n_terminos >= 3 and mejor.cobertura >= 0.70:
        return "COBERTURA_FUERTE", (
            f"mejor={mejor.norma} art.{mejor.articulo}; "
            f"cobertura={mejor.cobertura:.3f}; terminos={n_terminos}"
        )
    if n_terminos >= 2 and mejor.cobertura >= 0.45:
        return "COBERTURA_PARCIAL", (
            f"mejor={mejor.norma} art.{mejor.articulo}; "
            f"cobertura={mejor.cobertura:.3f}; terminos={n_terminos}"
        )
    return "INSUFICIENTE", (
        f"mejor={mejor.norma} art.{mejor.articulo}; "
        f"cobertura={mejor.cobertura:.3f}; terminos={n_terminos}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audita cobertura del corpus oficial para pseudonormas sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--parte", default=None)
    ap.add_argument("--tema", type=int, default=None)
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    seleccion = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        if clase != "JURIDICO_DOCTRINAL_TRANSVERSAL":
            continue
        if args.parte and tema.parte.upper() != args.parte.upper():
            continue
        if args.tema is not None and tema.numero_tema != args.tema:
            continue
        seleccion.append(borradores_mod.construir_borrador(tema, evidencia))

    with comun.conectar_sqlite_solo_lectura(db) as con:
        articulos = respaldos_mod.cargar_articulos(con)
    idf = respaldos_mod.construir_idf(articulos)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - COBERTURA RESPALDOS PSEUDONORMATIVOS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Artículos oficiales indexados.... {len(articulos)}")
    print(f"Pseudonormas evaluadas............ {len(seleccion)}")
    print()

    conteo = Counter()
    total = 0
    for borrador in seleccion:
        print(f"{borrador.parte:<8} | {borrador.numero_tema:>2} | {borrador.identidad}")
        for unidad in borrador.unidades:
            total += 1
            propuestas = respaldos_mod.proponer(
                unidad.texto_temario, articulos, idf, args.top
            )
            estado, motivo = diagnosticar(propuestas)
            conteo[estado] += 1
            print(f"  {unidad.codigo} | {estado:<18} | {unidad.texto_temario}")
            print(f"       {motivo}")
        print()

    print("Resumen:")
    print(f"  Unidades evaluadas.............. {total}")
    print(f"  Cobertura fuerte................ {conteo['COBERTURA_FUERTE']}")
    print(f"  Cobertura parcial............... {conteo['COBERTURA_PARCIAL']}")
    print(f"  Insuficiente.................... {conteo['INSUFICIENTE']}")
    print()
    print("Las etiquetas son diagnósticas y NO validan jurídicamente los respaldos.")
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
