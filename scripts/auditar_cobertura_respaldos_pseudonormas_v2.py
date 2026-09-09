"""
Constructor Temario V2 - auditoría de recuperabilidad de respaldos pseudonormativos.

SOLO LECTURA.

Esta auditoría NO mide corrección jurídica. Mide únicamente cuánto texto de la
unidad encuentra el buscador léxico en algún artículo del corpus oficial.

La salida usa RECUPERABILIDAD, no COBERTURA, para evitar interpretar una
coincidencia textual como respaldo jurídicamente pertinente. Un resultado alto
puede ser un falso positivo y siempre requiere revisión jurídica posterior.

Reglas diagnósticas:
- RECUPERABILIDAD_ALTA: >=3 términos coincidentes y cobertura léxica >=0.70.
- RECUPERABILIDAD_MEDIA: >=2 términos y cobertura léxica >=0.45.
- RECUPERABILIDAD_BAJA: resto.

Ninguna etiqueta acepta o valida respaldos.
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
        return "RECUPERABILIDAD_BAJA", "sin coincidencias"

    mejor = propuestas[0]
    n_terminos = len(mejor.terminos)
    if n_terminos >= 3 and mejor.cobertura >= 0.70:
        estado = "RECUPERABILIDAD_ALTA"
    elif n_terminos >= 2 and mejor.cobertura >= 0.45:
        estado = "RECUPERABILIDAD_MEDIA"
    else:
        estado = "RECUPERABILIDAD_BAJA"

    motivo = (
        f"coincidencia_top={mejor.norma} art.{mejor.articulo}; "
        f"cobertura_lexica={mejor.cobertura:.3f}; terminos={n_terminos}; "
        "pertinencia_juridica=NO_EVALUADA"
    )
    return estado, motivo


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audita recuperabilidad léxica del corpus oficial para pseudonormas."
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
    print("CONSTRUCTOR TEMARIO V2 - RECUPERABILIDAD RESPALDOS - SOLO LECTURA")
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
            print(f"  {unidad.codigo} | {estado:<22} | {unidad.texto_temario}")
            print(f"       {motivo}")
        print()

    print("Resumen:")
    print(f"  Unidades evaluadas.............. {total}")
    print(f"  Recuperabilidad alta............ {conteo['RECUPERABILIDAD_ALTA']}")
    print(f"  Recuperabilidad media........... {conteo['RECUPERABILIDAD_MEDIA']}")
    print(f"  Recuperabilidad baja............ {conteo['RECUPERABILIDAD_BAJA']}")
    print()
    print("IMPORTANTE: recuperabilidad léxica != pertinencia jurídica.")
    print("Todos los candidatos siguen requiriendo validación jurídica.")
    print("La base de datos NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
