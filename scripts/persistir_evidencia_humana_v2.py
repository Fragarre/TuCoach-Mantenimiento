"""
Constructor Temario V2 - persistencia de evidencia humana ya aceptada.

Por defecto SOLO REVISION. Con --aplicar escribe únicamente en tablas V2:
- fuentes_conocimiento
- temario_pseudoarticulo_respaldos

NO decide pertinencia, NO modifica tablas consolidadas, corpus, temario.csv ni bancos.
Exige que la pseudonorma persistida coincida exactamente con la identidad aprobada.
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
TABLAS = {
    "temario_pseudonormas",
    "temario_pseudoarticulos",
    "temario_pseudoarticulo_proposiciones",
    "fuentes_conocimiento",
    "temario_pseudoarticulo_respaldos",
}


def cargar(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def backup(path: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = path.with_name(f"{path.stem}_antes_evidencia_v2_{marca}{path.suffix}")
    shutil.copy2(path, destino)
    return destino


def comprobar_esquema(con: sqlite3.Connection) -> None:
    existentes = {str(r[0]) for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    faltan = sorted(TABLAS - existentes)
    if faltan:
        raise RuntimeError("Faltan tablas V2: " + ", ".join(faltan))


def validar_artefactos(rec: dict, decisiones: dict, evidencia: dict) -> None:
    if rec.get("naturaleza") != "RECUPERACION_FUENTES_EXTERNAS_V2":
        raise RuntimeError("recuperacion-json no es RECUPERACION_FUENTES_EXTERNAS_V2")
    if rec.get("estado") != "LISTO_PARA_REVISION_CONTENIDO":
        raise RuntimeError("La recuperación no está LISTO_PARA_REVISION_CONTENIDO")
    if decisiones.get("naturaleza") != "DECISIONES_HUMANAS_PERTINENCIA_V2":
        raise RuntimeError("decisiones-json no es DECISIONES_HUMANAS_PERTINENCIA_V2")
    if decisiones.get("estado") != "REVISION_HUMANA_COMPLETADA":
        raise RuntimeError("Las decisiones humanas no están completadas")
    if evidencia.get("naturaleza") != "EVIDENCIA_HUMANA_PERTINENCIA_V2":
        raise RuntimeError("evidencia-json no es EVIDENCIA_HUMANA_PERTINENCIA_V2")
    if evidencia.get("estado") != "EVIDENCIA_HUMANA_MATERIALIZADA":
        raise RuntimeError("La evidencia humana no está materializada")

    claves = ("caso", "pseudonorma", "unidad")
    for k in claves:
        valores = {str(x.get(k) or "") for x in (rec, decisiones, evidencia)}
        if len(valores) != 1 or "" in valores:
            raise RuntimeError(f"Artefactos incompatibles en {k}: {sorted(valores)}")


def indice_recuperacion(rec: dict) -> dict[str, dict]:
    salida = {}
    for r in rec.get("resultados") or []:
        ident = str(r.get("identificador_oficial") or "")
        if not ident:
            raise RuntimeError("Fuente recuperada sin identificador_oficial")
        if ident in salida:
            raise RuntimeError(f"Fuente recuperada duplicada: {ident}")
        if r.get("estado_identidad") != "IDENTIDAD_VERIFICADA":
            raise RuntimeError(f"Identidad no verificada: {ident}")
        if r.get("estado_recuperacion") != "RECUPERADA_PARA_REVISION":
            raise RuntimeError(f"Fuente no recuperada para revisión: {ident}")
        salida[ident] = r
    return salida


def indice_decisiones(decisiones: dict) -> dict[tuple[str, str], dict]:
    salida = {}
    for d in decisiones.get("decisiones") or []:
        k = (str(d.get("codigo_proposicion") or ""), str(d.get("identificador_oficial") or ""))
        if not all(k):
            raise RuntimeError("Decisión humana incompleta")
        if k in salida:
            raise RuntimeError(f"Decisión humana duplicada: {k}")
        salida[k] = d
    return salida


def relaciones_pertinentes(evidencia: dict, decisiones: dict, rec: dict) -> list[dict]:
    rec_idx = indice_recuperacion(rec)
    dec_idx = indice_decisiones(decisiones)
    salida = []
    for rel in evidencia.get("relaciones") or []:
        codigo = str(rel.get("codigo_proposicion") or "")
        ident = str(rel.get("identificador_oficial") or "")
        k = (codigo, ident)
        dec = dec_idx.get(k)
        if dec is None:
            raise RuntimeError(f"Evidencia sin decisión humana correspondiente: {k}")
        decision = str(dec.get("decision") or "").upper()
        if decision != str(rel.get("decision_humana") or "").upper():
            raise RuntimeError(f"Decisión incoherente entre artefactos: {k}")
        if decision != "PERTINENTE":
            continue
        fragmentos = rel.get("evidencia_aceptada") or []
        if not fragmentos:
            raise RuntimeError(f"Relación PERTINENTE sin evidencia: {k}")
        fuente = rec_idx.get(ident)
        if fuente is None:
            raise RuntimeError(f"Fuente pertinente no aparece en recuperación: {ident}")
        salida.append({
            "codigo_proposicion": codigo,
            "identificador_oficial": ident,
            "tipo_fuente": str(rel.get("tipo_fuente") or fuente.get("tipo_fuente") or "FUENTE_INSTITUCIONAL"),
            "fundamento": dec.get("fundamento") or rel.get("fundamento_humano"),
            "fragmentos": fragmentos,
            "fuente": fuente,
        })
    return salida


def resolver_pseudonorma(con: sqlite3.Connection, convocatoria: str, codigo: str) -> int:
    filas = con.execute(
        """
        SELECT pn.id
        FROM temario_pseudonormas pn
        JOIN temario_temas tt ON tt.id=pn.tema_id
        JOIN temarios t ON t.id=tt.temario_id
        JOIN convocatorias c ON c.id=t.convocatoria_id
        WHERE c.codigo=? AND pn.codigo=?
        """,
        (convocatoria, codigo),
    ).fetchall()
    if len(filas) != 1:
        raise RuntimeError(
            f"Identidad pseudonormativa no resuelta exactamente: {codigo} en {convocatoria}; coincidencias={len(filas)}"
        )
    return int(filas[0][0])


def indice_proposiciones(con: sqlite3.Connection, pseudonorma_id: int) -> dict[str, tuple[int, int]]:
    filas = con.execute(
        """
        SELECT p.codigo, p.id, pa.id
        FROM temario_pseudoarticulo_proposiciones p
        JOIN temario_pseudoarticulos pa ON pa.id=p.pseudoarticulo_id
        WHERE pa.pseudonorma_id=?
        """,
        (pseudonorma_id,),
    ).fetchall()
    salida: dict[str, tuple[int, int]] = {}
    for codigo, prop_id, pa_id in filas:
        c = str(codigo)
        if c in salida:
            raise RuntimeError(f"Código de proposición ambiguo dentro de pseudonorma: {c}")
        salida[c] = (int(prop_id), int(pa_id))
    return salida


def upsert_fuente(con: sqlite3.Connection, rel: dict) -> int:
    f = rel["fuente"]
    ident = rel["identificador_oficial"]
    tipo = rel["tipo_fuente"]
    organismo = str(f.get("organismo") or "")
    nombre = str(f.get("descripcion") or ident)
    localizador = str(
        f.get("url_documento_verificada")
        or f.get("url_oficial")
        or f.get("url_final")
        or ""
    )
    fila = con.execute(
        """
        SELECT id FROM fuentes_conocimiento
        WHERE tipo_fuente=? AND organismo=? AND nombre=? AND identificador_oficial=?
        """,
        (tipo, organismo, nombre, ident),
    ).fetchone()
    if fila is None:
        con.execute(
            """
            INSERT INTO fuentes_conocimiento
            (tipo_fuente,organismo,nombre,identificador_oficial,localizador_oficial,estado_verificacion)
            VALUES(?,?,?,?,?,'VERIFICADO')
            """,
            (tipo, organismo, nombre, ident, localizador),
        )
        fila = con.execute("SELECT last_insert_rowid()").fetchone()
    else:
        con.execute(
            """
            UPDATE fuentes_conocimiento
            SET localizador_oficial=?, estado_verificacion='VERIFICADO', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (localizador, int(fila[0])),
        )
    return int(fila[0])


