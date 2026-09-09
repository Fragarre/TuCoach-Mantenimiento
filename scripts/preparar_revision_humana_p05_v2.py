"""
Constructor Temario V2 - preparación de dossier de revisión humana del piloto P05.

NO MODIFICA LA BASE DE DATOS.
NO MODIFICA EL CORPUS.
NO ACEPTA AUTOMÁTICAMENTE RESPALDOS.

Consume la auditoría de pertinencia jurídica P05 y genera un dossier Markdown compacto
para revisión humana. Reduce ruido, prioriza fragmentos con mayor cobertura de anclas
y conserva la trazabilidad exacta (fuente, posiciones y texto candidato).
"""
from __future__ import annotations

import argparse
import json
import re
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


def seleccionar_fragmentos(ficha: dict, maximo: int = 3) -> list[dict]:
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

    candidatos.sort(
        key=lambda x: (
            -int(x.get("cobertura_anclas", 0)),
            int(x.get("inicio", 0)),
        )
    )

    elegidos: list[dict] = []
    for cand in candidatos:
        if any(solapa(cand, e) for e in elegidos):
            continue
        elegidos.append(cand)
        if len(elegidos) >= maximo:
            break
    return elegidos


def escapar_md(texto: str) -> str:
    return texto.replace("\n", " ").strip()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Prepara dossier Markdown de revisión humana para P05 sin modificar BD/corpus."
    )
    ap.add_argument("--pertinencia-json", required=True)
    ap.add_argument("--salida-md", required=True)
    ap.add_argument("--max-fragmentos", type=int, default=3)
    args = ap.parse_args()

    entrada = Path(args.pertinencia_json).expanduser().resolve()
    salida = Path(args.salida_md).expanduser().resolve()

    data = json.loads(entrada.read_text(encoding="utf-8"))
    if data.get("naturaleza") != "AUDITORIA_PERTINENCIA_JURIDICA_P05":
        raise SystemExit("ERROR: el JSON no es una auditoría de pertinencia P05.")
    if data.get("estado") != "LISTO_PARA_REVISION_HUMANA":
        raise SystemExit("ERROR: la auditoría no está LISTO_PARA_REVISION_HUMANA.")

    fichas = data.get("fichas") or []
    if not fichas:
        raise SystemExit("ERROR: la auditoría no contiene fichas para revisión.")

    lineas: list[str] = []
    lineas.append("# Constructor Temario V2 — Revisión humana P05")
    lineas.append("")
    lineas.append("**Piloto:** A1 Especial 1 — P05")
    lineas.append("")
    lineas.append("> Este dossier NO valida automáticamente ninguna proposición. La coincidencia textual solo sirve para localizar evidencia candidata en fuentes oficiales.")
    lineas.append("")
    lineas.append("## Instrucciones de revisión")
    lineas.append("")
    lineas.append("Para cada proposición, comprobar si al menos un fragmento respalda materialmente la afirmación. Marcar una sola decisión: `PERTINENTE`, `DESCARTADO` o `PENDIENTE`. No aceptar una proposición por mera presencia de palabras clave.")
    lineas.append("")

    total_fragmentos = 0
    for ficha in fichas:
        codigo = ficha.get("codigo_proposicion", "?")
        ident = ficha.get("identificador_oficial", "?")
        tipo = ficha.get("tipo_fuente", "?")
        proposicion = ficha.get("proposicion", "")
        seleccion = seleccionar_fragmentos(ficha, maximo=max(1, args.max_fragmentos))
        total_fragmentos += len(seleccion)

        lineas.append(f"## {codigo}")
        lineas.append("")
        lineas.append(f"**Proposición a revisar:** {escapar_md(proposicion)}")
        lineas.append("")
        lineas.append(f"**Fuente oficial:** `{ident}`  ")
        lineas.append(f"**Tipo:** `{tipo}`")
        lineas.append("")
        lineas.append("**Decisión humana:** [ ] PERTINENTE  [ ] DESCARTADO  [ ] PENDIENTE")
        lineas.append("")
        lineas.append("**Observación / fundamento:**")
        lineas.append("")
        lineas.append("---")
        lineas.append("")

        if not seleccion:
            lineas.append("No hay fragmentos candidatos seleccionados automáticamente. Revisión manual necesaria.")
            lineas.append("")
            continue

        for i, frag in enumerate(seleccion, start=1):
            lineas.append(f"### Fragmento candidato {i}")
            lineas.append("")
            lineas.append(
                f"Posición aproximada: `{frag.get('inicio')}–{frag.get('fin')}` | "
                f"ancla de origen: `{frag.get('ancla')}` | cobertura de anclas: `{frag.get('cobertura_anclas')}`"
            )
            lineas.append("")
            lineas.append(f"> {escapar_md(frag.get('fragmento', ''))}")
            lineas.append("")

    lineas.append("## Resultado global")
    lineas.append("")
    lineas.append("Estado del dossier: `PENDIENTE_REVISION_HUMANA`  ")
    lineas.append("BD modificada: `NO`  ")
    lineas.append("Corpus modificado: `NO`  ")
    lineas.append("Pertinencia aceptada automáticamente: `NO`")
    lineas.append("")

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas), encoding="utf-8")

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - DOSSIER REVISION HUMANA P05 - SIN BD")
    print("=" * 78)
    print(f"Fichas............................ {len(fichas)}")
    print(f"Fragmentos seleccionados......... {total_fragmentos}")
    print(f"Markdown.......................... {salida}")
    print("Estado............................ PENDIENTE_REVISION_HUMANA")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
