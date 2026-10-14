"""
TuCoach - Generación IA desatendida para rellenar huecos del banco.

Repite tandas del generador en modo --rellenar-huecos, convocatoria a
convocatoria, hasta cumplir un límite. No contiene lógica de generación propia:
cada tanda es una llamada a scripts/generar_preguntas_juridicas_ia.py.

SEGURIDAD
---------
- Sin --ejecutar solo muestra el plan (convocatorias, referencias generables y
  coste estimado). No llama a la IA ni escribe en la base.
- Copia de seguridad con fecha de db/oposiciones.sqlite3 antes de empezar.
- Evita que Windows suspenda el equipo mientras se ejecuta.
- Se detiene si ocurre cualquiera de estas condiciones:
    * gasto acumulado >= --tope-usd (se comprueba antes de cada tanda);
    * tiempo transcurrido >= --max-horas;
    * --max-fallos tandas consecutivas con error;
    * tasa de rechazo > --max-rechazo en una tanda (si tiene >= 10 candidatas);
    * errores de saldo/cuota de OpenAI.
- Es reanudable: el modo de huecos solo mira referencias que siguen sin pregunta.

Ejemplos (desde la raíz de TuCoach-Mantenimiento):

    python scripts\\generar_huecos_desatendido.py
    python scripts\\generar_huecos_desatendido.py --ejecutar --tope-usd 15
    python scripts\\generar_huecos_desatendido.py --ejecutar --convocatorias 2 1
"""

from __future__ import annotations

import argparse
import ctypes
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
GENERADOR = SCRIPTS / "generar_preguntas_juridicas_ia.py"
REGISTROS = RAIZ / "registros"

# Orden por defecto: de menor a mayor hueco (A1-ADM antes que CCJJ: tras la
# ejecución del 13/10 es la que conserva más huecos generables). No incluye los bancos municipales
# Modelo de Examen-*-AYT (ids 4-7): su banco solo admite origen AYTO-examen.
CONVOCATORIAS_DEFECTO = (2, 1, 3, 8)

COSTE_ESTIMADO_POR_CANDIDATA_USD = 0.0045
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

RE_COSTE = re.compile(r"^Coste\.+\s*\$([0-9]+(?:\.[0-9]+)?)", re.MULTILINE)
RE_ESTADO = re.compile(r"^\s*Generaci\S{1,3}n\s+\d+\s+\|.*\|\s*(\w+)\s*\|", re.MULTILINE)
RE_ERROR_CUOTA = re.compile(
    r"insufficient_quota|billing|exceeded your current quota|invalid_api_key",
    re.IGNORECASE,
)


def evitar_suspension(activar: bool) -> None:
    """Pide a Windows que no suspenda el equipo (no hace nada en otros sistemas)."""
    if sys.platform != "win32":
        return
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if activar else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


def generables(db: Path, convocatoria_id: int) -> tuple[int, str]:
    """Referencias sin pregunta con fuente suficiente. Solo lectura."""
    sys.path.insert(0, str(SCRIPTS))
    import generar_preguntas_juridicas_ia as g  # noqa: PLC0415

    with g.conectar_maestra(db) as con:
        codigo = con.execute(
            "SELECT codigo FROM convocatorias WHERE id=?", (convocatoria_id,)
        ).fetchone()
        if codigo is None:
            raise RuntimeError(f"No existe la convocatoria {convocatoria_id}.")
        contextos, _ = g.obtener_referencias_huecos(con, convocatoria_id)
        validos = g.filtrar_contextos_por_fuente(contextos, "TEORICA")
    return len(validos), str(codigo["codigo"])


