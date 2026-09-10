"""
Constructor Temario V2 - cargar un manifiesto V2 aprobado en persistencia.

Por defecto SOLO REVISION: valida y muestra qué escribiría. Con --aplicar escribe
únicamente en las tablas V2 aditivas. No modifica temarios, temario_temas,
temario_referencias, normas, articulos_fuente, bancos ni corpus existente.

Si el tema aporta `pseudonorma_codigo`, esa identidad estable se conserva. Para
manifiestos anteriores que no tengan ese campo se mantiene el identificador legado
`PSEUDO:{PARTE}_{TEMA}`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
TABLAS_V2 = {
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "fuentes_conocimiento",
    "temario_pseudoarticulo_respaldos",
}


def backup(path: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = path.with_name(f"{path.stem}_antes_carga_v2_{marca}{path.suffix}")
    shutil.copy2(path, destino)
    return destino


def cargar_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("naturaleza") != "MANIFIESTO_CONSTRUCTOR_TEMARIO_V2":
        raise RuntimeError("El JSON no es MANIFIESTO_CONSTRUCTOR_TEMARIO_V2.")
    reglas = data.get("reglas") or {}
    for k in ("modifica_bd", "modifica_corpus", "modifica_temario_csv", "modifica_bancos"):
        if reglas.get(k) is not False:
            raise RuntimeError(f"Regla de seguridad inválida en manifiesto: {k}")
    return data


def comprobar_esquema(con: sqlite3.Connection) -> None:
    existentes = {
        str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    faltan = sorted(TABLAS_V2 - existentes)
    if faltan:
        raise RuntimeError("Falta ejecutar migración V2. Tablas ausentes: " + ", ".join(faltan))


def indice_temas(con: sqlite3.Connection, convocatoria_codigo: str) -> dict[tuple[str,int], int]:
    filas = con.execute(
        """
        SELECT tt.id, tt.parte, tt.numero_tema
        FROM temario_temas tt
        JOIN temarios t ON t.id=tt.temario_id
        JOIN convocatorias c ON c.id=t.convocatoria_id
        WHERE c.codigo=?
        """,
        (convocatoria_codigo,),
    ).fetchall()
    return {(str(r[1]).upper(), int(r[2])): int(r[0]) for r in filas}


def planificar(data: dict, con: sqlite3.Connection, convocatoria: str) -> list[dict]:
    idx = indice_temas(con, convocatoria)
    operaciones = []
    for item in data.get("temas") or []:
        pseudo = item.get("pseudoarticulos") or []
        if not pseudo:
            continue
        clave = (str(item.get("parte") or "").upper(), int(item.get("numero_tema")))
        tema_id = idx.get(clave)
        if tema_id is None:
            raise RuntimeError(f"No existe temario_temas para {clave[0]} {clave[1]} en {convocatoria}.")
        operaciones.append({"tema_id": tema_id, "item": item})
    return operaciones


def codigo_pseudonorma(item: dict) -> str:
    explicito = str(item.get("pseudonorma_codigo") or "").strip()
    if explicito:
        if not explicito.startswith("PSEUDO:"):
            raise RuntimeError(f"pseudonorma_codigo inválido: {explicito}")
        return explicito
    return f"PSEUDO:{str(item.get('parte')).upper()}_{int(item.get('numero_tema')):02d}"


def upsert_pseudonorma(con, tema_id: int, codigo: str, estado: str) -> int:
    con.execute(
        """
        INSERT INTO temario_pseudonormas(tema_id,codigo,estado)
        VALUES(?,?,?)
        ON CONFLICT(tema_id,codigo) DO UPDATE SET estado=excluded.estado, updated_at=CURRENT_TIMESTAMP
        """, (tema_id,codigo,estado)
    )
    return int(con.execute(
        "SELECT id FROM temario_pseudonormas WHERE tema_id=? AND codigo=?",
        (tema_id,codigo)
    ).fetchone()[0])


def upsert_pseudoarticulo(con, pid: int, p: dict, orden: int) -> int:
    con.execute(
        """
        INSERT INTO temario_pseudoarticulos
        (pseudonorma_id,codigo,orden,texto_temario,tipo_contenido,sintesis,estado)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(pseudonorma_id,codigo) DO UPDATE SET
          orden=excluded.orden,texto_temario=excluded.texto_temario,
          tipo_contenido=excluded.tipo_contenido,sintesis=excluded.sintesis,
          estado=excluded.estado,updated_at=CURRENT_TIMESTAMP
        """,
        (pid,str(p.get("codigo")),orden,str(p.get("texto_temario") or ""),
         str(p.get("tipo_contenido") or "MIXTO"),p.get("sintesis"),str(p.get("estado") or "PENDIENTE"))
    )
    return int(con.execute(
        "SELECT id FROM temario_pseudoarticulos WHERE pseudonorma_id=? AND codigo=?",
        (pid,str(p.get("codigo")))
    ).fetchone()[0])


def upsert_respaldo_local(con, pseudoarticulo_id: int, articulo_fuente_id: int, motivo: str) -> None:
    """Idempotencia sin alterar el esquema: una propuesta local por pseudoartículo+artículo."""
    fila = con.execute(
        """
        SELECT id
        FROM temario_pseudoarticulo_respaldos
        WHERE pseudoarticulo_id=?
          AND proposicion_id IS NULL
          AND articulo_fuente_id=?
          AND fuente_conocimiento_id IS NULL
        """,
        (pseudoarticulo_id, articulo_fuente_id),
    ).fetchone()
    if fila is None:
        con.execute(
            """
            INSERT INTO temario_pseudoarticulo_respaldos
            (pseudoarticulo_id,proposicion_id,articulo_fuente_id,fuente_conocimiento_id,
             pertinencia,estado,motivo,evidencia_texto,evidencia_localizador)
            VALUES(?,NULL,?,NULL,?,?,?,?,?)
            """,
            (
                pseudoarticulo_id,
                articulo_fuente_id,
                "PROPUESTA",
                "PENDIENTE_VERIFICACION",
                motivo,
                None,
                None,
            ),
        )
    else:
        con.execute(
            """
            UPDATE temario_pseudoarticulo_respaldos
            SET pertinencia='PROPUESTA',
                estado='PENDIENTE_VERIFICACION',
                motivo=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (motivo, int(fila[0])),
        )


