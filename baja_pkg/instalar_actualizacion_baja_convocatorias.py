from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

PAQUETE = Path(__file__).resolve().parent
RAIZ = Path.cwd().resolve()

ARCHIVOS = [
    (PAQUETE / 'menu_mantenimiento.py', RAIZ / 'menu_mantenimiento.py'),
    (PAQUETE / 'scripts' / 'gestionar_estado_convocatoria.py', RAIZ / 'scripts' / 'gestionar_estado_convocatoria.py'),
    (PAQUETE / 'scripts' / 'validacion_completa.py', RAIZ / 'scripts' / 'validacion_completa.py'),
    (PAQUETE / 'scripts' / 'mantener_banco_preguntas.py', RAIZ / 'scripts' / 'mantener_banco_preguntas.py'),
    (PAQUETE / 'scripts' / 'sincronizar_bancos.py', RAIZ / 'scripts' / 'sincronizar_bancos.py'),
    (PAQUETE / 'scripts' / 'auditar_banco_preguntas.py', RAIZ / 'scripts' / 'auditar_banco_preguntas.py'),
    (PAQUETE / 'scripts' / 'auditar_bancos_seleccion.py', RAIZ / 'scripts' / 'auditar_bancos_seleccion.py'),
    (PAQUETE / 'scripts' / 'configurar_modelo_examen.py', RAIZ / 'scripts' / 'configurar_modelo_examen.py'),
    (PAQUETE / 'scripts' / 'alta_convocatoria_orquestador.py', RAIZ / 'scripts' / 'alta_convocatoria_orquestador.py'),
    (PAQUETE / 'scripts' / 'actualizar_contenidos_supabase.py', RAIZ / 'scripts' / 'actualizar_contenidos_supabase.py'),
]


def main() -> int:
    if not (RAIZ / 'scripts').is_dir() or not (RAIZ / 'db').is_dir():
        raise RuntimeError(
            'Ejecute este instalador desde la raíz de OpoCoach-Mantenimiento. '
            'Deben existir las carpetas scripts y db.'
        )

    faltan_fuente = [str(src) for src, _ in ARCHIVOS if not src.is_file()]
    if faltan_fuente:
        raise RuntimeError('Faltan archivos del paquete:\n' + '\n'.join(faltan_fuente))

    # Los destinos existentes, salvo el script nuevo, son obligatorios.
    obligatorios = [dst for src, dst in ARCHIVOS if src.name != 'gestionar_estado_convocatoria.py']
    faltan_destino = [str(p) for p in obligatorios if not p.is_file()]
    if faltan_destino:
        raise RuntimeError(
            'No se instalará nada porque faltan archivos actuales esperados:\n'
            + '\n'.join(faltan_destino)
        )

    # Validación sintáctica completa antes de tocar el proyecto.
    for src, _ in ARCHIVOS:
        py_compile.compile(str(src), doraise=True)

    marca = datetime.now().strftime('%Y%m%d_%H%M%S')
    carpeta_backup = RAIZ / 'copias_actualizacion' / f'antes_baja_convocatorias_{marca}'
    carpeta_backup.mkdir(parents=True, exist_ok=False)

    copiados: list[tuple[Path, Path | None]] = []
    try:
        for src, dst in ARCHIVOS:
            previo = None
            if dst.exists():
                rel = dst.relative_to(RAIZ)
                previo = carpeta_backup / rel
                previo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, previo)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copiados.append((dst, previo))
    except Exception:
        for dst, previo in reversed(copiados):
            if previo and previo.exists():
                shutil.copy2(previo, dst)
            elif dst.exists():
                dst.unlink()
        raise

    print('=' * 78)
    print('ACTUALIZACIÓN DE BAJA/REACTIVACIÓN INSTALADA')
    print('=' * 78)
    print(f'Backup de scripts: {carpeta_backup}')
    print('La base de datos NO ha sido modificada.')
    print()
    print('Cambios principales:')
    print('- nueva opción de Baja / reactivar convocatoria en Menú 2;')
    print('- las validaciones/sincronizaciones globales procesan solo convocatorias activas;')
    print('- una baja elimina solo banco_preguntas de la convocatoria;')
    print('- lote, temario, reglas, modelo y corpus normativo/RAG se conservan;')
    print('- el alta no reactiva de forma implícita una convocatoria inactiva;')
    print('- Supabase admite la nueva columna activa cuando el snapshot la contiene.')
    print()
    print('La columna convocatorias.activa se crea únicamente al aplicar la primera baja.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
