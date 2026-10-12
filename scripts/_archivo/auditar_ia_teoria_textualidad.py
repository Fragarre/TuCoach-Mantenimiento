from __future__ import annotations
import argparse, csv, re, sqlite3, unicodedata
from pathlib import Path
from datetime import datetime

RAIZ = Path(__file__).resolve().parents[1]
DB = RAIZ / "db" / "oposiciones.sqlite3"
SALIDA = RAIZ / "auditorias" / "calidad_juridica"

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = re.sub(r"[“”«»\"'´`]", "", s)
    s = re.sub(r"[^a-z0-9%]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def art(s):
    s = norm(s)
    s = re.sub(r"^(articulo|art)\s*", "", s).rstrip(".")
    return s

def main():
    ap = argparse.ArgumentParser(description="SOLO LECTURA: mide textualidad de la opción correcta en preguntas jurídicas IA teóricas.")
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--salida", type=Path, default=SALIDA)
    args = ap.parse_args()
    db = args.db.resolve()
    outdir = args.salida.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")

    filas = con.execute("""
        SELECT id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d,respuesta_correcta,
               tipo_fuente,origen_oposicion,teorica_practica,
               norma_id_normalizada,nombre_norma_normalizado,nombre_norma,
               articulo_normalizado,articulo
        FROM lote_preguntas
        WHERE tipo_clasificacion='JURIDICA'
          AND lower(coalesce(tipo_fuente,'')) LIKE '%ia%'
          AND upper(coalesce(teorica_practica,'TEORICA'))='TEORICA'
        ORDER BY id
    """).fetchall()

    fuentes = {}
    for r in con.execute("SELECT norma_id,id_fuente FROM norma_fuentes ORDER BY id"):
        fuentes.setdefault(int(r["norma_id"]), []).append(str(r["id_fuente"]))

    cache = {}
    resultados=[]
    for q in filas:
        letra=(q["respuesta_correcta"] or "").strip().upper()
        correcta=q[f"opcion_{letra.lower()}"] if letra in "ABCD" else ""
        nid=q["norma_id_normalizada"]
        objetivo=art(q["articulo_normalizado"] or q["articulo"])
        textos=[]
        if nid is not None:
            for fuente in fuentes.get(int(nid),[]):
                key=(fuente,objetivo)
                if key not in cache:
                    rr=con.execute("""
                        SELECT texto FROM articulos_fuente
                        WHERE id_boe=?
                    """,(fuente,)).fetchall()
                    cache[key]=[x["texto"] for x in rr if art(
                        con.execute("SELECT articulo_boe FROM articulos_fuente WHERE id_boe=? AND texto=? LIMIT 1",(fuente,x["texto"])).fetchone()[0]
                    )==objetivo] if rr else []
                textos.extend(cache[key])
        texto=" ".join(textos)
        localizado=bool(textos)
        textual= localizado and norm(correcta) in norm(texto) and bool(norm(correcta))
        resultados.append({
            "id":q["id"],"respuesta_correcta":letra,"opcion_correcta":correcta,
            "norma":q["nombre_norma_normalizado"] or q["nombre_norma"] or "",
            "articulo":q["articulo_normalizado"] or q["articulo"] or "",
            "texto_localizado":"SI" if localizado else "NO",
            "correcta_textual":"SI" if textual else "NO",
            "enunciado":q["enunciado"] or ""
        })
    con.close()

    total=len(resultados)
    loc=sum(r["texto_localizado"]=="SI" for r in resultados)
    tex=sum(r["correcta_textual"]=="SI" for r in resultados)
    no_tex=[r for r in resultados if r["texto_localizado"]=="SI" and r["correcta_textual"]=="NO"]
    marca=datetime.now().strftime("%Y%m%d_%H%M%S")
    csvp=outdir/f"ia_teoria_textualidad_{marca}.csv"
    if resultados:
        with csvp.open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=resultados[0].keys(),delimiter=";")
            w.writeheader(); w.writerows(resultados)

    print("="*78)
    print("AUDITORÍA IA JURÍDICA TEÓRICA - TEXTUALIDAD - SOLO LECTURA")
    print("="*78)
    print(f"Base: {db}")
    print(f"Preguntas IA jurídicas teóricas........... {total}")
    print(f"Con artículo localizado.................... {loc}")
    print(f"Opción correcta textual en el artículo..... {tex}")
    print(f"Artículo localizado pero correcta no textual {len(no_tex)}")
    print(f"Sin artículo localizado.................... {total-loc}")
    print()
    print(f"Informe: {csvp}")
    print("La base de datos NO ha sido modificada.")

if __name__=="__main__":
    main()
