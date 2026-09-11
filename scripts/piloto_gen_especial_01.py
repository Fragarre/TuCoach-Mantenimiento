"""
Piloto Version 2 - GEN para A1 / ESPECIAL 1.

FASE ACTUAL: SOLO REVISION.

Objetivo:
- validar que la convocatoria A1 y ESPECIAL 1 existen en la BD;
- definir de forma reproducible la fuente GEN del piloto;
- mostrar exactamente qué artículos GEN se pretenden crear y enlazar;
- NO modificar la base de datos;
- NO llamar a la API de OpenAI;
- NO resolver referencias BOE/DOGV/DOUE.

Este script es deliberadamente estrecho: solo cubre el piloto A1 / ESPECIAL 1.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"

CODIGO_CONVOCATORIA = "A1-01_01_26"
PARTE = "ESPECIAL"
TEMA = 1
NOMBRE_GEN = "GEN - Las fuentes del derecho administrativo (I)"
ID_FUENTE_GEN = "GEN-A1-ESPECIAL-01"


@dataclass(frozen=True)
class ArticuloGen:
    numero: int
    titulo: str

    @property
    def id_bloque(self) -> str:
        return f"{ID_FUENTE_GEN}-ART-{self.numero:03d}"


ARTICULOS_GEN = (
    ArticuloGen(1, "Concepto y sistema de fuentes del Derecho administrativo"),
    ArticuloGen(2, "Aplicación, interpretación y eficacia de las normas"),
    ArticuloGen(3, "Derecho de la Unión Europea: sistema de fuentes y tipos de actos"),
    ArticuloGen(4, "Primacía del Derecho de la Unión Europea y relación con la Constitución"),
    ArticuloGen(5, "Tratados internacionales: incorporación, eficacia y aplicación"),
    ArticuloGen(6, "La Constitución como fuente. La ley y sus clases"),
    ArticuloGen(7, "Principios de legalidad, reserva de ley, jerarquía normativa y competencia"),
    ArticuloGen(8, "Estatutos de autonomía y leyes de las comunidades autónomas"),
    ArticuloGen(9, "Costumbre y principios generales del Derecho"),
)

REFERENCIAS_DIRECTAS = (
    ("Código Civil", "1-7"),
    ("Ley 25/2014, de 27 de noviembre, de Tratados y otros Acuerdos Internacionales", "23,28-30"),
    ("Tratado de Funcionamiento de la Unión Europea", "288"),
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Piloto GEN A1 ESPECIAL 1 en modo SOLO REVISION."
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument(
        "--json",
        action="store_true",
        help="Muestra también el plan completo en JSON.",
    )
    return p


def columnas(con: sqlite3.Connection, tabla: str) -> set[str]:
    return {str(f[1]) for f in con.execute(f"PRAGMA table_info({tabla})")}


def validar_estructura(con: sqlite3.Connection) -> None:
    requeridas = {
        "convocatorias": {"id", "codigo"},
        "temarios": {"id", "convocatoria_id"},
        "temario_temas": {"id", "temario_id", "parte", "numero_tema", "titulo"},
        "temario_referencias": {
            "id",
            "tema_id",
            "nombre_norma_csv",
            "nombre_norma_normalizada",
            "articulo_solicitado",
            "estado",
        },
        "articulos_fuente": {
            "id",
            "id_boe",
            "id_bloque",
            "articulo_boe",
            "titulo_bloque",
            "texto",
            "hash_texto",
        },
    }

    faltas: list[str] = []
    for tabla, cols in requeridas.items():
        reales = columnas(con, tabla)
        if not reales:
            faltas.append(f"no existe {tabla}")
            continue
        ausentes = sorted(cols - reales)
        if ausentes:
            faltas.append(f"{tabla}: faltan {', '.join(ausentes)}")

    if faltas:
        raise RuntimeError("Estructura incompatible: " + "; ".join(faltas))


def localizar_objetivo(con: sqlite3.Connection) -> tuple[int, int, int, str]:
    conv = con.execute(
        "SELECT id FROM convocatorias WHERE codigo=?",
        (CODIGO_CONVOCATORIA,),
    ).fetchone()
    if conv is None:
        raise RuntimeError(
            f"No existe la convocatoria {CODIGO_CONVOCATORIA!r}."
        )

    temarios = con.execute(
        "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id",
        (int(conv[0]),),
    ).fetchall()
    if len(temarios) != 1:
        raise RuntimeError(
            "La convocatoria debe tener exactamente un temario importado. "
            f"Encontrados: {len(temarios)}."
        )

    tema = con.execute(
        """
        SELECT id, titulo
        FROM temario_temas
        WHERE temario_id=? AND UPPER(TRIM(parte))=? AND numero_tema=?
        """,
        (int(temarios[0][0]), PARTE, TEMA),
    ).fetchone()
    if tema is None:
        raise RuntimeError(
            f"No existe {PARTE} {TEMA} en el temario de {CODIGO_CONVOCATORIA}."
        )

    return int(conv[0]), int(temarios[0][0]), int(tema[0]), str(tema[1])


def detectar_existencias(con: sqlite3.Connection, tema_id: int) -> dict:
    refs_gen = con.execute(
        """
        SELECT id, nombre_norma_csv, articulo_solicitado, estado, articulo_fuente_id
        FROM temario_referencias
        WHERE tema_id=? AND UPPER(TRIM(nombre_norma_csv))=UPPER(?)
        ORDER BY articulo_solicitado, id
        """,
        (tema_id, NOMBRE_GEN),
    ).fetchall()

    articulos_gen = con.execute(
        """
        SELECT id, id_bloque, articulo_boe, titulo_bloque,
               LENGTH(TRIM(COALESCE(texto,''))) AS longitud_texto
        FROM articulos_fuente
        WHERE id_boe=?
        ORDER BY articulo_boe, id_bloque
        """,
        (ID_FUENTE_GEN,),
    ).fetchall()

    return {
        "referencias_gen_existentes": [dict(r) for r in refs_gen],
        "articulos_gen_existentes": [dict(r) for r in articulos_gen],
    }


def plan_escritura(tema_id: int) -> dict:
    articulos = []
    for art in ARTICULOS_GEN:
        articulos.append(
            {
                **asdict(art),
                "id_fuente": ID_FUENTE_GEN,
                "id_bloque": art.id_bloque,
                "articulo_boe": str(art.numero),
                "nombre_norma_csv": NOMBRE_GEN,
                "estado_referencia": "COMPLETADO",
                "tema_id": tema_id,
                "texto": "<PENDIENTE_DE_GENERAR_Y_VALIDAR>",
            }
        )

    return {
        "convocatoria": CODIGO_CONVOCATORIA,
        "parte": PARTE,
        "tema": TEMA,
        "nombre_gen": NOMBRE_GEN,
        "id_fuente_gen": ID_FUENTE_GEN,
        "referencias_directas": [
            {"norma": norma, "articulos": articulos_ref}
            for norma, articulos_ref in REFERENCIAS_DIRECTAS
        ],
        "articulos_gen": articulos,
    }


def main() -> int:
    args = parser().parse_args()
    db = Path(args.db).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {db}")

    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        validar_estructura(con)
        convocatoria_id, temario_id, tema_id, titulo_tema = localizar_objetivo(con)
        existentes = detectar_existencias(con, tema_id)

    plan = plan_escritura(tema_id)

    print("=" * 78)
    print("PILOTO GEN - A1 / ESPECIAL 1 - SOLO REVISION")
    print("=" * 78)
    print(f"BD:                    {db}")
    print(f"Convocatoria:          {CODIGO_CONVOCATORIA} (id={convocatoria_id})")
    print(f"Temario id:            {temario_id}")
    print(f"Tema:                  {PARTE} {TEMA} (id={tema_id})")
    print(f"Título:                {titulo_tema}")
    print(f"Fuente GEN:            {NOMBRE_GEN}")
    print(f"Identificador fuente:  {ID_FUENTE_GEN}")
    print(f"Artículos GEN:         {len(ARTICULOS_GEN)}")
    print()

    print("REFERENCIAS DIRECTAS CERRADAS")
    for norma, articulos in REFERENCIAS_DIRECTAS:
        print(f"- {norma}: {articulos}")

    print()
    print("PLAN DE ARTÍCULOS GEN")
    for art in ARTICULOS_GEN:
        print(f"- Artículo {art.numero}: {art.titulo}")
        print(f"  bloque={art.id_bloque}")

    print()
    print("ESTADO ACTUAL EN BD")
    print(
        "- referencias GEN existentes: "
        f"{len(existentes['referencias_gen_existentes'])}"
    )
    print(
        "- artículos GEN existentes:    "
        f"{len(existentes['articulos_gen_existentes'])}"
    )

    if existentes["referencias_gen_existentes"] or existentes["articulos_gen_existentes"]:
        print("ATENCIÓN: ya existe contenido GEN del piloto; no se debe escribir hasta revisarlo.")

    print()
    print("MODO SOLO REVISION: 0 escrituras en la base de datos.")
    print("La generación del texto y la aplicación quedan deliberadamente fuera de esta fase.")

    if args.json:
        print()
        print(json.dumps(
            {"plan": plan, "existente": existentes},
            ensure_ascii=False,
            indent=2,
        ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
