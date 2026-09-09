"""
Constructor Temario V2 - resolución conservadora de normas candidatas.

SOLO LECTURA respecto de SQLite.

Esta fase:
- reutiliza la extracción estructural V2;
- trabaja sólo con temas clasificados como JURIDICO;
- detecta únicamente referencias normativas LITERALES del PDF;
- intenta vincularlas de forma unívoca con el catálogo canónico `normas`;
- NO infiere normas para temas sin cita literal;
- NO resuelve artículos;
- NO modifica BD, temario.csv ni bancos.

Uso:
    python scripts/resolver_normas_temario_v2.py \
        --pdf data_convocatorias/CONV_C1-01_58_26/temario_C1.pdf

Opcional:
    --db db/oposiciones.sqlite3
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura


RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"


@dataclass(frozen=True)
class CitaLiteral:
    texto: str
    clave: str
    tipo: str


@dataclass(frozen=True)
class NormaCatalogo:
    norma_id: int
    nombre_canonico: str
    clave_normalizada: str
    textos: tuple[str, ...]
    ids_fuente: tuple[str, ...]


PATRON_NUMERADA = re.compile(
    r"\b("
    r"Ley\s+Org[aá]nica|"
    r"Real\s+Decreto\s+Legislativo|"
    r"Real\s+Decreto(?:-ley|\s+ley)?|"
    r"Decreto\s+Legislativo|"
    r"Decreto(?:-ley|\s+ley)?|"
    r"Ley|Orden"
    r")\s+(\d+)\s*/\s*(\d{4})\b",
    re.IGNORECASE,
)

CITAS_NOMINALES = (
    (
        re.compile(r"\bConstituci[oó]n Espa[nñ]ola(?:\s+de\s+1978)?\b", re.I),
        "constitucion espanola",
        "ALIAS_CONSTITUCION",
    ),
    (
        re.compile(r"\bEstatuto de Autonom[ií]a de la Comunitat Valenciana\b", re.I),
        "estatuto autonomia comunitat valenciana",
        "ALIAS_ESTATUTO_CV",
    ),
    (
        re.compile(r"\bTratado de la Uni[oó]n Europea\b", re.I),
        "tratado de la union europea",
        "NOMBRE_LITERAL",
    ),
    (
        re.compile(r"\bTratado de Funcionamiento de la Uni[oó]n Europea\b", re.I),
        "tratado de funcionamiento de la union europea",
        "NOMBRE_LITERAL",
    ),
    (
        re.compile(r"\bLey de Contratos del Sector P[uú]blico\b", re.I),
        "ley de contratos del sector publico",
        "ALIAS_LCSP",
    ),
)


def normalizar(texto: str | None) -> str:
    valor = unicodedata.normalize("NFKD", texto or "")
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    valor = valor.lower()
    valor = re.sub(r"[^a-z0-9/]+", " ", valor)
    return re.sub(r"\s+", " ", valor).strip()


def normalizar_tipo(tipo: str) -> str:
    t = normalizar(tipo)
    t = t.replace("real decreto ley", "real decreto-ley")
    t = t.replace("decreto ley", "decreto-ley")
    return t


def clave_numerada(tipo: str, numero: str, anio: str) -> str:
    return f"{normalizar_tipo(tipo)} {int(numero)}/{anio}"


def detectar_citas(texto: str) -> list[CitaLiteral]:
    citas: list[CitaLiteral] = []
    vistos: set[str] = set()

    for m in PATRON_NUMERADA.finditer(texto):
        literal = m.group(0)
        clave = clave_numerada(m.group(1), m.group(2), m.group(3))
        if clave not in vistos:
            citas.append(CitaLiteral(literal, clave, "NUMERO_ANIO_LITERAL"))
            vistos.add(clave)

    for patron, clave, tipo in CITAS_NOMINALES:
        m = patron.search(texto)
        if not m:
            continue
        if clave in vistos:
            continue
        citas.append(CitaLiteral(m.group(0), clave, tipo))
        vistos.add(clave)

    return citas


def cargar_catalogo(con) -> list[NormaCatalogo]:
    normas = con.execute(
        "SELECT id, nombre_canonico, clave_normalizada FROM normas ORDER BY id"
    ).fetchall()
    resultado: list[NormaCatalogo] = []

    for n in normas:
        fuentes = con.execute(
            """
            SELECT id_fuente, titulo_fuente
            FROM norma_fuentes
            WHERE norma_id=?
            ORDER BY id
            """,
            (int(n["id"]),),
        ).fetchall()
        textos = {
            normalizar(n["nombre_canonico"]),
            normalizar(n["clave_normalizada"]),
        }
        ids_fuente: list[str] = []
        for f in fuentes:
            textos.add(normalizar(f["titulo_fuente"]))
            if f["id_fuente"]:
                ids_fuente.append(str(f["id_fuente"]).strip())

        resultado.append(
            NormaCatalogo(
                norma_id=int(n["id"]),
                nombre_canonico=str(n["nombre_canonico"] or ""),
                clave_normalizada=str(n["clave_normalizada"] or ""),
                textos=tuple(sorted(t for t in textos if t)),
                ids_fuente=tuple(ids_fuente),
            )
        )
    return resultado


def primera_clave_numerada(texto: str) -> str | None:
    """Devuelve la primera identidad tipo+número+año presente en un texto."""
    m = PATRON_NUMERADA.search(texto)
    if not m:
        return None
    return clave_numerada(m.group(1), m.group(2), m.group(3))


def coincide_identidad_numerada(cita: CitaLiteral, norma: NormaCatalogo) -> bool:
    """
    Una ley citada dentro del título de OTRA norma no identifica esa otra norma.

    Ejemplo que debe rechazarse:
      cita: Ley 38/2003
      catálogo: Real Decreto 887/2006 ... Reglamento de la Ley 38/2003

    Se acepta sólo cuando la identidad principal del nombre/clave/título es la
    misma cita numerada.
    """
    return any(
        primera_clave_numerada(texto) == cita.clave
        for texto in norma.textos
    )


def preferir_fuente_oficial_si_unica(candidatos: list[NormaCatalogo]) -> list[NormaCatalogo]:
    """
    Si varias entradas representan el mismo nombre literal pero sólo una está
    vinculada a una fuente oficial, conserva esa única entrada. Si hay cero o
    varias con fuente, no decide: mantiene la ambigüedad.
    """
    if len(candidatos) <= 1:
        return candidatos
    con_fuente = [n for n in candidatos if n.ids_fuente]
    if len(con_fuente) == 1:
        return con_fuente
    return candidatos


def candidatos_para_cita(cita: CitaLiteral, catalogo: list[NormaCatalogo]) -> list[NormaCatalogo]:
    if cita.tipo == "NUMERO_ANIO_LITERAL":
        return [n for n in catalogo if coincide_identidad_numerada(cita, n)]

    if cita.tipo == "ALIAS_CONSTITUCION":
        por_fuente = [
            n for n in catalogo
            if "BOE-A-1978-31229" in n.ids_fuente
        ]
        if por_fuente:
            return por_fuente
        candidatos = [
            n for n in catalogo
            if any("constitucion espanola" in t for t in n.textos)
        ]
        return preferir_fuente_oficial_si_unica(candidatos)

    if cita.tipo == "ALIAS_ESTATUTO_CV":
        por_fuente = [
            n for n in catalogo
            if "BOE-A-1982-17235" in n.ids_fuente
        ]
        if por_fuente:
            return por_fuente
        candidatos = [
            n for n in catalogo
            if any(
                "ley organica 5/1982" in t
                or "estatuto de autonomia de la comunitat valenciana" in t
                or "estatuto de autonomia de la comunidad valenciana" in t
                for t in n.textos
            )
        ]
        return preferir_fuente_oficial_si_unica(candidatos)

    if cita.tipo == "ALIAS_LCSP":
        por_fuente = [
            n for n in catalogo
            if "BOE-A-2017-12902" in n.ids_fuente
        ]
        if len(por_fuente) == 1:
            return por_fuente
        # Respaldo igualmente estricto: identidad canónica exacta Ley 9/2017.
        candidatos = [
            n for n in catalogo
            if normalizar(n.nombre_canonico) == "ley 9/2017"
            or normalizar(n.clave_normalizada) == "ley 9/2017"
        ]
        return candidatos if len(candidatos) == 1 else []

    # Para nombres sin número/año no hay similitud libre. Se exige presencia
    # literal en el nombre/clave/título canónico. Si varias entradas describen
    # el mismo nombre y exactamente una tiene fuente oficial, se prefiere esa.
    candidatos = [
        n for n in catalogo
        if any(cita.clave in t for t in n.textos)
    ]
    return preferir_fuente_oficial_si_unica(candidatos)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resuelve sólo citas normativas literales contra el catálogo canónico, en solo lectura."
    )
    parser.add_argument("--pdf", required=True, help="PDF oficial del temario")
    parser.add_argument("--db", default=str(DB_PREDETERMINADA), help="SQLite de mantenimiento")
    args = parser.parse_args()

    ruta_pdf = Path(args.pdf).expanduser().resolve()
    ruta_db = Path(args.db).expanduser().resolve()

    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(ruta_pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    with comun.conectar_sqlite_solo_lectura(ruta_db) as con:
        catalogo = cargar_catalogo(con)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - NORMAS CANDIDATAS EXPLÍCITAS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {ruta_pdf}")
    print(f"BD............................... {ruta_db}")
    print(f"Normas canónicas disponibles.... {len(catalogo)}")
    print()

    total_citas = 0
    resueltas = 0
    revision = 0
    temas_juridicos_sin_cita = 0

    for tema in temas:
        if tema.clasificacion == "NO_JURIDICO":
            continue

        if tema.clasificacion == "REVISION":
            print(
                f"{tema.parte:<8} | {tema.numero_tema:>2} | REVISION_TIPO | "
                "sin resolución normativa automática"
            )
            revision += 1
            continue

        citas = detectar_citas(tema.texto)
        if not citas:
            temas_juridicos_sin_cita += 1
            print(
                f"{tema.parte:<8} | {tema.numero_tema:>2} | REVISION_NORMA | "
                "tema jurídico sin cita normativa identificable de forma literal"
            )
            revision += 1
            continue

        for cita in citas:
            total_citas += 1
            candidatos = candidatos_para_cita(cita, catalogo)
            if len(candidatos) == 1:
                n = candidatos[0]
                resueltas += 1
                print(
                    f"{tema.parte:<8} | {tema.numero_tema:>2} | EXACTA | "
                    f"{cita.texto} -> [{n.norma_id}] {n.nombre_canonico}"
                )
            else:
                revision += 1
                motivo = "SIN_COINCIDENCIA" if not candidatos else f"AMBIGUA_{len(candidatos)}"
                print(
                    f"{tema.parte:<8} | {tema.numero_tema:>2} | REVISION_NORMA | "
                    f"{cita.texto} -> {motivo}"
                )
                for n in candidatos[:5]:
                    print(f"    [{n.norma_id}] {n.nombre_canonico}")

    print()
    print("Resumen:")
    print(f"  Citas literales detectadas..... {total_citas}")
    print(f"  Coincidencias canónicas exactas {resueltas}")
    print(f"  Incidencias/revisión............ {revision}")
    print(f"  Jurídicos sin cita literal...... {temas_juridicos_sin_cita}")
    print()
    print("La base de datos NO ha sido modificada.")
    print("No se han inferido normas ni resuelto artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