def serializar_evidencia(fragmentos: list[dict]) -> tuple[str, str]:
    textos = [str(x.get("fragmento") or "") for x in fragmentos]
    localizadores = [
        {
            "ancla": x.get("ancla"),
            "tipo_localizador": x.get("tipo_localizador"),
            "inicio": x.get("inicio"),
            "fin": x.get("fin"),
            "anclas_presentes": x.get("anclas_presentes") or [],
        }
        for x in fragmentos
    ]
    return (
        json.dumps(textos, ensure_ascii=False, separators=(",", ":")),
        json.dumps(localizadores, ensure_ascii=False, separators=(",", ":")),
    )


def upsert_respaldo(
    con: sqlite3.Connection,
    pseudoarticulo_id: int,
    proposicion_id: int,
    fuente_id: int,
    fundamento: str | None,
    fragmentos: list[dict],
) -> None:
    evidencia_texto, evidencia_localizador = serializar_evidencia(fragmentos)
    fila = con.execute(
        """
        SELECT id FROM temario_pseudoarticulo_respaldos
        WHERE pseudoarticulo_id=? AND proposicion_id=?
          AND articulo_fuente_id IS NULL AND fuente_conocimiento_id=?
        """,
        (pseudoarticulo_id, proposicion_id, fuente_id),
    ).fetchone()
    if fila is None:
        con.execute(
            """
            INSERT INTO temario_pseudoarticulo_respaldos
            (pseudoarticulo_id,proposicion_id,articulo_fuente_id,fuente_conocimiento_id,
             pertinencia,estado,motivo,evidencia_texto,evidencia_localizador)
            VALUES(?,?,NULL,?,'PERTINENTE','VERIFICADO',?,?,?)
            """,
            (pseudoarticulo_id, proposicion_id, fuente_id, fundamento, evidencia_texto, evidencia_localizador),
        )
    else:
        con.execute(
            """
            UPDATE temario_pseudoarticulo_respaldos
            SET pertinencia='PERTINENTE', estado='VERIFICADO', motivo=?,
                evidencia_texto=?, evidencia_localizador=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (fundamento, evidencia_texto, evidencia_localizador, int(fila[0])),
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recuperacion-json", required=True)
    ap.add_argument("--decisiones-json", required=True)
    ap.add_argument("--evidencia-json", required=True)
    ap.add_argument("--convocatoria", required=True)
    ap.add_argument("--db", default=str(DB_DEFECTO))
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    paths = [Path(x).expanduser().resolve() for x in (
        args.recuperacion_json, args.decisiones_json, args.evidencia_json
    )]
    rec, decisiones, evidencia = [cargar(p) for p in paths]
    validar_artefactos(rec, decisiones, evidencia)
    relaciones = relaciones_pertinentes(evidencia, decisiones, rec)
    codigo_pn = str(evidencia["pseudonorma"])
    db = Path(args.db).expanduser().resolve()

    with sqlite3.connect(db) as con:
        con.execute("PRAGMA foreign_keys=ON")
        comprobar_esquema(con)
        pn_id = resolver_pseudonorma(con, args.convocatoria, codigo_pn)
        props = indice_proposiciones(con, pn_id)
        faltan = sorted({r["codigo_proposicion"] for r in relaciones} - set(props))
        if faltan:
            raise RuntimeError("Proposiciones no persistidas: " + ", ".join(faltan))

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - PERSISTENCIA EVIDENCIA HUMANA")
    print("=" * 78)
    print(f"Convocatoria..................... {args.convocatoria}")
    print(f"Pseudonorma...................... {codigo_pn}")
    print(f"Relaciones PERTINENTE............ {len(relaciones)}")
    print(f"Modo............................. {'APLICAR' if args.aplicar else 'SOLO REVISION'}")

    if not args.aplicar:
        print("Validación previa................ OK")
        print("No se ha modificado la base de datos.")
        return 0

    copia = backup(db)
    try:
        with sqlite3.connect(db) as con:
            con.execute("PRAGMA foreign_keys=ON")
            comprobar_esquema(con)
            con.execute("BEGIN IMMEDIATE")
            pn_id = resolver_pseudonorma(con, args.convocatoria, codigo_pn)
            props = indice_proposiciones(con, pn_id)
            for rel in relaciones:
                prop_id, pa_id = props[rel["codigo_proposicion"]]
                fuente_id = upsert_fuente(con, rel)
                upsert_respaldo(
                    con, pa_id, prop_id, fuente_id,
                    rel.get("fundamento"), rel["fragmentos"],
                )
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(f"foreign_key_check: {len(fk)} incidencias")
            con.commit()
    except Exception:
        shutil.copy2(copia, db)
        raise

    print(f"Backup........................... {copia}")
    print("Persistencia evidencia........... OK")
    print("Tablas consolidadas.............. SIN CAMBIOS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
