from pathlib import Path
import shutil
from datetime import datetime

RAIZ = Path(__file__).resolve().parent
RUTA = RAIZ / "scripts" / "gestionar_estado_convocatoria.py"

VIEJO = '''        "docs_rag": int(con.execute("SELECT COUNT(*) FROM convocatoria_documentos_corpus WHERE convocatoria_id=?", (cid,)).fetchone()[0]) if "convocatoria_documentos_corpus" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} else 0,
'''

NUEVO = '''        # El Chat/RAG vigente no usa convocatoria_documentos_corpus como
        # vínculo efectivo. Su ámbito se deriva del temario: cada norma COMPLETADA
        # de la convocatoria habilita su fuente normativa completa en articulos_fuente.
        "normas_rag": int(
            con.execute(
                """
                SELECT COUNT(DISTINCT tr.norma_id)
                FROM temario_referencias tr
                JOIN temario_temas tt ON tt.id = tr.tema_id
                JOIN temarios t ON t.id = tt.temario_id
                WHERE t.convocatoria_id = ?
                  AND tr.estado = 'COMPLETADO'
                  AND tr.norma_id IS NOT NULL
                """,
                (cid,),
            ).fetchone()[0]
        ),
        "fuentes_rag": int(
            con.execute(
                """
                SELECT COUNT(DISTINCT af.id_boe)
                FROM temario_referencias tr
                JOIN temario_temas tt ON tt.id = tr.tema_id
                JOIN temarios t ON t.id = tt.temario_id
                JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
                WHERE t.convocatoria_id = ?
                  AND tr.estado = 'COMPLETADO'
                  AND af.texto IS NOT NULL
                  AND TRIM(af.texto) <> ''
                """,
                (cid,),
            ).fetchone()[0]
        ),
'''

VIEJO_PRINT = '''        print(f"Referencias del temario: {d['referencias']} | Documentos RAG vinculados: {d['docs_rag']}")
'''

NUEVO_PRINT = '''        print(
            f"Referencias del temario: {d['referencias']} | "
            f"Normas RAG del temario: {d['normas_rag']} | "
            f"Fuentes RAG enlazadas: {d['fuentes_rag']}"
        )
'''


def main() -> int:
    if not RUTA.is_file():
        raise FileNotFoundError(f"No existe: {RUTA}")

    texto = RUTA.read_text(encoding="utf-8")
    if texto.count(VIEJO) != 1 or texto.count(VIEJO_PRINT) != 1:
        raise RuntimeError(
            "El script instalado no coincide exactamente con la versión esperada. "
            "No se ha modificado nada."
        )

    nuevo = texto.replace(VIEJO, NUEVO, 1).replace(VIEJO_PRINT, NUEVO_PRINT, 1)

    copia_dir = RAIZ / "copias_actualizacion"
    copia_dir.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    copia = copia_dir / f"gestionar_estado_convocatoria_antes_corregir_rag_{marca}.py"
    shutil.copy2(RUTA, copia)

    temporal = RUTA.with_suffix(".py.tmp")
    temporal.write_text(nuevo, encoding="utf-8")
    temporal.replace(RUTA)

    print("=" * 78)
    print("DIAGNÓSTICO RAG DE BAJA CORREGIDO")
    print("=" * 78)
    print(f"Backup: {copia}")
    print("La base de datos NO ha sido modificada.")
    print("Ahora el diagnóstico cuenta las normas/fuentes RAG reales derivadas del temario.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
