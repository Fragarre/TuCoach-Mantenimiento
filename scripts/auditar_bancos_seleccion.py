from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from collections import defaultdict
from pathlib import Path


def cargar_modulo_constructor(ruta: Path):
    spec = importlib.util.spec_from_file_location("mantener_banco_preguntas_audit", ruta)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se puede cargar {ruta}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def resolver_parte_convocatoria(con, convocatoria_id, pregunta, tema):
    filas = con.execute(
        """
        SELECT DISTINCT r.convocatoria_parte_id, r.prioridad
        FROM convocatoria_parte_reglas AS r
        JOIN convocatoria_partes AS cp ON cp.id = r.convocatoria_parte_id
        WHERE cp.convocatoria_id = ?
          AND (r.temario_parte IS NULL OR UPPER(r.temario_parte) = UPPER(?))
          AND (r.tipo_contenido IS NULL OR UPPER(r.tipo_contenido) = UPPER(?))
          AND (r.teorica_practica IS NULL OR UPPER(r.teorica_practica) = UPPER(COALESCE(?, '')))
          AND (r.tema_no_juridico IS NULL OR UPPER(r.tema_no_juridico) = UPPER(COALESCE(?, '')))
        ORDER BY r.prioridad, r.convocatoria_parte_id
        """,
        (
            convocatoria_id,
            tema["parte"],
            tema["tipo_contenido"],
            pregunta.get("teorica_practica"),
            pregunta.get("tema_no_juridico"),
        ),
    ).fetchall()
    if not filas:
        return None, "SIN_REGLA_PARTE"
    prioridad = int(filas[0]["prioridad"])
    partes = sorted({int(f["convocatoria_parte_id"]) for f in filas if int(f["prioridad"]) == prioridad})
    if len(partes) != 1:
        return None, "REGLAS_PARTE_AMBIGUAS"
    return partes[0], None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--constructor", required=True)
    args = parser.parse_args()

    db = Path(args.db).resolve()
    constructor = cargar_modulo_constructor(Path(args.constructor).resolve())

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        print("AUDITORÍA DE SELECCIÓN DE BANCOS")
        print("=" * 76)
        print(f"integrity_check....................... {con.execute('PRAGMA integrity_check').fetchone()[0]}")
        print(f"foreign_key_check..................... {len(con.execute('PRAGMA foreign_key_check').fetchall())}")

        convocatorias = con.execute(
            "SELECT id, codigo FROM convocatorias ORDER BY id"
        ).fetchall()

        total_incidencias = 0
        for conv in convocatorias:
            cid = int(conv["id"])
            temarios = con.execute(
                "SELECT id FROM temarios WHERE convocatoria_id = ? ORDER BY id",
                (cid,),
            ).fetchall()
            if len(temarios) != 1:
                print(f"{conv['codigo']}: ERROR temarios={len(temarios)}")
                total_incidencias += 1
                continue
            tid = int(temarios[0]["id"])

            refs, inv_refs, dup_refs = constructor.cargar_referencias_juridicas(con, tid)
            eqs, inv_eq, dup_eq = constructor.cargar_equivalencias_no_juridicas(con, tid)
            jur = constructor.seleccionar_juridicas(con, refs, {})
            nojur = constructor.seleccionar_no_juridicas(con, eqs, {})
            esperadas = {int(p["id"]): p for p in jur["nuevas"] + nojur["nuevas"]}

            reales_rows = con.execute(
                """
                SELECT bp.id AS banco_id, bp.pregunta_id, bp.tipo_vinculacion,
                       bp.metodo_vinculacion, bp.estado, bp.convocatoria_parte_id,
                       bpt.tema_id, bpt.es_principal
                FROM banco_preguntas bp
                LEFT JOIN banco_preguntas_temas bpt ON bpt.banco_pregunta_id = bp.id
                WHERE bp.convocatoria_id = ?
                ORDER BY bp.pregunta_id, bpt.id
                """,
                (cid,),
            ).fetchall()
            reales = defaultdict(list)
            for r in reales_rows:
                reales[int(r["pregunta_id"])].append(dict(r))

            faltan = set(esperadas) - set(reales)
            sobran = set(reales) - set(esperadas)
            tema_mal = []
            tipo_mal = []
            metodo_mal = []
            estado_mal = []
            principal_mal = []
            parte_nula = []
            parte_mal = []
            sin_regla = []

            for pid in sorted(set(esperadas) & set(reales)):
                e = esperadas[pid]
                rr = reales[pid]
                principales = [r for r in rr if r["es_principal"] == 1]
                if len(principales) != 1 or len(rr) != 1:
                    principal_mal.append(pid)
                    continue
                r = principales[0]
                if int(r["tema_id"]) != int(e["tema_id"]):
                    tema_mal.append(pid)
                if r["tipo_vinculacion"] != e["tipo_vinculacion_banco"]:
                    tipo_mal.append(pid)
                if r["metodo_vinculacion"] != e["metodo_vinculacion"]:
                    metodo_mal.append(pid)
                if r["estado"] != "INCLUIDA":
                    estado_mal.append(pid)

                tema = dict(con.execute(
                    "SELECT parte, tipo_contenido FROM temario_temas WHERE id = ?",
                    (int(e["tema_id"]),),
                ).fetchone())
                q = dict(con.execute(
                    "SELECT teorica_practica, tema_no_juridico FROM lote_preguntas WHERE id = ?",
                    (pid,),
                ).fetchone())
                parte_esperada, error = resolver_parte_convocatoria(con, cid, q, tema)
                if error:
                    sin_regla.append(pid)
                elif r["convocatoria_parte_id"] is None:
                    parte_nula.append(pid)
                elif int(r["convocatoria_parte_id"]) != int(parte_esperada):
                    parte_mal.append(pid)

            incid = (
                len(inv_refs) + len(dup_refs) + len(inv_eq) + len(dup_eq)
                + len(faltan) + len(sobran) + len(tema_mal) + len(tipo_mal)
                + len(metodo_mal) + len(estado_mal) + len(principal_mal)
                + len(parte_mal) + len(sin_regla)
            )
            # parte_nula se informa aparte: es la incidencia funcional detectada.
            incid += len(parte_nula)
            total_incidencias += incid

            print()
            print(conv["codigo"])
            print("-" * 76)
            print(f"Esperadas por reglas actuales......... {len(esperadas)}")
            print(f"Reales en banco....................... {len(reales)}")
            print(f"Faltantes............................. {len(faltan)}")
            print(f"Sobrantes............................. {len(sobran)}")
            print(f"Tema principal incorrecto............. {len(tema_mal)}")
            print(f"Tipo/método/estado incorrecto......... {len(tipo_mal)+len(metodo_mal)+len(estado_mal)}")
            print(f"Vínculo principal incorrecto.......... {len(principal_mal)}")
            print(f"Referencias/equivalencias ambiguas.... {len(inv_refs)+len(dup_refs)+len(inv_eq)+len(dup_eq)}")
            print(f"Parte existente incorrecta............ {len(parte_mal)}")
            print(f"Sin regla/ambigua de parte............. {len(sin_regla)}")
            print(f"Parte de convocatoria NULA............. {len(parte_nula)}")

        print()
        print("=" * 76)
        print(f"INCIDENCIAS TOTALES................... {total_incidencias}")
        return 1 if total_incidencias else 0


if __name__ == "__main__":
    raise SystemExit(main())
