"""
OpoCoach-Mantenimiento
Auditoría / planificación V5 del corpus DOGV/Generalitat.

SOLO LECTURA.

Corrección respecto de V4:
- nunca selecciona un artículo por "bloque más largo";
- detecta si el PDF contiene ÍNDICE + ARTICULADO mediante duplicación masiva;
- en ese caso usa la rúbrica de la primera aparición (índice) como ancla y
  selecciona la aparición posterior con la misma rúbrica;
- si no existe índice duplicado, usa la primera aparición estructural;
- segmenta los cuerpos únicamente entre los encabezados seleccionados, de modo
  que una remisión interna "Artículo N ..." no corta el artículo actual.

No modifica SQLite.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

from pypdf import PdfReader


RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
FUENTES = RAIZ / "fuentes_normativas"


@dataclass(frozen=True)
class Fuente:
    norma: str
    id_fuente: str
    archivo: str
    idioma: str
    canonica: bool = True
    columna_pdf: str | None = None


FUENTES_PDF = (
    Fuente("Decreto 30/2025", "DOGV-2025-3226",
           "decreto_30_2025_dogv.pdf", "es", True),
    Fuente("Decreto 30/2025", "LOCAL-DOGV-DECRETO-30-2025",
           "Decreto 30_2025.pdf", "es", False),
    Fuente("Decreto 76/2026", "DOGV-2026-13821",
           "decreto_76_2026_dogv.pdf", "es", True),
    Fuente("Decreto 77/2019", "DOGV-CONSOLIDADO-D-2019-077",
           "decreto_77_2019_consolidado_dogv.pdf", "va", True),
    Fuente("Decreto 42/2019", "LOCAL-DOGV-DECRETO-42-2019",
           "Decreto 42_2019.pdf", "es", True),
    Fuente("Decreto 54/2025", "LOCAL-DOGV-DECRETO-54-2025",
           "Decreto 54_2025.pdf", "es", True),
    Fuente("Orden 19/2013", "LOCAL-DOGV-ORDEN-19-2013",
           "ORDEN 192013, de 3 de diciembre.pdf", "es", True, "derecha"),
    Fuente("Ley 4/2026", "LOCAL-DOGV-LEY-4-2026",
           "ley_4_2026_presupuestos_dogv.pdf", "es", True),
)


@dataclass(frozen=True)
class Aparicion:
    numero: int
    posicion: int
    linea: str


def limpiar(valor: object | None) -> str:
    return " ".join(str(valor or "").split()).strip()


def normalizar(texto: str) -> str:
    valor = unicodedata.normalize("NFKD", texto or "")
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    valor = valor.lower()
    valor = re.sub(r"\s+", " ", valor)
    return valor.strip(" .;:-")


PATRON = re.compile(
    r"(?im)^[ \t]*(?:art[ií]culo|article)[ \t]+"
    r"(\d+)"
    r"(?![ \t]*\.\s*\d)"
    r"(?:[ \t]*\.)?"
    r"(?=[ \t]+|$)"
)


def limpiar_linea_pdf(linea: str) -> str:
    linea = (linea or "").replace("\u00ad", "").replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", linea).strip()


def normalizar_texto_paginas(paginas: list[str]) -> str:
    salida = []
    for texto in paginas:
        lineas = []
        for linea in (texto or "").splitlines():
            limpia = limpiar_linea_pdf(linea)
            if not limpia:
                continue
            # Cabeceras/pies DOGV evidentes.
            if re.match(r"^(?:Núm\.|Num\.)\s+\d+", limpia, re.I):
                continue
            if re.match(r"^CVE:\s*DOGV", limpia, re.I):
                continue
            if limpia in (
                "Diari Oficial de la Generalitat Valenciana",
                "Diario Oficial de la Generalitat Valenciana",
            ):
                continue
            lineas.append(limpia)
        salida.append("\n".join(lineas))

    texto = "\n".join(salida)
    texto = re.sub(
        r"(?<=\w)[ \t]*-[ \t]*\n(?=\w)",
        "",
        texto,
    )
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def leer_pdf_pymupdf(
    ruta: Path,
    columna_pdf: str | None = None,
) -> str:
    with pymupdf.open(ruta) as doc:
        paginas = []
        for pagina in doc:
            if columna_pdf is None:
                texto_pagina = pagina.get_text("text") or ""
            elif columna_pdf == "derecha":
                rect = pagina.rect
                clip = pymupdf.Rect(
                    rect.x0 + rect.width / 2,
                    rect.y0,
                    rect.x1,
                    rect.y1,
                )
                texto_pagina = pagina.get_text("text", clip=clip) or ""
            elif columna_pdf == "izquierda":
                rect = pagina.rect
                clip = pymupdf.Rect(
                    rect.x0,
                    rect.y0,
                    rect.x0 + rect.width / 2,
                    rect.y1,
                )
                texto_pagina = pagina.get_text("text", clip=clip) or ""
            else:
                raise RuntimeError(
                    f"Columna PDF no soportada: {columna_pdf}"
                )
            paginas.append(texto_pagina)

    texto = normalizar_texto_paginas(paginas)
    if not texto:
        raise RuntimeError(f"PDF sin texto extraíble con pymupdf: {ruta}")
    return texto


def leer_pdf_pypdf(ruta: Path) -> str:
    lector = PdfReader(str(ruta))
    paginas = [(pagina.extract_text() or "") for pagina in lector.pages]
    texto = normalizar_texto_paginas(paginas)
    if not texto:
        raise RuntimeError(f"PDF sin texto extraíble con pypdf: {ruta}")
    return texto


def evaluar_extraccion(texto: str):
    apariciones = detectar_apariciones(texto)
    nums, parcial, razon = inventario(apariciones)
    grupos = agrupar_por_numero(apariciones, nums)
    modo_indice, duplicados, concordantes = detectar_modo_indice(
        grupos, nums
    )
    seleccionados, errores_sel = seleccionar_encabezados(
        grupos, nums, modo_indice
    )
    bloques, errores_seg = segmentar_cuerpos(
        texto, seleccionados, nums
    )
    errores = errores_sel + errores_seg

    return {
        "texto": texto,
        "apariciones": apariciones,
        "nums": nums,
        "parcial": parcial,
        "razon": razon,
        "grupos": grupos,
        "modo_indice": modo_indice,
        "duplicados": duplicados,
        "concordantes": concordantes,
        "seleccionados": seleccionados,
        "bloques": bloques,
        "errores": errores,
    }


def obtener_extraccion_valida(
    ruta: Path,
    fuente: Fuente | None = None,
):
    intentos = []
    columna_pdf = fuente.columna_pdf if fuente is not None else None

    # Los PDF bilingües en columnas deben conservar una sola lengua.
    # En esos casos se usa el recorte geométrico de PyMuPDF; no se hace
    # fallback a extracción de página completa porque mezclaría idiomas.
    motores = (
        (("pymupdf", leer_pdf_pymupdf),)
        if columna_pdf is not None
        else (
            ("pymupdf", leer_pdf_pymupdf),
            ("pypdf", leer_pdf_pypdf),
        )
    )

    for motor, lector in motores:
        try:
            if motor == "pymupdf":
                texto = lector(ruta, columna_pdf=columna_pdf)
            else:
                texto = lector(ruta)

            if fuente is not None and fuente.idioma == "es":
                valencianos = re.findall(
                    r"(?im)^[ \t]*article[ \t]+\d+",
                    texto,
                )
                if columna_pdf is not None and valencianos:
                    raise RuntimeError(
                        "El recorte configurado como castellano contiene "
                        f"{len(valencianos)} encabezados 'Article'."
                    )

            resultado = evaluar_extraccion(texto)
            resultado["motor"] = motor
            intentos.append(resultado)

            if not resultado["errores"]:
                return resultado, intentos
        except Exception as exc:
            intentos.append(
                {
                    "motor": motor,
                    "errores": [
                        f"{exc.__class__.__name__}: {exc}"
                    ],
                }
            )

    # Ningún motor quedó limpio. Elegimos sólo para diagnóstico el intento
    # con menos errores, pero el plan seguirá bloqueado.
    validos = [x for x in intentos if "nums" in x]
    if not validos:
        raise RuntimeError(
            "Ningún motor pudo extraer texto utilizable del PDF."
        )

    mejor = min(
        validos,
        key=lambda x: (
            len(x["errores"]),
            0 if x["motor"] == "pymupdf" else 1,
        ),
    )
    return mejor, intentos


def detectar_apariciones(texto: str) -> list[Aparicion]:
    resultado = []
    for m in PATRON.finditer(texto):
        fin = texto.find("\n", m.start())
        if fin < 0:
            fin = len(texto)
        resultado.append(
            Aparicion(
                numero=int(m.group(1)),
                posicion=m.start(),
                linea=limpiar(texto[m.start():fin]),
            )
        )
    return resultado


def inventario(apariciones: list[Aparicion]) -> tuple[list[int], bool, str]:
    numeros = {a.numero for a in apariciones}
    if not numeros:
        raise RuntimeError("No se detectaron artículos.")

    if 1 in numeros:
        fin = 1
        while fin + 1 in numeros:
            fin += 1
        return list(range(1, fin + 1)), False, f"Prefijo consecutivo 1-{fin}."

    ordenados = sorted(numeros)
    mejor = (ordenados[0], ordenados[0])
    ini = fin = ordenados[0]
    for n in ordenados[1:]:
        if n == fin + 1:
            fin = n
        else:
            if fin - ini > mejor[1] - mejor[0]:
                mejor = (ini, fin)
            ini = fin = n
    if fin - ini > mejor[1] - mejor[0]:
        mejor = (ini, fin)

    ini, fin = mejor
    return (
        list(range(ini, fin + 1)),
        True,
        f"Sin artículo 1; tramo consecutivo más largo {ini}-{fin}.",
    )


def rubrica(linea: str, numero: int) -> str:
    m = re.match(
        rf"(?i)^(?:art[ií]culo|article)\s+{numero}"
        rf"(?:\s*\.)?\s*(.*)$",
        limpiar(linea),
    )
    return normalizar(m.group(1)) if m else ""


def rubricas_compatibles(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def agrupar_por_numero(
    apariciones: list[Aparicion],
    nums: list[int],
) -> dict[int, list[Aparicion]]:
    permitido = set(nums)
    grupos = {n: [] for n in nums}
    for a in apariciones:
        if a.numero in permitido:
            grupos[a.numero].append(a)
    return grupos


def detectar_modo_indice(
    grupos: dict[int, list[Aparicion]],
    nums: list[int],
) -> tuple[bool, int, int]:
    """
    Se considera que existe índice + cuerpo cuando al menos el 60 % de los
    artículos inventariados aparecen dos o más veces y, además, al menos el
    50 % tiene una aparición posterior con rúbrica compatible con la primera.
    """
    duplicados = 0
    concordantes = 0

    for n in nums:
        aps = grupos[n]
        if len(aps) >= 2:
            duplicados += 1
            r0 = rubrica(aps[0].linea, n)
            if r0:
                if any(
                    rubricas_compatibles(r0, rubrica(a.linea, n))
                    for a in aps[1:]
                ):
                    concordantes += 1

    umbral_dup = max(2, int(len(nums) * 0.60))
    umbral_conc = max(2, int(len(nums) * 0.50))
    return (
        duplicados >= umbral_dup and concordantes >= umbral_conc,
        duplicados,
        concordantes,
    )


def seleccionar_encabezados(
    grupos: dict[int, list[Aparicion]],
    nums: list[int],
    modo_indice: bool,
) -> tuple[dict[int, Aparicion], list[str]]:
    seleccionados = {}
    errores = []

    for n in nums:
        aps = grupos[n]
        if not aps:
            errores.append(f"art. {n}: sin aparición")
            continue

        if not modo_indice:
            # En fuentes sin índice masivamente duplicado no podemos elegir
            # cada artículo de forma independiente: una remisión interna a un
            # artículo posterior puede aparecer antes de su encabezado real.
            #
            # La identidad estructural exige una secuencia física creciente
            # N, N+1, N+2... Por tanto elegimos la PRIMERA aparición del
            # artículo actual que esté situada después del encabezado ya
            # seleccionado para el artículo anterior.
            if not seleccionados:
                seleccionados[n] = aps[0]
                continue

            numero_anterior = nums[nums.index(n) - 1]
            anterior = seleccionados.get(numero_anterior)
            if anterior is None:
                errores.append(
                    f"art. {n}: no puede resolverse porque falta el "
                    f"encabezado del art. {numero_anterior}"
                )
                continue

            posteriores = [
                a for a in aps
                if a.posicion > anterior.posicion
            ]

            if not posteriores:
                errores.append(
                    f"art. {n}: ninguna aparición posterior al art. "
                    f"{numero_anterior} ({anterior.posicion})"
                )
                continue

            seleccionados[n] = posteriores[0]
            continue

        # Índice + cuerpo:
        # la primera aparición proporciona la rúbrica de identidad.
        referencia = rubrica(aps[0].linea, n)

        compatibles = [
            a for a in aps[1:]
            if rubricas_compatibles(
                referencia,
                rubrica(a.linea, n),
            )
        ]

        if compatibles:
            # La primera compatible posterior al índice es el encabezado del
            # articulado. No usamos "la más larga" ni "la última".
            seleccionados[n] = compatibles[0]
            continue

        # Algunos índices pueden truncar o perder la rúbrica. Si el artículo
        # sólo tiene dos apariciones, la segunda es el cuerpo por estructura.
        if len(aps) == 2:
            seleccionados[n] = aps[1]
            continue

        errores.append(
            f"art. {n}: {len(aps)} apariciones y ninguna rúbrica posterior "
            f"compatible con índice '{referencia}'"
        )

    # Deben quedar estrictamente ordenados por número y posición.
    prev = None
    for n in nums:
        a = seleccionados.get(n)
        if a is None:
            continue
        if prev is not None and a.posicion <= prev.posicion:
            errores.append(
                f"art. {n}: posición {a.posicion} no posterior a art. "
                f"{prev.numero} ({prev.posicion})"
            )
        prev = a

    return seleccionados, errores


def segmentar_cuerpos(
    texto: str,
    seleccionados: dict[int, Aparicion],
    nums: list[int],
) -> tuple[dict[int, dict], list[str]]:
    bloques = {}
    errores = []

    for i, n in enumerate(nums):
        a = seleccionados.get(n)
        if a is None:
            continue

        if i + 1 < len(nums):
            sig = seleccionados.get(nums[i + 1])
            if sig is None:
                errores.append(f"art. {n}: falta encabezado siguiente")
                continue
            fin = sig.posicion
        else:
            fin = len(texto)

        bloque = texto[a.posicion:fin].strip()

        salto = bloque.find("\n")
        resto = bloque[salto + 1:].strip() if salto >= 0 else ""

        if len(limpiar(resto)) < 20:
            errores.append(
                f"art. {n}: cuerpo insuficiente "
                f"({len(limpiar(resto))} caracteres)"
            )
            continue

        bloques[n] = {
            "aparicion": a,
            "titulo": a.linea,
            "texto": bloque,
        }

    return bloques, errores


def filas_bd(conexion: sqlite3.Connection, id_fuente: str):
    return conexion.execute(
        """
        SELECT id, id_bloque, articulo_boe, titulo_bloque, texto
        FROM articulos_fuente
        WHERE id_boe = ?
        ORDER BY id
        """,
        (id_fuente,),
    ).fetchall()


def articulo_inicio(texto: str) -> str:
    m = PATRON.match((texto or "").lstrip())
    return m.group(1) if m else ""


def auditar_filas(
    fuente: Fuente,
    filas,
    bloques: dict[int, dict],
):
    sospechosas = []

    for fila in filas:
        declarado = limpiar(fila["articulo_boe"])
        if not declarado.isdigit():
            continue

        n = int(declarado)
        titulo_bd = limpiar(fila["titulo_bloque"])
        texto_bd = str(fila["texto"] or "")

        detectado = (
            articulo_inicio(titulo_bd)
            or articulo_inicio(texto_bd[:1000])
        )

        if detectado and detectado != declarado:
            sospechosas.append(
                (
                    fila["id"],
                    declarado,
                    f"identidad numérica: empieza por {detectado}",
                    titulo_bd,
                )
            )
            continue

        oficial = bloques.get(n)
        if oficial is None or fuente.idioma != "es":
            continue

        rb = rubrica(titulo_bd, n)
        if not rb and texto_bd:
            rb = rubrica(limpiar(texto_bd.splitlines()[0]), n)

        ro = rubrica(oficial["titulo"], n)

        if rb and ro and not rubricas_compatibles(rb, ro):
            sospechosas.append(
                (
                    fila["id"],
                    declarado,
                    f"rúbrica incompatible: BD='{rb}' / OFICIAL='{ro}'",
                    titulo_bd,
                )
            )

    return sospechosas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFECTO))
    args = ap.parse_args()

    db = Path(args.db).resolve()
    if not db.exists():
        raise FileNotFoundError(db)

    print("=" * 78)
    print("AUDITORÍA / PLAN V7 CORPUS DOGV - SOLO LECTURA")
    print("=" * 78)
    print(f"Base de datos: {db}")
    print(f"Fuentes: {FUENTES}")
    print("Escrituras en BD: 0")
    print()

    resumen = {
        "fuentes": 0,
        "leidas": 0,
        "ausentes": 0,
        "parciales": 0,
        "errores": 0,
        "faltantes": 0,
        "reparaciones": 0,
    }

    datos = []

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")

        for fuente in FUENTES_PDF:
            resumen["fuentes"] += 1
            ruta = FUENTES / fuente.archivo
            filas = filas_bd(con, fuente.id_fuente)

            print("-" * 78)
            print(f"{fuente.norma} | {fuente.id_fuente}")
            print(f"  PDF: {fuente.archivo}")
            print(f"  Filas BD: {len(filas)}")

            if not ruta.exists():
                resumen["ausentes"] += 1
                print("  FUENTE AUSENTE")
                datos.append(None)
                continue

            try:
                resultado, intentos = obtener_extraccion_valida(ruta, fuente)

                texto = resultado["texto"]
                apariciones = resultado["apariciones"]
                nums = resultado["nums"]
                parcial = resultado["parcial"]
                razon = resultado["razon"]
                grupos = resultado["grupos"]
                modo_indice = resultado["modo_indice"]
                duplicados = resultado["duplicados"]
                concordantes = resultado["concordantes"]
                seleccionados = resultado["seleccionados"]
                bloques = resultado["bloques"]
                errores = resultado["errores"]
                motor = resultado["motor"]

            except Exception as exc:
                resumen["errores"] += 1
                print(f"  ERROR DE FUENTE: {exc}")
                datos.append(None)
                continue

            resumen["leidas"] += 1
            if parcial:
                resumen["parciales"] += 1
            resumen["errores"] += len(errores)

            print(f"  Motor de extracción seleccionado: {motor}")
            if len(intentos) > 1:
                print("  Motores probados:")
                for intento in intentos:
                    print(
                        f"    {intento['motor']}: "
                        f"{len(intento.get('errores', []))} error(es)"
                    )

            print(f"  Inventario: {nums[0]}-{nums[-1]} ({len(nums)})")
            print(f"  Motivo: {razon}")
            print(f"  Tipo: {'PARCIAL' if parcial else 'DESDE ARTÍCULO 1'}")
            print(
                f"  Índice duplicado detectado: {'SÍ' if modo_indice else 'NO'} "
                f"(duplicados={duplicados}, rúbricas concordantes={concordantes})"
            )
            print(
                f"  Encabezados de cuerpo seleccionados: "
                f"{len(seleccionados)}/{len(nums)}"
            )
            print(
                f"  Cuerpos recuperados y validados: "
                f"{len(bloques)}/{len(nums)}"
            )
            print(f"  Errores de identidad/extracción: {len(errores)}")
            for error in errores:
                print(f"    {error}")

            sospechosas = auditar_filas(
                fuente, filas, bloques
            )
            resumen["reparaciones"] += len(sospechosas)

            print(f"  Filas existentes a revisar/reparar: {len(sospechosas)}")
            for s in sospechosas:
                print(
                    f"    BD {s[0]} | art. {s[1]} | {s[2]} | {s[3]}"
                )

            bd_nums = {
                int(limpiar(f["articulo_boe"]))
                for f in filas
                if limpiar(f["articulo_boe"]).isdigit()
            }
            oficiales = set(nums)
            faltan_id = oficiales - bd_nums

            print(f"  Faltantes bajo este id: {len(faltan_id)}")
            if faltan_id:
                print(
                    "  Faltantes: "
                    + ", ".join(map(str, sorted(faltan_id)))
                )

            datos.append(
                {
                    "fuente": fuente,
                    "oficiales": oficiales,
                    "filas": filas,
                    "parcial": parcial,
                    "bloques": bloques,
                    "sospechosas": sospechosas,
                    "errores": errores,
                }
            )

        print()
        print("=" * 78)
        print("COBERTURA Y PLAN REAL POR NORMA")
        print("=" * 78)

        validos = [d for d in datos if d is not None]

        for norma in sorted({d["fuente"].norma for d in validos}):
            grupo = [d for d in validos if d["fuente"].norma == norma]
            completas = [d for d in grupo if not d["parcial"]]

            if completas:
                oficiales = set().union(*(d["oficiales"] for d in completas))
                tipo = "DESDE ARTÍCULO 1"
            else:
                oficiales = set().union(*(d["oficiales"] for d in grupo))
                tipo = "SÓLO FUENTE PARCIAL"

            cubiertos = set()
            reparar = set()

            for d in grupo:
                for fila in d["filas"]:
                    art = limpiar(fila["articulo_boe"])
                    if art.isdigit():
                        cubiertos.add(int(art))
                for s in d["sospechosas"]:
                    reparar.add(int(s[1]))

            faltan = oficiales - cubiertos
            resumen["faltantes"] += len(faltan)

            print(norma)
            print(f"  Referencia: {tipo}")
            print(f"  Artículos oficiales: {len(oficiales)}")
            print(f"  Cubiertos: {len(oficiales & cubiertos)}")
            print(f"  Ausentes: {len(faltan)}")
            print(f"  Reparar: {len(reparar)}")
            if faltan:
                print(
                    "  Ausentes: "
                    + ", ".join(map(str, sorted(faltan)))
                )
            if reparar:
                print(
                    "  Reparar: "
                    + ", ".join(map(str, sorted(reparar)))
                )

    print()
    print("=" * 78)
    print("RESUMEN V7")
    print("=" * 78)
    print(f"Fuentes configuradas:                {resumen['fuentes']}")
    print(f"Fuentes leídas:                      {resumen['leidas']}")
    print(f"Fuentes ausentes:                    {resumen['ausentes']}")
    print(f"Fuentes parciales:                   {resumen['parciales']}")
    print(f"Errores identidad/extracción:        {resumen['errores']}")
    print(f"Artículos realmente ausentes:        {resumen['faltantes']}")
    print(f"Filas existentes a reparar:          {resumen['reparaciones']}")
    print(
        f"Acciones previstas:                  "
        f"{resumen['faltantes'] + resumen['reparaciones']}"
    )
    print()
    print("La base de datos no ha sido modificada.")

    if resumen["ausentes"] or resumen["errores"]:
        print("PLAN DOGV: REQUIERE REVISIÓN")
        return 1

    if resumen["reparaciones"]:
        print("PLAN DOGV: VÁLIDO, CON REPARACIONES IDENTIFICADAS")
        return 0

    print("PLAN DOGV: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"ERROR FATAL: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        raise
