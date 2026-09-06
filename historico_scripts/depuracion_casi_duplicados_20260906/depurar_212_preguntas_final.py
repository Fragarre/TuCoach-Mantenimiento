#!/usr/bin/env python3
"""
ELIMINACIÓN DEFINITIVA DE LAS 212 PREGUNTAS DE LOS 106 PARES
DE CASI DUPLICADOS.

Operación destructiva pero protegida:
- Detecta nuevamente los 106 pares.
- Obtiene los 212 IDs.
- Verifica que todos existan.
- Verifica que no haya referencias en simulacros u otras tablas que
  impidan/eludan la eliminación.
- Crea backup físico antes de modificar.
- En una única transacción:
    1) elimina banco_preguntas de esas 212 preguntas;
    2) elimina lote_preguntas de esas 212 preguntas.
- Las filas dependientes de banco_preguntas_temas se eliminan mediante
  la FK/cascade definida por el esquema.
- Comprueba FK e integridad.
- Hace COMMIT solo si todo es correcto.
- Hace ROLLBACK ante cualquier error.
"""

import sqlite3
import shutil
import sys
from datetime import datetime
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


def quote_ident(s):
    return '"' + s.replace('"', '""') + '"'


def tablas(con):
    return [
        r[0] for r in con.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
    ]


def referencias_a_lote(con, pid):
    refs = []
    for table in tablas(con):
        fks = con.execute(
            f"PRAGMA foreign_key_list({quote_ident(table)})"
        ).fetchall()
        for fk in fks:
            if fk[2] != "lote_preguntas":
                continue
            from_col = fk[3]
            count = con.execute(
                f"SELECT COUNT(*) FROM {quote_ident(table)} "
                f"WHERE {quote_ident(from_col)}=?",
                (pid,)
            ).fetchone()[0]
            if count:
                refs.append((table, from_col, fk[4], count, fk[6]))
    return refs


def main():
    if len(sys.argv) != 2:
        print(r"Uso: python depurar_212_preguntas_final.py db\oposiciones.sqlite3")
        return 2

    db = Path(sys.argv[1]).resolve()
    if not db.exists():
        print(f"ERROR: no existe la BD: {db}")
        return 2

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")

    backup = None
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print(f"ERROR: integrity_check previo: {integrity}")
            return 1

        pares = detectar_pares(con)
        grupos = formar_grupos(pares)

        print("=" * 78)
        print("ELIMINACIÓN DE LAS 212 PREGUNTAS")
        print("=" * 78)
        print(f"Pares detectados.................. {len(pares)}")
        print(f"Grupos............................ {len(grupos)}")

        if len(pares) != 106 or len(grupos) != 106:
            print("ERROR: el conjunto actual ya no coincide con los 106 pares.")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        if any(len(g) != 2 for g in grupos):
            print("ERROR: existe un grupo que no contiene exactamente 2 preguntas.")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        ids = sorted({x for g in grupos for x in g})
        print(f"Preguntas afectadas............... {len(ids)}")

        if len(ids) != 212:
            print("ERROR: se esperaban exactamente 212 IDs.")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        ph = ",".join("?" for _ in ids)

        existing = {
            r[0] for r in con.execute(
                f"SELECT id FROM lote_preguntas WHERE id IN ({ph})", ids
            )
        }
        if len(existing) != 212:
            print(f"ERROR: se esperaban 212 preguntas y existen {len(existing)}.")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        # Referencias a lote_preguntas fuera de banco_preguntas.
        # banco_preguntas se permite porque se eliminará primero.
        referencias_bloqueantes = []
        for pid in ids:
            for table, col, to_col, count, on_delete in referencias_a_lote(con, pid):
                if table != "banco_preguntas":
                    referencias_bloqueantes.append(
                        (pid, table, col, to_col, count, on_delete)
                    )

        if referencias_bloqueantes:
            print("ERROR: existen referencias fuera de banco_preguntas.")
            for r in referencias_bloqueantes:
                print(" ", r)
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        banco_antes = con.execute(
            f"SELECT COUNT(*) FROM banco_preguntas "
            f"WHERE pregunta_id IN ({ph})", ids
        ).fetchone()[0]

        print(f"Vinculaciones de banco........... {banco_antes}")

        # Backup físico.
        con.close()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db.with_name(db.name + f".bak_{timestamp}")
        shutil.copy2(db, backup)

        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")

        # 1. Eliminar TODAS las vinculaciones con bancos.
        cur = con.execute(
            f"DELETE FROM banco_preguntas WHERE pregunta_id IN ({ph})", ids
        )
        banco_borradas = cur.rowcount

        if banco_borradas != banco_antes:
            raise RuntimeError(
                f"Filas banco inesperadas: esperadas {banco_antes}, "
                f"eliminadas {banco_borradas}"
            )

        # 2. Eliminar TODAS las preguntas.
        cur = con.execute(
            f"DELETE FROM lote_preguntas WHERE id IN ({ph})", ids
        )
        preguntas_borradas = cur.rowcount

        if preguntas_borradas != 212:
            raise RuntimeError(
                f"Preguntas inesperadas: esperadas 212, "
                f"eliminadas {preguntas_borradas}"
            )

        # Comprobaciones dentro de la transacción.
        restantes = con.execute(
            f"SELECT COUNT(*) FROM lote_preguntas WHERE id IN ({ph})", ids
        ).fetchone()[0]
        banco_restante = con.execute(
            f"SELECT COUNT(*) FROM banco_preguntas WHERE pregunta_id IN ({ph})",
            ids
        ).fetchone()[0]

        if restantes != 0 or banco_restante != 0:
            raise RuntimeError(
                f"Quedan referencias: preguntas={restantes}, banco={banco_restante}"
            )

        fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(
                f"foreign_key_check con errores: {fk_errors[:20]}"
            )

        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check posterior: {integrity}")

        con.commit()

        print()
        print("=" * 78)
        print("ELIMINACIÓN COMPLETADA CORRECTAMENTE")
        print("=" * 78)
        print(f"Preguntas eliminadas.............. {preguntas_borradas}")
        print(f"Vinculaciones de banco eliminadas {banco_borradas}")
        print("Referencias restantes............. 0")
        print("foreign_key_check................. OK")
        print("integrity_check................... ok")
        print("COMMIT............................ OK")
        print(f"Backup............................ {backup}")
        return 0

    except Exception as exc:
        try:
            con.rollback()
        except Exception:
            pass
        print()
        print("=" * 78)
        print("ERROR — ROLLBACK EJECUTADO")
        print("=" * 78)
        print(str(exc))
        if backup:
            print(f"Backup disponible................ {backup}")
        print("La base de datos no ha quedado con cambios parciales.")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
