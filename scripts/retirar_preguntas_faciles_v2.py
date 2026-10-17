"""Retira de los bancos todas las preguntas jurídicas TEÓRICAS fáciles que no son oficiales (G2).

Criterio (nivel_dificultad guardado por calcular_nivel_dificultad.py):
  - tipo_clasificacion = 'JURIDICA' y teorica_practica distinta de 'PRACTICA'
    (los supuestos prácticos se dejan para una auditoría posterior);
  - nivel_dificultad >= 2 (IA anterior al v5 y tests de academia/webs con pistas);
  - NO se retiran las de exámenes oficiales: tipo_fuente apoyo, AYTO-examen y modelo,
    ni las marcadas en preguntas_examen_oficial.

Protección: ningún bloque NORMA ni ninguna parte de ninguna convocatoria se queda con menos
de 3 veces las preguntas que necesita un simulacro; si hiciera falta, se conservan las de
nivel más bajo (más difíciles) de ese ámbito.

No borra nada (mismo mecanismo que la retirada del 16/10):
  - fila en preguntas_exclusiones: clasificacion 'DIFICULTAD_BAJA', estado 'RETIRADA';
  - vínculos de banco_preguntas -> 'REVISION' con motivo 'DIFICULTAD_BAJA'.

Uso (desde TuCoach-Mantenimiento):
    python scripts/retirar_preguntas_faciles_v2.py             -> SIMULACIÓN (no cambia nada)
    python scripts/retirar_preguntas_faciles_v2.py --aplicar   -> copia de seguridad + retirada
    python scripts/retirar_preguntas_faciles_v2.py --deshacer  -> devuelve a los bancos lo retirado por este script
Después de aplicar o deshacer: menú 5 (publicación) para llevarlo a la web.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN = "retirada_dificultad_nivel_v2"
MOTIVO_BANCO = "DIFICULTAD_BAJA"
FUENTES_OFICIALES = ("apoyo", "AYTO-examen", "modelo")
MULTIPLICADOR = 3


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def calcular(con):
    con.row_factory = sqlite3.Row
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    excluidas = {r[0] for r in con.execute(
        "SELECT pregunta_id FROM preguntas_exclusiones WHERE estado IN ('CUARENTENA','RETIRADA')")}
    oficiales = {r[0] for r in con.execute("SELECT pregunta_id FROM preguntas_examen_oficial")} \
        if "preguntas_examen_oficial" in tablas else set()
    lp = {r["id"]: r for r in con.execute(
        """SELECT id, enunciado, respuesta_correcta, tipo_fuente, tipo_clasificacion, teorica_practica,
                  nivel_dificultad, norma_id_normalizada, nombre_norma_normalizado, articulo_normalizado
           FROM lote_preguntas""")}
    candidatas = {}
    for i, r in lp.items():
        if (i in excluidas or i in oficiales or r["tipo_clasificacion"] != "JURIDICA"
                or str(r["teorica_practica"] or "").upper() == "PRACTICA"
                or r["tipo_fuente"] in FUENTES_OFICIALES or (r["nivel_dificultad"] or 1) < 2):
            continue
        candidatas[i] = r

    ambitos = []
    bloques = defaultdict(lambda: defaultdict(int))
    for pid, nid, cant in con.execute(
            "SELECT convocatoria_parte_id, norma_id, cantidad FROM convocatoria_modelo_bloques WHERE tipo_bloque='NORMA'"):
        bloques[pid][nid] += cant
    for parte in con.execute(
            "SELECT p.id, p.nombre, p.numero_preguntas, c.codigo FROM convocatoria_partes p "
            "JOIN convocatorias c ON c.id = p.convocatoria_id"):
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
                dentro = sorted((i for i in ids if i in retirar),
                                key=lambda i: (candidatas[i]["nivel_dificultad"] or 9, i))
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
    copia = db.with_name(f"oposiciones_backup_antes_deshacer_retirada_v2_{marca}.sqlite3")
    d = sqlite3.connect(copia); con.backup(d); d.close()
    print(f"Copia de seguridad: {copia}")
    with con:
        con.execute("DELETE FROM preguntas_exclusiones WHERE origen_auditoria=?", (ORIGEN,))
        con.execute(
            "UPDATE banco_preguntas SET estado='INCLUIDA', motivo_revision=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE motivo_revision=? AND estado='REVISION' AND pregunta_id IN (SELECT value FROM json_each(?))",
            (MOTIVO_BANCO, str(ids)))
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
    if a.deshacer:
        return deshacer(db)

    con = sqlite3.connect(db)
    sql_excl = con.execute("SELECT sql FROM sqlite_master WHERE name='preguntas_exclusiones'").fetchone()[0]
    if "DIFICULTAD_BAJA" not in sql_excl:
        print("preguntas_exclusiones no admite 'DIFICULTAD_BAJA'. Ejecuta antes retirar_preguntas_faciles.py "
              "(la retirada del 16/10 amplió la tabla). No se hace nada.")
        return 1
    lp, candidatas, retirar, protegidas_por = calcular(con)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = RAIZ / "auditorias" / f"retirada_dificultad_v2_{marca}"
    carpeta.mkdir(parents=True, exist_ok=True)
    evidencia = carpeta / ("lista_retirada.csv" if a.aplicar else "lista_retirada_SIMULACION.csv")
    with open(evidencia, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["pregunta_id", "tipo_fuente", "nivel", "norma", "articulo", "enunciado"])
        for i in sorted(retirar):
            r = lp[i]
            w.writerow([i, r["tipo_fuente"], r["nivel_dificultad"], r["nombre_norma_normalizado"],
                        r["articulo_normalizado"], r["enunciado"]])
    en_bancos = {r[0] for r in con.execute(
        "SELECT DISTINCT pregunta_id FROM banco_preguntas WHERE estado='INCLUIDA' AND pregunta_id IN "
        "(SELECT value FROM json_each(?))", (str(sorted(retirar)),))}
    vinculos = con.execute(
        "SELECT COUNT(*) FROM banco_preguntas WHERE estado='INCLUIDA' AND pregunta_id IN "
        "(SELECT value FROM json_each(?))", (str(sorted(retirar)),)).fetchone()[0]

    print(f"Base de datos: {db}")
    print(f"Candidatas (jurídicas teóricas no oficiales de nivel >= 2): {len(candidatas)}")
    print(f"Protegidas para no dejar bloques por debajo de {MULTIPLICADOR}x: {len(candidatas) - len(retirar)}")
    for nombre, n in sorted(protegidas_por.items(), key=lambda x: -x[1]):
        print(f"   - {nombre}: {n}")
    print(f"PREGUNTAS A RETIRAR: {len(retirar)}  (de ellas en bancos: {len(en_bancos)}; vínculos: {vinculos})")
    for (fuente, nivel), n in sorted(Counter((lp[i]["tipo_fuente"], lp[i]["nivel_dificultad"])
                                             for i in en_bancos).items()):
        print(f"   en bancos · {fuente:20s} nivel {nivel}: {n}")
    print(f"Lista: {evidencia}")
    if not a.aplicar:
        print("\nSIMULACIÓN: no se ha modificado nada. Para aplicar, añade --aplicar")
        return 0

    con.close()
    copia = db.with_name(f"oposiciones_backup_antes_retirada_v2_{marca}.sqlite3")
    o = sqlite3.connect(db); d = sqlite3.connect(copia); o.backup(d); d.close(); o.close()
    print(f"\nCopia de seguridad: {copia}")
    huella = sha256(evidencia)
    con = sqlite3.connect(db, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        for i in sorted(retirar):
            r = lp[i]
            con.execute(
                """INSERT INTO preguntas_exclusiones (pregunta_id, clasificacion, estado,
                       respuesta_almacenada, respuesta_demostrada, motivo, evidencia_ruta,
                       evidencia_sha256, origen_auditoria, decision_usuario)
                   VALUES (?, 'DIFICULTAD_BAJA', 'RETIRADA', ?, NULL, ?, ?, ?, ?, 1)""",
                (i, str(r["respuesta_correcta"] or "").strip().upper() or None,
                 f"DIFICULTAD_BAJA: nivel={r['nivel_dificultad']} fuente={r['tipo_fuente']}",
                 str(evidencia), huella, ORIGEN))
        con.execute(
            "UPDATE banco_preguntas SET estado='REVISION', motivo_revision=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE estado='INCLUIDA' AND pregunta_id IN (SELECT value FROM json_each(?))",
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
    integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    print(f"Preguntas retiradas: {n_excl}")
    print(f"Vínculos de banco pasados a REVISION: {vinculos}")
    print(f"Integridad: {integridad}")
    print("RESULTADO:", "OK" if integridad == "ok" else "REVISAR")
    print("Siguiente: python scripts/validacion_completa.py y después menú 5 para publicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
