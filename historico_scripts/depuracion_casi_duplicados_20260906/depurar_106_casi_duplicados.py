#!/usr/bin/env python3
"""
DEPURACIÓN DEFINITIVA DE LOS 106 CASI DUPLICADOS

- Detecta los mismos pares que validacion_completa.py.
- Verifica que sean exactamente 106 grupos binarios.
- Crea una copia .bak de la BD antes de modificar.
- Elimina TODAS las filas de banco_preguntas correspondientes a las 106 preguntas.
  Esto provoca el borrado en cascada de banco_preguntas_temas si la BD está
  configurada así.
- Elimina después las 106 preguntas de lote_preguntas.
- Comprueba referencias FK a lote_preguntas en TODAS las tablas antes de borrar.
- Ejecuta todo en una única transacción.
- Si algo falla, hace ROLLBACK.
- Después comprueba foreign_key_check e integrity_check.

No modifica Git ni otros archivos de configuración.
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


def tablas(con):
    return [
        r[0] for r in con.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
    ]


def referencias_a_lote(con, pid):
    """
    Devuelve referencias reales mediante FKs desde cualquier tabla hacia
    lote_preguntas. No depende de conocer de antemano el esquema.
    """
    refs = []
    for table in tablas(con):
        fks = con.execute(
            f"PRAGMA foreign_key_list({quote_ident(table)})"
        ).fetchall()
        for fk in fks:
            # id, seq, table, from, to, on_update, on_delete, match, ...
            ref_table = fk[2]
            from_col = fk[3]
            to_col = fk[4]
            if ref_table != "lote_preguntas":
                continue
            sql = (
                f"SELECT COUNT(*) FROM {quote_ident(table)} "
                f"WHERE {quote_ident(from_col)}=?"
            )
            count = con.execute(sql, (pid,)).fetchone()[0]
            if count:
                refs.append((table, from_col, to_col, count))
    return refs


def quote_ident(s):
    return '"' + s.replace('"', '""') + '"'


def main():
    if len(sys.argv) != 2:
        print("Uso:")
        print(r"  .venv\Scripts\python.exe scripts\depurar_106_casi_duplicados.py db\oposiciones.sqlite3")
        return 2

    db = Path(sys.argv[1]).resolve()
    if not db.exists():
        print(f"ERROR: no existe la BD: {db}")
        return 2

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(db.name + f".bak_{timestamp}")

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")

    try:
        # La BD debe estar sana antes de empezar.
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print(f"ERROR: integrity_check previo: {integrity}")
            return 1

        pares = detectar_pares(con)
        grupos = formar_grupos(pares)

        print("=" * 78)
        print("DEPURACIÓN DE 106 CASI DUPLICADOS")
        print("=" * 78)
        print(f"Pares detectados.................. {len(pares)}")
        print(f"Grupos............................ {len(grupos)}")

        if len(pares) != 106 or len(grupos) != 106:
            print("ERROR: el conjunto actual no coincide con los 106 auditados.")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        if any(len(g) != 2 for g in grupos):
            print("ERROR: existe un grupo con más de 2 preguntas.")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        ids = sorted({x for g in grupos for x in g})
        print(f"Preguntas afectadas............... {len(ids)}")

        # Confirmar que las 106 preguntas existen.
        placeholders = ",".join("?" for _ in ids)
        existing = {
            r[0] for r in con.execute(
                f"SELECT id FROM lote_preguntas WHERE id IN ({placeholders})",
                ids
            )
        }
        if len(existing) != 106:
            print(f"ERROR: se esperaban 106 preguntas y existen {len(existing)}.")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        # Verificación global de referencias a lote_preguntas.
        referencias = []
        for pid in ids:
            refs = referencias_a_lote(con, pid)
            if refs:
                referencias.append((pid, refs))

        if referencias:
            print("ERROR: hay referencias FK a alguna pregunta.")
            for pid, refs in referencias:
                print(f"  {pid}: {refs}")
            print("NO SE MODIFICA LA BASE DE DATOS.")
            return 1

        banco_count = con.execute(
            f"SELECT COUNT(*) FROM banco_preguntas "
            f"WHERE pregunta_id IN ({placeholders})", ids
        ).fetchone()[0]

        print(f"Filas banco a eliminar........... {banco_count}")

        # Backup físico antes de tocar la BD.
        con.close()
        shutil.copy2(db, backup)
        print(f"Copia de seguridad................ {backup}")

        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")

        # Primero eliminar vinculaciones con bancos.
        cur = con.execute(
            f"DELETE FROM banco_preguntas "
            f"WHERE pregunta_id IN ({placeholders})", ids
        )
        deleted_bank = cur.rowcount

        # Después eliminar las preguntas.
        cur = con.execute(
            f"DELETE FROM lote_preguntas "
            f"WHERE id IN ({placeholders})", ids
        )
        deleted_questions = cur.rowcount

        if deleted_bank != banco_count:
            raise RuntimeError(
                f"Se esperaban {banco_count} filas de banco y se eliminaron "
                f"{deleted_bank}"
            )
        if deleted_questions != 106:
            raise RuntimeError(
                f"Se esperaban 106 preguntas y se eliminaron "
                f"{deleted_questions}"
            )

        fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(f"foreign_key_check con errores: {fk_errors[:10]}")

        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check posterior: {integrity}")

        con.commit()

        # Comprobación final.
        remaining = con.execute(
            f"SELECT COUNT(*) FROM lote_preguntas "
            f"WHERE id IN ({placeholders})", ids
        ).fetchone()[0]
        remaining_bank = con.execute(
            f"SELECT COUNT(*) FROM banco_preguntas "
            f"WHERE pregunta_id IN ({placeholders})", ids
        ).fetchone()[0]

        if remaining != 0 or remaining_bank != 0:
            raise RuntimeError(
                f"Comprobación final fallida: preguntas={remaining}, "
                f"banco={remaining_bank}"
            )

        print()
        print("=" * 78)
        print("DEPURACIÓN COMPLETADA")
        print("=" * 78)
        print(f"Preguntas eliminadas.............. {deleted_questions}")
        print(f"Vinculaciones de banco eliminadas {deleted_bank}")
        print("foreign_key_check................. OK")
        print("integrity_check................... ok")
        print("Comprobación final................ OK")
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
        print(exc)
        print(f"Backup disponible................ {backup if backup.exists() else 'NO CREADO'}")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
