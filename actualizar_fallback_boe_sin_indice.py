from __future__ import annotations

from pathlib import Path
import shutil
from datetime import datetime

RAIZ = Path(__file__).resolve().parent
SCRIPTS = RAIZ / "scripts"

CAMBIOS = {
    "ampliar_corpus_chat.py": [
        (
            '            if id_boe in DATOS_IDS_VERIFICADOS:\n'
            '                resumen["sin_indice"] += 1\n'
            '                resumen["guardados"] += int(fila["filas"])\n'
            '                print("  Estado: EXCLUIDO_SIN_INDICE_CONSOLIDADO")\n'
            '                continue',
            '            if id_boe in DATOS_IDS_VERIFICADOS:\n'
            '                resumen["sin_indice"] += 1\n'
            '                resumen["guardados"] += int(fila["filas"])\n'
            '                resumen["errores"] += 1\n'
            '                print("  Estado: SIN_INDICE_CONSOLIDADO")\n'
            '                print(\n'
            '                    "  El proveedor BOE no puede verificar la norma completa; "\n'
            '                    "se requiere fallback PDF local."\n'
            '                )\n'
            '                continue'
        ),
        (
            '    print(f"EXCLUIDO_SIN_INDICE_CONSOLIDADO:        {resumen[\'sin_indice\']}")',
            '    print(f"SIN_INDICE_CONSOLIDADO_REQUIERE_FALLBACK: {resumen[\'sin_indice\']}")'
        ),
    ],
    "pdf_normas.py": [
        (
            '    m = re.search(r"(?im)^\\s*Referencia:\\s*(BOE-A-\\d{4}-\\d+)\\s*$", cabecera)\n'
            '    if m:\n'
            '        return m.group(1).upper()\n'
            '    m = re.search(r"(?im)^\\s*CVE:\\s*(DOGV-(?:[A-Z]-)?\\d{4}-\\d+)\\b", cabecera)',
            '    m = re.search(r"(?im)^\\s*Referencia:\\s*(BOE-A-\\d{4}-\\d+)\\s*$", cabecera)\n'
            '    if m:\n'
            '        return m.group(1).upper()\n'
            '    # Los PDF oficiales del BOE publicados en el diario llevan el identificador\n'
            '    # como CVE en el pie, aunque no incluyan una línea "Referencia:".\n'
            '    m = re.search(r"(?im)\\bcve:\\s*(BOE-A-\\d{4}-\\d+)\\b", cabecera)\n'
            '    if m:\n'
            '        return m.group(1).upper()\n'
            '    m = re.search(r"(?im)^\\s*CVE:\\s*(DOGV-(?:[A-Z]-)?\\d{4}-\\d+)\\b", cabecera)'
        ),
        (
            '    if id_fuente.startswith("BOE-A-"):\n'
            '        for linea in lineas[:30]:\n'
            '            if linea in {"Jefatura del Estado", "Ministerio de la Presidencia", "Cortes Generales"}:\n'
            '                return linea\n'
            '        return "Boletín Oficial del Estado"',
            '    if id_fuente.startswith("BOE-A-"):\n'
            '        # En disposiciones autonómicas publicadas también en el BOE, el\n'
            '        # departamento jurídico es la comunidad autónoma, no el propio BOE.\n'
            '        for linea in lineas[:30]:\n'
            '            n = normalizar(linea)\n'
            '            if n == "comunitat valenciana":\n'
            '                return "Comunitat Valenciana"\n'
            '            if linea in {"Jefatura del Estado", "Ministerio de la Presidencia", "Cortes Generales"}:\n'
            '                return linea\n'
            '        return "Boletín Oficial del Estado"'
        ),
    ],
}

def copia_seguridad(ruta: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = ruta.with_name(f"{ruta.stem}_backup_{marca}{ruta.suffix}")
    shutil.copy2(ruta, destino)
    return destino

def main() -> int:
    if not SCRIPTS.is_dir():
        raise RuntimeError(
            "Ejecute este archivo desde la raíz de OpoCoach-Mantenimiento; "
            "debe existir la carpeta scripts."
        )

    rutas = {nombre: SCRIPTS / nombre for nombre in CAMBIOS}
    for nombre, ruta in rutas.items():
        if not ruta.is_file():
            raise FileNotFoundError(f"No existe: {ruta}")

    originales = {nombre: ruta.read_text(encoding="utf-8") for nombre, ruta in rutas.items()}
    nuevos = {}

    # Validar TODOS los bloques antes de escribir ningún archivo.
    for nombre, pares in CAMBIOS.items():
        texto = originales[nombre]
        for viejo, nuevo in pares:
            veces = texto.count(viejo)
            if veces != 1:
                raise RuntimeError(
                    f"{nombre}: se esperaba exactamente una coincidencia del bloque; "
                    f"encontradas: {veces}. No se modifica nada."
                )
            texto = texto.replace(viejo, nuevo, 1)
        nuevos[nombre] = texto

    copias = {nombre: copia_seguridad(ruta) for nombre, ruta in rutas.items()}

    try:
        for nombre, ruta in rutas.items():
            temporal = ruta.with_suffix(ruta.suffix + ".tmp")
            temporal.write_text(nuevos[nombre], encoding="utf-8")
            temporal.replace(ruta)
    except Exception:
        for nombre, ruta in rutas.items():
            shutil.copy2(copias[nombre], ruta)
        raise

    print("=" * 78)
    print("ACTUALIZACIÓN APLICADA")
    print("=" * 78)
    print("ampliar_corpus_chat.py:")
    print("  - BOE sin índice consolidado ya no se considera validado.")
    print("  - fuerza el fallback PDF local del orquestador.")
    print("pdf_normas.py:")
    print("  - reconoce CVE BOE-A-* en PDF oficiales.")
    print("  - identifica Comunitat Valenciana como departamento cuando corresponde.")
    print()
    for nombre, copia in copias.items():
        print(f"Backup {nombre}: {copia}")
    print()
    print("La base de datos NO ha sido modificada.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
