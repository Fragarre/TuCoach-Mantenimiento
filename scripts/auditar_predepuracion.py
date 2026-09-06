#!/usr/bin/env python3
"""
AUDITORÍA FINAL PRE-DEPURACIÓN DE CASI DUPLICADOS
SOLO LECTURA.

No hace UPDATE, DELETE, INSERT ni CREATE.
Comprueba los 106 grupos detectados con la misma regla que el validador
y verifica si la consolidación propuesta es estructuralmente segura.
"""

import sqlite3
import sys
from pathlib import Path


def norm(s):
    return " ".join((s or "").strip().lower().split())


def distancia(a, b, limite=5):
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limite:
        return limite + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        mn = i
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1,
                           prev[j - 1] + (ca != cb)))
            mn = min(mn, cur[-1])
        if mn > limite:
            return limite + 1
        prev = cur
    return prev[-1]


def detectar_pares(con):
    rows = con.execute("""
        SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d
        FROM lote_preguntas ORDER BY id
    """).fetchall()

    indices = {i: {} for i in range(1, 6)}

    for row in rows:
        vals = [norm(x) for x in row[1:]]
        for omit in range(1, 6):
            key = tuple(vals[i - 1] for i in range(1, 6) if i != omit)
            indices[omit].setdefault(key, []).append(
                (row[0], vals[omit - 1])
            )

    pares = set()
    for grupos in indices.values():
        for candidatos in grupos.values():
            for i in range(len(candidatos)):
                for j in range(i + 1, len(candidatos)):
                    a, va = candidatos[i]
                    b, vb = candidatos[j]
                    if va != vb and distancia(va, vb) <= 5:
                        pares.add(tuple(sorted((a, b))))
    return sorted(pares)


def formar_grupos(pares):
    adj = {}
    for a, b in pares:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    vistos = set()
    grupos = []
    for n in sorted(adj):
        if n in vistos:
            continue
        pila = [n]
        g = set()
        while pila:
            x = pila.pop()
            if x in vistos:
                continue
            vistos.add(x)
            g.add(x)
            pila.extend(adj.get(x, ()))
        grupos.append(sorted(g))
    return grupos


def obtener_banco(con, pid):
    return con.execute("""
        SELECT id, convocatoria_id, tipo_vinculacion, estado,
               metodo_vinculacion, motivo_revision
        FROM banco_preguntas
        WHERE pregunta_id=?
        ORDER BY convocatoria_id, id
    """, (pid,)).fetchall()


def obtener_temas(con, bid):
    existe = con.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='banco_preguntas_temas'
    """).fetchone()
    if not existe:
        return None

    cols = [r[1] for r in con.execute(
        "PRAGMA table_info(banco_preguntas_temas)"
    ).fetchall()]
    if "banco_pregunta_id" not in cols:
        return None

    return con.execute(
        "SELECT * FROM banco_preguntas_temas "
        "WHERE banco_pregunta_id=? ORDER BY 1",
        (bid,)
    ).fetchall()


def obtener_simulacros(con, pid):
    existe = con.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='simulacro_preguntas'
    """).fetchone()
    if not existe:
        return []
    return con.execute("""
        SELECT id, simulacro_id, pregunta_id, banco_pregunta_id
        FROM simulacro_preguntas
        WHERE pregunta_id=?
    """, (pid,)).fetchall()


