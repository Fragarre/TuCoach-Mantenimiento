"""
Piloto Version 2 - GEN para A1 / ESPECIAL 1.

FASE ACTUAL: SOLO REVISION.

La fuente de identidad del piloto es la carpeta real de la convocatoria:
    data_convocatorias/CONV_A1-01_01_26_ADM/

El script:
- lee convocatoria.json para obtener el codigo real;
- localiza ESPECIAL 1 en el CSV disponible de esa carpeta;
- si la convocatoria ya esta importada en la BD, cruza tambien su estado;
- si aun no esta importada, NO falla: trabaja en modo pre-importacion;
- define de forma reproducible la fuente GEN del piloto;
- muestra exactamente que articulos GEN se pretenden crear y enlazar;
- NO modifica la base de datos;
- NO llama a la API de OpenAI;
- NO resuelve referencias BOE/DOGV/DOUE.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
CARPETA_DEFECTO = RAIZ / "data_convocatorias" / "CONV_A1-01_01_26_ADM"

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
    (
        "Ley 25/2014, de 27 de noviembre, de Tratados y otros Acuerdos Internacionales",
        "23,28-30",
    ),
    ("Tratado de Funcionamiento de la Unión Europea", "288"),
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Piloto GEN A1 ESPECIAL 1 en modo SOLO REVISION."
    )
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument(
        "--carpeta",
        default=str(CARPETA_DEFECTO),
        help="Carpeta real de la convocatoria A1 del piloto.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Muestra tambien el plan completo en JSON.",
    )
    return p


def limpiar(v: object | None) -> str:
    return " ".join(str(v or "").split()).strip()


def cargar_convocatoria(carpeta: Path) -> dict:
    ruta = carpeta / "convocatoria.json"
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe: {ruta}")

    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    convocatoria = datos.get("convocatoria") or {}
    codigo = limpiar(convocatoria.get("codigo"))
    puesto = limpiar(convocatoria.get("puesto")).upper()

    if not codigo:
        raise RuntimeError("convocatoria.json no contiene convocatoria.codigo.")
    if puesto != "A1":
        raise RuntimeError(
            f"La carpeta del piloto no corresponde a A1: puesto={puesto!r}."
        )

    return datos


def localizar_csv_temario(carpeta: Path, datos: dict) -> Path:
    candidatos: list[Path] = []

    # 1. Ruta declarada por la propia convocatoria, si existe fisicamente.
    temario = datos.get("temario") or {}
    declarada = limpiar(temario.get("csv"))
    if declarada:
        ruta_declarada = RAIZ / Path(declarada)
        candidatos.append(ruta_declarada)

    # 2. Nombres reales actualmente presentes en la carpeta del piloto.
    candidatos.extend(
        [
            carpeta / "temario.csv",
            carpeta / "temario_A1_COMPLETO.csv",
            carpeta / "temario_A1_provisional_independiente.csv",
        ]
    )

    vistos: set[Path] = set()
    for ruta in candidatos:
        ruta = ruta.resolve()
        if ruta in vistos:
            continue
        vistos.add(ruta)
        if ruta.is_file():
            return ruta

    detalle = "\n- ".join(str(x) for x in candidatos)
    raise FileNotFoundError(
        "No se encontro ningun CSV de temario utilizable. Candidatos:\n- " + detalle
    )


def leer_especial_1(ruta_csv: Path) -> tuple[str, list[dict[str, str]]]:
    ultimo_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with ruta_csv.open("r", encoding=encoding, newline="") as f:
                lector = csv.DictReader(f)
                filas = [
                    {str(k or ""): limpiar(v) for k, v in fila.items()}
                    for fila in lector
                    if limpiar(fila.get("parte")).upper() == PARTE
                    and limpiar(fila.get("tema")) == str(TEMA)
                ]
            if not filas:
                raise RuntimeError(
                    f"No existe {PARTE} {TEMA} en {ruta_csv}."
                )
            titulos = {limpiar(f.get("titulo")) for f in filas if limpiar(f.get("titulo"))}
            if len(titulos) != 1:
                raise RuntimeError(
                    f"{PARTE} {TEMA} no tiene un titulo inequivoco en {ruta_csv}: "
                    f"{sorted(titulos)}"
                )
            return next(iter(titulos)), filas
        except UnicodeDecodeError as exc:
            ultimo_error = exc

    if ultimo_error:
        raise ultimo_error
    raise RuntimeError(f"No se pudo leer {ruta_csv}.")


def columnas(con: sqlite3.Connection, tabla: str) -> set[str]:
    return {str(f[1]) for f in con.execute(f"PRAGMA table_info({tabla})")}


def tabla_existe(con: sqlite3.Connection, tabla: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,),
    ).fetchone() is not None


def localizar_en_bd(
    db: Path,
    codigo: str,
) -> dict:
    resultado = {
        "convocatoria_importada": False,
        "convocatoria_id": None,
        "temario_id": None,
        "tema_id": None,
        "titulo_tema_bd": None,
        "referencias_gen_existentes": [],
        "articulos_gen_existentes": [],
    }

    if not db.is_file():
        resultado["aviso"] = f"La BD no existe: {db}"
        return resultado

    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row

        if not tabla_existe(con, "convocatorias"):
            resultado["aviso"] = "La BD no contiene la tabla convocatorias."
            return resultado

        conv = con.execute(
            "SELECT id FROM convocatorias WHERE codigo=?",
            (codigo,),
        ).fetchone()
        if conv is None:
            return resultado

        resultado["convocatoria_importada"] = True
        resultado["convocatoria_id"] = int(conv[0])

        if not all(
            tabla_existe(con, t)
            for t in ("temarios", "temario_temas")
        ):
            resultado["aviso"] = "La convocatoria existe, pero faltan tablas de temario."
            return resultado

        temarios = con.execute(
            "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id",
            (int(conv[0]),),
        ).fetchall()
        if len(temarios) != 1:
            resultado["aviso"] = (
                "La convocatoria existe, pero no tiene exactamente un temario importado. "
                f"Encontrados: {len(temarios)}."
            )
            return resultado

        temario_id = int(temarios[0][0])
        resultado["temario_id"] = temario_id
        tema = con.execute(
            """
            SELECT id, titulo FROM temario_temas
            WHERE temario_id=? AND UPPER(TRIM(parte))=? AND numero_tema=?
            """,
            (temario_id, PARTE, TEMA),
        ).fetchone()
        if tema is None:
            resultado["aviso"] = f"La convocatoria esta importada, pero no contiene {PARTE} {TEMA}."
            return resultado

        tema_id = int(tema[0])
        resultado["tema_id"] = tema_id
        resultado["titulo_tema_bd"] = str(tema[1])

        if tabla_existe(con, "temario_referencias") and "articulo_fuente_id" in columnas(con, "temario_referencias"):
            refs = con.execute(
                """
                SELECT id, nombre_norma_csv, articulo_solicitado, estado, articulo_fuente_id
                FROM temario_referencias
                WHERE tema_id=? AND UPPER(TRIM(nombre_norma_csv))=UPPER(?)
                ORDER BY articulo_solicitado, id
                """,
                (tema_id, NOMBRE_GEN),
            ).fetchall()
            resultado["referencias_gen_existentes"] = [dict(r) for r in refs]

        if tabla_existe(con, "articulos_fuente"):
            arts = con.execute(
                """
                SELECT id, id_bloque, articulo_boe, titulo_bloque,
                       LENGTH(TRIM(COALESCE(texto,''))) AS longitud_texto
                FROM articulos_fuente
                WHERE id_boe=?
                ORDER BY articulo_boe, id_bloque
                """,
                (ID_FUENTE_GEN,),
            ).fetchall()
            resultado["articulos_gen_existentes"] = [dict(r) for r in arts]

    return resultado


def plan_escritura(codigo: str, tema_id: int | None) -> dict:
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
        "convocatoria": codigo,
        "parte": PARTE,
        "tema": TEMA,
        "nombre_gen": NOMBRE_GEN,
        "id_fuente_gen": ID_FUENTE_GEN,
        "referencias_directas": [
            {"norma": norma, "articulos": refs}
            for norma, refs in REFERENCIAS_DIRECTAS
        ],
        "articulos_gen": articulos,
    }


def main() -> int:
    args = parser().parse_args()
    carpeta = Path(args.carpeta).resolve()
    db = Path(args.db).resolve()

    if not carpeta.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de convocatoria: {carpeta}")

    datos = cargar_convocatoria(carpeta)
    codigo = limpiar((datos.get("convocatoria") or {}).get("codigo"))
    ruta_csv = localizar_csv_temario(carpeta, datos)
    titulo_csv, filas_es1 = leer_especial_1(ruta_csv)
    estado_bd = localizar_en_bd(db, codigo)
    plan = plan_escritura(codigo, estado_bd.get("tema_id"))

    print("=" * 78)
    print("PILOTO GEN - A1 / ESPECIAL 1 - SOLO REVISION")
    print("=" * 78)
    print(f"Carpeta:               {carpeta}")
    print(f"Convocatoria:          {codigo}")
    print(f"CSV usado:             {ruta_csv.name}")
    print(f"Tema:                  {PARTE} {TEMA}")
    print(f"Titulo CSV:            {titulo_csv}")
    print(f"Filas actuales ES1:    {len(filas_es1)}")
    print(f"Fuente GEN:            {NOMBRE_GEN}")
    print(f"Identificador fuente:  {ID_FUENTE_GEN}")
    print(f"Articulos GEN:         {len(ARTICULOS_GEN)}")
    print()

    print("ESTADO EN BD")
    if estado_bd["convocatoria_importada"]:
        print(f"- convocatoria importada: SI (id={estado_bd['convocatoria_id']})")
        print(f"- temario id:             {estado_bd['temario_id']}")
        print(f"- tema id:                {estado_bd['tema_id']}")
        if estado_bd.get("aviso"):
            print(f"- aviso:                  {estado_bd['aviso']}")
    else:
        print("- convocatoria importada: NO")
        print("- modo:                   PRE-IMPORTACION")

    print()
    print("REFERENCIAS DIRECTAS CERRADAS")
    for norma, articulos in REFERENCIAS_DIRECTAS:
        print(f"- {norma}: {articulos}")

    print()
    print("PLAN DE ARTICULOS GEN")
    for art in ARTICULOS_GEN:
        print(f"- Articulo {art.numero}: {art.titulo}")
        print(f"  bloque={art.id_bloque}")

    print()
    print("GEN YA EXISTENTE EN BD")
    print(f"- referencias GEN: {len(estado_bd['referencias_gen_existentes'])}")
    print(f"- articulos GEN:   {len(estado_bd['articulos_gen_existentes'])}")

    if estado_bd["referencias_gen_existentes"] or estado_bd["articulos_gen_existentes"]:
        print("ATENCION: ya existe contenido GEN del piloto; no se debe escribir hasta revisarlo.")

    print()
    print("MODO SOLO REVISION: 0 escrituras en la base de datos.")
    print("La generacion del texto y la aplicacion quedan fuera de esta fase.")

    if args.json:
        print()
        print(
            json.dumps(
                {
                    "carpeta": str(carpeta),
                    "csv": str(ruta_csv),
                    "estado_bd": estado_bd,
                    "plan": plan,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
