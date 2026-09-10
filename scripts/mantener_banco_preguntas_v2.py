"""
Vinculación aditiva de preguntas PSEUDO/MIXTA del Constructor Temario V2.

No sustituye mantener_banco_preguntas.py. Las preguntas REAL y NO_JURIDICA
siguen tratándose exclusivamente por el constructor consolidado.

Por defecto SOLO REVISION. Con --guardar crea backup y añade únicamente
banco_preguntas + banco_preguntas_temas para contextos V2 válidos.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from mantener_banco_preguntas import resolver_parte_convocatoria

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
AUDITORIA = RAIZ / "auditorias" / "mantener_banco_v2"

CRITERIOS = {
    "PSEUDO": "PSEUDOARTICULO_V2",
    "MIXTA": "MIXTO_V2",
}
METODOS = dict(CRITERIOS)


def existe_tabla(con: sqlite3.Connection, nombre: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nombre,),
    ).fetchone() is not None


def buscar_convocatoria(con: sqlite3.Connection, codigo: str | None, cid: int | None) -> sqlite3.Row:
    if cid is not None:
        fila = con.execute("SELECT * FROM convocatorias WHERE id=?", (cid,)).fetchone()
    else:
        fila = con.execute("SELECT * FROM convocatorias WHERE codigo=?", ((codigo or "").strip(),)).fetchone()
    if fila is None:
        raise RuntimeError("La convocatoria indicada no existe.")
    return fila


def buscar_temario(con: sqlite3.Connection, convocatoria_id: int) -> sqlite3.Row:
    filas = con.execute(
        "SELECT id,nombre FROM temarios WHERE convocatoria_id=? ORDER BY id",
        (convocatoria_id,),
    ).fetchall()
    if len(filas) != 1:
        raise RuntimeError(
            "La convocatoria debe tener exactamente un temario. "
            f"Encontrados: {len(filas)}."
        )
    return filas[0]


def cargar_contextos(con: sqlite3.Connection, temario_id: int, convocatoria_id: int) -> dict[str, list[dict[str, Any]]]:
    resultado: dict[str, list[dict[str, Any]]] = {
        "nuevas": [],
        "ya_existentes": [],
        "invalidas": [],
    }
    if not existe_tabla(con, "pregunta_contextos_v2"):
        return resultado

    filas = con.execute(
        """
        SELECT
            pc.id AS contexto_id,
            pc.pregunta_id,
            pc.tema_id,
            pc.tipo_contexto,
            pc.pseudoarticulo_id,
            pc.temario_referencia_id,
            lp.tipo_clasificacion,
            lp.criterio_inclusion_banco,
            lp.norma_id_normalizada,
            lp.articulo_normalizado,
            lp.tipo_norma_normalizado,
            lp.nombre_norma_normalizado,
            lp.teorica_practica,
            lp.tema_no_juridico,
            tt.parte,
            tt.numero_tema,
            tt.titulo,
            tt.tipo_contenido,
            bp.id AS banco_pregunta_id
        FROM pregunta_contextos_v2 pc
        JOIN lote_preguntas lp ON lp.id=pc.pregunta_id
        JOIN temario_temas tt ON tt.id=pc.tema_id
        LEFT JOIN banco_preguntas bp
          ON bp.convocatoria_id=? AND bp.pregunta_id=pc.pregunta_id
        WHERE tt.temario_id=?
          AND pc.tipo_contexto IN ('PSEUDO','MIXTA')
        ORDER BY pc.id
        """,
        (convocatoria_id, temario_id),
    ).fetchall()

    for sql in filas:
        f = dict(sql)
        errores: list[str] = []
        tipo = str(f["tipo_contexto"] or "").strip().upper()
        criterio = str(f["criterio_inclusion_banco"] or "").strip().upper()

        if str(f["tipo_clasificacion"] or "").strip().upper() != "JURIDICA":
            errores.append("TIPO_CLASIFICACION_NO_JURIDICA")
        if criterio != CRITERIOS[tipo]:
            errores.append(f"CRITERIO_ESPERADO_{CRITERIOS[tipo]}")

        # Barrera de compatibilidad: impide que el constructor legacy pueda
        # interpretar PSEUDO/MIXTA como una referencia norma-artículo REAL.
        if f["norma_id_normalizada"] is not None:
            errores.append("NORMA_ID_LEGACY_DEBE_SER_NULA")
        if str(f["articulo_normalizado"] or "").strip():
            errores.append("ARTICULO_LEGACY_DEBE_SER_VACIO")
        if str(f["tipo_norma_normalizado"] or "").strip():
            errores.append("TIPO_NORMA_LEGACY_DEBE_SER_VACIO")
        if str(f["nombre_norma_normalizado"] or "").strip():
            errores.append("NOMBRE_NORMA_LEGACY_DEBE_SER_VACIO")

        pseudo = con.execute(
            """
            SELECT tp.id,pn.tema_id,tp.estado
            FROM temario_pseudoarticulos tp
            JOIN temario_pseudonormas pn ON pn.id=tp.pseudonorma_id
            WHERE tp.id=?
            """,
            (int(f["pseudoarticulo_id"]),),
        ).fetchone()
        if pseudo is None or int(pseudo["tema_id"]) != int(f["tema_id"]):
            errores.append("PSEUDOARTICULO_NO_PERTENECE_AL_TEMA")

        if tipo == "MIXTA":
            ref = con.execute(
                "SELECT id,tema_id FROM temario_referencias WHERE id=?",
                (int(f["temario_referencia_id"]),),
            ).fetchone()
            if ref is None or int(ref["tema_id"]) != int(f["tema_id"]):
                errores.append("REFERENCIA_REAL_NO_PERTENECE_AL_TEMA")

        if errores:
            resultado["invalidas"].append({**f, "motivo": "|".join(errores)})
            continue

        if f["banco_pregunta_id"] is not None:
            resultado["ya_existentes"].append(f)
            continue

        resultado["nuevas"].append(
            {
                **f,
                "tipo_vinculacion_banco": "JURIDICA",
                "metodo_vinculacion": METODOS[tipo],
            }
        )

    return resultado


def preparar_partes(con: sqlite3.Connection, convocatoria_id: int, filas: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int]]:
    preparadas: list[tuple[dict[str, Any], int]] = []
    for f in filas:
        tema = {
            "parte": f["parte"],
            "tipo_contenido": f["tipo_contenido"],
        }
        parte_id, error = resolver_parte_convocatoria(con, convocatoria_id, f, tema)
        if error is not None or parte_id is None:
            raise RuntimeError(
                f"No se puede asignar parte a pregunta {f['pregunta_id']}: "
                f"{error or 'PARTE_NO_RESUELTA'}"
            )
        preparadas.append((f, int(parte_id)))
    return preparadas


def insertar(con: sqlite3.Connection, convocatoria_id: int, preparadas: list[tuple[dict[str, Any], int]]) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        for f, parte_id in preparadas:
            cur = con.execute(
                """
                INSERT INTO banco_preguntas(
                    convocatoria_id,pregunta_id,convocatoria_parte_id,
                    tipo_vinculacion,estado,metodo_vinculacion,motivo_revision
                ) VALUES(?,?,?,?,?,?,NULL)
                """,
                (
                    convocatoria_id,
                    int(f["pregunta_id"]),
                    parte_id,
                    "JURIDICA",
                    "INCLUIDA",
                    f["metodo_vinculacion"],
                ),
            )
            con.execute(
                """
                INSERT INTO banco_preguntas_temas(
                    banco_pregunta_id,tema_id,es_principal
                ) VALUES(?,?,1)
                """,
                (int(cur.lastrowid), int(f["tema_id"])),
            )

        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check: {len(fk)} incidencia(s)")
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity_check no devuelve ok")
        con.commit()
    except Exception:
        con.rollback()
        raise


def guardar_resumen(datos: dict[str, Any]) -> None:
    AUDITORIA.mkdir(parents=True, exist_ok=True)
    (AUDITORIA / "resumen.json").write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFECTO))
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--convocatoria-id", type=int)
    grupo.add_argument("--codigo")
    ap.add_argument("--guardar", action="store_true")
    args = ap.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        raise RuntimeError(f"No existe la BD: {db}")

    backup: Path | None = None
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        conv = buscar_convocatoria(con, args.codigo, args.convocatoria_id)
        cid = int(conv["id"])
        temario = buscar_temario(con, cid)
        tid = int(temario["id"])
        seleccion = cargar_contextos(con, tid, cid)
        preparadas = preparar_partes(con, cid, seleccion["nuevas"])

        if args.guardar and seleccion["invalidas"]:
            raise RuntimeError(
                f"Hay {len(seleccion['invalidas'])} contexto(s) V2 inválido(s). No se escribe."
            )

        if args.guardar and preparadas:
            marca = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = db.with_name(f"{db.stem}_backup_antes_banco_v2_{marca}{db.suffix}")
            shutil.copy2(db, backup)
            try:
                insertar(con, cid, preparadas)
            except Exception:
                con.close()
                shutil.copy2(backup, db)
                raise

    resumen = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "base_datos": str(db),
        "modo": "GUARDAR" if args.guardar else "SOLO_REVISION",
        "convocatoria_id": cid,
        "convocatoria_codigo": str(conv["codigo"]),
        "temario_id": tid,
        "v2_nuevas": len(seleccion["nuevas"]),
        "v2_ya_existentes": len(seleccion["ya_existentes"]),
        "v2_invalidas": len(seleccion["invalidas"]),
        "backup": str(backup) if backup else None,
    }
    guardar_resumen(resumen)

    print("=" * 78)
    print("MANTENIMIENTO BANCO V2 - PSEUDO/MIXTA")
    print("=" * 78)
    print(f"Convocatoria..................... {cid} | {conv['codigo']}")
    print(f"Temario.......................... {tid} | {temario['nombre']}")
    print(f"Modo............................. {'GUARDAR' if args.guardar else 'SOLO REVISION'}")
    print(f"V2 nuevas........................ {len(seleccion['nuevas'])}")
    print(f"V2 ya existentes................. {len(seleccion['ya_existentes'])}")
    print(f"V2 inválidas...................... {len(seleccion['invalidas'])}")
    if backup:
        print(f"Backup........................... {backup}")
    if not args.guardar:
        print("BD............................... SIN CAMBIOS")
    return 0 if not seleccion["invalidas"] else 2


if __name__ == "__main__":
    raise SystemExit(main())