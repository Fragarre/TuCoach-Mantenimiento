#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, sqlite3, shutil
from pathlib import Path
from datetime import datetime
CAMBIOS = {2984: ('opcion_c', 'El órgano judicial que se estime incompetente para la resolución de un asunto remitirá directamente las actuaciones al órgano que considere competente, debiendo notificar esta circunstancia a los interesados', 'El órgano administrativo que se estime incompetente remitirá las actuaciones a su superior jerárquico para que determine el órgano competente.', 'opcion_a'), 3828: ('opcion_b', 'Es una forma voluntaria definitiva de provisión de puestos de trabajo que procede, en casos de urgente e inaplazable necesidad cuando concurran causas razonadas de interés público, cuando los puestos queden desiertos en las correspondientes convocatorias o se encuentren pendientes de su provisión definitiva o cuando estén sujetos a reserva por acuerdo del Consell.', 'La comisión de servicios es una forma obligatoria, temporal y excepcional de provisión de puestos de trabajo.', 'opcion_a'), 5234: ('opcion_c', 'Si el Congreso de los Diputados, por el voto de las tres cuartas partes de sus miembros, otorgare su confianza a dicho candidato, el Rey le nombrará Presidente. De no alcanzarse dicha mayoría, se someterá la misma propuesta a nueva votación cuarenta y ocho horas después de la anterior, y la confianza se entenderá otorgada si obtuviere la mayoría simple.', 'Si el Congreso, por el voto de tres quintos de sus miembros, otorgare su confianza al candidato, el Rey le nombrará Presidente; de no alcanzarse, se repetirá la votación 48 horas después y bastará mayoría simple.', 'opcion_a'), 10499: ('opcion_c', 'Tendrán derecho a solicitar el traslado provisional a otro puesto propio de su cuerpo, escala o agrupación profesional funcionarial o, en su caso, grupo profesional, de análogas características, siendo necesario que sea vacante de necesaria cobertura', 'Tendrán derecho a solicitar el traslado definitivo a otro puesto de análogas características, sin necesidad de que sea vacante de necesaria cobertura.', 'opcion_a'), 14100: ('opcion_c', 'A conocer, en un momento anterior al trámite de audiencia, el estado de la tramitación de los procedimientos en los que tengan la condición de interesados; el sentido del silencio administrativo que corresponda, en caso de que la Administración no dicte ni notifique resolución expresa en plazo; el órgano competente para su instrucción, en su caso, y resolución; y los actos de trámite dictados. Asimismo, también tendrán derecho a acceder y a obtener copia de los documentos contenidos en los citados procedimientos', 'A conocer únicamente después del trámite de audiencia el estado de tramitación del procedimiento y a obtener copia de los documentos contenidos en él.', 'opcion_a'), 16356: ('opcion_d', 'Tiene derecho a percibir las retribuciones complementarias y, en su caso, las prestaciones familiares por hijo a cargo. El tiempo que permanezcan en esta situación será computable a efectos de antigüedad y de derechos del Régimen de Seguridad Social que sea aplicable.', 'Tiene derecho a percibir las retribuciones básicas y complementarias y el tiempo en excedencia forzosa será computable a efectos de antigüedad y Seguridad Social.', 'opcion_a'), 17481: ('opcion_d', 'Cualquier persona podrá recabar de los tribunales la tutela del derecho a la igualdad entre mujeres y hombres, de acuerdo con lo establecido en el artículo 53.2 de la Constitución en cuanto contive vigente la relación en la que supuestamente se ha producido la discriminación.', 'Únicamente las personas físicas podrán recabar de los tribunales la tutela del derecho a la igualdad entre mujeres y hombres.', 'opcion_b'), 21465: ('opcion_d', 'Serán firmados por el Conseller o Consellers correspondientes y refrendados por el President.', 'Los Decretos del Consell serán firmados y refrendados por el President.', 'opcion_b')}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--bd",required=True)
    ap.add_argument("--aplicar",action="store_true")
    a=ap.parse_args(); db=Path(a.bd).resolve()
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row
    try:
        cols=[r[1] for r in con.execute("PRAGMA table_info(lote_preguntas)")]
        filas={}
        for qid,(campo,viejo,nuevo,gemela) in CAMBIOS.items():
            r=con.execute("SELECT * FROM lote_preguntas WHERE id=?",(qid,)).fetchone()
            if r is None or r[campo]!=viejo or r[gemela]!=viejo:
                raise SystemExit(f"SEGURIDAD: ID {qid} no coincide exactamente con el caso revisado. No se hace nada.")
            filas[qid]=dict(r)
        print("="*78); print("CORREGIR 8 DISTRACTORES DUPLICADOS"); print("="*78)
        print("Preguntas comprobadas....................... 8")
        print("Modo........................................ "+("ESCRITURA" if a.aplicar else "SOLO LECTURA"))
        print("Campos permitidos........................... una opción por pregunta")
        if not a.aplicar:
            print("\nNo se ha modificado la base de datos."); return
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        backup=db.with_name(db.stem+f"_backup_8_distractores_{stamp}"+db.suffix)
        shutil.copy2(db,backup); print(f"\nBackup: {backup}")
        con.execute("BEGIN IMMEDIATE")
        try:
            for qid,(campo,viejo,nuevo,gemela) in CAMBIOS.items():
                cur=con.execute(f'UPDATE lote_preguntas SET "{campo}"=? WHERE id=? AND "{campo}"=?',(nuevo,qid,viejo))
                if cur.rowcount!=1: raise RuntimeError(f"ID {qid}: actualización no unívoca.")
            for qid,(campo,viejo,nuevo,gemela) in CAMBIOS.items():
                ahora=dict(con.execute("SELECT * FROM lote_preguntas WHERE id=?",(qid,)).fetchone())
                antes=filas[qid]
                for c in cols:
                    esperado=nuevo if c==campo else antes[c]
                    if ahora[c]!=esperado: raise RuntimeError(f"ID {qid}: cambio inesperado en {c}.")
                opts=[ahora[x] for x in ("opcion_a","opcion_b","opcion_c","opcion_d")]
                if len(set(opts))!=4: raise RuntimeError(f"ID {qid}: siguen existiendo opciones duplicadas.")
            con.commit()
        except Exception:
            con.rollback(); raise
        print("Opciones modificadas........................ 8")
        print("Cambios fuera de la opción autorizada....... 0")
        print("Duplicados literales en estas 8 preguntas... 0")
        print("COMMIT....................................... OK")
        print("banco_preguntas.............................. SIN MODIFICAR")
    finally: con.close()
if __name__=="__main__": main()
