"""
Depuración segura de casi duplicados en lote_preguntas.

- Detecta exactamente la misma regla que validacion_completa.py:
  4 de 5 campos idénticos tras normalización + distancia Levenshtein <= 5
  en el quinto.
- Forma grupos por conectividad: si A~B y B~C, A/B/C forman un grupo.
- Conserva una sola pregunta por grupo.
- Preferencia de conservación:
    1) preguntas referenciadas en banco_preguntas;
    2) dentro de ellas, la de menor id.
    3) si ninguna está referenciada, la de menor id.
- No elimina una pregunta referenciada por banco_preguntas.
- Hace copia de seguridad antes de borrar.
- Exporta a CSV las preguntas eliminadas.
- Comprueba integridad SQLite antes de confirmar.
"""

from __future__ import annotations

import csv
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_DB = RAIZ / "db" / "oposiciones.sqlite3"
CARPETA_COPIAS = RUTA_DB.parent / "copias_seguridad"


def normalizar(texto: str | None) -> str:
    return re.sub(
        r"\s+",
        " ",
        (texto or "").replace("\xa0", " ")
    ).strip().lower()


def distancia_edicion_hasta(a: str, b: str, limite: int = 5) -> int | None:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limite:
        return None

    anterior = list(range(len(b) + 1))

    for i, ca in enumerate(a, 1):
        minimo_j = max(1, i - limite)
        maximo_j = min(len(b), i + limite)

        actual = [limite + 1] * (len(b) + 1)
        actual[0] = i
        minimo_fila = limite + 1

        for j in range(minimo_j, maximo_j + 1):
            coste = 0 if ca == b[j - 1] else 1
            actual[j] = min(
                anterior[j] + 1,
                actual[j - 1] + 1,
                anterior[j - 1] + coste,
            )
            minimo_fila = min(minimo_fila, actual[j])

        if minimo_fila > limite:
            return None

        anterior = actual

    distancia = anterior[len(b)]
    return distancia if distancia <= limite else None


def detectar_pares(con: sqlite3.Connection):
    filas = con.execute(
        """
        SELECT id, enunciado, opcion_a, opcion_b, opcion_c, opcion_d
        FROM lote_preguntas
        ORDER BY id
        """
    ).fetchall()

    normalizadas = {}
    indices = [{} for _ in range(5)]

    for fila in filas:
        pid = int(fila[0])
        valores = tuple(normalizar(fila[i]) for i in range(1, 6))
        normalizadas[pid] = valores

        for distinto in range(5):
            firma = valores[:distinto] + valores[distinto + 1:]
            indices[distinto].setdefault(firma, []).append(
                (pid, valores[distinto])
            )

    pares = set()

    for indice in indices:
        for candidatos in indice.values():
            if len(candidatos) < 2:
                continue

            for i in range(len(candidatos)):
                id_a, texto_a = candidatos[i]

                for j in range(i + 1, len(candidatos)):
                    id_b, texto_b = candidatos[j]

                    if texto_a == texto_b:
                        continue

                    if distancia_edicion_hasta(texto_a, texto_b, 5) is not None:
                        pares.add((min(id_a, id_b), max(id_a, id_b)))

    return filas, normalizadas, sorted(pares)


def formar_grupos(pares):
    padre = {}

    def encontrar(x):
        padre.setdefault(x, x)
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(a, b):
        ra, rb = encontrar(a), encontrar(b)
        if ra != rb:
            padre[rb] = ra

    for a, b in pares:
        unir(a, b)

    grupos = {}
    for a, b in pares:
        raiz = encontrar(a)
        grupos.setdefault(raiz, set()).update((a, b))

    return [sorted(ids) for ids in grupos.values()]


def main():
    if not RUTA_DB.is_file():
        print(f"ERROR: no existe la base de datos: {RUTA_DB}")
        return 1

    with sqlite3.connect(RUTA_DB) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")

        filas, normalizadas, pares = detectar_pares(con)
        grupos = formar_grupos(pares)

        referencias = {
            int(row["pregunta_id"])
            for row in con.execute(
                "SELECT DISTINCT pregunta_id FROM banco_preguntas"
            )
        }

        por_id = {int(f["id"]): f for f in filas}

        # Plan definitivo:
        # - Si ambas están en banco, conservar la de menor ID y reasignar
        #   sus referencias a la conservada, salvo colisión.
        # - Si solo una está en banco, conservar esa.
        # - Si ninguna está en banco, conservar la de menor ID.
        plan = []
        colisiones = []

        for grupo in grupos:
            refs = {
                pid: con.execute(
                    "SELECT convocatoria_id FROM banco_preguntas "
                    "WHERE pregunta_id=? ORDER BY convocatoria_id",
                    (pid,),
                ).fetchall()
                for pid in grupo
            }
            referenciadas = [pid for pid in grupo if refs[pid]]

            if len(referenciadas) == 1:
                conservar = referenciadas[0]
            else:
                conservar = min(grupo)

            eliminar = next(pid for pid in grupo if pid != conservar)

            # Comprobar colisiones por convocatoria antes de reasignar.
            conv_conservar = {int(r[0]) for r in refs[conservar]}
            conv_eliminar = {int(r[0]) for r in refs[eliminar]}
            comunes = sorted(conv_conservar & conv_eliminar)

            if comunes:
                colisiones.append(
                    (conservar, eliminar, comunes)
                )

            plan.append({
                "conservar": conservar,
                "eliminar": eliminar,
                "refs_conservar": sorted(conv_conservar),
                "refs_eliminar": sorted(conv_eliminar),
                "colisiones": comunes,
            })

        print("=" * 78)
        print("AUDITORÍA DE CASI DUPLICADOS")
        print("=" * 78)
        print(f"Pares detectados.................. {len(pares)}")
        print(f"Grupos de duplicidad.............. {len(grupos)}")
        print(f"Grupos sin colisión............... {len(plan) - len(colisiones)}")
        print(f"Grupos CON colisión............... {len(colisiones)}")
        print(f"Preguntas que se eliminarían...... {len(plan) if not colisiones else len(plan)-len(colisiones)}")
        print()

        print("PLAN:")
        for item in plan:
            estado = "SIN REFERENCIAS" if not item["refs_eliminar"] else (
                f"reasignar bancos {item['refs_eliminar']}"
            )
            if item["colisiones"]:
                estado += f" | COLISIÓN {item['colisiones']}"
            print(
                f"  conservar {item['conservar']} <- eliminar "
                f"{item['eliminar']} | {estado}"
            )

        if colisiones:
            print()
            print("NO SE HARÁ NINGÚN BORRADO AUTOMÁTICO.")
            print("Hay colisiones de banco que requieren resolución.")
            return 2

        if not plan:
            print("No hay preguntas eliminables.")
            return 0

        print()
        print("NOTA: en los casos con ambas preguntas en banco, las referencias")
        print("se reasignarán a la pregunta conservada antes de eliminar la otra.")
        print()
        respuesta = input(
            "Escribe ELIMINAR para crear copia de seguridad, "
            "reasignar referencias y borrar estas "
            f"{len(plan)} preguntas: "
        ).strip()

        if respuesta != "ELIMINAR":
            print("Operación cancelada. No se ha modificado la base.")
            return 0

        eliminables = [
            (item["eliminar"], item["conservar"])
            for item in plan
        ]

        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        CARPETA_COPIAS.mkdir(parents=True, exist_ok=True)
        backup = CARPETA_COPIAS / f"oposiciones_antes_casi_duplicados_{marca}.sqlite3"
        shutil.copy2(RUTA_DB, backup)

        exportacion = CARPETA_COPIAS / f"casi_duplicados_eliminados_{marca}.csv"

        ids = [pid for pid, _ in eliminables]
        placeholders = ",".join("?" for _ in ids)

        filas_eliminar = con.execute(
            f"""
            SELECT *
            FROM lote_preguntas
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            ids,
        ).fetchall()

        if len(filas_eliminar) != len(ids):
            raise RuntimeError("No se realizará ningún cambio: faltan IDs.")

        with exportacion.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=filas_eliminar[0].keys())
            writer.writeheader()
            writer.writerows(dict(row) for row in filas_eliminar)

        con.execute("BEGIN IMMEDIATE")
        try:
            # Reasignar las referencias de banco. Como ya se han auditado
            # colisiones, cada INSERT puede hacerse con seguridad.
            for item in plan:
                eliminar = item["eliminar"]
                conservar = item["conservar"]

                for conv_id in item["refs_eliminar"]:
                    filas_banco = con.execute(
                        """
                        SELECT tipo_vinculacion, estado, metodo_vinculacion,
                               motivo_revision
                        FROM banco_preguntas
                        WHERE convocatoria_id=? AND pregunta_id=?
                        """,
                        (conv_id, eliminar),
                    ).fetchall()

                    for fila in filas_banco:
                        con.execute(
                            """
                            UPDATE banco_preguntas
                            SET pregunta_id=?, updated_at=CURRENT_TIMESTAMP
                            WHERE convocatoria_id=? AND pregunta_id=?
                            """,
                            (conservar, conv_id, eliminar),
                        )

            con.execute(
                f"DELETE FROM lote_preguntas WHERE id IN ({placeholders})",
                ids,
            )

            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            integridad = con.execute("PRAGMA integrity_check").fetchone()[0]

            if fk or integridad != "ok":
                con.rollback()
                raise RuntimeError(
                    f"Operación revertida: foreign_key_check={len(fk)}, "
                    f"integrity_check={integridad}"
                )

            con.commit()
        except Exception:
            con.rollback()
            raise

    print()
    print("=" * 78)
    print("DEPURACIÓN COMPLETADA")
    print("=" * 78)
    print(f"Preguntas eliminadas............... {len(eliminables)}")
    print(f"Copia de seguridad................. {backup}")
    print(f"CSV de preguntas eliminadas........ {exportacion}")
    print()
    print("Siguiente paso: ejecutar validacion_completa.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