def main():
    if len(sys.argv) != 2:
        print("Uso: python auditar_predepuracion.py RUTA_DB")
        return 2

    db = Path(sys.argv[1])
    if not db.exists():
        print(f"No existe la base: {db}")
        return 2

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")

    pares = detectar_pares(con)
    grupos = formar_grupos(pares)

    print("=" * 78)
    print("AUDITORÍA FINAL PRE-DEPURACIÓN — SOLO LECTURA")
    print("=" * 78)
    print(f"Pares detectados.................. {len(pares)}")
    print(f"Grupos............................ {len(grupos)}")

    ok = 0
    avisos = 0
    errores = 0
    total_banco_eliminar = 0
    total_preguntas_eliminar = 0

    for g in grupos:
        # Para el caso actual son pares. Si apareciera un grupo mayor,
        # se marca como error para evitar decisiones automáticas.
        if len(g) != 2:
            print(f"ERROR grupo no binario: {g}")
            errores += 1
            continue

        a, b = g
        ba = obtener_banco(con, a)
        bb = obtener_banco(con, b)

        # Conservación:
        # una sola referenciada -> conservarla;
        # ambas/ninguna -> menor ID.
        if ba and not bb:
            keep, delete = a, b
            bk, bd = ba, bb
        elif bb and not ba:
            keep, delete = b, a
            bk, bd = bb, ba
        else:
            keep, delete = min(a, b), max(a, b)
            bk = obtener_banco(con, keep)
            bd = obtener_banco(con, delete)

        sims_delete = obtener_simulacros(con, delete)
        sims_keep = obtener_simulacros(con, keep)

        # Comprobaciones de banco.
        colisiones = []
        incompatibles = []
        temas_ok = True

        keep_by_conv = {r[1]: r for r in bk}
        delete_by_conv = {r[1]: r for r in bd}

        for conv in sorted(set(keep_by_conv) & set(delete_by_conv)):
            rk = keep_by_conv[conv]
            rd = delete_by_conv[conv]

            # Si ambas están en la misma convocatoria, no se puede
            # reasignar: la solución es eliminar la fila duplicada.
            # Comprobamos que sus metadatos sean compatibles.
            campos_k = rk[2:6]
            campos_d = rd[2:6]
            if campos_k != campos_d:
                incompatibles.append(
                    (conv, campos_k, campos_d)
                )

            tk = obtener_temas(con, rk[0])
            td = obtener_temas(con, rd[0])
            if tk is not None and td is not None and tk != td:
                temas_ok = False

        # Convocatorias que solo tiene la pregunta a eliminar:
        # esas filas sí serían candidatas a reasignación.
        reasignar = sorted(set(delete_by_conv) - set(keep_by_conv))
        eliminar_banco = sorted(set(keep_by_conv) & set(delete_by_conv))

        if sims_delete:
            errores += 1
            estado = "ERROR: simulacro referencia pregunta a eliminar"
        elif incompatibles:
            errores += 1
            estado = "ERROR: metadatos banco incompatibles"
        elif not temas_ok:
            errores += 1
            estado = "ERROR: temas asociados diferentes"
        else:
            estado = "OK"
            ok += 1

        total_banco_eliminar += len(eliminar_banco)
        total_preguntas_eliminar += 1

        if estado != "OK":
            print()
            print(f"GRUPO {g}: {estado}")
            print(f"  conservar={keep} eliminar={delete}")
            if incompatibles:
                print(f"  incompatibles={incompatibles}")
            if not temas_ok:
                print("  temas: DIFERENTES")
            if sims_delete:
                print(f"  simulacros eliminar={sims_delete}")

        # Los casos que requieren acción especial también se muestran.
        if len(bk) > 0 and len(bd) > 0:
            avisos += 1
            print()
            print(f"COLISIÓN RESOLUBLE {g}: conservar {keep}, eliminar {delete}")
            print(f"  convocatorias conservada: {sorted(keep_by_conv)}")
            print(f"  convocatorias duplicada:  {sorted(delete_by_conv)}")
            print(f"  filas banco a eliminar:   {eliminar_banco}")
            print(f"  referencias a reasignar:  {reasignar}")
            print(f"  simulacros del eliminado: {len(sims_delete)}")

    print()
    print("=" * 78)
    print("RESULTADO")
    print("=" * 78)
    print(f"OK................................ {ok}")
    print(f"Casos con ambas en banco.......... {avisos}")
    print(f"ERRORES........................... {errores}")
    print(f"Preguntas a eliminar.............. {total_preguntas_eliminar}")
    print(f"Filas de banco duplicadas......... {total_banco_eliminar}")
    print()
    if errores == 0:
        print("RESULTADO: APTO PARA PREPARAR LA DEPURACIÓN.")
        print("NO SE HA MODIFICADO LA BASE DE DATOS.")
    else:
        print("RESULTADO: NO APTO. No ejecutar ninguna depuración.")
        print("NO SE HA MODIFICADO LA BASE DE DATOS.")

    con.close()
    return 0 if errores == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
