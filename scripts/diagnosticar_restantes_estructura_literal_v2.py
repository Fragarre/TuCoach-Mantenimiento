"""
Constructor Temario V2 - diagnóstico acotado de casos estructurales restantes.

SOLO LECTURA.

Comprueba exclusivamente:
- Constitución (norma 1): denominación real de capítulos del Título III;
- Ley 5/1983 (norma 49): fuentes disponibles y jerarquía reconstruible;
- TUE/TFUE (normas 84/83): fuentes disponibles y jerarquía reconstruible.

No consulta temario.csv ni temario_referencias. No modifica BD, corpus ni caché.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import comun
import auditar_representabilidad_estructural_online_v2 as online

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"
NORMAS = (1, 49, 84, 83)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    args = ap.parse_args()
    db = Path(args.db).expanduser().resolve()

    with comun.conectar_sqlite_solo_lectura(db) as con:
        for nid in NORMAS:
            norma = con.execute(
                "SELECT id, nombre_canonico FROM normas WHERE id=?", (nid,)
            ).fetchone()
            print("=" * 78)
            print(f"NORMA {nid}: {norma['nombre_canonico'] if norma else 'NO ENCONTRADA'}")
            fuentes = con.execute(
                """
                SELECT id_fuente, titulo_fuente
                FROM norma_fuentes
                WHERE norma_id=?
                ORDER BY id
                """,
                (nid,),
            ).fetchall()
            if not fuentes:
                print("  FUENTES: NINGUNA")
                continue

            for f in fuentes:
                fid = str(f["id_fuente"] or "")
                print(f"  FUENTE: {fid}")
                print(f"    titulo: {f['titulo_fuente'] or '-'}")
                payload, origen = online.cargar_payload(fid)
                print(f"    origen estructura: {origen}")
                if payload is None:
                    print("    estructura: NO DISPONIBLE")
                    continue
                r = payload.get("resumen", {})
                print(
                    "    estructura: "
                    f"titulos={r.get('titulos',0)} "
                    f"capitulos={r.get('capitulos',0)} "
                    f"secciones={r.get('secciones',0)} "
                    f"articulos={r.get('articulos',0)}"
                )

                if nid == 1:
                    caps = []
                    for a in payload.get("articulos", []):
                        if a.get("titulo") and "TÍTULO III" in a.get("titulo", "").upper():
                            c = a.get("capitulo")
                            if c and c not in caps:
                                caps.append(c)
                    print("    capítulos bajo Título III:")
                    for c in caps:
                        print(f"      - {c}")

                if nid in (49, 84, 83):
                    print("    títulos (máx. 20):")
                    for t in payload.get("titulos", [])[:20]:
                        print(f"      - {t}")
                    print("    capítulos (máx. 20):")
                    for c in payload.get("capitulos", [])[:20]:
                        print(f"      - {c}")

    print()
    print("No se ha consultado temario_referencias.")
    print("No se ha consultado temario.csv.")
    print("La base de datos NO ha sido modificada.")
    print("El corpus NO ha sido modificado.")
    print("La caché NO ha sido modificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
