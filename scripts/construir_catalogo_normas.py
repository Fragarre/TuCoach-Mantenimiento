"""
OpoCoach - Construcción del catálogo de normas.

Construye el catálogo desde:
- lote_preguntas: por clave textual normalizada;
- temario_referencias sin fuente resuelta: por clave textual normalizada;
- temario_referencias con articulo_fuente_id: prioritariamente por identidad
  documental persistida en norma_fuentes.

Es idempotente y no modifica lote_preguntas ni temario_referencias.
"""
from pathlib import Path
import sqlite3

from normalizador_normas import normalizar_norma
from norma_fuentes import asegurar_esquema, sembrar_desde_enlaces_existentes, completar_fuentes_temario

RUTA_BD = Path(__file__).resolve().parent.parent / 'db' / 'oposiciones.sqlite3'


def main() -> None:
    if not RUTA_BD.exists():
        raise FileNotFoundError(f'No existe la base de datos: {RUTA_BD}')

    with sqlite3.connect(RUTA_BD) as conexion:
        creada_tabla = asegurar_esquema(conexion)
        sembradas, conflictos_semilla = sembrar_desde_enlaces_existentes(conexion)
        if conflictos_semilla:
            raise RuntimeError(
                'Conflictos fuente->norma detectados al sembrar norma_fuentes:\n- '
                + '\n- '.join(conflictos_semilla[:20])
            )

        # Solo las fuentes sin identidad documental siguen dependiendo del texto.
        filas = conexion.execute(
            """
            SELECT DISTINCT nombre_norma_normalizado AS nombre
            FROM lote_preguntas
            WHERE nombre_norma_normalizado IS NOT NULL
              AND TRIM(nombre_norma_normalizado) <> ''

            UNION ALL

            SELECT DISTINCT tr.nombre_norma_normalizada AS nombre
            FROM temario_referencias tr
            WHERE tr.nombre_norma_normalizada IS NOT NULL
              AND TRIM(tr.nombre_norma_normalizada) <> ''
              AND tr.articulo_fuente_id IS NULL
            """
        ).fetchall()

        catalogo = {}
        for (nombre,) in filas:
            clave = normalizar_norma(nombre)
            if clave and clave not in catalogo:
                catalogo[clave] = nombre.strip()

        creadas_texto = 0
        for clave, nombre_canonico in sorted(catalogo.items()):
            conexion.execute(
                'INSERT OR IGNORE INTO normas(nombre_canonico,clave_normalizada) VALUES (?,?)',
                (nombre_canonico, clave),
            )
            if conexion.execute('SELECT changes()').fetchone()[0] == 1:
                creadas_texto += 1

        resumen_fuentes = completar_fuentes_temario(conexion)
        if resumen_fuentes['conflictos']:
            raise RuntimeError(
                f"Hay {resumen_fuentes['conflictos']} fuentes con conflicto de identidad normativa."
            )

        conexion.commit()
        total = conexion.execute('SELECT COUNT(*) FROM normas').fetchone()[0]
        total_fuentes = conexion.execute('SELECT COUNT(*) FROM norma_fuentes').fetchone()[0]

    print('Catálogo de normas construido.')
    print(f"Tabla norma_fuentes creada: {'SI' if creada_tabla else 'NO'}")
    print(f'Fuentes sembradas desde enlaces existentes: {sembradas}')
    print(f'Claves textuales detectadas: {len(catalogo)}')
    print(f'Normas nuevas por texto:     {creadas_texto}')
    print(f"Fuentes analizadas:           {resumen_fuentes['fuentes']}")
    print(f"Fuentes ya enlazadas:         {resumen_fuentes['ya_enlazadas']}")
    print(f"Fuentes enlazadas a norma existente: {resumen_fuentes['enlazadas_existentes']}")
    print(f"Normas nuevas desde fuente:   {resumen_fuentes['normas_creadas']}")
    print(f"Fuentes pendientes:           {resumen_fuentes['pendientes']}")
    print(f'Total en catálogo:            {total}')
    print(f'Total fuentes identificadas:  {total_fuentes}')


if __name__ == '__main__':
    main()
