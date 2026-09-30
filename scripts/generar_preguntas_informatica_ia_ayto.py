"""Genera informática IA para los tres temas de Apoyo-C1/C2-AYT.

No usa el plan fijo genérico ni el sincronizador común: deriva los temas del
temario y vincula cada pregunta publicada directamente a su banco municipal.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import generar_preguntas_informatica as base
from calibrar_dificultad_ia import calibrar, filtrar_candidatas_altas

ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
ORIGENES = {"APOYO-C1-AYT": "AYTO-C1", "APOYO-C2-AYT": "AYTO-C2"}
BOOTSTRAP = {
    "procesador de textos": ("OFIMATICA_WORD",),
    "hojas de calculo": ("OFIMATICA_EXCEL",),
    "correo electronico": ("OUTLOOK", "NAVEGADORES", "SEGURIDAD", "SISTEMA_OPERATIVO"),
}


def normalizar(texto: object) -> str:
    return base.normalizar_texto(str(texto or ""))


def convocatoria_y_temas(con: sqlite3.Connection, codigo: str) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    convocatoria = con.execute("SELECT id,codigo FROM convocatorias WHERE UPPER(codigo)=?", (codigo.upper(),)).fetchone()
    if convocatoria is None or str(convocatoria["codigo"]).upper() not in ORIGENES:
        raise RuntimeError("El código debe ser Apoyo-C1-AYT o Apoyo-C2-AYT.")
    temas = con.execute(
        """SELECT tt.id,tt.numero_tema,tt.titulo FROM temarios t
           JOIN temario_temas tt ON tt.temario_id=t.id
           WHERE t.convocatoria_id=? AND UPPER(tt.tipo_contenido)='INFORMATICA'
           ORDER BY tt.numero_tema,tt.id""", (int(convocatoria["id"]),)
    ).fetchall()
    if len(temas) != 3:
        raise RuntimeError("La convocatoria debe tener exactamente sus tres temas de informática.")
    return convocatoria, temas


def categorias_bootstrap(titulo: str) -> tuple[str, ...]:
    limpio = normalizar(titulo)
    for prefijo, categorias in BOOTSTRAP.items():
        if limpio.startswith(prefijo):
            return categorias
    raise RuntimeError(f"No existe equivalencia de arranque para el tema: {titulo}")


def muestra_calibracion(con: sqlite3.Connection, convocatoria_id: int, titulo: str) -> list[dict]:
    propias = [dict(fila) for fila in con.execute(
        """SELECT lp.id,lp.enunciado,lp.opcion_a,lp.opcion_b,lp.opcion_c,lp.opcion_d
           FROM banco_preguntas bp JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
           WHERE bp.convocatoria_id=? AND bp.estado='INCLUIDA'
             AND lp.tipo_clasificacion='INFORMATICA' AND UPPER(lp.tema_no_juridico)=UPPER(?)
           ORDER BY lp.id""", (convocatoria_id, titulo)
    )]
    if len(propias) >= 5:
        return propias
    categorias = categorias_bootstrap(titulo)
    marcadores = ",".join("?" for _ in categorias)
    return [dict(fila) for fila in con.execute(
        f"""SELECT id,enunciado,opcion_a,opcion_b,opcion_c,opcion_d FROM lote_preguntas
            WHERE tipo_clasificacion='INFORMATICA' AND UPPER(tema_no_juridico) IN ({marcadores})
            ORDER BY id""", categorias
    )]


def casi_duplicada(con: sqlite3.Connection, convocatoria_id: int, pregunta: base.Pregunta) -> bool:
    objetivo = " | ".join(normalizar(getattr(pregunta, campo)) for campo in ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d"))
    for fila in con.execute(
        """SELECT lp.enunciado,lp.opcion_a,lp.opcion_b,lp.opcion_c,lp.opcion_d
           FROM banco_preguntas bp JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
           WHERE bp.convocatoria_id=? AND bp.estado='INCLUIDA'""", (convocatoria_id,)
    ):
        existente = " | ".join(normalizar(valor) for valor in fila)
        if SequenceMatcher(None, objetivo, existente, autojunk=False).ratio() >= .965:
            return True
    return False


def enlazar_banco(con: sqlite3.Connection, convocatoria_id: int, importacion_id: int, tema_por_nombre: dict[str, int]) -> None:
    parte = con.execute(
        """SELECT cp.id FROM convocatoria_partes cp JOIN convocatoria_parte_reglas r
           ON r.convocatoria_parte_id=cp.id WHERE cp.convocatoria_id=?
           AND UPPER(r.tipo_contenido)='INFORMATICA' ORDER BY r.prioridad,cp.id LIMIT 1""",
        (convocatoria_id,)
    ).fetchone()
    if parte is None:
        raise RuntimeError("No existe parte de informática configurada.")
    for pregunta in con.execute("SELECT id,tema_no_juridico FROM lote_preguntas WHERE importacion_fichero_id=?", (importacion_id,)):
        tema_id = tema_por_nombre.get(str(pregunta["tema_no_juridico"]).upper())
        if tema_id is None:
            raise RuntimeError("La pregunta generada no corresponde a un tema de informática del temario.")
        cur = con.execute(
            """INSERT INTO banco_preguntas(convocatoria_id,pregunta_id,convocatoria_parte_id,tipo_vinculacion,estado,metodo_vinculacion,motivo_revision)
               VALUES(?,?,?,'INFORMATICA','INCLUIDA','ORIGEN_AYTO_TEMA_INFORMATICA',NULL)""",
            (convocatoria_id, int(pregunta["id"]), int(parte["id"])),
        )
        con.execute("INSERT INTO banco_preguntas_temas(banco_pregunta_id,tema_id,es_principal) VALUES(?,?,1)", (int(cur.lastrowid), tema_id))


def main() -> int:
    p = argparse.ArgumentParser(description="Informática IA para apoyo municipal.")
    p.add_argument("--db", type=Path, default=DB_DEFECTO)
    p.add_argument("--codigo", required=True)
    p.add_argument("--tema-id", type=int)
    p.add_argument("--cantidad", type=int, default=9)
    p.add_argument("--modelo", default=base.MODELO_PREDETERMINADO)
    p.add_argument("--guardar", action="store_true")
    p.add_argument("--simular", action="store_true")
    args = p.parse_args()
    if args.cantidad < 1:
        raise RuntimeError("cantidad debe ser positiva.")
    with sqlite3.connect(args.db) as con:
        con.row_factory = sqlite3.Row
        convocatoria, temas = convocatoria_y_temas(con, args.codigo)
        if args.tema_id is not None:
            temas = [tema for tema in temas if int(tema["id"]) == args.tema_id]
            if not temas:
                raise RuntimeError("El tema no es informático de esta convocatoria.")
        print(f"Convocatoria: {convocatoria['codigo']} | origen: {ORIGENES[str(convocatoria['codigo']).upper()]}")
        print("Temas: " + "; ".join(f"{t['numero_tema']}. {t['titulo']}" for t in temas))
        if args.simular:
            print("Simulación: no se invoca IA ni se modifica la base.")
            return 0
        from openai_api import seleccionar_fragmento_json
        todas: list[base.Pregunta] = []
        existentes = base.cargar_enunciados_existentes(con)
        generados_globales: set[str] = set()
        construir_prompt_base = base.construir_prompt
        nivel = "C1" if str(convocatoria["codigo"]).upper() == "APOYO-C1-AYT" else "C2"
        for tema in temas:
            muestras = muestra_calibracion(con, int(convocatoria["id"]), str(tema["titulo"]))
            perfil = calibrar(muestras, ambito=f"{convocatoria['codigo']} · {tema['titulo']}", modelo=args.modelo, seleccionar_json=seleccionar_fragmento_json)
            print(f"Tema {tema['numero_tema']}: p75={perfil.percentil_75}/4, p90={perfil.percentil_90}/4")
            anexo = "\n\n" + perfil.texto_prompt()
            base.construir_prompt = lambda *a, **k: (
                construir_prompt_base(*a, **k)
                .replace("grupo C2\nadministrativo", f"grupo {nivel}\nadministrativo")
                + anexo
            )
            generadas = base.generar_categoria(con, str(tema["titulo"]).upper(), [(str(tema["titulo"]), args.cantidad)], args.cantidad, base.TAMANO_LOTE_PREDETERMINADO, args.modelo, existentes, generados_globales)
            candidatas = [p.__dict__ for p in generadas if not casi_duplicada(con, int(convocatoria["id"]), p)]
            altas = filtrar_candidatas_altas(candidatas, perfil=perfil, modelo=args.modelo, seleccionar_json=seleccionar_fragmento_json)
            todas.extend(base.Pregunta(**p) for p in altas)
        if not todas:
            raise RuntimeError("No ha superado la calibración ninguna candidata.")
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        base.ORIGEN_OPOSICION_PREDETERMINADO = ORIGENES[str(convocatoria["codigo"]).upper()]
        ruta = base.guardar_csv(todas, base.AUDITORIAS / f"generar_informatica_ayto_{sello}")
        print(f"CSV de auditoría: {ruta}\nCandidatas altas: {len(todas)}")
        if not args.guardar:
            print("Vista previa: no se ha modificado la base.")
            return 0
        con.execute("BEGIN IMMEDIATE")
        try:
            imp = base.insertar_preguntas(con, todas, ruta, ORIGENES[str(convocatoria["codigo"]).upper()])
            enlazar_banco(con, int(convocatoria["id"]), imp, {str(t["titulo"]).upper(): int(t["id"]) for t in temas})
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("La publicación produciría errores de claves foráneas.")
            con.commit()
        except Exception:
            con.rollback(); raise
        print(f"Publicadas e incluidas en banco: {len(todas)}")
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc: print(f"ERROR: {exc}"); raise SystemExit(1)
