"""
Constructor Temario V2 - revisión humana interactiva del alcance de UN tema.

NO modifica BD, corpus, temario.csv ni bancos. Sólo escribe un JSON de decisión.

Regla dura:
- dentro de una convocatoria, una combinación norma-artículo sólo puede pertenecer
  a un único tema;
- se comprueba contra la ocupación inicial del manifiesto y contra decisiones V2
  previas compatibles encontradas en el mismo directorio de salida;
- una selección con conflictos no puede aceptarse.

Flujo:
- lee COLA_REVISION_HUMANA_V2;
- selecciona un tema por parte/número;
- en JURIDICO_EXPLICITO usa las normas literales que todavía requieren delimitar;
- en JURIDICO_DELIMITABLE el revisor elige explícitamente las normas;
- mantiene visible el texto oficial del tema durante la selección;
- muestra la estructura oficial disponible de cada norma como bloques B1..Bn;
- el revisor selecciona bloques y puede añadir/excluir artículos;
- materializa la decisión en un JSON trazable, sin persistencia consolidada.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import comun
import resolver_normas_temario_v2 as resolver_normas
import resolver_estructura_literal_v2 as estructura_literal
import auditar_representabilidad_estructural_online_v2 as online


RAIZ = comun.raiz_proyecto()


def cargar_json(ruta: Path) -> dict:
    if not ruta.is_file():
        raise SystemExit(f"ERROR: no existe {ruta}")
    return json.loads(ruta.read_text(encoding="utf-8"))


def elegir_tema(cola: dict, parte: str, numero: int) -> dict:
    matches = [
        x for x in cola.get("temas", [])
        if str(x.get("parte", "")).upper() == parte.upper()
        and int(x.get("numero_tema")) == numero
    ]
    if len(matches) != 1:
        raise SystemExit(f"ERROR: tema {parte} {numero} no encontrado de forma unívoca en la cola")
    return matches[0]


def parse_ids(texto: str) -> list[int]:
    salida = []
    for trozo in texto.replace(";", ",").split(","):
        trozo = trozo.strip()
        if not trozo:
            continue
        try:
            n = int(trozo)
        except ValueError:
            raise ValueError(f"ID no válido: {trozo}")
        if n not in salida:
            salida.append(n)
    return salida


def parse_articulos(texto: str) -> list[str]:
    """Admite etiquetas exactas y rangos enteros simples, p.ej. 1,2 bis,5-9."""
    salida: list[str] = []
    for trozo in texto.replace(";", ",").split(","):
        t = trozo.strip()
        if not t:
            continue
        if "-" in t:
            partes = [x.strip() for x in t.split("-", 1)]
            if len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
                a, b = int(partes[0]), int(partes[1])
                paso = 1 if b >= a else -1
                for n in range(a, b + paso, paso):
                    s = str(n)
                    if s not in salida:
                        salida.append(s)
                continue
        if t not in salida:
            salida.append(t)
    return salida


def normalizar_articulo(valor) -> str:
    return " ".join(str(valor or "").strip().lower().split())


def catalogo_y_fuentes(db: Path):
    with comun.conectar_sqlite_solo_lectura(db) as con:
        catalogo = resolver_normas.cargar_catalogo(con)
        fuentes: dict[int, list[str]] = {}
        for row in con.execute("SELECT norma_id, id_fuente FROM norma_fuentes ORDER BY norma_id, id_fuente"):
            fuentes.setdefault(int(row[0]), []).append(str(row[1]))
    return catalogo, fuentes


def nombre_norma(catalogo, nid: int) -> str:
    for n in catalogo:
        if int(n.norma_id) == nid:
            return n.nombre_canonico
    return ""


def validar_normas(catalogo, ids: list[int]) -> None:
    existentes = {int(n.norma_id) for n in catalogo}
    desconocidos = [x for x in ids if x not in existentes]
    if desconocidos:
        raise SystemExit("ERROR: norma_id inexistente(s) en catálogo: " + ", ".join(map(str, desconocidos)))


def cargar_bloques_norma(nid: int, fuentes: dict[int, list[str]]) -> tuple[str | None, str | None, list[dict]]:
    disponibles = []
    for fid in fuentes.get(nid, []):
        payload, origen = online.cargar_payload(fid)
        if payload and payload.get("articulos"):
            disponibles.append((fid, origen, payload))

    if not disponibles:
        return None, None, []

    if len(disponibles) == 1:
        fid, origen, payload = disponibles[0]
    else:
        print("  Hay varias fuentes estructuradas disponibles:")
        for i, (f, o, p) in enumerate(disponibles, 1):
            print(f"    {i}. {f} | {o} | artículos={len(p.get('articulos', []))}")
        while True:
            eleccion = input("  Fuente a usar [1]: ").strip() or "1"
            if eleccion.isdigit() and 1 <= int(eleccion) <= len(disponibles):
                fid, origen, payload = disponibles[int(eleccion) - 1]
                break
            print("  Opción no válida.")

    bloques = estructura_literal.indice_bloques(payload)
    return fid, origen, bloques


def articulo_labels(bloque: dict) -> list[str]:
    return [str(a.get("articulo")) for a in bloque.get("articulos", []) if a.get("articulo") is not None]


def mostrar_contexto_tema(texto_oficial: str) -> None:
    print("\n" + "=" * 78)
    print("TEXTO OFICIAL DEL TEMA")
    print("=" * 78)
    print(texto_oficial)
    print("=" * 78)


def clave_tema(parte, numero) -> tuple[str, int]:
    return str(parte or "").upper(), int(numero)


def construir_ocupados_iniciales(cola: dict, tema_actual: dict) -> dict[tuple[int, str], list[dict]]:
    actual = clave_tema(tema_actual["parte"], tema_actual["numero_tema"])
    ocupados: dict[tuple[int, str], list[dict]] = {}
    for registro in cola.get("ocupacion_inicial", []):
        try:
            nid = int(registro["norma_id"])
        except (KeyError, TypeError, ValueError):
            continue
        art = normalizar_articulo(registro.get("articulo"))
        if not art:
            continue
        otros = []
        for p in registro.get("propietarios", []):
            try:
                propietario = clave_tema(p.get("parte"), p.get("numero_tema"))
            except (TypeError, ValueError):
                continue
            if propietario != actual:
                otros.append({"parte": p.get("parte"), "numero_tema": p.get("numero_tema"), "origen": "MANIFIESTO"})
        if otros:
            ocupados[(nid, art)] = otros
    return ocupados


def incorporar_decisiones_previas(
    ocupados: dict[tuple[int, str], list[dict]],
    directorio: Path,
    cola_ruta: Path,
    tema_actual: dict,
    salida_actual: Path,
) -> int:
    """Añade decisiones humanas previas del mismo flujo encontradas junto a la salida."""
    actual = clave_tema(tema_actual["parte"], tema_actual["numero_tema"])
    contador = 0
    if not directorio.is_dir():
        return 0

    for ruta in directorio.glob("*.json"):
        if ruta.resolve() == salida_actual.resolve():
            continue
        try:
            payload = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("naturaleza") != "DECISION_HUMANA_ALCANCE_TEMA_V2":
            continue
        try:
            if Path(payload.get("origen_cola", "")).resolve() != cola_ruta.resolve():
                continue
            propietario_clave = clave_tema(payload.get("parte"), payload.get("numero_tema"))
        except (OSError, TypeError, ValueError):
            continue
        if propietario_clave == actual:
            continue
        if payload.get("estado") != "REVISION_COMPLETA":
            continue

        propietario = {
            "parte": payload.get("parte"),
            "numero_tema": payload.get("numero_tema"),
            "origen": ruta.name,
        }
        for decision in payload.get("decisiones_norma", []):
            try:
                nid = int(decision["norma_id"])
            except (KeyError, TypeError, ValueError):
                continue
            for articulo in decision.get("articulos", []):
                art = normalizar_articulo(articulo)
                if not art:
                    continue
                clave = (nid, art)
                propietarios = ocupados.setdefault(clave, [])
                if propietario not in propietarios:
                    propietarios.append(propietario)
                    contador += 1
    return contador


def conflictos_articulos(nid: int, articulos: list[str], ocupados: dict[tuple[int, str], list[dict]]) -> list[dict]:
    salida = []
    for art in articulos:
        propietarios = ocupados.get((nid, normalizar_articulo(art)), [])
        if propietarios:
            salida.append({"articulo": art, "propietarios": propietarios})
    return salida


def descripcion_bloque(b: dict, nid: int, ocupados: dict[tuple[int, str], list[dict]]) -> str:
    partes = []
    if b.get("titulo_padre"):
        partes.append(f"T{b['titulo_padre'][1]}")
    if b.get("capitulo_padre"):
        partes.append(f"C{b['capitulo_padre'][1]}")
    pref = " > ".join(partes)
    if pref:
        pref += " > "
    arts = articulo_labels(b)
    rango = "-" if not arts else (arts[0] if len(arts) == 1 else f"{arts[0]}..{arts[-1]}")
    conflictos = conflictos_articulos(nid, arts, ocupados)
    aviso = f" | OCUPADOS={len(conflictos)}" if conflictos else ""
    return f"{pref}{b['tipo']} {b['texto']} | n={len(arts)} | {rango}{aviso}"


def seleccionar_alcance_norma(
    nid: int,
    nombre: str,
    fuentes: dict[int, list[str]],
    texto_oficial: str,
    ocupados: dict[tuple[int, str], list[dict]],
) -> dict:
    mostrar_contexto_tema(texto_oficial)
    print("\n" + "-" * 78)
    print(f"NORMA [{nid}] {nombre}")
    fid, origen, bloques = cargar_bloques_norma(nid, fuentes)
    if not bloques:
        print("  SIN FUENTE ESTRUCTURADA CON ARTÍCULOS. No puede delimitarse en este revisor.")
        return {
            "norma_id": nid,
            "nombre_canonico": nombre,
            "estado": "PENDIENTE_FUENTE",
            "fuente": None,
            "bloques_aceptados": [],
            "articulos": [],
            "articulos_incluidos_manual": [],
            "articulos_excluidos_manual": [],
            "conflictos_unicidad": [],
        }

    print(f"  Fuente: {fid} | {origen}")
    print("  Bloques disponibles:")
    for i, b in enumerate(bloques, 1):
        print(f"    B{i:<3} {descripcion_bloque(b, nid, ocupados)}")

    while True:
        mostrar_contexto_tema(texto_oficial)
        print("  Selecciona bloques por número: p.ej. 2,5,8. Enter = ninguno.")
        print("  Los bloques marcados OCUPADOS contienen artículos ya asignados a otro tema.")
        while True:
            bruto = input("  Bloques: ").strip()
            try:
                indices = parse_ids(bruto) if bruto else []
            except ValueError as exc:
                print(f"  {exc}")
                continue
            if all(1 <= x <= len(bloques) for x in indices):
                break
            print("  Hay un número de bloque fuera de rango.")

        seleccionados = [bloques[i - 1] for i in indices]
        articulos: list[str] = []
        for b in seleccionados:
            for art in articulo_labels(b):
                if art not in articulos:
                    articulos.append(art)

        mostrar_contexto_tema(texto_oficial)
        incluidos = parse_articulos(input("  Artículos adicionales (opcional): ").strip())
        excluidos = parse_articulos(input("  Artículos a excluir (opcional): ").strip())

        for art in incluidos:
            if art not in articulos:
                articulos.append(art)
        articulos = [a for a in articulos if normalizar_articulo(a) not in {normalizar_articulo(x) for x in excluidos}]

        conflictos = conflictos_articulos(nid, articulos, ocupados)
        if conflictos:
            print("\n  CONFLICTO: esta selección reutiliza combinaciones norma-artículo de otros temas:")
            for c in conflictos:
                destinos = ", ".join(f"{p['parte']} {p['numero_tema']}" for p in c["propietarios"])
                print(f"    [{nid}] art. {c['articulo']} -> ya asignado a {destinos}")
            print("  La selección NO se acepta. Debes rehacerla o excluir esos artículos.")
            continue
        break

    print(f"  Resultado: {len(articulos)} artículos")
    if articulos:
        muestra = ", ".join(articulos[:25]) + (" ..." if len(articulos) > 25 else "")
        print(f"  {muestra}")

    return {
        "norma_id": nid,
        "nombre_canonico": nombre,
        "estado": "REVISADO" if articulos else "PENDIENTE",
        "fuente": fid,
        "origen_fuente": origen,
        "bloques_aceptados": [
            {
                "indice": i,
                "tipo": bloques[i - 1].get("tipo"),
                "texto": bloques[i - 1].get("texto"),
                "articulos": articulo_labels(bloques[i - 1]),
            }
            for i in indices
        ],
        "articulos": articulos,
        "articulos_incluidos_manual": incluidos,
        "articulos_excluidos_manual": excluidos,
        "conflictos_unicidad": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Revisa manualmente el alcance de un tema V2 sin tocar datos consolidados.")
    ap.add_argument("--cola", required=True)
    ap.add_argument("--parte", required=True)
    ap.add_argument("--tema", required=True, type=int)
    ap.add_argument("--salida-json", required=True)
    args = ap.parse_args()

    cola_ruta = Path(args.cola).expanduser().resolve()
    cola = cargar_json(cola_ruta)
    tema = elegir_tema(cola, args.parte, args.tema)
    db = Path(cola["db"]).expanduser().resolve()
    salida = Path(args.salida_json).expanduser().resolve()
    catalogo, fuentes = catalogo_y_fuentes(db)

    if not cola.get("reglas", {}).get("unicidad_norma_articulo_por_convocatoria"):
        raise SystemExit(
            "ERROR: la cola no contiene la regla de unicidad norma-artículo. "
            "Regenera la cola con preparar_revision_humana_v2.py."
        )
    if cola.get("conflictos_ocupacion_inicial"):
        raise SystemExit(
            "ERROR: la ocupación inicial ya contiene combinaciones norma-artículo en más de un tema. "
            "La revisión queda bloqueada hasta diagnosticar ese conflicto."
        )

    ocupados = construir_ocupados_iniciales(cola, tema)
    previos = incorporar_decisiones_previas(ocupados, salida.parent, cola_ruta, tema, salida)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - REVISION HUMANA DE ALCANCE - SIN PERSISTENCIA")
    print("=" * 78)
    print(f"Tema............................. {tema['parte']} {tema['numero_tema']}")
    print(f"Clasificación.................... {tema['clasificacion_v2']}")
    print(f"Pares ocupados por otros temas... {len(ocupados)}")
    print(f"Ocupaciones de revisiones previas {previos}")
    print("Regla unicidad................... ACTIVA")
    mostrar_contexto_tema(tema["texto_oficial"])

    ya_resueltos = []
    if tema["clasificacion_v2"] == "JURIDICO_EXPLICITO":
        pendientes = []
        for n in tema.get("normas_resueltas_literalmente", []):
            if n.get("requiere_delimitar_alcance"):
                pendientes.append(int(n["norma_id"]))
            else:
                ya_resueltos.append({
                    "norma_id": int(n["norma_id"]),
                    "nombre_canonico": n.get("nombre_canonico"),
                    "articulos_ya_resueltos": int(n.get("articulos_ya_resueltos", 0)),
                    "origen": "MANIFIESTO_RESUELTO_MECANICAMENTE",
                })
        normas_elegidas = pendientes
        print("Normas a delimitar............... " + (", ".join(map(str, normas_elegidas)) or "NINGUNA"))
    elif tema["clasificacion_v2"] == "JURIDICO_DELIMITABLE":
        print("\nCandidatas orientativas (NO son decisión):")
        for c in tema.get("normas_candidatas", []):
            print(f"  [{c['norma_id']}] {c['nombre_canonico']} | score={c.get('score_contenido')}")
        mostrar_contexto_tema(tema["texto_oficial"])
        while True:
            bruto = input("Norma_id aceptadas para este tema (separadas por coma): ").strip()
            try:
                normas_elegidas = parse_ids(bruto)
                validar_normas(catalogo, normas_elegidas)
                if normas_elegidas:
                    break
                print("Debe aceptar al menos una norma o cancelar con Ctrl+C.")
            except ValueError as exc:
                print(exc)
    else:
        raise SystemExit("ERROR: este revisor sólo cubre JURIDICO_EXPLICITO y JURIDICO_DELIMITABLE")

    decisiones = []
    for nid in normas_elegidas:
        decision = seleccionar_alcance_norma(
            nid,
            nombre_norma(catalogo, nid),
            fuentes,
            tema["texto_oficial"],
            ocupados,
        )
        decisiones.append(decision)
        if decision["estado"] == "REVISADO":
            propietario = {"parte": tema["parte"], "numero_tema": tema["numero_tema"], "origen": "REVISION_ACTUAL"}
            for art in decision["articulos"]:
                ocupados.setdefault((nid, normalizar_articulo(art)), []).append(propietario)

    completo = bool(decisiones) and all(x["estado"] == "REVISADO" for x in decisiones)
    salida_payload = {
        "version": 2,
        "naturaleza": "DECISION_HUMANA_ALCANCE_TEMA_V2",
        "modo": "SIN_PERSISTENCIA",
        "origen_cola": str(cola_ruta),
        "parte": tema["parte"],
        "numero_tema": tema["numero_tema"],
        "texto_oficial": tema["texto_oficial"],
        "clasificacion_v2": tema["clasificacion_v2"],
        "estado": "REVISION_COMPLETA" if completo else "REVISION_INCOMPLETA",
        "normas_ya_resueltas_mecanicamente": ya_resueltos,
        "decisiones_norma": decisiones,
        "reglas": {
            "decision_normas_humana": tema["clasificacion_v2"] == "JURIDICO_DELIMITABLE",
            "decision_bloques_articulos_humana": True,
            "unicidad_norma_articulo_por_convocatoria": True,
            "conflictos_unicidad_permitidos": False,
            "consulta_temario_referencias": False,
            "modifica_bd": False,
            "modifica_corpus": False,
            "modifica_temario_csv": False,
            "modifica_bancos": False,
        },
    }

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(salida_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 78)
    print(f"Estado revisión.................. {salida_payload['estado']}")
    print(f"Decisión JSON.................... {salida}")
    print("Regla unicidad norma-artículo.... CUMPLIDA")
    print("No se ha modificado BD, corpus, temario.csv ni bancos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
