"""Retira de los bancos las preguntas jurídicas generadas por IA con dificultad baja.

Criterio (el mismo que usa el filtro de dificultad de la web):
  - tipo_fuente = 'ia_generada', tipo_clasificacion = 'JURIDICA'
  - puntuación de pistas >= 20 (niveles 3 y 4 del filtro)

Protección: nunca se retira una pregunta si con ello alguna parte o algún bloque
NORMA de cualquier convocatoria se queda con menos de 8 veces las preguntas que
necesita. En ese caso se conservan las de menor puntuación de pistas.

No borra nada. Usa el mismo mecanismo que la cuarentena:
  - fila en preguntas_exclusiones con clasificacion='DIFICULTAD_BAJA', estado='RETIRADA'
  - vínculos de banco_preguntas -> estado 'REVISION', motivo 'DIFICULTAD_BAJA'
La primera vez amplía la restricción de preguntas_exclusiones para admitir
'DIFICULTAD_BAJA' (tabla local de Mantenimiento; no se publica en Supabase).

Uso (desde TuCoach-Mantenimiento):
    python scripts/retirar_preguntas_faciles.py              -> SIMULACIÓN (no cambia nada)
    python scripts/retirar_preguntas_faciles.py --aplicar    -> copia de seguridad + retirada
    python scripts/retirar_preguntas_faciles.py --deshacer   -> devuelve a los bancos todo lo retirado por este script
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN = "retirada_dificultad_pistas_v1"
MOTIVO_BANCO = "DIFICULTAD_BAJA"
MULTIPLICADOR = 8
UMBRAL_PISTAS = 20

# --- Puntuación de pistas (idéntica a backend/app/dificultad.py de TuCoach-Web) ---
_ABS = re.compile(
    r"\b(siempre|nunca|en ningún caso|en todo caso|exclusivamente|únicamente|solo|sólo|"
    r"cualquier|todos? los|jamás|sin excepción|con independencia)\b", re.I)
_TODAS = re.compile(
    r"todas (las )?(anteriores|opciones|respuestas)? ?(son )?correctas|todas las anteriores", re.I)


def _palabras(t):
    return {p for p in re.findall(r"\w+", (t or "").lower()) if len(p) > 5}


def puntuacion_pistas(enunciado, opciones, correcta):
    s, senales = 0, []
    largos = {k: len(v or "") for k, v in opciones.items()}
    orden = sorted(largos.values(), reverse=True)
    if largos[correcta] == orden[0] and orden[0] > orden[1]:
        s += 25; senales.append("correcta_mas_larga")
        if largos[correcta] >= 1.3 * orden[1]:
            s += 10; senales.append("mucho_mas_larga")
    if not _ABS.search(opciones[correcta] or ""):
        n = sum(1 for k, v in opciones.items() if k != correcta and _ABS.search(v or ""))
        if n:
            s += min(30, 12 * n); senales.append(f"absolutos_en_distractores:{n}")
    if _TODAS.search(opciones[correcta] or ""):
        s += 15; senales.append("todas_correctas")
    e = _palabras(enunciado)
    co = {k: len(e & _palabras(v)) for k, v in opciones.items()}
    if co[correcta] > 0 and co[correcta] == max(co.values()) and list(co.values()).count(co[correcta]) == 1:
        s += 15; senales.append("eco_enunciado")
    if len(enunciado or "") < 90:
        s += 10; senales.append("enunciado_corto")
    return min(s, 100), "|".join(senales)


MIGRACION = """
CREATE TABLE preguntas_exclusiones_nueva (
    pregunta_id INTEGER PRIMARY KEY
        REFERENCES lote_preguntas(id) ON DELETE RESTRICT,
    clasificacion TEXT NOT NULL
        CHECK (clasificacion IN ('ERROR_DEMOSTRADO', 'NO_DETERMINABLE', 'DIFICULTAD_BAJA')),
    estado TEXT NOT NULL DEFAULT 'CUARENTENA'
        CHECK (estado IN ('CUARENTENA', 'RETIRADA')),
    respuesta_almacenada TEXT NOT NULL
        CHECK (respuesta_almacenada IN ('A', 'B', 'C', 'D')),
    respuesta_demostrada TEXT
        CHECK (respuesta_demostrada IS NULL OR respuesta_demostrada IN ('A', 'B', 'C', 'D')),
    motivo TEXT NOT NULL,
    evidencia_ruta TEXT NOT NULL,
    evidencia_sha256 TEXT NOT NULL
        CHECK (length(evidencia_sha256) = 64),
    origen_auditoria TEXT NOT NULL,
    decision_usuario INTEGER NOT NULL DEFAULT 0
        CHECK (decision_usuario IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (clasificacion = 'ERROR_DEMOSTRADO' AND respuesta_demostrada IS NOT NULL)
        OR
        (clasificacion IN ('NO_DETERMINABLE', 'DIFICULTAD_BAJA') AND respuesta_demostrada IS NULL)
    )
);
INSERT INTO preguntas_exclusiones_nueva SELECT * FROM preguntas_exclusiones;
DROP TABLE preguntas_exclusiones;
ALTER TABLE preguntas_exclusiones_nueva RENAME TO preguntas_exclusiones;
CREATE INDEX idx_preguntas_exclusiones_estado ON preguntas_exclusiones(estado, clasificacion);
"""


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def calcular(con):
    con.row_factory = sqlite3.Row
    excluidas = {r[0] for r in con.execute("SELECT pregunta_id FROM preguntas_exclusiones")}
    lp = {r["id"]: r for r in con.execute(
        """SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d, respuesta_correcta,
                  tipo_fuente, tipo_clasificacion, norma_id_normalizada, teorica_practica,
                  nombre_norma_normalizado, articulo_normalizado
           FROM lote_preguntas""")}
    candidatas = {}
    for i, r in lp.items():
        corr = str(r["respuesta_correcta"] or "").strip().upper()
        if (i in excluidas or r["tipo_fuente"] != "ia_generada"
                or r["tipo_clasificacion"] != "JURIDICA" or corr not in "ABCD" or not corr):
            continue
        p, sen = puntuacion_pistas(
            r["enunciado"], {k: r["opcion_" + k.lower()] for k in "ABCD"}, corr)
        if p >= UMBRAL_PISTAS:
            candidatas[i] = (p, sen, corr)

    # Ámbitos a proteger: cada parte y cada bloque NORMA (preguntas teóricas de esa norma)
    ambitos = []
    bloques = defaultdict(lambda: defaultdict(int))
    for pid, nid, cant in con.execute(
            "SELECT convocatoria_parte_id, norma_id, cantidad FROM convocatoria_modelo_bloques "
            "WHERE tipo_bloque='NORMA'"):
        bloques[pid][nid] += cant
    for parte in con.execute(
            "SELECT p.id, p.nombre, p.numero_preguntas, c.codigo FROM convocatoria_partes p "
            "JOIN convocatorias c ON c.id=p.convocatoria_id"):
        ids = {r[0] for r in con.execute(
            "SELECT pregunta_id FROM banco_preguntas WHERE convocatoria_parte_id=? AND estado='INCLUIDA'",
            (parte["id"],)) if r[0] not in excluidas}
        ambitos.append((f"{parte['codigo']} · {parte['nombre']}", ids, int(parte["numero_preguntas"])))
        for nid, cant in bloques[parte["id"]].items():
            sub = {i for i in ids if lp[i]["norma_id_normalizada"] == nid
                   and str(lp[i]["teorica_practica"] or "").upper() == "TEORICA"}
            ambitos.append((f"{parte['codigo']} · {parte['nombre']} · norma {nid}", sub, int(cant)))

    retirar = set(candidatas)
    protegidas_por = defaultdict(int)
    cambio = True
    while cambio:
        cambio = False
        for nombre, ids, necesarias in ambitos:
            minimo = MULTIPLICADOR * necesarias
            restantes = len(ids - retirar)
            if restantes < minimo:
                dentro = sorted((i for i in ids if i in retirar), key=lambda i: candidatas[i][0])
                for i in dentro[: minimo - restantes]:
                    retirar.discard(i)
                    protegidas_por[nombre] += 1
                    cambio = True
    return lp, candidatas, retirar, protegidas_por


def deshacer(db: Path) -> int:
    con = sqlite3.connect(db)
    ids = [r[0] for r in con.execute(
        "SELECT pregunta_id FROM preguntas_exclusiones WHERE origen_auditoria=?", (ORIGEN,))]
    print(f"Preguntas retiradas por este script: {len(ids)}")
    if not ids:
        return 0
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    copia = db.with_name(f"oposiciones_backup_antes_deshacer_retirada_{marca}.sqlite3")
    d = sqlite3.connect(copia); con.backup(d); d.close()
    print(f"Copia de seguridad: {copia}")
    with con:
        # Primero se quita la exclusión (los disparadores impiden incluir una pregunta excluida)
        con.execute("DELETE FROM preguntas_exclusiones WHERE origen_auditoria=?", (ORIGEN,))
        con.execute("UPDATE banco_preguntas SET estado='INCLUIDA', motivo_revision=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE motivo_revision=? AND estado='REVISION'",
                    (MOTIVO_BANCO,))
    print("Retirada deshecha. Ejecuta después la sincronización de bancos habitual.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(RAIZ / "db" / "oposiciones.sqlite3"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--aplicar", action="store_true")
    g.add_argument("--deshacer", action="store_true")
    a = ap.parse_args()
    db = Path(a.db).resolve()
    if not db.exists():
        print(f"No existe la base: {db}"); return 1
    if a.deshacer:
        return deshacer(db)

    con = sqlite3.connect(db)
    lp, candidatas, retirar, protegidas_por = calcular(con)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = RAIZ / "auditorias" / f"retirada_dificultad_{marca}"
    carpeta.mkdir(parents=True, exist_ok=True)
    evidencia = carpeta / ("lista_retirada.csv" if a.aplicar else "lista_retirada_SIMULACION.csv")
    with open(evidencia, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["pregunta_id", "puntuacion_pistas", "senales", "norma", "articulo", "enunciado"])
        for i in sorted(retirar):
            p, sen, _ = candidatas[i]
            w.writerow([i, p, sen, lp[i]["nombre_norma_normalizado"], lp[i]["articulo_normalizado"],
                        lp[i]["enunciado"]])
    vinculos = sum(1 for _ in con.execute(
        f"SELECT 1 FROM banco_preguntas WHERE estado='INCLUIDA' AND pregunta_id IN "
        f"(SELECT value FROM json_each(?))", (str(sorted(retirar)),)))

    print(f"Base de datos: {db}")
    print(f"Preguntas IA jurídicas con pistas >= {UMBRAL_PISTAS}: {len(candidatas)}")
    print(f"Protegidas para no dejar partes o bloques cortos: {len(candidatas) - len(retirar)}")
    for nombre, n in sorted(protegidas_por.items(), key=lambda x: -x[1]):
        print(f"   - {nombre}: {n}")
    print(f"PREGUNTAS A RETIRAR: {len(retirar)}  (vínculos de banco afectados: {vinculos})")
    print(f"Lista: {evidencia}")

    if not a.aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0

    con.close()
    copia = db.with_name(f"oposiciones_backup_antes_retirada_dificultad_{marca}.sqlite3")
    origen = sqlite3.connect(db); destino = sqlite3.connect(copia)
    origen.backup(destino); destino.close(); origen.close()
    print(f"\nCopia de seguridad: {copia}")
    huella = sha256(evidencia)

    con = sqlite3.connect(db, isolation_level=None)
    con.execute("PRAGMA foreign_keys=OFF")
    try:
        con.execute("BEGIN IMMEDIATE")
        sql_actual = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='preguntas_exclusiones'").fetchone()[0]
        if "DIFICULTAD_BAJA" not in sql_actual:
            antes = con.execute("SELECT COUNT(*) FROM preguntas_exclusiones").fetchone()[0]
            # Los disparadores que protegen los bancos referencian esta tabla:
            # se guardan, se eliminan durante la reconstrucción y se recrean idénticos.
            disparadores = con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                "AND sql LIKE '%preguntas_exclusiones%'").fetchall()
            for nombre, _ in disparadores:
                con.execute(f'DROP TRIGGER "{nombre}"')
            for sentencia in [x for x in MIGRACION.split(";") if x.strip()]:
                con.execute(sentencia)
            for _, sql_disparador in disparadores:
                con.execute(sql_disparador)
            despues = con.execute("SELECT COUNT(*) FROM preguntas_exclusiones").fetchone()[0]
            recreados = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND sql LIKE '%preguntas_exclusiones%'").fetchone()[0]
            if antes != despues or recreados != len(disparadores):
                raise RuntimeError("La ampliación de preguntas_exclusiones no se ha completado bien.")
            print(f"Tabla preguntas_exclusiones ampliada ({despues} filas conservadas, "
                  f"{recreados} disparadores de protección recreados).")
        for i in sorted(retirar):
            p, sen, corr = candidatas[i]
            con.execute(
                """INSERT INTO preguntas_exclusiones (pregunta_id, clasificacion, estado,
                       respuesta_almacenada, respuesta_demostrada, motivo, evidencia_ruta,
                       evidencia_sha256, origen_auditoria, decision_usuario)
                   VALUES (?, 'DIFICULTAD_BAJA', 'RETIRADA', ?, NULL, ?, ?, ?, ?, 1)""",
                (i, corr, f"DIFICULTAD_BAJA: pistas={p} ({sen})", str(evidencia), huella, ORIGEN))
        con.execute(
            f"UPDATE banco_preguntas SET estado='REVISION', motivo_revision=?, updated_at=CURRENT_TIMESTAMP "
            f"WHERE estado='INCLUIDA' AND pregunta_id IN (SELECT value FROM json_each(?))",
            (MOTIVO_BANCO, str(sorted(retirar))))
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        n_excl = con.execute("SELECT COUNT(*) FROM preguntas_exclusiones WHERE origen_auditoria=?",
                             (ORIGEN,)).fetchone()[0]
        n_incl = con.execute(
            "SELECT COUNT(*) FROM banco_preguntas WHERE estado='INCLUIDA' AND pregunta_id IN "
            "(SELECT value FROM json_each(?))", (str(sorted(retirar)),)).fetchone()[0]
        if fk or n_excl != len(retirar) or n_incl != 0:
            raise RuntimeError(f"Comprobación fallida: fk={fk[:3]} exclusiones={n_excl} incluidas={n_incl}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        print("ERROR: no se ha modificado nada (la transacción se ha deshecho).")
        raise
    finally:
        con.execute("PRAGMA foreign_keys=ON")
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM lote_preguntas").fetchone()[0]
    con.close()
    print(f"Preguntas retiradas: {n_excl}")
    print(f"Vínculos de banco pasados a REVISION: {vinculos}")
    print(f"Preguntas en lote_preguntas: {total} (no cambia: no se borra nada)")
    print(f"Integridad de la base: {integridad}")
    print("RESULTADO:", "OK" if integridad == "ok" else "REVISAR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