def huecos_todas(db: Path) -> list[tuple[int, str, int, int, int]]:
    """
    Huecos reales de TODAS las convocatorias (solo lectura).

    Un hueco es una referencia norma-artículo del temario sin ninguna pregunta
    INCLUIDA en el banco de esa convocatoria, vengan de donde vengan.
    Devuelve (id, código, referencias, sin_pregunta, generables).
    """
    sys.path.insert(0, str(SCRIPTS))
    import generar_preguntas_juridicas_ia as g  # noqa: PLC0415

    resultado = []
    with g.conectar_maestra(db) as con:
        convs = con.execute("SELECT id, codigo FROM convocatorias ORDER BY id").fetchall()
        for c in convs:
            cid = int(c["id"])
            cubiertas = set()
            for f in con.execute(
                """
                SELECT DISTINCT lp.norma_id_normalizada AS n, lp.articulo_normalizado AS a
                FROM banco_preguntas bp JOIN lote_preguntas lp ON lp.id = bp.pregunta_id
                WHERE bp.convocatoria_id = ? AND bp.estado = 'INCLUIDA'
                  AND lp.tipo_clasificacion = 'JURIDICA' AND lp.norma_id_normalizada IS NOT NULL
                """,
                (cid,),
            ):
                art = g.normalizar_articulo(f["a"])
                if art is not None:
                    cubiertas.add((int(f["n"]), art))
            refs = sin = gen = 0
            for f in con.execute(
                """
                SELECT tr.norma_id, tr.articulo_solicitado, af.texto
                FROM temarios t
                JOIN temario_temas tt ON tt.temario_id = t.id
                JOIN temario_referencias tr ON tr.tema_id = tt.id
                JOIN articulos_fuente af ON af.id = tr.articulo_fuente_id
                WHERE t.convocatoria_id = ? AND tr.norma_id IS NOT NULL
                """,
                (cid,),
            ):
                refs += 1
                art = g.normalizar_articulo(f["articulo_solicitado"])
                if art is not None and (int(f["norma_id"]), art) in cubiertas:
                    continue
                sin += 1
                texto = f["texto"] or ""
                if len(texto) <= g.MAX_CHARS_FUENTE_HUECOS and g.fuente_oficial_suficiente(texto, "TEORICA"):
                    gen += 1
            resultado.append((cid, str(c["codigo"]), refs, sin, gen))
    return resultado


def imprimir_huecos(db: Path, seleccion: list[int], escribir=print) -> int:
    filas = huecos_todas(db)
    escribir("HUECOS REALES POR CONVOCATORIA (referencias sin ninguna pregunta en su banco)")
    escribir(f"  {'id':>3} {'convocatoria':<26} {'refs':>6} {'sin preg.':>10} {'generables':>11}  en el plan")
    total = 0
    for cid, codigo, refs, sin, gen in filas:
        marca = "sí" if cid in seleccion else "-"
        escribir(f"  {cid:>3} {codigo[:26]:<26} {refs:>6} {sin:>10} {gen:>11}  {marca}")
        total += gen
    return total


def analizar_salida(texto: str) -> dict[str, float | int | bool]:
    aprobadas = sum(1 for e in RE_ESTADO.findall(texto) if e.upper() == "APROBADA")
    rechazadas = sum(1 for e in RE_ESTADO.findall(texto) if e.upper().startswith("RECHAZADA"))
    coste = sum(float(c) for c in RE_COSTE.findall(texto))
    errores = len(re.findall(r"^\s*ERROR:", texto, re.MULTILINE))
    return {
        "aprobadas": aprobadas,
        "rechazadas": rechazadas,
        "errores": errores,
        "coste": coste,
        "cuota": bool(RE_ERROR_CUOTA.search(texto)),
        "sin_huecos": "No hay referencias sin pregunta" in texto,
    }