def cargar_operacion(con, op: dict) -> None:
    item = op["item"]
    tema_id = op["tema_id"]
    codigo_pn = codigo_pseudonorma(item)
    pid = upsert_pseudonorma(con, tema_id, codigo_pn, str(item.get("estado_v2") or "PENDIENTE"))

    for orden, p in enumerate(item.get("pseudoarticulos") or [], 1):
        paid = upsert_pseudoarticulo(con, pid, p, orden)
        for prop in p.get("proposiciones") or []:
            codigo = str(prop.get("codigo") or "")
            con.execute(
                """
                INSERT INTO temario_pseudoarticulo_proposiciones
                (pseudoarticulo_id,codigo,texto,esencial,tipo,estado)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(pseudoarticulo_id,codigo) DO UPDATE SET
                  texto=excluded.texto,esencial=excluded.esencial,tipo=excluded.tipo,estado=excluded.estado
                """,
                (paid,codigo,str(prop.get("texto") or ""),1 if prop.get("esencial") else 0,
                 str(prop.get("tipo") or "MIXTO"),str(prop.get("estado") or "PROPUESTA"))
            )

        for c in p.get("candidatos_locales_prioritarios") or []:
            aid = int(c["articulo_fuente_id"])
            existe = con.execute("SELECT 1 FROM articulos_fuente WHERE id=?", (aid,)).fetchone()
            if not existe:
                raise RuntimeError(f"articulo_fuente_id inexistente: {aid}")
            upsert_respaldo_local(con, paid, aid, str(c.get("motivo") or ""))

        for f in p.get("fuentes") or []:
            if not isinstance(f, dict):
                continue
            if not str(f.get("nombre") or "").strip():
                continue
            identificador = f.get("identificador_oficial")
            organismo = str(f.get("organismo") or "")
            nombre = str(f.get("nombre"))
            tipo = str(f.get("tipo_fuente") or "FUENTE_INSTITUCIONAL")

            if identificador is None:
                existente = con.execute(
                    """
                    SELECT id FROM fuentes_conocimiento
                    WHERE tipo_fuente=? AND organismo=? AND nombre=?
                      AND identificador_oficial IS NULL
                    """,
                    (tipo, organismo, nombre),
                ).fetchone()
                if existente is None:
                    con.execute(
                        """
                        INSERT INTO fuentes_conocimiento
                        (tipo_fuente,organismo,nombre,identificador_oficial,localizador_oficial,estado_verificacion)
                        VALUES(?,?,?,NULL,?,?)
                        """,
                        (tipo, organismo, nombre, f.get("localizador_oficial"),
                         str(f.get("estado_verificacion") or "PENDIENTE")),
                    )
                else:
                    con.execute(
                        """
                        UPDATE fuentes_conocimiento
                        SET localizador_oficial=?, estado_verificacion=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (f.get("localizador_oficial"), str(f.get("estado_verificacion") or "PENDIENTE"), int(existente[0])),
                    )
            else:
                con.execute(
                    """
                    INSERT INTO fuentes_conocimiento
                    (tipo_fuente,organismo,nombre,identificador_oficial,localizador_oficial,estado_verificacion)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(tipo_fuente,organismo,nombre,identificador_oficial) DO UPDATE SET
                      localizador_oficial=excluded.localizador_oficial,
                      estado_verificacion=excluded.estado_verificacion,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (tipo, organismo, nombre, identificador, f.get("localizador_oficial"),
                     str(f.get("estado_verificacion") or "PENDIENTE")),
                )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifiesto", required=True)
    ap.add_argument("--convocatoria", required=True)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    manifiesto = Path(args.manifiesto).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    data = cargar_json(manifiesto)

    with sqlite3.connect(db) as con:
        comprobar_esquema(con)
        ops = planificar(data, con, args.convocatoria)

    print("="*78)
    print("CONSTRUCTOR TEMARIO V2 - CARGA PERSISTENCIA")
    print("="*78)
    print(f"BD............................... {db}")
    print(f"Manifiesto....................... {manifiesto}")
    print(f"Convocatoria..................... {args.convocatoria}")
    print(f"Modo............................. {'APLICAR' if args.aplicar else 'SOLO REVISION'}")
    print(f"Temas con pseudonorma............ {len(ops)}")
    print(f"Pseudoartículos.................. {sum(len(x['item'].get('pseudoarticulos') or []) for x in ops)}")
    for op in ops:
        print(f"  {op['item'].get('parte')} {op['item'].get('numero_tema')}: {codigo_pseudonorma(op['item'])}")

    if not args.aplicar:
        print("No se ha modificado la base de datos.")
        return 0

    copia = backup(db)
    try:
        with sqlite3.connect(db) as con:
            con.execute("PRAGMA foreign_keys=ON")
            comprobar_esquema(con)
            con.execute("BEGIN IMMEDIATE")
            for op in ops:
                cargar_operacion(con, op)
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(f"foreign_key_check: {len(fk)} incidencias")
            con.commit()
    except Exception:
        shutil.copy2(copia, db)
        raise

    print(f"Backup........................... {copia}")
    print("Carga V2......................... OK")
    print("No se ha modificado temario_referencias ni ninguna tabla consolidada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
