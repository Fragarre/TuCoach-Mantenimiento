"""Calibración relativa de dificultad para generadores IA de TuCoach.

La dificultad no se presupone: un evaluador clasifica una muestra del banco de
la convocatoria y devuelve anclas reutilizables en generación y validación.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from typing import Any, Callable


@dataclass(frozen=True)
class PerfilDificultad:
    ambito: str
    ids_muestra: tuple[int, ...]
    mediana: int
    percentil_75: int
    percentil_90: int
    anclas_medias: tuple[dict[str, Any], ...]
    anclas_altas: tuple[dict[str, Any], ...]

    def texto_prompt(self) -> str:
        return """
PERFIL DE DIFICULTAD DE LA CONVOCATORIA
La dificultad se mide RELATIVAMENTE contra la muestra real del banco indicada
abajo, no contra una oposición abstracta. ALTA exige, como mínimo, el umbral
del percentil 75 ({p75}/4); MUY_ALTA exige aproximarse al percentil 90
({p90}/4). Si no alcanza ALTA respecto a estas anclas, devuelve
INSUFICIENTE.

Anclas medias de la convocatoria:
{medias}

Anclas altas de la convocatoria:
{altas}
""".strip().format(
            p75=self.percentil_75,
            p90=self.percentil_90,
            medias=json.dumps(self.anclas_medias, ensure_ascii=False),
            altas=json.dumps(self.anclas_altas, ensure_ascii=False),
        )

    def serializar(self) -> dict[str, Any]:
        return asdict(self)


def _percentil(valores: list[int], fraccion: float) -> int:
    if not valores:
        raise ValueError("No hay puntuaciones para calibrar.")
    posicion = max(0, math.ceil(len(valores) * fraccion) - 1)
    return sorted(valores)[posicion]


def _resumen(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(p["id"]),
        "enunciado": str(p["enunciado"]),
        "A": str(p["opcion_a"]), "B": str(p["opcion_b"]),
        "C": str(p["opcion_c"]), "D": str(p["opcion_d"]),
    }


def calibrar(
    preguntas: list[dict[str, Any]],
    *,
    ambito: str,
    modelo: str,
    seleccionar_json: Callable[..., dict[str, Any]],
    limite: int = 18,
) -> PerfilDificultad:
    """Devuelve perfil de dificultad para una muestra estable del banco."""
    muestra = preguntas[:limite]
    if len(muestra) < 5:
        raise RuntimeError(
            f"{ambito}: se requieren al menos cinco preguntas de banco para calibrar dificultad."
        )
    datos = [_resumen(p) for p in muestra]
    prompt = f"""
Actúas como calibrador independiente de dificultad para una convocatoria.
No determines la corrección jurídica/técnica; puntúa la exigencia relativa de
cada pregunta REAL de su banco: 1=BAJA, 2=MEDIA, 3=ALTA, 4=MUY_ALTA.
Considera precisión exigida, proximidad de distractores, número de condiciones
relevantes y razonamiento necesario. No uses el prestigio de una norma o tema.

ÁMBITO: {ambito}
PREGUNTAS:
{json.dumps(datos, ensure_ascii=False)}

Devuelve SOLO JSON: {{"puntuaciones":[{{"id":1,"dificultad":1}}, ...]}}
""".strip()
    respuesta = seleccionar_json(
        prompt=prompt, modelo=modelo, operacion="calibrar_dificultad_banco_ia"
    )
    crudas = respuesta.get("puntuaciones") if isinstance(respuesta, dict) else None
    por_id = {
        int(item.get("id")): int(item.get("dificultad"))
        for item in (crudas or [])
        if isinstance(item, dict)
        and str(item.get("id", "")).isdigit()
        and str(item.get("dificultad", "")).isdigit()
        and 1 <= int(item["dificultad"]) <= 4
    }
    if set(por_id) != {int(p["id"]) for p in muestra}:
        raise RuntimeError("La calibración IA no devolvió una puntuación válida para toda la muestra.")
    puntuadas = [(p, por_id[int(p["id"])]) for p in muestra]
    valores = [nota for _, nota in puntuadas]
    mediana, p75, p90 = _percentil(valores, .5), _percentil(valores, .75), _percentil(valores, .9)
    medias = [_resumen(p) for p, nota in puntuadas if nota == mediana][:3]
    altas = [_resumen(p) for p, nota in puntuadas if nota >= p75][:3]
    if not medias or not altas:
        raise RuntimeError("La calibración no pudo seleccionar anclas de dificultad suficientes.")
    return PerfilDificultad(
        ambito=ambito, ids_muestra=tuple(int(p["id"]) for p in muestra),
        mediana=mediana, percentil_75=p75, percentil_90=p90,
        anclas_medias=tuple(medias), anclas_altas=tuple(altas),
    )


def filtrar_candidatas_altas(
    candidatas: list[dict[str, Any]], *, perfil: PerfilDificultad,
    modelo: str, seleccionar_json: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    """Conserva solo candidatas que igualan el umbral alto del perfil."""
    if not candidatas:
        return []
    datos = [{"id": indice, **_resumen({"id": indice, **pregunta})}
             for indice, pregunta in enumerate(candidatas, start=1)]
    respuesta = seleccionar_json(
        prompt=(
            "Evalúa estas candidatas con el siguiente perfil relativo. "
            "Puntúa de 1 a 4. Solo alcanza ALTA/MUY_ALTA si iguala o supera "
            "el percentil 75 del banco. Devuelve SOLO JSON con "
            '{"puntuaciones":[{"id":1,"dificultad":1}]}.'
            "\n\n" + perfil.texto_prompt() + "\n\nCANDIDATAS:\n" +
            json.dumps(datos, ensure_ascii=False)
        ),
        modelo=modelo, operacion="validar_dificultad_relativa_ia",
    )
    notas = {
        int(item.get("id")): int(item.get("dificultad"))
        for item in (respuesta.get("puntuaciones") or [])
        if isinstance(item, dict)
        and str(item.get("id", "")).isdigit()
        and str(item.get("dificultad", "")).isdigit()
        and 1 <= int(item["dificultad"]) <= 4
    }
    if set(notas) != set(range(1, len(candidatas) + 1)):
        raise RuntimeError("La validación relativa no devolvió puntuaciones válidas para todas las candidatas.")
    return [p for indice, p in enumerate(candidatas, start=1) if notas[indice] >= perfil.percentil_75]
