"""
Constructor Temario V2 - dossier GENERAL de revisión humana de pertinencia.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA AUTOMÁTICAMENTE RESPALDOS.

Consume AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2 y genera Markdown compacto,
agrupado por proposición y fuente, conservando trazabilidad de cada fragmento.
"""
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path


def norm(s: str) -> str:
    return " ".join((s or "").split())


def cobertura_anclas(fragmento: str, anclas: list[str]) -> int:
    bajo = fragmento.casefold()
    return sum(1 for a in anclas if a.casefold() in bajo)


def solapa(a: dict, b: dict, margen: int = 180) -> bool:
    return not (
        int(a.get("fin", 0)) + margen < int(b.get("inicio", 0))
        or int(b.get("fin", 0)) + margen < int(a.get("inicio", 0))
    )


def seleccionar_fragmentos(ficha: dict, maximo: int) -> list[dict]:
    anclas = ficha.get("anclas") or []
    candidatos = []
    for f in ficha.get("fragmentos_candidatos") or []:
        frag = norm(f.get("fragmento", ""))
        if not frag:
            continue
        x = dict(f)
        x["fragmento"] = frag
        x["cobertura_anclas"] = cobertura_anclas(frag, anclas)
        candidatos.append(x)
    candidatos.sort(key=lambda x: (-int(x.get("cobertura_anclas", 0)), int(x.get("inicio", 0))))
    elegidos: list[dict] = []
    for cand in candidatos:
        if any(solapa(cand, e) for e in elegidos):
            continue
        elegidos.append(cand)
        if len(elegidos) >= maximo:
            break
    return elegidos


def md(s: str) -> str:
    return norm(s).replace("`", "'")


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepara dossier general de revisión humana sin BD/corpus.")
    ap.add_argument("--pertinencia-json", required=True)
    ap.add_argument("--salida-md", required=True)
    ap.add_argument("--max-fragmentos", type=int, default=3)
    args = ap.parse_args()

    entrada = Path(args.pertinencia_json).expanduser().resolve()
    salida = Path(args.salida_md).expanduser().resolve()
    data = json.loads(entrada.read_text(encoding="utf-8"))

    if data.get("naturaleza") != "AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2":
        raise SystemExit("ERROR: el JSON no es AUDITORIA_PERTINENCIA_FUENTES_EXTERNAS_V2.")
    if data.get("estado") != "LISTO_PARA_REVISION_HUMANA":
        raise SystemExit("ERROR: la auditoría no está LISTO_PARA_REVISION_HUMANA.")
    fichas = data.get("fichas") or []
    if not fichas:
        raise SystemExit("ERROR: no hay fichas para revisión.")

    grupos: OrderedDict[str, list[dict]] = OrderedDict()
    for ficha in fichas:
        grupos.setdefault(ficha.get("codigo_proposicion", "?"), []).append(ficha)

    lineas = [
        "# Constructor Temario V2 — Revisión humana de pertinencia",
        "",
        f"**Caso:** `{md(data.get('caso', '?'))}`  ",
        f"**Pseudonorma:** `{md(data.get('pseudonorma', '?'))}`  ",
        f"**Unidad:** `{md(data.get('unidad', '?'))}`",
        "",
        "> Este dossier no valida automáticamente ninguna proposición. Las anclas y su cobertura solo priorizan fragmentos de fuentes recuperadas para revisión jurídica humana.",
        "",
        "## Criterio de revisión",
        "",
        "Para cada relación proposición/fuente marcar `PERTINENTE`, `DESCARTADO` o `PENDIENTE`. La decisión debe basarse en el contenido material del fragmento, no en la coincidencia de palabras. Una fuente sectorial solo respalda doctrina general si el pasaje formula realmente esa doctrina con alcance general.",
        "",
    ]

    total_fragmentos = 0
    for codigo, grupo in grupos.items():
        lineas += [f"## {md(codigo)}", "", f"**Proposición:** {md(grupo[0].get('proposicion', ''))}", ""]
        for ficha in grupo:
            ident = ficha.get("identificador_oficial", "?")
            seleccion = seleccionar_fragmentos(ficha, max(1, args.max_fragmentos))
            total_fragmentos += len(seleccion)
            lineas += [
                f"### Fuente `{md(ident)}`",
                "",
                f"Tipo: `{md(ficha.get('tipo_fuente', '?'))}`  ",
                "**Decisión humana:** [ ] PERTINENTE  [ ] DESCARTADO  [ ] PENDIENTE  ",
                "**Observación / fundamento:**",
                "",
            ]
            if not seleccion:
                lineas += ["No se seleccionaron fragmentos automáticamente. Requiere búsqueda/revisión manual.", ""]
                continue
            for i, frag in enumerate(seleccion, 1):
                lineas += [
                    f"#### Fragmento {i}",
                    "",
                    f"Posición: `{frag.get('inicio')}–{frag.get('fin')}` | ancla: `{md(frag.get('ancla', ''))}` | cobertura: `{frag.get('cobertura_anclas')}`",
                    "",
                    f"> {md(frag.get('fragmento', ''))}",
                    "",
                ]

    lineas += [
        "## Resultado global",
        "",
        "Estado: `PENDIENTE_REVISION_HUMANA`  ",
        "BD modificada: `NO`  ",
        "Corpus modificado: `NO`  ",
        "Pertinencia aceptada automáticamente: `NO`",
        "",
    ]
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas), encoding="utf-8")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - DOSSIER GENERAL REVISION HUMANA - SIN BD")
    print("=" * 78)
    print(f"Caso.............................. {data.get('caso')}")
    print(f"Proposiciones..................... {len(grupos)}")
    print(f"Fichas fuente/proposición......... {len(fichas)}")
    print(f"Fragmentos seleccionados......... {total_fragmentos}")
    print(f"Markdown.......................... {salida}")
    print("Estado............................ PENDIENTE_REVISION_HUMANA")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
