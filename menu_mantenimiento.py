"""
TuCoach - menú de mantenimiento.

El cuerpo estable del menú se conserva en menu_mantenimiento_core.py.
Este punto de entrada añade extensiones controladas:
- auditoría de fidelidad PDF ↔ temario.csv;
- auditoría IA de simulacros ↔ PDF oficial del temario;
- mantenimiento integral de temario por convocatoria;
- corpus, normalización y reconciliación del banco.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import menu_mantenimiento_core as _base

for _nombre in dir(_base):
    if not _nombre.startswith("_"):
        globals().setdefault(_nombre, getattr(_base, _nombre))

_importar_temario_manual_base = _base.importar_temario_manual


def seleccionar_convocatoria_temario() -> tuple[str, Path] | None:
    """Selecciona una convocatoria activa y resuelve su temario.csv real."""
    db = _base.RAIZ / "db" / "oposiciones.sqlite3"
    if not db.is_file():
        print(f"\nERROR: no existe la base:\n{db}")
        return None

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        columnas = {r[1] for r in con.execute("PRAGMA table_info(convocatorias)")}
        if "codigo" not in columnas:
            print("\nERROR: convocatorias no contiene la columna codigo.")
            return None
        campos = ["id", "codigo"]
        if "puesto" in columnas:
            campos.append("puesto")
        if "temario_csv" in columnas:
            campos.append("temario_csv")
        sql = f"SELECT {', '.join(campos)} FROM convocatorias"
        params: tuple[object, ...] = ()
        if "activa" in columnas:
            sql += " WHERE COALESCE(activa,1)=1"
        sql += " ORDER BY id"
        filas = con.execute(sql, params).fetchall()

    if not filas:
        print("\nNo hay convocatorias activas.")
        return None

    print("\nConvocatorias activas")
    print("-" * 78)
    for i, fila in enumerate(filas, 1):
        puesto = str(fila["puesto"] or "").strip() if "puesto" in fila.keys() else ""
        sufijo = f" | {puesto}" if puesto else ""
        print(f"{i}. {fila['codigo']}{sufijo}")
    print("0. Volver")

    while True:
        op = input("Opción: ").strip()
        if op == "0":
            return None
        if op.isdigit() and 1 <= int(op) <= len(filas):
            fila = filas[int(op) - 1]
            codigo = str(fila["codigo"])
            ruta_guardada = (
                str(fila["temario_csv"] or "").strip()
                if "temario_csv" in fila.keys()
                else ""
            )
            ruta = Path(ruta_guardada) if ruta_guardada else Path("data_convocatorias") / f"CONV_{codigo}" / "temario.csv"
            if not ruta.is_absolute():
                ruta = _base.RAIZ / ruta
            ruta = ruta.resolve()
            if not ruta.is_file():
                print(f"\nERROR: no existe el temario.csv de {codigo}:\n{ruta}")
                return None
            return codigo, ruta
        print("Opción no válida.")


def mantener_temario_convocatoria_menu() -> None:
    _base.cabecera_submenu(
        "MANTENIMIENTO INTEGRAL DE TEMARIO",
        "[CSV → BD → CORPUS → NORMALIZACIÓN → BANCO → VALIDACIÓN] "
        "Propaga un temario.csv ya aprobado. lote_preguntas nunca se modifica.",
    )
    seleccion = seleccionar_convocatoria_temario()
    if seleccion is None:
        return
    codigo, ruta_csv = seleccion

    print("\nSelección")
    print("-" * 78)
    print(f"Convocatoria: {codigo}")
    print(f"Temario CSV:  {ruta_csv}")

    if _base.ejecutar_script(
        "orquestar_mantenimiento_temario.py",
        "--codigo", codigo,
        "--csv", str(ruta_csv),
    ) != 0:
        _base.pausa()
        return

    if not _base.pedir_si_no(
        "¿Aplicar el mantenimiento completo de esta convocatoria?"
    ):
        print("Operación cancelada. La base no ha sido modificada por el orquestador.")
        _base.pausa()
        return

    if _base.ejecutar_script(
        "orquestar_mantenimiento_temario.py",
        "--codigo", codigo,
        "--csv", str(ruta_csv),
        "--aplicar",
    ) != 0:
        print("\nEl mantenimiento se ha detenido por una incidencia.")
        _base.pausa()
        return

    print(f"\nRESULTADO: OK - mantenimiento integral de {codigo} completado.")
    _base.pausa()


def importar_temario_manual() -> None:
    while True:
        _base.cabecera_submenu(
            "IMPORTAR / SINCRONIZAR TEMARIO CSV",
            "El mantenimiento integral propaga un temario.csv aprobado a BD, corpus, normalización y banco. La importación manual avanzada se conserva.",
        )
        print("1. Mantenimiento integral de temario por convocatoria")
        print("2. Importar/sincronizar CSV manualmente [AVANZADO]")
        print("0. Volver")
        op = input("Opción: ").strip()
        if op == "0":
            return
        if op == "1":
            mantener_temario_convocatoria_menu()
        elif op == "2":
            _importar_temario_manual_base()
        else:
            print("Opción no válida.")


def auditar_fidelidad_temario_menu() -> None:
    _base.cabecera_submenu(
        "AUDITAR FIDELIDAD PDF ↔ TEMARIO.CSV",
        "[IA · CONSULTA OFICIAL · INFORME] Compara el PDF oficial con un temario.csv. Las diferencias confirmadas pueden aplicarse opcionalmente después de crear una copia del temario base. Las dudas nunca se aplican automáticamente.",
    )
    _base.ejecutar_script("auditar_fidelidad_temario.py")
    _base.pausa()


def auditar_simulacros_temario_oficial_menu() -> None:
    _base.cabecera_submenu(
        "AUDITAR SIMULACROS ↔ TEMARIO OFICIAL",
        "[IA · SOLO LECTURA] Compara directamente las preguntas de uno o varios "
        "simulacros PDF con el PDF oficial del temario. No usa temario.csv ni la "
        "base de datos. Genera informes JSON y HTML en auditorias/.",
    )
    print("Criterio conservador:")
    print("- OK: encaje razonable en un epígrafe oficial.")
    print("- DUDOSA: existe una duda real de inclusión.")
    print("- FUERA_TEMARIO: exclusión clara.")
    print()
    if _base.pedir_si_no("¿Iniciar la auditoría?"):
        _base.ejecutar_script("auditar_simulacros_temario_oficial.py")
    _base.pausa()


def submenu_auditorias() -> None:
    while True:
        _base.cabecera_submenu(
            "5. AUDITORÍAS Y DIAGNÓSTICO",
            "Herramientas de verificación. La validación completa es la prueba de regresión principal.",
        )
        print("1. Validación completa                                [SOLO LECTURA]")
        print("2. Auditoría general de la base                       [SOLO LECTURA]")
        print("3. Auditoría de selección de bancos                   [SOLO LECTURA]")
        print("4. Auditoría funcional de un banco                    [SOLO LECTURA]")
        print("5. Auditoría global lote ↔ banco                      [SOLO LECTURA]")
        print("6. Auditoría de estructura del banco                  [SOLO LECTURA]")
        print("7. Auditoría del corpus/temario                       [SOLO LECTURA]")
        print("8. Auditar posibles objetos obsoletos                 [SOLO LECTURA]")
        print("9. Inventariar denominaciones de normas               [SOLO LECTURA]")
        print("10. Buscar norma por respuesta correcta               [IA · DIAGNÓSTICO]")
        print("11. Auditar materiales de estudio                     [SOLO LECTURA]")
        print("12. Auditar fidelidad PDF ↔ temario.csv               [IA · INFORME → BACKUP/APLICAR]")
        print("13. Auditar simulacros ↔ temario oficial              [IA · SOLO LECTURA]")
        print("0. Volver")
        op = input("Opción: ").strip()
        if op == "0":
            return
        acciones = {
            "1": _base.validacion_completa,
            "2": _base.auditar_bd_directo,
            "3": _base.auditar_bancos_seleccion_menu,
            "4": _base.auditar_banco,
            "5": _base.auditar_consistencia_global_menu,
            "6": _base.auditar_estructura_banco_menu,
            "7": _base.auditar_corpus_temario_menu,
            "8": _base.auditar_esquema_menu,
            "9": _base.inventariar_normas_menu,
            "10": _base.buscar_norma_respuesta_correcta_menu,
            "11": _base.auditar_materiales_estudio_menu,
            "12": auditar_fidelidad_temario_menu,
            "13": auditar_simulacros_temario_oficial_menu,
        }
        fn = acciones.get(op)
        if fn:
            fn()
        else:
            print("Opción no válida.")


_base.importar_temario_manual = importar_temario_manual
_base.mantener_temario_convocatoria_menu = mantener_temario_convocatoria_menu
_base.submenu_auditorias = submenu_auditorias
_base.auditar_fidelidad_temario_menu = auditar_fidelidad_temario_menu
_base.auditar_simulacros_temario_oficial_menu = auditar_simulacros_temario_oficial_menu


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