class Registro:
    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta
        ruta.parent.mkdir(parents=True, exist_ok=True)

    def escribir(self, texto: str) -> None:
        linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {texto}"
        print(linea, flush=True)
        with self.ruta.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DB_DEFECTO))
    p.add_argument("--convocatorias", type=int, nargs="+", default=list(CONVOCATORIAS_DEFECTO))
    p.add_argument("--tanda", type=int, default=50, help="Candidatas por tanda.")
    p.add_argument("--tope-usd", type=float, default=15.0)
    p.add_argument("--max-horas", type=float, default=6.0)
    p.add_argument("--max-fallos", type=int, default=3)
    p.add_argument("--max-rechazo", type=float, default=0.5)
    p.add_argument("--objetivo-por-convocatoria", type=int, default=0,
                   help="Máximo de candidatas por convocatoria (0 = hasta agotar huecos).")
    p.add_argument("--ejecutar", action="store_true", help="Sin esta opción solo se muestra el plan.")
    args = p.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1
    if args.tanda <= 0 or args.tope_usd <= 0:
        print("ERROR: --tanda y --tope-usd deben ser positivos.")
        return 1

    print("=" * 78)
    print("PLAN DE GENERACIÓN DESATENDIDA")
    print("=" * 78)
    imprimir_huecos(db, args.convocatorias)
    print()
    total = 0
    plan: list[tuple[int, str, int]] = []
    for cid in args.convocatorias:
        n, codigo = generables(db, cid)
        plan.append((cid, codigo, n))
        total += n
        print(f"  convocatoria {cid:>2} | {codigo:<20} | referencias generables: {n:>5}")
    estimado = total * COSTE_ESTIMADO_POR_CANDIDATA_USD
    print(f"\nTotal de referencias generables.... {total}")
    print(f"Coste estimado (1 candidata/ref)... {estimado:.2f} USD")
    print(f"Tope configurado................... {args.tope_usd:.2f} USD")
    print(f"Tanda............................... {args.tanda} candidatas")
    if not args.ejecutar:
        print("\nMODO PLAN: no se ha llamado a la IA ni modificado ningún archivo.")
        print("Para ejecutar de verdad añada --ejecutar.")
        return 0

    marca = datetime.now().strftime("%Y%m%d_%H%M")
    registro = Registro(REGISTROS / f"huecos_desatendido_{marca}.log")
    salida_completa = REGISTROS / f"huecos_desatendido_{marca}.salida.txt"

    copia = db.with_name(f"{db.stem}_antes_desatendido_{marca}{db.suffix}")
    shutil.copy2(db, copia)
    registro.escribir(f"Copia de seguridad: {copia.name} ({copia.stat().st_size} bytes)")
    registro.escribir(
        f"Inicio. Tope {args.tope_usd:.2f} USD · máx. {args.max_horas} h · tanda {args.tanda}"
    )

    evitar_suspension(True)
    inicio = time.time()
    gastado = 0.0
    aprobadas_total = 0
    rechazadas_total = 0
    fallos_seguidos = 0
    motivo_parada = "todas las convocatorias completadas"

    try:
        for cid, codigo, _ in plan:
            enviadas_conv = 0
            sin_progreso = 0
            restantes_previos: int | None = None
            registro.escribir(f"--- Convocatoria {cid} ({codigo}) ---")
            while True:
                if (time.time() - inicio) / 3600 >= args.max_horas:
                    motivo_parada = "tiempo máximo alcanzado"
                    raise StopIteration
                cantidad = args.tanda
                if args.objetivo_por_convocatoria:
                    cantidad = min(cantidad, args.objetivo_por_convocatoria - enviadas_conv)
                    if cantidad <= 0:
                        registro.escribir(f"Objetivo de {args.objetivo_por_convocatoria} alcanzado para {codigo}.")
                        break
                restantes, _ = generables(db, cid)
                if restantes == 0:
                    registro.escribir(f"{codigo}: sin referencias generables pendientes.")
                    break
                # Una tanda que no evalúa nada y no reduce los huecos se repetiría
                # indefinidamente (13/10/2026: 514 tandas sobre 1 referencia de CCJJ).
                if restantes_previos is not None and restantes >= restantes_previos and sin_progreso >= 2:
                    registro.escribir(
                        f"{codigo}: {restantes} referencia(s) no generables tras "
                        f"{sin_progreso} tandas sin progreso; se pasa a la siguiente convocatoria."
                    )
                    break
                cantidad = min(cantidad, restantes)
                previsto = cantidad * COSTE_ESTIMADO_POR_CANDIDATA_USD
                if gastado + previsto > args.tope_usd:
                    motivo_parada = f"tope de gasto ({gastado:.2f} + {previsto:.2f} > {args.tope_usd:.2f} USD)"
                    raise StopIteration

                registro.escribir(f"Tanda de {cantidad} · pendientes {restantes} · gastado {gastado:.2f} USD")
                r = subprocess.run(
                    [sys.executable, str(GENERADOR), "--db", str(db), "--convocatoria-id", str(cid),
                     "--rellenar-huecos", "--cantidad", str(cantidad)],
                    cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                texto = (r.stdout or "") + "\n" + (r.stderr or "")
                with salida_completa.open("a", encoding="utf-8") as f:
                    f.write(f"\n{'=' * 30} {codigo} {datetime.now():%H:%M:%S} {'=' * 30}\n{texto}")

                a = analizar_salida(texto)
                gastado += float(a["coste"])
                aprobadas_total += int(a["aprobadas"])
                rechazadas_total += int(a["rechazadas"])
                enviadas_conv += cantidad
                registro.escribir(
                    f"  -> aprobadas {a['aprobadas']} · rechazadas {a['rechazadas']} · "
                    f"errores {a['errores']} · coste {a['coste']:.4f} USD · código {r.returncode}"
                )

                try:
                    pendientes_global = [
                        f"{cod}:{gen}" for _, cod, _, _, gen in huecos_todas(db)
                    ]
                    registro.escribir("  Huecos generables ahora -> " + " · ".join(pendientes_global))
                except Exception as exc:  # el informe nunca debe detener la generación
                    registro.escribir(f"  (No se pudo calcular el informe global: {exc})")

                if a["cuota"]:
                    motivo_parada = "error de saldo/cuota/clave de OpenAI"
                    raise StopIteration
                if r.returncode != 0 or (a["errores"] and not a["aprobadas"] and not a["rechazadas"]):
                    fallos_seguidos += 1
                    registro.escribir(f"  Fallo {fallos_seguidos}/{args.max_fallos}")
                    if fallos_seguidos >= args.max_fallos:
                        motivo_parada = f"{args.max_fallos} tandas consecutivas con error"
                        raise StopIteration
                    time.sleep(30)
                    continue
                fallos_seguidos = 0

                evaluadas = int(a["aprobadas"]) + int(a["rechazadas"])
                if evaluadas == 0 and int(a["aprobadas"]) == 0:
                    sin_progreso += 1
                else:
                    sin_progreso = 0
                restantes_previos = restantes
                if evaluadas >= 10 and int(a["rechazadas"]) / evaluadas > args.max_rechazo:
                    motivo_parada = (
                        f"tasa de rechazo anormal ({a['rechazadas']}/{evaluadas}) en {codigo}"
                    )
                    raise StopIteration
                if a["sin_huecos"]:
                    break
    except StopIteration:
        pass
    except KeyboardInterrupt:
        motivo_parada = "interrumpido manualmente"
    finally:
        evitar_suspension(False)

    horas = (time.time() - inicio) / 3600
    registro.escribir("=" * 60)
    registro.escribir(f"FIN: {motivo_parada}")
    registro.escribir(
        f"Aprobadas {aprobadas_total} · rechazadas {rechazadas_total} · "
        f"gastado {gastado:.2f} USD · {horas:.2f} h"
    )
    registro.escribir("Estado final de los huecos:")
    try:
        imprimir_huecos(db, args.convocatorias, escribir=registro.escribir)
    except Exception as exc:
        registro.escribir(f"(No se pudo calcular el informe final: {exc})")
    registro.escribir(f"Registro: {registro.ruta.name} · salida completa: {salida_completa.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
