"""
Constructor Temario V2 - auditoría de clasificación jurídica.

SOLO LECTURA.

No modifica la clasificación ni resuelve normas o artículos. Reutiliza la
clasificación de alcance y señala únicamente casos fronterizos que conviene
revisar antes de pasar a la resolución normativa.

Criterios conservadores:
- JURIDICO_DELIMITABLE con evidencia mínima (2 señales) -> revisar.
- JURIDICO_DELIMITABLE obtenido solo por el bloque -> revisar.
- JURIDICO_DOCTRINAL_TRANSVERSAL con una sola señal doctrinal -> revisar.
- Temas doctrinales con una cita normativa literal simultánea -> revisar si la
  cita podría delimitar una parte sustancial del tema.

La auditoría no decide la clase correcta: produce una cola de revisión.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

import extraer_estructura_temario_v2 as estructura
import clasificar_alcance_juridico_v2 as alcance
import resolver_normas_temario_v2 as resolver


def _numero_senales(evidencia: str) -> int | None:
    m = re.search(r";senales=(\d+)", evidencia or "")
    return int(m.group(1)) if m else None


def _numero_senales_doctrinales(evidencia: str) -> int:
    prefijo = "SENAL_DOCTRINAL:"
    if not (evidencia or "").startswith(prefijo):
        return 0
    resto = evidencia[len(prefijo):].strip()
    if not resto:
        return 0
    return len([x for x in resto.split(",") if x.strip()])


def auditar_tema(tema: estructura.TemaExtraido, clase: str, evidencia: str) -> list[tuple[str, str]]:
    avisos: list[tuple[str, str]] = []

    if clase == "JURIDICO_DELIMITABLE":
        n = _numero_senales(evidencia)
        if n == 2:
            avisos.append((
                "MEDIA",
                "DELIMITABLE con el mínimo de 2 señales; comprobar que la materia no sea doctrinal/transversal.",
            ))
        if evidencia.startswith("BLOQUE_JURIDICO_ACOTABLE:"):
            avisos.append((
                "ALTA",
                "DELIMITABLE inferido solo por el bloque; falta evidencia temática específica en el texto del tema.",
            ))

    if clase == "JURIDICO_DOCTRINAL_TRANSVERSAL":
        n_doc = _numero_senales_doctrinales(evidencia)
        if n_doc == 1:
            avisos.append((
                "MEDIA",
                "DOCTRINAL_TRANSVERSAL apoyado en una sola señal; comprobar que no sea una materia jurídicamente acotable.",
            ))

        citas = resolver.detectar_citas(tema.texto)
        if citas:
            nombres = ", ".join(c.texto for c in citas[:3])
            avisos.append((
                "MEDIA",
                f"El tema doctrinal contiene también cita(s) literal(es): {nombres}. Revisar si delimitan una parte sustancial del alcance.",
            ))

    return avisos


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audita casos fronterizos de la clasificación jurídica V2 sin modificar datos."
    )
    ap.add_argument("--pdf", required=True)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for tema in temas:
        estructura.clasificar_tema(tema)

    resultados = []
    for tema in temas:
        clase, evidencia = alcance.clasificar(tema)
        avisos = auditar_tema(tema, clase, evidencia)
        if avisos:
            resultados.append((tema, clase, evidencia, avisos))

    prioridades = Counter(
        prioridad
        for _, _, _, avisos in resultados
        for prioridad, _ in avisos
    )

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - AUDITORIA DE CLASIFICACION JURIDICA - SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"Temas............................ {len(temas)}")
    print(f"Temas señalados.................. {len(resultados)}")
    print(f"Avisos ALTA...................... {prioridades.get('ALTA', 0)}")
    print(f"Avisos MEDIA..................... {prioridades.get('MEDIA', 0)}")
    print()

    for tema, clase, evidencia, avisos in resultados:
        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | {clase}")
        print(f"  evidencia: {evidencia}")
        for prioridad, motivo in avisos:
            print(f"  [{prioridad}] {motivo}")
        print(f"  texto: {tema.texto}")
        print()

    print("La auditoría no ha modificado ninguna clasificación.")
    print("No se ha consultado ni modificado temario_referencias.")
    print("No se han aceptado normas ni resuelto artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
