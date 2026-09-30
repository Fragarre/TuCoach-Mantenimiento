"""Generador IA jurídico para las convocatorias Apoyo-*-AYT.

Reutiliza literalmente el motor de generación, doble validación, desempate,
auditoría probatoria y control de originalidad del generador jurídico común.
Solo adapta la obtención de contexto y la publicación para las convocatorias
municipales: su pertenencia al banco viene dada por AYTO-A1/A2/C1/C2, no por el
sincronizador genérico.

No hay ejecución implícita: --simular y --listar-referencias son de lectura.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import generar_preguntas_juridicas_ia as base


ROOT = Path(__file__).resolve().parents[1]
DB_DEFECTO = ROOT / "db" / "oposiciones.sqlite3"
ORIGENES = {
    "APOYO-A1-AYT": "AYTO-A1",
    "APOYO-A2-AYT": "AYTO-A2",
    "APOYO-C1-AYT": "AYTO-C1",
    "APOYO-C2-AYT": "AYTO-C2",
}
UMBRAL_SIMILITUD_BANCO = 0.965  # mismo umbral del control común de clonación

_cargar_ejemplos_base = base.cargar_ejemplos
_comprobar_duplicado_base = base.comprobar_no_duplicada
_aprobar_base = base.aprobar
_contexto_actual: base.ContextoReferencia | None = None


def _normalizar(valor: object) -> str:
    return re.sub(r"\s+", " ", str(valor or "").strip().casefold())


def _clave_pregunta(pregunta: dict[str, Any]) -> str:
    return " | ".join(
        _normalizar(pregunta.get(campo))
        for campo in ("enunciado", "opcion_a", "opcion_b", "opcion_c", "opcion_d")
    )


def _convocatoria(con: sqlite3.Connection, codigo: str) -> sqlite3.Row:
    fila = con.execute(
        "SELECT id, codigo FROM convocatorias WHERE UPPER(codigo)=?",
        (codigo.upper(),),
    ).fetchone()
    if fila is None or str(fila["codigo"]).upper() not in ORIGENES:
        raise RuntimeError("La convocatoria debe ser Apoyo-A1/A2/C1/C2-AYT.")
    return fila


def _origen(codigo: str) -> str:
    try:
        return ORIGENES[str(codigo).strip().upper()]
    except KeyError as exc:
        raise RuntimeError(f"Código municipal no reconocido: {codigo!r}") from exc


def cargar_contexto_ayto(
    con: sqlite3.Connection,
    convocatoria_id: int,
    referencia_id: int,
) -> base.ContextoReferencia:
    """Resuelve la norma desde la clave del CSV sin mutar temario_referencias."""
    fila = con.execute(
        """
        SELECT c.id AS convocatoria_id, c.codigo AS convocatoria_codigo,
               t.id AS temario_id, tr.id AS referencia_id, tr.tema_id,
               tt.parte, tt.numero_tema, tt.titulo AS titulo_tema,
               n.id AS norma_id, tr.nombre_norma_csv,
               tr.nombre_norma_normalizada, tr.articulo_solicitado,
               tr.articulo_fuente_id, af.articulo_boe,
               af.texto AS texto_articulo
        FROM convocatorias c
        JOIN temarios t ON t.convocatoria_id=c.id
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        JOIN articulos_fuente af ON af.id=tr.articulo_fuente_id
        JOIN normas n
          ON LOWER(TRIM(n.clave_normalizada))=
             LOWER(TRIM(tr.nombre_norma_normalizada))
        WHERE c.id=? AND tr.id=?
          AND UPPER(TRIM(tt.tipo_contenido))='JURIDICO'
        """,
        (convocatoria_id, referencia_id),
    ).fetchone()
    if fila is None:
        raise RuntimeError(
            "La referencia no pertenece al temario municipal, no tiene texto "
            "oficial o su norma no se resuelve en el catálogo."
        )
    datos = dict(fila)
    datos["origen_oposicion"] = _origen(str(datos["convocatoria_codigo"]))
    return base.ContextoReferencia(**datos)


def contextos_ayto(
    con: sqlite3.Connection,
    convocatoria_id: int,
    *,
    referencia_id: int | None,
    tema_id: int | None,
) -> list[base.ContextoReferencia]:
    filtros = ["tt.tipo_contenido='JURIDICO'", "tr.articulo_fuente_id IS NOT NULL"]
    params: list[object] = [convocatoria_id]
    if referencia_id is not None:
        filtros.append("tr.id=?")
        params.append(referencia_id)
    if tema_id is not None:
        filtros.append("tt.id=?")
        params.append(tema_id)
    filas = con.execute(
        """
        SELECT tr.id
        FROM temarios t
        JOIN temario_temas tt ON tt.temario_id=t.id
        JOIN temario_referencias tr ON tr.tema_id=tt.id
        JOIN normas n ON LOWER(TRIM(n.clave_normalizada))=
                        LOWER(TRIM(tr.nombre_norma_normalizada))
        WHERE t.convocatoria_id=? AND """ + " AND ".join(filtros) + " ORDER BY tr.id",
        params,
    ).fetchall()
    resultado = []
    for fila in filas:
        contexto = cargar_contexto_ayto(con, convocatoria_id, int(fila["id"]))
        if base.normalizar_articulo(contexto.articulo_solicitado) is not None:
            resultado.append(contexto)
    if not resultado:
        raise RuntimeError("No hay referencias jurídicas municipales utilizables en el ámbito indicado.")
    return resultado


def cargar_ejemplos_ayto(
    con: sqlite3.Connection,
    ctx: base.ContextoReferencia,
    max_ejemplos: int,
) -> list[dict[str, Any]]:
    """Prioriza mismo norma-artículo y usa el banco municipal como respaldo."""
    exactos = _cargar_ejemplos_base(con, ctx, max_ejemplos)
    if exactos:
        return exactos
    filas = con.execute(
        """
        SELECT lp.id, lp.enunciado, lp.opcion_a, lp.opcion_b, lp.opcion_c,
               lp.opcion_d, lp.respuesta_correcta, lp.articulo,
               lp.articulo_normalizado, lp.teorica_practica,
               lp.origen_oposicion, lp.tipo_fuente, lp.tipo_norma,
               lp.nombre_norma, lp.tipo_norma_normalizado,
               lp.nombre_norma_normalizado
        FROM banco_preguntas bp
        JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
        WHERE bp.convocatoria_id=? AND bp.estado='INCLUIDA'
          AND lp.tipo_clasificacion='JURIDICA'
        ORDER BY lp.id
        LIMIT ?
        """,
        (ctx.convocatoria_id, max_ejemplos),
    ).fetchall()
    return [dict(fila) for fila in filas]


def comprobar_no_duplicada_ayto(
    con: sqlite3.Connection,
    pregunta: dict[str, Any],
) -> None:
    """Impide duplicados exactos o prácticamente idénticos en el banco destino."""
    _comprobar_duplicado_base(con, pregunta)
    if _contexto_actual is None:
        return
    candidata = _clave_pregunta(pregunta)
    filas = con.execute(
        """
        SELECT lp.id, lp.enunciado, lp.opcion_a, lp.opcion_b, lp.opcion_c, lp.opcion_d
        FROM banco_preguntas bp
        JOIN lote_preguntas lp ON lp.id=bp.pregunta_id
        WHERE bp.convocatoria_id=? AND bp.estado='INCLUIDA'
        """,
        (_contexto_actual.convocatoria_id,),
    )
    for fila in filas:
        existente = _clave_pregunta(dict(fila))
        similitud = SequenceMatcher(None, candidata, existente).ratio()
        if similitud >= UMBRAL_SIMILITUD_BANCO:
            raise RuntimeError(
                "La candidata duplica o casi duplica la pregunta "
                f"{fila['id']} del banco municipal (similitud={similitud:.1%})."
            )


def _parte_para_contexto(con: sqlite3.Connection, ctx: base.ContextoReferencia) -> int:
    filas = con.execute(
        """
        SELECT cp.id, r.prioridad
        FROM convocatoria_partes cp
        JOIN convocatoria_parte_reglas r ON r.convocatoria_parte_id=cp.id
        WHERE cp.convocatoria_id=?
          AND UPPER(r.temario_parte)=UPPER(?)
          AND UPPER(r.tipo_contenido)='JURIDICO'
          AND UPPER(COALESCE(r.teorica_practica,''))='TEORICA'
        ORDER BY r.prioridad, cp.id
        """,
        (ctx.convocatoria_id, ctx.parte),
    ).fetchall()
    if not filas:
        raise RuntimeError("La referencia jurídica no tiene una parte de convocatoria compatible.")
    prioridad = int(filas[0]["prioridad"])
    partes = {int(fila["id"]) for fila in filas if int(fila["prioridad"]) == prioridad}
    if len(partes) != 1:
        raise RuntimeError("La referencia jurídica tiene reglas de parte ambiguas.")
    return next(iter(partes))


def aprobar_ayto(
    con: sqlite3.Connection,
    generacion_id: int,
    modo_publicacion: str = "HUMANA",
) -> int:
    """Publica con el motor común y materializa solo su banco municipal."""
    with base.conectar_auxiliar() as aux:
        generacion = aux.execute(
            "SELECT convocatoria_contexto_id, temario_referencia_id FROM generaciones_preguntas_ia WHERE id=?",
            (generacion_id,),
        ).fetchone()
    if generacion is None:
        raise RuntimeError("No existe la generación IA.")
    contexto = cargar_contexto_ayto(
        con, int(generacion["convocatoria_contexto_id"]), int(generacion["temario_referencia_id"])
    )
    global _contexto_actual
    anterior = _contexto_actual
    _contexto_actual = contexto
    try:
        pregunta_id = _aprobar_base(con, generacion_id, modo_publicacion)
    finally:
        _contexto_actual = anterior

    parte_id = _parte_para_contexto(con, contexto)
    con.execute("BEGIN IMMEDIATE")
    try:
        existe = con.execute(
            "SELECT id FROM banco_preguntas WHERE convocatoria_id=? AND pregunta_id=?",
            (contexto.convocatoria_id, pregunta_id),
        ).fetchone()
        if existe is None:
            cursor = con.execute(
                """
                INSERT INTO banco_preguntas
                (convocatoria_id, pregunta_id, convocatoria_parte_id,
                 tipo_vinculacion, estado, metodo_vinculacion, motivo_revision)
                VALUES (?, ?, ?, 'JURIDICA', 'INCLUIDA',
                        'ORIGEN_AYTO_NORMA_ARTICULO', NULL)
                """,
                (contexto.convocatoria_id, pregunta_id, parte_id),
            )
            con.execute(
                """
                INSERT INTO banco_preguntas_temas (banco_pregunta_id, tema_id, es_principal)
                VALUES (?, ?, 1)
                """,
                (int(cursor.lastrowid), contexto.tema_id),
            )
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("La vinculación municipal produciría errores de claves foráneas.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    return pregunta_id


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generación IA jurídica para Apoyo-*-AYT.")
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--codigo", required=True)
    seleccion = p.add_mutually_exclusive_group()
    seleccion.add_argument("--referencia-id", type=int)
    seleccion.add_argument("--tema-id", type=int)
    p.add_argument("--listar-referencias", action="store_true")
    p.add_argument("--simular", action="store_true")
    p.add_argument("--tipo", choices=("TEORICA",), default="TEORICA")
    p.add_argument("--cantidad", type=int, default=1)
    p.add_argument("--max-ejemplos", type=int, default=base.MAX_EJEMPLOS_DEFECTO)
    p.add_argument("--modelo-generacion", default=base.MODELO_DEFECTO)
    p.add_argument("--modelo-validacion", default=base.MODELO_DEFECTO)
    return p


def main() -> int:
    args = parser().parse_args()
    if args.cantidad <= 0 or args.max_ejemplos <= 0:
        raise RuntimeError("cantidad y max-ejemplos deben ser positivos.")
    ruta = Path(args.db).resolve()
    with base.conectar_maestra(ruta) as con:
        convocatoria = _convocatoria(con, args.codigo)
        contextos = contextos_ayto(
            con, int(convocatoria["id"]), referencia_id=args.referencia_id, tema_id=args.tema_id
        )
        print(f"Convocatoria: {convocatoria['codigo']} | origen: {_origen(convocatoria['codigo'])}")
        print(f"Referencias jurídicas utilizables: {len(contextos)}")
        if args.listar_referencias:
            for ctx in contextos:
                print(f"{ctx.referencia_id} | tema {ctx.numero_tema} | {ctx.nombre_norma_csv} | art. {ctx.articulo_solicitado}")
            return 0
        if args.simular:
            print("Simulación: no se invoca IA ni se modifica la base.")
            return 0

        # Se preserva el motor común: generación, doble check, desempate,
        # auditoría ciega y control de originalidad. Solo se intercepta la
        # publicación para usar el banco municipal y evitar la sincronización
        # genérica al cierre.
        base.cargar_contexto = cargar_contexto_ayto
        base.cargar_ejemplos = cargar_ejemplos_ayto
        base.comprobar_no_duplicada = comprobar_no_duplicada_ayto
        base.aprobar = aprobar_ayto
        base.generar_lote(
            con, contextos, args.cantidad, args.tipo,
            args.modelo_generacion, args.modelo_validacion,
            args.max_ejemplos, usar_modelo_examen=False,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
