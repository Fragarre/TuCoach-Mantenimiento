"""
NetReto - menú de mantenimiento.

El cuerpo estable del menú se conserva en menu_mantenimiento_core.py.
Este punto de entrada añade extensiones controladas sin modificar el menú estable:
- auditoría de fidelidad PDF ↔ temario.csv;
- sincronización determinista del temario C1-01_58_26;
- normalización normativa, corpus y reconciliación del banco.
"""
from __future__ import annotations

import menu_mantenimiento_core as _base

for _nombre in dir(_base):
    if not _nombre.startswith("_"):
        globals().setdefault(_nombre, getattr(_base, _nombre))

_importar_temario_manual_base = _base.importar_temario_manual


def sincronizar_temario_c1_58_26_menu() -> None:
    codigo = "C1-01_58_26"
    ruta_csv = "data_convocatorias/CONV_C1-01_58_26/temario.csv"
    _base.cabecera_submenu(
        "SINCRONIZAR TEMARIO C1-01_58_26",
        "[REVISIÓN → BACKUP/APLICAR → BD → CORPUS → NORMALIZACIÓN → BANCO → VALIDACIÓN] "
        "Reconcilia temario.csv con el conjunto jurídico validado. lote_preguntas nunca se modifica.",
    )

    if _base.ejecutar_script("sincronizar_temario_c1_58_26.py") != 0:
        _base.pausa(); return
    if not _base.pedir_si_no("¿Aplicar exactamente las altas y bajas mostradas y continuar el proceso completo?"):
        _base.pausa(); return
    if _base.ejecutar_script("sincronizar_temario_c1_58_26.py", "--aplicar") != 0:
        _base.pausa(); return
    if _base.ejecutar_script(
        "importar_temario.py", "--convocatoria", codigo, "--csv", ruta_csv, "--sincronizar-eliminaciones"
    ) != 0:
        _base.pausa(); return

    # El corpus resuelve articulo_fuente_id. Después se fija la identidad normativa
    # reutilizando norma_fuentes/normas; este paso no toca lote_preguntas.
    if _base.ejecutar_script("construir_corpus_doble_convocatoria.py", "--codigo", codigo, "--aplicar") != 0:
        _base.pausa(); return
    if _base.ejecutar_script("normalizar_temario_convocatoria.py", "--codigo", codigo) != 0:
        print("\nLa sincronización se detiene: quedan identidades normativas ambiguas o pendientes.")
        _base.pausa(); return

    # Exige conjunto exacto, norma_id completo y corpus 1.340/1.340.
    if _base.ejecutar_script("validar_temario_c1_58_26_db.py") != 0:
        print("\nLa sincronización C1 no se considera terminada: temario, normalización o corpus incompletos.")
        _base.pausa(); return

    # Primero se retiran SOLO vínculos del banco que ya no cumplen el temario.
    # lote_preguntas permanece intacto. Después se incorporan las nuevas coincidencias.
    if _base.ejecutar_script(
        "reconciliar_sobrantes_banco.py",
        "--db", "db/oposiciones.sqlite3",
        "--constructor", "scripts/mantener_banco_preguntas.py",
        "--codigo", codigo,
        "--guardar",
    ) != 0:
        _base.pausa(); return
    if _base.ejecutar_script("mantener_banco_preguntas.py", "--codigo", codigo, "--guardar") != 0:
        _base.pausa(); return

    # Postcondiciones de idempotencia del banco: cero sobrantes y cero nuevas.
    if _base.ejecutar_script(
        "reconciliar_sobrantes_banco.py",
        "--db", "db/oposiciones.sqlite3",
        "--constructor", "scripts/mantener_banco_preguntas.py",
        "--codigo", codigo,
    ) != 0:
        print("\nERROR: el banco conserva vínculos sobrantes tras la reconciliación.")
        _base.pausa(); return
    if _base.ejecutar_script("mantener_banco_preguntas.py", "--codigo", codigo) != 0:
        print("\nERROR: el banco no ha quedado estable tras la actualización.")
        _base.pausa(); return
    if _base.ejecutar_script("validar_temario_c1_58_26_db.py") != 0:
        _base.pausa(); return

    print("\nRESULTADO C1: OK - temario, corpus, normalización y banco reconciliados.")
    print("lote_preguntas: NO MODIFICADO por este flujo.")
    _base.pausa()


def importar_temario_manual() -> None:
    while True:
        _base.cabecera_submenu(
            "IMPORTAR / SINCRONIZAR TEMARIO CSV",
            "Primero ofrece el proceso determinista validado para C1-01_58_26. La importación manual avanzada original se mantiene disponible.",
        )
        print("1. Sincronizar C1-01_58_26 [CSV → BD → CORPUS → NORMALIZACIÓN → BANCO]")
        print("2. Importar/sincronizar CSV manualmente [AVANZADO]")
        print("0. Volver")
        op = input("Opción: ").strip()
        if op == "0": return
        if op == "1": sincronizar_temario_c1_58_26_menu()
        elif op == "2": _importar_temario_manual_base()
        else: print("Opción no válida.")


def auditar_fidelidad_temario_menu() -> None:
    _base.cabecera_submenu(
        "AUDITAR FIDELIDAD PDF ↔ TEMARIO.CSV",
        "[IA · CONSULTA OFICIAL · INFORME] Compara el PDF oficial con un temario.csv. Las diferencias confirmadas pueden aplicarse opcionalmente después de crear una copia del temario base. Las dudas nunca se aplican automáticamente.",
    )
    _base.ejecutar_script("auditar_fidelidad_temario.py")
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
        print("0. Volver")
        op = input("Opción: ").strip()
        if op == "0": return
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
        }
        fn = acciones.get(op)
        if fn: fn()
        else: print("Opción no válida.")


_base.importar_temario_manual = importar_temario_manual
_base.sincronizar_temario_c1_58_26_menu = sincronizar_temario_c1_58_26_menu
_base.submenu_auditorias = submenu_auditorias
_base.auditar_fidelidad_temario_menu = auditar_fidelidad_temario_menu


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
