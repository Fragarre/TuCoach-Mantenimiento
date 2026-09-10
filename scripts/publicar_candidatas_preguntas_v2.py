"""Publica candidatas PSEUDO V2 auditadas en lote_preguntas + pregunta_contextos_v2.

Vía estrictamente aditiva y separada del generador jurídico REAL consolidado.
Por defecto SOLO REVISION. Con --aplicar crea backup y escribe únicamente:
- lote_preguntas
- pregunta_contextos_v2

No modifica bancos. La vinculación posterior corresponde a
mantener_banco_preguntas_v2.py.

Contrato actual: solo PSEUDO TEORICA. MIXTA y PRACTICA no se infieren.
Solo admite candidatas que hayan superado validar_candidatas_preguntas_v2.py.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from validar_candidatas_preguntas_v2 import auditoria_ok, revision_ok

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
ESTADOS_FINALES = {
    "RESUELTO", "ACEPTADO", "ACEPTADA", "APROBADO_PARA_PERSISTENCIA",
    "ACEPTABLE_FORMALMENTE",
}
TIPO_FUENTE = "ia_generada_v2"
CRITERIO = "PSEUDOARTICULO_V2"
ESTADO_PUBLICABLE = "VALIDADA_AUDITADA_V2"


def leer_jsonl(path: Path) -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for numero, linea in enumerate(f, 1):
            if not linea.strip():
                continue
            try:
                dato = json.loads(linea)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"JSON inválido en {path}, línea {numero}: {exc}") from exc
            if not isinstance(dato, dict):
                raise RuntimeError(f"La línea {numero} no contiene un objeto JSON.")
            salida.append(dato)
    return salida


def nivel_desde_codigo(codigo: str) -> str:
    m = re.match(r"^(A1|A2|C1|C2)(?:\b|[-_])", str(codigo or "").strip().upper())
    if not m:
        raise RuntimeError(f"No se puede obtener A1/A2/C1/C2 del código {codigo!r}.")
    return m.group(1)


def normalizar_texto(valor: Any) -> str:
    return re.sub(r"\s+", " ", str(valor or "").strip().casefold())


def clave_pregunta(dato: dict[str, Any] | sqlite3.Row) -> tuple[str, str, str, str, str]:
    return tuple(
        normalizar_texto(dato[c])
        for c in ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d")
    )


def comprobar_tablas(con: sqlite3.Connection) -> None:
    necesarias = {
        "convocatorias", "temarios", "temario_temas", "temario_pseudonormas",
        "temario_pseudoarticulos", "temario_pseudoarticulo_respaldos",
        "lote_preguntas", "pregunta_contextos_v2",
    }
    existentes = {
        str(r[0]) for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    faltan = sorted(necesarias - existentes)
    if faltan:
        raise RuntimeError("Faltan tablas necesarias: " + ", ".join(faltan))


def seleccionar_convocatoria(con: sqlite3.Connection, codigo: str) -> tuple[int, int]:
    conv = con.execute(
        "SELECT id,codigo FROM convocatorias WHERE codigo=?",
        (codigo,),
    ).fetchone()
    if conv is None:
        raise RuntimeError(f"No existe convocatoria {codigo}.")
    temarios = con.execute(
        "SELECT id FROM temarios WHERE convocatoria_id=? ORDER BY id",
        (int(conv["id"]),),
    ).fetchall()
    if len(temarios) != 1:
        raise RuntimeError(
            "La convocatoria debe tener exactamente un temario; "
            f"encontrados={len(temarios)}."
        )
    return int(conv["id"]), int(temarios[0]["id"])


def validar_auditoria_embebida(candidata: dict[str, Any]) -> None:
    bloque = candidata.get("validacion_v2")
    if not isinstance(bloque, dict):
        raise RuntimeError("La candidata no contiene validacion_v2.")
    v1 = bloque.get("revision_1")
    v2 = bloque.get("revision_2")
    aud = bloque.get("auditoria_ciega")
    if not all(isinstance(x, dict) for x in (v1, v2, aud)):
        raise RuntimeError("validacion_v2 está incompleta.")
    if not revision_ok(v1) or not revision_ok(v2):
        raise RuntimeError("La candidata no supera las dos revisiones V2.")
    if not auditoria_ok(aud, str(candidata.get("respuesta_correcta") or "")):
        raise RuntimeError("La candidata no supera la auditoría ciega V2.")


def validar_candidata(
    con: sqlite3.Connection,
    candidata: dict[str, Any],
    temario_id: int,
) -> dict[str, Any]:
    if candidata.get("tipo_registro") != "CANDIDATA_PREGUNTA_V2":
        raise RuntimeError("El registro no es CANDIDATA_PREGUNTA_V2.")
    if str(candidata.get("estado") or "") != ESTADO_PUBLICABLE:
        raise RuntimeError(
            "Solo se publican candidatas en estado VALIDADA_AUDITADA_V2."
        )
    if str(candidata.get("tipo_contexto") or "").upper() != "PSEUDO":
        raise RuntimeError("Esta fase solo publica PSEUDO; MIXTA no se infiere.")

    obligatorios = (
        "tema_id", "pseudoarticulo_id", "pseudonorma", "pseudoarticulo",
        "enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d",
        "respuesta_correcta", "explicacion", "respaldo_ids", "validacion_v2",
    )
    for campo in obligatorios:
        if candidata.get(campo) in (None, "", []):
            raise RuntimeError(f"Candidata incompleta: {campo}")
    correcta = str(candidata["respuesta_correcta"]).strip().upper()
    if correcta not in {"A", "B", "C", "D"}:
        raise RuntimeError("respuesta_correcta debe ser A, B, C o D.")
    validar_auditoria_embebida(candidata)

    tema_id = int(candidata["tema_id"])
    pseudoarticulo_id = int(candidata["pseudoarticulo_id"])
    fila = con.execute(
        """
        SELECT tt.temario_id, pn.codigo AS pseudonorma, pn.estado AS pn_estado,
               pa.codigo AS pseudoarticulo, pa.estado AS pa_estado, pn.tema_id
        FROM temario_pseudoarticulos pa
        JOIN temario_pseudonormas pn ON pn.id=pa.pseudonorma_id
        JOIN temario_temas tt ON tt.id=pn.tema_id
        WHERE pa.id=?
        """,
        (pseudoarticulo_id,),
    ).fetchone()
    if fila is None:
        raise RuntimeError(f"No existe pseudoarticulo_id={pseudoarticulo_id}.")
    if int(fila["temario_id"]) != temario_id or int(fila["tema_id"]) != tema_id:
        raise RuntimeError("El pseudoartículo no pertenece al tema/temario de la convocatoria.")
    if str(fila["pseudonorma"]) != str(candidata["pseudonorma"]):
        raise RuntimeError("La identidad de pseudonorma no coincide con la persistida.")
    if str(fila["pseudoarticulo"]) != str(candidata["pseudoarticulo"]):
        raise RuntimeError("La identidad de pseudoartículo no coincide con la persistida.")
    if str(fila["pn_estado"] or "").strip() not in ESTADOS_FINALES:
        raise RuntimeError("La pseudonorma no está en estado final aceptado.")
    if str(fila["pa_estado"] or "").strip() not in ESTADOS_FINALES:
        raise RuntimeError("El pseudoartículo no está en estado final aceptado.")

    ids = candidata["respaldo_ids"]
    if not isinstance(ids, list) or not ids:
        raise RuntimeError("respaldo_ids debe ser una lista no vacía.")
    try:
        respaldo_ids = [int(x) for x in ids]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("respaldo_ids contiene valores inválidos.") from exc
    if len(respaldo_ids) != len(set(respaldo_ids)):
        raise RuntimeError("respaldo_ids contiene duplicados.")

    marcas = ",".join("?" for _ in respaldo_ids)
    filas = con.execute(
        f"""
        SELECT id,pseudoarticulo_id,pertinencia,estado
        FROM temario_pseudoarticulo_respaldos
        WHERE id IN ({marcas})
        """,
        tuple(respaldo_ids),
    ).fetchall()
    por_id = {int(r["id"]): r for r in filas}
    faltan = [x for x in respaldo_ids if x not in por_id]
    if faltan:
        raise RuntimeError(f"No existen respaldos citados: {faltan}")
    invalidos = [
        rid for rid in respaldo_ids
        if int(por_id[rid]["pseudoarticulo_id"]) != pseudoarticulo_id
        or str(por_id[rid]["pertinencia"] or "").upper() != "PERTINENTE"
        or str(por_id[rid]["estado"] or "").upper() != "VERIFICADO"
    ]
    if invalidos:
        raise RuntimeError(
            "La candidata cita respaldos ajenos/no verificados: " + str(invalidos)
        )

    return {
        **candidata,
        "tema_id": tema_id,
        "pseudoarticulo_id": pseudoarticulo_id,
        "respuesta_correcta": correcta,
        "respaldo_ids": respaldo_ids,
    }


def buscar_existente(
    con: sqlite3.Connection,
    candidata: dict[str, Any],
) -> tuple[str, int | None]:
    objetivo = clave_pregunta(candidata)
    coincidencias: list[int] = []
    for fila in con.execute(
        "SELECT id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d FROM lote_preguntas"
    ):
        if clave_pregunta(fila) == objetivo:
            coincidencias.append(int(fila["id"]))
    if not coincidencias:
        return "NUEVA", None
    if len(coincidencias) != 1:
        raise RuntimeError(
            f"La candidata coincide con {len(coincidencias)} preguntas existentes."
        )

    pregunta_id = coincidencias[0]
    contexto = con.execute(
        """
        SELECT tipo_contexto,tema_id,pseudoarticulo_id,temario_referencia_id
        FROM pregunta_contextos_v2
        WHERE pregunta_id=?
        """,
        (pregunta_id,),
    ).fetchall()
    if len(contexto) == 1:
        c = contexto[0]
        if (
            str(c["tipo_contexto"]).upper() == "PSEUDO"
            and int(c["tema_id"]) == int(candidata["tema_id"])
            and int(c["pseudoarticulo_id"]) == int(candidata["pseudoarticulo_id"])
            and c["temario_referencia_id"] is None
        ):
            return "YA_EXISTENTE", pregunta_id
    raise RuntimeError(
        f"Colisión: lote_preguntas.id={pregunta_id} tiene el mismo texto pero no el contexto PSEUDO esperado."
    )


def insertar(
    con: sqlite3.Connection,
    candidata: dict[str, Any],
    origen_oposicion: str,
) -> int:
    cur = con.execute(
        """
        INSERT INTO lote_preguntas(
            enunciado,opcion_a,opcion_b,opcion_c,opcion_d,respuesta_correcta,
            tipo_clasificacion,origen_oposicion,tipo_fuente,teorica_practica,
            criterio_inclusion_banco
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidata["enunciado"], candidata["opcion_a"], candidata["opcion_b"],
            candidata["opcion_c"], candidata["opcion_d"], candidata["respuesta_correcta"],
            "JURIDICA", origen_oposicion, TIPO_FUENTE, "TEORICA", CRITERIO,
        ),
    )
    pregunta_id = int(cur.lastrowid)
    con.execute(
        """
        INSERT INTO pregunta_contextos_v2(
            pregunta_id,tema_id,tipo_contexto,pseudoarticulo_id,temario_referencia_id
        ) VALUES(?,?, 'PSEUDO', ?, NULL)
        """,
        (pregunta_id, int(candidata["tema_id"]), int(candidata["pseudoarticulo_id"])),
    )
    return pregunta_id


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Publica candidatas PSEUDO V2 auditadas sin intervenir en los bancos."
    )
    ap.add_argument("--candidatas", required=True)
    ap.add_argument("--codigo", required=True)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    candidatas_path = Path(args.candidatas).expanduser().resolve()
    if not db.is_file():
        raise RuntimeError(f"No existe la BD: {db}")
    if not candidatas_path.is_file():
        raise RuntimeError(f"No existe el fichero de candidatas: {candidatas_path}")
    candidatas = leer_jsonl(candidatas_path)
    if not candidatas:
        raise RuntimeError("No hay candidatas para revisar.")

    backup: Path | None = None
    nuevas: list[dict[str, Any]] = []
    existentes: list[tuple[dict[str, Any], int]] = []

    con = sqlite3.connect(db)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        comprobar_tablas(con)
        _, temario_id = seleccionar_convocatoria(con, args.codigo)
        origen = nivel_desde_codigo(args.codigo)

        for candidata_original in candidatas:
            candidata = validar_candidata(con, candidata_original, temario_id)
            estado, pregunta_id = buscar_existente(con, candidata)
            if estado == "YA_EXISTENTE":
                existentes.append((candidata, int(pregunta_id)))
            else:
                nuevas.append(candidata)

        if args.aplicar and nuevas:
            marca = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = db.with_name(
                f"{db.stem}_backup_antes_publicar_candidatas_v2_{marca}{db.suffix}"
            )
            shutil.copy2(db, backup)
            try:
                con.execute("BEGIN IMMEDIATE")
                for candidata in nuevas:
                    insertar(con, candidata, origen)
                fk = con.execute("PRAGMA foreign_key_check").fetchall()
                if fk:
                    raise RuntimeError(f"foreign_key_check: {len(fk)} incidencia(s)")
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("integrity_check no devuelve ok")
                con.commit()
            except Exception:
                con.rollback()
                con.close()
                shutil.copy2(backup, db)
                raise
    finally:
        try:
            con.close()
        except Exception:
            pass

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PUBLICACION CANDIDATAS PSEUDO")
    print("=" * 78)
    print(f"Convocatoria..................... {args.codigo}")
    print(f"Modo............................. {'APLICAR' if args.aplicar else 'SOLO REVISION'}")
    print(f"Candidatas....................... {len(candidatas)}")
    print(f"Nuevas........................... {len(nuevas)}")
    print(f"Ya existentes.................... {len(existentes)}")
    print("Estado exigido................... VALIDADA_AUDITADA_V2")
    print("Tipo contexto.................... PSEUDO")
    print("Tipo pregunta.................... TEORICA")
    print("Criterio banco................... PSEUDOARTICULO_V2")
    if backup:
        print(f"Backup........................... {backup}")
    print("Banco............................ SIN CAMBIOS")
    if not args.aplicar:
        print("BD............................... SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
