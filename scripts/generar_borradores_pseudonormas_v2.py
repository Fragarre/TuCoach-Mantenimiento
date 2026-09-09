"""
Constructor Temario V2 - generador de borradores pseudonormativos.

SOLO LECTURA.

Selecciona únicamente temas clasificados como JURIDICO_DOCTRINAL_TRANSVERSAL
y los transforma en una estructura inicial de pseudonorma. La segmentación es
conservadora: no redacta contenido jurídico, no busca fuentes, no acepta
respaldos y no modifica la base de datos.

Toda unidad nace en REVISION.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

import clasificar_alcance_juridico_v2 as alcance
import extraer_estructura_temario_v2 as estructura


@dataclass
class UnidadPseudo:
    codigo: str
    texto_temario: str
    titulo_provisional: str
    estado: str = "REVISION"
    tipo_contenido: str = "PENDIENTE_CLASIFICACION"
    sintesis: str | None = None
    respaldos: list[dict] | None = None
    notas_revision: list[str] | None = None

    def __post_init__(self) -> None:
        if self.respaldos is None:
            self.respaldos = []
        if self.notas_revision is None:
            self.notas_revision = []


@dataclass
class BorradorPseudo:
    identidad: str
    parte: str
    numero_tema: int
    bloque_numero: str | None
    bloque_titulo: str | None
    texto_tema_oficial: str
    clasificacion: str
    evidencia_clasificacion: str
    estado: str
    unidades: list[UnidadPseudo]


def normalizar_slug(texto: str, max_len: int = 70) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).upper()
    x = re.sub(r"[^A-Z0-9]+", "_", x).strip("_")
    return x[:max_len].rstrip("_") or "TEMA"


def limpiar_fragmento(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "")).strip(" ;.:\t\r\n")


def extraer_rotulo(texto: str) -> str:
    """Obtiene un título provisional sin inventar contenido.

    Usa las primeras palabras del propio fragmento; no resume semánticamente.
    """
    limpio = limpiar_fragmento(texto)
    if not limpio:
        return "Unidad pendiente"
    palabras = limpio.split()
    if len(palabras) <= 12:
        return limpio
    return " ".join(palabras[:12]) + "…"


def segmentar_tema(texto: str) -> list[str]:
    """Segmentación conservadora del texto oficial.

    Reglas:
    - separa por punto y punto y coma;
    - conserva dos puntos dentro del fragmento porque a menudo introducen una
      enumeración jurídicamente inseparable;
    - elimina fragmentos vacíos;
    - no fusiona ni completa unidades mediante inferencia.
    """
    bruto = re.sub(r"\s+", " ", texto or "").strip()
    if not bruto:
        return []

    partes = re.split(r"(?<=[.;])\s+", bruto)
    salida: list[str] = []
    for parte in partes:
        frag = limpiar_fragmento(parte)
        if frag:
            salida.append(frag)
    return salida


def identidad_pseudo(tema: estructura.TemaExtraido, unidades: list[str]) -> str:
    base = unidades[0] if unidades else tema.texto
    # Si el primer fragmento incluye dos puntos, la parte previa suele actuar
    # como rótulo literal del tema y produce una identidad más estable.
    rotulo = base.split(":", 1)[0].strip()
    return f"PSEUDO:{normalizar_slug(tema.parte)}_{tema.numero_tema}_{normalizar_slug(rotulo)}"


def construir_borrador(
    tema: estructura.TemaExtraido,
    evidencia: str,
) -> BorradorPseudo:
    fragmentos = segmentar_tema(tema.texto)
    unidades = [
        UnidadPseudo(
            codigo=f"P{i:02d}",
            texto_temario=fragmento,
            titulo_provisional=extraer_rotulo(fragmento),
        )
        for i, fragmento in enumerate(fragmentos, 1)
    ]

    if not unidades:
        unidades = [
            UnidadPseudo(
                codigo="P01",
                texto_temario=tema.texto,
                titulo_provisional="Unidad pendiente",
                notas_revision=["No se pudo segmentar automáticamente el texto oficial."],
            )
        ]

    return BorradorPseudo(
        identidad=identidad_pseudo(tema, fragmentos),
        parte=tema.parte,
        numero_tema=tema.numero_tema,
        bloque_numero=getattr(tema, "bloque_numero", None),
        bloque_titulo=getattr(tema, "bloque_titulo", None),
        texto_tema_oficial=tema.texto,
        clasificacion="JURIDICO_DOCTRINAL_TRANSVERSAL",
        evidencia_clasificacion=evidencia,
        estado="REVISION",
        unidades=unidades,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Genera borradores de pseudonormas para temas doctrinales, sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--salida-json")
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    borradores: list[BorradorPseudo] = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        if clase == "JURIDICO_DOCTRINAL_TRANSVERSAL":
            borradores.append(construir_borrador(tema, evidencia))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - BORRADORES PSEUDONORMATIVOS - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"Temas totales.................... {len(temas)}")
    print(f"Temas doctrinales................ {len(borradores)}")
    print()

    total_unidades = 0
    for borrador in borradores:
        print(
            f"{borrador.parte:<8} | {borrador.numero_tema:>2} | "
            f"{borrador.identidad}"
        )
        print(f"  evidencia: {borrador.evidencia_clasificacion}")
        print(f"  estado: {borrador.estado}")
        for unidad in borrador.unidades:
            total_unidades += 1
            print(f"  {unidad.codigo} | {unidad.estado} | {unidad.titulo_provisional}")
            print(f"       {unidad.texto_temario}")
        print()

    print("Resumen:")
    print(f"  Pseudonormas borrador........... {len(borradores)}")
    print(f"  Unidades pseudonormativas....... {total_unidades}")
    print()
    print("No se ha redactado contenido jurídico.")
    print("No se han buscado ni aceptado respaldos.")
    print("La base de datos NO ha sido modificada.")

    if args.salida_json:
        salida = Path(args.salida_json).expanduser().resolve()
        salida.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "naturaleza": "BORRADORES_PSEUDONORMATIVOS",
            "estado": "REVISION",
            "pdf": str(pdf),
            "borradores": [asdict(x) for x in borradores],
        }
        salida.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON escrito...................... {salida}")
        print("La escritura anterior afecta sólo al fichero de salida solicitado.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
