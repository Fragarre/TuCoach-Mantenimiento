"""
OpoCoach-Mantenimiento
Auditoría de ampliación del corpus DOUE: TUE y TFUE.

SOLO LECTURA.

- Usa los PDF oficiales de fuentes_normativas/.
- Detecta el inventario consecutivo de artículos.
- Selecciona encabezados reales en orden físico.
- Si existe índice + cuerpo, usa rúbricas del índice para localizar el cuerpo.
- Valida cuerpo suficiente.
- Compara con articulos_fuente.
- No escribe en SQLite.

Uso:
    python scripts/auditar_ampliacion_corpus_doue.py
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
FUENTES = RAIZ / "fuentes_normativas"


@dataclass(frozen=True)
class Fuente:
    norma: str
    id_fuente: str
    archivo: str


FUENTES_DOUE = (
    Fuente("TUE", "DOUE-C-2010-083-TUE", "TUE_2010.pdf"),
    Fuente("TFUE", "DOUE-C-2010-083-TFUE", "TFUE_2010.pdf"),
)


@dataclass(frozen=True)
class Aparicion:
    numero: int
    posicion: int
    linea: str


def limpiar(v) -> str:
    return " ".join(str(v or "").split()).strip()


def normalizar(t: str) -> str:
    s = unicodedata.normalize("NFKD", t or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s.lower())
    return s.strip(" .;:-")


def es_cabecera_pie(linea: str) -> bool:
    s = limpiar(linea)
    patrones = (
        r"^\d{1,2}\.\d{1,2}\.\d{4}\s+ES\s+Diario Oficial",
        r"^ES\s+C\s+\d+/\d+\s+Diario Oficial",
        r"^C\s+\d+/\d+\s+ES\s+Diario Oficial",
        r"^Diario Oficial de la Unión Europea$",
    )
    return any(re.search(p, s, re.I) for p in patrones)


def leer_pdf(ruta: Path) -> str:
    lector = PdfReader(str(ruta))
    paginas = []
    for pagina in lector.pages:
        lineas = []
        for linea in (pagina.extract_text() or "").splitlines():
            linea = limpiar(linea.replace("\u00ad", "").replace("\xa0", " "))
            if not linea or es_cabecera_pie(linea):
                continue
            lineas.append(linea)
        paginas.append("\n".join(lineas))
    texto = "\n".join(paginas)
    texto = re.sub(r"(?<=\w)[ \t]*-[ \t]*\n(?=\w)", "", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    if not texto.strip():
        raise RuntimeError(f"Sin texto extraíble: {ruta}")
    return texto.strip()


PATRON = re.compile(
    r"(?im)^[ \t]*Art[ií]culo[ \t]+(\d+)"
    r"(?![ \t]*\.\s*\d)"
    r"(?:[ \t]*\.)?"
    r"(?=[ \t]+|$)"
)


def apariciones(texto: str) -> list[Aparicion]:
    res = []
    for m in PATRON.finditer(texto):
        fin = texto.find("\n", m.start())
        if fin < 0:
            fin = len(texto)
        res.append(
            Aparicion(
                numero=int(m.group(1)),
                posicion=m.start(),
                linea=limpiar(texto[m.start():fin]),
            )
        )
    return res


def rubrica(linea: str, n: int) -> str:
    m = re.match(
        rf"(?i)^art[ií]culo\s+{n}(?:\s*\.)?\s*(.*)$",
        limpiar(linea),
    )
    return normalizar(m.group(1)) if m else ""


def compatibles(a: str, b: str) -> bool:
    return bool(a and b) and (
        a == b or a.startswith(b) or b.startswith(a)
    )


def inventario(aps: list[Aparicion]) -> list[int]:
    nums = {a.numero for a in aps}
    if 1 not in nums:
        raise RuntimeError("No se detectó el artículo 1.")
    fin = 1
    while fin + 1 in nums:
        fin += 1
    if fin < 2:
        raise RuntimeError("No existe una secuencia útil desde el artículo 1.")
    return list(range(1, fin + 1))


def agrupar(aps, nums):
    g = {n: [] for n in nums}
    permitido = set(nums)
    for a in aps:
        if a.numero in permitido:
            g[a.numero].append(a)
    return g


def modo_indice(grupos, nums):
    dup = 0
    conc = 0
    for n in nums:
        a = grupos[n]
        if len(a) >= 2:
            dup += 1
            r0 = rubrica(a[0].linea, n)
            if r0 and any(
                compatibles(r0, rubrica(x.linea, n))
                for x in a[1:]
            ):
                conc += 1

    return (
        dup >= max(2, int(len(nums) * .60))
        and conc >= max(2, int(len(nums) * .50)),
        dup,
        conc,
    )


def seleccionar(grupos, nums, tiene_indice):
    seleccion = {}
    errores = []

    for i, n in enumerate(nums):
        candidatas = grupos[n]
        if not candidatas:
            errores.append(f"art. {n}: sin aparición")
            continue

        anterior = seleccion.get(nums[i - 1]) if i else None
        min_pos = anterior.posicion if anterior else -1

        if tiene_indice:
            referencia = rubrica(candidatas[0].linea, n)
            posteriores = [
                a for a in candidatas[1:]
                if a.posicion > min_pos
                and compatibles(referencia, rubrica(a.linea, n))
            ]
            if posteriores:
                seleccion[n] = posteriores[0]
                continue

            # Si sólo hay índice + una aparición posterior, permitimos esa
            # aparición aunque el PDF haya truncado la rúbrica.
            posteriores_todas = [
                a for a in candidatas[1:]
                if a.posicion > min_pos
            ]
            if len(candidatas) == 2 and posteriores_todas:
                seleccion[n] = posteriores_todas[0]
                continue

            errores.append(
                f"art. {n}: no se localizó cuerpo compatible con índice"
            )
            continue

        posteriores = [
            a for a in candidatas
            if a.posicion > min_pos
        ]
        if not posteriores:
            errores.append(
                f"art. {n}: sin aparición posterior al artículo anterior"
            )
            continue
        seleccion[n] = posteriores[0]

    return seleccion, errores


def segmentar(texto, seleccion, nums):
    bloques = {}
    errores = []
    for i, n in enumerate(nums):
        a = seleccion.get(n)
        if a is None:
            continue
        fin = (
            seleccion[nums[i + 1]].posicion
            if i + 1 < len(nums) and nums[i + 1] in seleccion
            else len(texto)
        )
        bloque = texto[a.posicion:fin].strip()
        salto = bloque.find("\n")
        cuerpo = bloque[salto + 1:].strip() if salto >= 0 else ""
        if len(limpiar(cuerpo)) < 20:
            errores.append(
                f"art. {n}: cuerpo insuficiente "
                f"({len(limpiar(cuerpo))} caracteres)"
            )
            continue
        bloques[n] = {
            "titulo": a.linea,
            "texto": bloque,
        }
    return bloques, errores


def filas_bd(con, id_fuente):
    return con.execute(
        """
        SELECT id, id_bloque, articulo_boe, titulo_bloque, texto
        FROM articulos_fuente
        WHERE id_boe=?
        ORDER BY id
        """,
        (id_fuente,),
    ).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFECTO))
    args = ap.parse_args()

    db = Path(args.db).resolve()
    if not db.exists():
        raise FileNotFoundError(db)

    print("=" * 78)
    print("AUDITORÍA AMPLIACIÓN CORPUS DOUE - SOLO LECTURA")
    print("=" * 78)
    print(f"Base de datos: {db}")
    print(f"Fuentes: {FUENTES}")
    print("Escrituras en BD: 0")
    print()

    total_faltan = 0
    errores_globales = 0

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")

        for f in FUENTES_DOUE:
            print("-" * 78)
            print(f"{f.norma} | {f.id_fuente}")
            ruta = FUENTES / f.archivo
            filas = filas_bd(con, f.id_fuente)
            print(f"  PDF: {f.archivo}")
            print(f"  Filas BD: {len(filas)}")

            if not ruta.exists():
                errores_globales += 1
                print("  ERROR: FUENTE AUSENTE")
                continue

            try:
                texto = leer_pdf(ruta)
                aps = apariciones(texto)
                nums = inventario(aps)
                grupos = agrupar(aps, nums)
                ind, dup, conc = modo_indice(grupos, nums)
                seleccion, errores1 = seleccionar(grupos, nums, ind)
                bloques, errores2 = segmentar(texto, seleccion, nums)
                errores = errores1 + errores2
            except Exception as exc:
                errores_globales += 1
                print(f"  ERROR: {exc}")
                continue

            errores_globales += len(errores)

            bd_nums = {
                int(limpiar(x["articulo_boe"]))
                for x in filas
                if limpiar(x["articulo_boe"]).isdigit()
            }
            oficiales = set(nums)
            faltan = oficiales - bd_nums
            extras = bd_nums - oficiales
            total_faltan += len(faltan)

            print(f"  Candidatos de artículo: {len(aps)}")
            print(
                f"  Inventario estructural: "
                f"{nums[0]}-{nums[-1]} ({len(nums)})"
            )
            print(
                f"  Índice duplicado: {'SÍ' if ind else 'NO'} "
                f"(duplicados={dup}, rúbricas concordantes={conc})"
            )
            print(
                f"  Encabezados de cuerpo: {len(seleccion)}/{len(nums)}"
            )
            print(
                f"  Cuerpos recuperados y validados: "
                f"{len(bloques)}/{len(nums)}"
            )
            print(f"  Errores identidad/extracción: {len(errores)}")
            for e in errores:
                print(f"    {e}")
            print(f"  Artículos realmente ausentes: {len(faltan)}")
            print(f"  Guardados fuera de inventario: {len(extras)}")

            if faltan:
                orden = sorted(faltan)
                if len(orden) <= 80:
                    print(
                        "  Ausentes: "
                        + ", ".join(map(str, orden))
                    )
                else:
                    print(
                        f"  Ausentes: {orden[0]}-{orden[-1]} "
                        f"({len(orden)} artículos; lista omitida)"
                    )
            if extras:
                print(
                    "  Extras: "
                    + ", ".join(map(str, sorted(extras)))
                )

    print()
    print("=" * 78)
    print("RESUMEN DOUE")
    print("=" * 78)
    print(f"Fuentes evaluadas:                 {len(FUENTES_DOUE)}")
    print(f"Errores identidad/extracción:      {errores_globales}")
    print(f"Artículos realmente ausentes:      {total_faltan}")
    print()
    print("La base de datos no ha sido modificada.")

    if errores_globales:
        print("PLAN DOUE: REQUIERE REVISIÓN")
        return 1

    print("PLAN DOUE: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR FATAL: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise
