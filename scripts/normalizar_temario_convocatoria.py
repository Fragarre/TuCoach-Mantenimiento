"""
Normaliza la identidad normativa de una convocatoria sin tocar lote_preguntas.

Contrato:
- la identidad solicitada procede exclusivamente de nombre_norma_csv;
- una referencia sin norma_id se enlaza con la identidad exacta del CSV;
- una referencia con norma_id no se renormaliza por equivalencias generales;
- si una norma existente pasa de identidad corta a la misma identidad con fecha,
  se PRECISA LA NORMA EXISTENTE conservando su norma_id;
- nunca se crean dos normas para representar corta/fechada de la misma norma;
- si una base corta corresponde a varias identidades fechadas, no se fusiona
  automáticamente;
- cualquier otro cambio de identidad queda fuera de este proceso;
- la fuente documental no decide norma_id;
- una segunda ejecución correcta debe producir cero cambios funcionales.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import sqlite3

from normalizador_normas import identidad_sin_fecha, normalizar_norma
from norma_fuentes import asegurar_esquema

RUTA_BD = Path(__file__).resolve().parent.parent / "db" / "oposiciones.sqlite3"


def referencias_convocatoria(con: sqlite3.Connection, codigo: str):
    return con.execute(
        """
        SELECT tr.id,
               tr.nombre_norma_csv,
               tr.nombre_norma_normalizada,
               tr.norma_id,
               af.id_boe
        FROM temario_referencias tr
        JOIN temario_temas tt ON tt.id=tr.tema_id
        JOIN temarios t ON t.id=tt.temario_id
        JOIN convocatorias cv ON cv.id=t.convocatoria_id
        LEFT JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        WHERE cv.codigo=?
          AND tr.nombre_norma_csv IS NOT NULL
          AND TRIM(tr.nombre_norma_csv)<>''
        ORDER BY tr.id
        """,
        (codigo,),
    ).fetchall()


def cargar_catalogo(con: sqlite3.Connection):
    por_clave = {}
    por_id = {}
    por_base = defaultdict(list)

    for nid, nombre, clave in con.execute(
        """
        SELECT id, nombre_canonico, clave_normalizada
        FROM normas
        ORDER BY id
        """
    ):
        nid = int(nid)
        clave = str(clave or "").strip()
        nombre = str(nombre or "")

        if not clave:
            continue

        por_clave[clave] = (nid, nombre)
        por_id[nid] = clave
        por_base[identidad_sin_fecha(clave)].append((nid, clave))

    return por_clave, por_id, por_base


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codigo", required=True)
    ap.add_argument("--db", type=Path, default=RUTA_BD)
    args = ap.parse_args()

    if not args.db.exists():
        raise FileNotFoundError(f"No existe la base de datos: {args.db}")

    con = sqlite3.connect(args.db)

    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN")
        asegurar_esquema(con)

        refs = referencias_convocatoria(con, args.codigo)
        if not refs:
            raise RuntimeError(
                f"No hay referencias para la convocatoria {args.codigo}."
            )

        catalogo, catalogo_por_id, catalogo_por_base = cargar_catalogo(con)

        normas_creadas = 0
        normas_precisadas = 0
        referencias_actualizadas = 0
        bloqueadas = []

        for rid, nombre_csv, nombre_normalizado, actual, _id_fuente in refs:
            texto_csv = str(nombre_csv or "").strip()
            identidad_nueva = normalizar_norma(texto_csv)

            if not identidad_nueva:
                raise RuntimeError(
                    f"Identidad normativa vacia en {args.codigo}: "
                    f"referencia {rid}, {texto_csv!r}"
                )

            # ---------------------------------------------------------
            # Referencia todavía sin norma_id.
            # ---------------------------------------------------------
            if actual is None:
                destino = catalogo.get(identidad_nueva)

                if destino is None:
                    base = identidad_sin_fecha(identidad_nueva)
                    candidatos = catalogo_por_base.get(base, [])

                    # Si la identidad solicitada es fechada y existe
                    # exactamente una identidad corta para esa base,
                    # se precisa esa norma conservando su ID.
                    cortos = [
                        (nid, clave)
                        for nid, clave in candidatos
                        if clave == base
                    ]

                    fechados = [
                        (nid, clave)
                        for nid, clave in candidatos
                        if clave != base
                    ]

                    if (
                        identidad_nueva != base
                        and len(cortos) == 1
                        and len(fechados) == 0
                    ):
                        nid_nuevo = cortos[0][0]

                        con.execute(
                            """
                            UPDATE normas
                            SET nombre_canonico=?,
                                clave_normalizada=?
                            WHERE id=?
                            """,
                            (texto_csv, identidad_nueva, nid_nuevo),
                        )

                        normas_precisadas += 1

                        catalogo, catalogo_por_id, catalogo_por_base = (
                            cargar_catalogo(con)
                        )

                    elif candidatos:
                        bloqueadas.append(
                            (
                                rid,
                                None,
                                candidatos,
                                identidad_nueva,
                                "identidad no resoluble de forma univoca",
                            )
                        )
                        continue

                    else:
                        cur = con.execute(
                            """
                            INSERT INTO normas(
                                nombre_canonico,
                                clave_normalizada
                            )
                            VALUES (?,?)
                            """,
                            (texto_csv, identidad_nueva),
                        )
                        nid_nuevo = int(cur.lastrowid)
                        normas_creadas += 1

                        catalogo, catalogo_por_id, catalogo_por_base = (
                            cargar_catalogo(con)
                        )
                else:
                    nid_nuevo = destino[0]

                con.execute(
                    """
                    UPDATE temario_referencias
                    SET norma_id=?,
                        nombre_norma_normalizada=?
                    WHERE id=?
                    """,
                    (nid_nuevo, identidad_nueva, rid),
                )
                referencias_actualizadas += 1
                continue

            # ---------------------------------------------------------
            # Referencia ya normalizada.
            # ---------------------------------------------------------
            actual = int(actual)
            identidad_actual = catalogo_por_id.get(actual)

            if identidad_actual is None:
                bloqueadas.append(
                    (
                        rid,
                        actual,
                        None,
                        identidad_nueva,
                        "norma_id inexistente en catalogo",
                    )
                )
                continue

            if identidad_actual == identidad_nueva:
                # Mantener también coherente el campo textual.
                if str(nombre_normalizado or "") != identidad_nueva:
                    con.execute(
                        """
                        UPDATE temario_referencias
                        SET nombre_norma_normalizada=?
                        WHERE id=?
                        """,
                        (identidad_nueva, rid),
                    )
                    referencias_actualizadas += 1
                continue

            base_actual = identidad_sin_fecha(identidad_actual)
            base_nueva = identidad_sin_fecha(identidad_nueva)

            es_precision_por_fecha = (
                identidad_actual == base_actual
                and base_nueva == base_actual
                and identidad_nueva != base_nueva
            )

            if not es_precision_por_fecha:
                # Fuera del alcance de este proceso.
                continue

            # Si ya existe otra identidad fechada para esta base,
            # no podemos decidir que represente la misma norma.
            candidatos = catalogo_por_base.get(base_actual, [])
            otras_fechadas = [
                (nid, clave)
                for nid, clave in candidatos
                if nid != actual and clave != base_actual
            ]

            if otras_fechadas:
                bloqueadas.append(
                    (
                        rid,
                        actual,
                        otras_fechadas,
                        identidad_nueva,
                        "base con varias identidades normativas",
                    )
                )
                continue

            # PRECISAR LA NORMA EXISTENTE: nunca crear un nuevo ID.
            con.execute(
                """
                UPDATE normas
                SET nombre_canonico=?,
                    clave_normalizada=?
                WHERE id=?
                """,
                (texto_csv, identidad_nueva, actual),
            )

            normas_precisadas += 1

            # Todas las referencias de temario que ya usan ese mismo
            # norma_id deben reflejar la identidad ahora precisada.
            con.execute(
                """
                UPDATE temario_referencias
                SET nombre_norma_normalizada=?
                WHERE norma_id=?
                """,
                (identidad_nueva, actual),
            )

            catalogo, catalogo_por_id, catalogo_por_base = (
                cargar_catalogo(con)
            )

        if bloqueadas:
            raise RuntimeError(
                f"Normalizacion bloqueada para {args.codigo}: "
                f"{len(bloqueadas)} casos no resolubles automaticamente. "
                f"Primeros casos: {bloqueadas[:20]!r}"
            )

        nulos = con.execute(
            """
            SELECT COUNT(*)
            FROM temario_referencias tr
            JOIN temario_temas tt ON tt.id=tr.tema_id
            JOIN temarios t ON t.id=tt.temario_id
            JOIN convocatorias cv ON cv.id=t.convocatoria_id
            WHERE cv.codigo=?
              AND tr.norma_id IS NULL
            """,
            (args.codigo,),
        ).fetchone()[0]

        if nulos:
            raise RuntimeError(
                f"Postcondicion incumplida: {nulos} referencias "
                "con norma_id NULL."
            )

        con.commit()

        print(f"Normalizacion de temario: {args.codigo}")
        print(f"Referencias revisadas:      {len(refs)}")
        print(f"Normas nuevas:              {normas_creadas}")
        print(f"Normas precisadas:          {normas_precisadas}")
        print(f"Referencias actualizadas:   {referencias_actualizadas}")
        print(f"Referencias sin norma_id:   {nulos}")
        print("RESULTADO: OK")

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
