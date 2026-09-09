"""
Constructor Temario V2 - diagnóstico conservador de normas implícitas.

SOLO LECTURA.

Objetivo: para temas que la extracción V2 deja en REVISION o para temas
JURIDICO sin cita literal, mostrar evidencia reutilizable de OTROS temarios ya
validados. No acepta ninguna norma, no resuelve artículos y no modifica datos.

La convocatoria usada como prueba debe excluirse expresamente para evitar
"copiar la respuesta" de su temario consolidado.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import comun
import extraer_estructura_temario_v2 as estructura
import resolver_normas_temario_v2 as resolver

RAIZ = comun.raiz_proyecto()
DB_PREDETERMINADA = RAIZ / "db" / "oposiciones.sqlite3"

STOP = {
    "de","del","la","las","el","los","y","e","o","u","a","al","en","por","para","con","sin",
    "un","una","unos","unas","su","sus","se","que","como","sobre","entre","i","ii","iii","iv",
}


def normalizar(texto: str) -> str:
    x = unicodedata.normalize("NFKD", texto or "")
    x = "".join(c for c in x if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", x).strip()


def tokens(texto: str) -> set[str]:
    return {t for t in normalizar(texto).split() if len(t) >= 4 and t not in STOP and not t.isdigit()}


def similitud(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cargar_evidencias(con, excluir_convocatoria: int):
    filas = con.execute(
        """
        SELECT c.id convocatoria_id, c.codigo convocatoria_codigo,
               tt.parte, tt.numero_tema, tt.titulo, n.id norma_id,
               n.nombre_canonico, COUNT(*) articulos
        FROM temario_temas tt
        JOIN temarios t ON t.id=tt.temario_id
        JOIN convocatorias c ON c.id=t.convocatoria_id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        JOIN normas n ON n.id=tr.norma_id
        WHERE c.id<>? AND tr.estado='COMPLETADO'
        GROUP BY c.id,c.codigo,tt.id,n.id,n.nombre_canonico
        ORDER BY c.id,tt.parte,tt.numero_tema,n.id
        """,
        (excluir_convocatoria,),
    ).fetchall()
    return filas


def main() -> int:
    ap = argparse.ArgumentParser(description="Propone evidencia normativa implícita sin modificar datos.")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--excluir-convocatoria", required=True, type=int,
                    help="ID de la convocatoria usada como prueba; sus mappings quedan fuera del diagnóstico")
    ap.add_argument("--db", default=str(DB_PREDETERMINADA))
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser().resolve()
    db = Path(args.db).expanduser().resolve()
    temas = estructura.extraer_temas(estructura.extraer_texto_pdf(pdf))
    for t in temas:
        estructura.clasificar_tema(t)

    pendientes = []
    for t in temas:
        if t.clasificacion == "REVISION":
            pendientes.append(t)
        elif t.clasificacion == "JURIDICO" and not resolver.detectar_citas(t.texto):
            pendientes.append(t)

    with comun.conectar_sqlite_solo_lectura(db) as con:
        evidencias = cargar_evidencias(con, args.excluir_convocatoria)

    print("=" * 78)
    print("CONSTRUCTOR TEMARIO V2 - NORMAS IMPLICITAS - DIAGNOSTICO SOLO LECTURA")
    print("=" * 78)
    print(f"PDF.............................. {pdf}")
    print(f"BD............................... {db}")
    print(f"Convocatoria excluida............ {args.excluir_convocatoria}")
    print(f"Temas pendientes analizados...... {len(pendientes)}")
    print("La convocatoria excluida NO participa como evidencia.")
    print()

    for tema in pendientes:
        por_norma = defaultdict(list)
        for r in evidencias:
            s = similitud(tema.texto, str(r["titulo"] or ""))
            if s > 0:
                por_norma[(int(r["norma_id"]), str(r["nombre_canonico"]))].append((s, r))

        ranking = []
        for (nid, nombre), items in por_norma.items():
            mejor = max(items, key=lambda x: x[0])
            ranking.append((mejor[0], nid, nombre, mejor[1]))
        ranking.sort(key=lambda x: (-x[0], x[1]))

        print(f"{tema.parte:<8} | {tema.numero_tema:>2} | {tema.clasificacion}")
        print(f"  {tema.texto}")
        if not ranking:
            print("  SIN EVIDENCIA REUTILIZABLE")
        else:
            for s, nid, nombre, r in ranking[:max(1,args.top)]:
                print(
                    f"  CANDIDATA [{nid}] {nombre} | similitud={s:.3f} | "
                    f"evidencia={r['convocatoria_codigo']} {r['parte']} {r['numero_tema']} | "
                    f"articulos_mapeados={r['articulos']}"
                )
                print(f"      tema evidencia: {r['titulo']}")
        print()

    print("No se ha modificado la base de datos.")
    print("Ninguna candidata ha sido aceptada automáticamente.")
    print("No se han resuelto artículos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
