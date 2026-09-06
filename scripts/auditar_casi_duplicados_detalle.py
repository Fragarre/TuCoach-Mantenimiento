#!/usr/bin/env python3
"""
AUDITORÍA DETALLADA DE CASI DUPLICADOS
SOLO LECTURA.

No modifica la base de datos.
Analiza los 106 pares/grupos detectados con la misma regla que
validacion_completa.py y detalla especialmente los casos en que
ambas preguntas ya están en banco_preguntas.
"""

import sqlite3
import sys
from pathlib import Path


def norm(s):
    return " ".join((s or "").strip().lower().split())


def lev(a, b, limite=5):
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limite:
        return limite + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        mn = i
        for j, cb in enumerate(b, 1):
            v = min(
                cur[-1] + 1,
                prev[j] + 1,
                prev[j - 1] + (ca != cb),
            )
            cur.append(v)
            mn = min(mn, v)
        if mn > limite:
            return limite + 1
        prev = cur
    return prev[-1]


def detectar(con):
    rows = con.execute("""
        SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d
        FROM lote_preguntas
        ORDER BY id
    """).fetchall()

    campos = range(1, 6)
    indices = {omit: {} for omit in campos}

    for row in rows:
        vals = [norm(x) for x in row[1:]]
        for omit in campos:
            key = tuple(vals[i - 1] for i in campos if i != omit)
            indices[omit].setdefault(key, []).append((row[0], vals[omit - 1]))

    pares = set()
    for omit in campos:
        for candidatos in indices[omit].values():
            if len(candidatos) < 2:
                continue
            for i in range(len(candidatos)):
                for j in range(i + 1, len(candidatos)):
                    a, va = candidatos[i]
                    b, vb = candidatos[j]
                    if va != vb and lev(va, vb, 5) <= 5:
                        pares.add(tuple(sorted((a, b))))

    return sorted(pares)


def tablas(con):
    return {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def banco(con, pid):
    return con.execute("""
        SELECT id, convocatoria_id, pregunta_id, tipo_vinculacion,
               estado, metodo_vinculacion, motivo_revision
        FROM banco_preguntas
        WHERE pregunta_id=?
        ORDER BY convocatoria_id, id
    """, (pid,)).fetchall()


def temas(con, banco_id):
    if "banco_preguntas_temas" not in tablas(con):
        return []
    cols = [r[1] for r in con.execute(
        "PRAGMA table_info(banco_preguntas_temas)"
    ).fetchall()]
    if "banco_pregunta_id" not in cols:
        return []
    # Intentamos mostrar las columnas disponibles sin asumir nombres.
    return con.execute(
        "SELECT * FROM banco_preguntas_temas WHERE banco_pregunta_id=?",
        (banco_id,)
    ).fetchall()


def simulacros(con, pid):
    if "simulacro_preguntas" not in tablas(con):
        return []
    return con.execute("""
        SELECT id, simulacro_id, pregunta_id, banco_pregunta_id
        FROM simulacro_preguntas
        WHERE pregunta_id=?
        ORDER BY id
    """, (pid,)).fetchall()


def grupos(pares):
    adj = {}
    for a, b in pares:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    vistos = set()
    out = []
    for n in sorted(adj):
        if n in vistos:
            continue
        stack = [n]
        g = set()
        while stack:
            x = stack.pop()
            if x in vistos:
                continue
            vistos.add(x)
            g.add(x)
            stack.extend(adj.get(x, ()))
        out.append(sorted(g))
    return out


def main():
    if len(sys.argv) != 2:
        print("Uso:")
        print("  python auditar_casi_duplicados_detalle.py RUTA_DB")
        return 2

    db = Path(sys.argv[1])
    if not db.exists():
        print(f"No existe la base: {db}")
        return 2

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")

    pares = detectar(con)
    gs = grupos(pares)

    print("=" * 78)
    print("AUDITORÍA DETALLADA DE CASI DUPLICADOS — SOLO LECTURA")
    print("=" * 78)
    print(f"Pares detectados.................. {len(pares)}")
    print(f"Grupos de duplicidad.............. {len(gs)}")

    if "banco_preguntas" not in tablas(con):
        print("ERROR: no existe banco_preguntas")
        return 1

    dobles = []
    una = []
    ninguna = []

    for g in gs:
        info = {pid: banco(con, pid) for pid in g}
        refs = [pid for pid in g if info[pid]]

        if len(refs) >= 2:
            dobles.append((g, info))
        elif len(refs) == 1:
            una.append((g, info))
        else:
            ninguna.append((g, info))

    print(f"Ambas en banco.................... {len(dobles)}")
    print(f"Solo una en banco................. {len(una)}")
    print(f"Ninguna en banco.................. {len(ninguna)}")
    print()

    print("=" * 78)
    print("CASOS CON AMBAS PREGUNTAS EN BANCO")
    print("=" * 78)

    for g, info in dobles:
        print()
        print(f"GRUPO: {g}")

        for pid in g:
            print(f"  PREGUNTA {pid}")
            rows = con.execute("""
                SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d
                FROM lote_preguntas WHERE id=?
            """, (pid,)).fetchone()

            if rows:
                print(f"    enunciado: {rows[1]}")

            for b in info[pid]:
                bid, conv, qid, tipo, estado, metodo, motivo = b
                print(
                    f"    BANCO id={bid} convocatoria={conv} "
                    f"tipo={tipo} estado={estado} metodo={metodo}"
                )
                if motivo:
                    print(f"      motivo_revision={motivo}")

                ts = temas(con, bid)
                if ts:
                    print(f"      temas_asociados={ts}")

            sims = simulacros(con, pid)
            if sims:
                print(f"    SIMULACROS: {sims}")
            else:
                print("    SIMULACROS: ninguno")

        convs = {}
        for pid in g:
            for b in info[pid]:
                convs.setdefault(b[1], []).append((pid, b[0]))

        print("  RESUMEN POR CONVOCATORIA:")
        for conv, vals in sorted(convs.items()):
            print(f"    convocatoria {conv}: {vals}")

    print()
    print("=" * 78)
    print("CONTROL DE REFERENCIAS A SIMULACROS")
    print("=" * 78)

    con_sim = 0
    for g in gs:
        for pid in g:
            if simulacros(con, pid):
                con_sim += 1
                print(f"  grupo={g} pregunta={pid} -> {simulacros(con, pid)}")

    print(f"Preguntas con referencia en simulacros: {con_sim}")

    print()
    print("=" * 78)
    print("CONCLUSIÓN")
    print("=" * 78)
    print("Esta auditoría NO modifica la base.")
    print("Con los casos anteriores podremos decidir si:")
    print("  1) eliminar directamente los 62 no referenciados; y")
    print("  2) consolidar los 44 casos de banco eliminando una fila de")
    print("     banco_preguntas y conservando la otra, cuando sus metadatos")
    print("     sean compatibles.")
    print("No se ejecuta ningún borrado ni UPDATE.")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
