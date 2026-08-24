"""
OpoCoach-Mantenimiento - Configuración explícita de reglas de partes.

Permite mantener convocatoria_parte_reglas desde menú, sin editar SQLite a mano.
No infiere reglas por el nombre de las partes. Toda escritura crea backup.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_DEFECTO = RAIZ / "db" / "oposiciones.sqlite3"
COPIAS = RAIZ / "db" / "copias_seguridad"


def conectar(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def backup(db: Path) -> Path:
    COPIAS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destino = COPIAS / f"{db.stem}_antes_reglas_partes_{marca}{db.suffix}"
    shutil.copy2(db, destino)
    return destino


def elegir_convocatoria(con: sqlite3.Connection) -> sqlite3.Row | None:
    filas = con.execute(
        "SELECT id,codigo,puesto FROM convocatorias ORDER BY id"
    ).fetchall()
    print("\nCONVOCATORIAS")
    print("-" * 78)
    for f in filas:
        print(f"{f['id']:>3}. {f['codigo']} | {f['puesto']}")
    print("  0. Cancelar")
    while True:
        v = input("Convocatoria ID: ").strip()
        if v == "0": return None
        if v.isdigit():
            r = con.execute(
                "SELECT id,codigo,puesto FROM convocatorias WHERE id=?", (int(v),)
            ).fetchone()
            if r is not None: return r
        print("ID no válido.")


def partes(con: sqlite3.Connection, cid: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT id,nombre,orden,numero_preguntas FROM convocatoria_partes "
        "WHERE convocatoria_id=? ORDER BY orden,id", (cid,)
    ).fetchall()


def reglas(con: sqlite3.Connection, cid: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT r.id, r.convocatoria_parte_id, cp.nombre AS parte_nombre,
               r.prioridad, r.temario_parte, r.tipo_contenido,
               r.teorica_practica, r.tema_no_juridico
        FROM convocatoria_parte_reglas r
        JOIN convocatoria_partes cp ON cp.id=r.convocatoria_parte_id
        WHERE cp.convocatoria_id=?
        ORDER BY cp.orden,r.prioridad,r.id
        """, (cid,)
    ).fetchall()


def mostrar_reglas(con: sqlite3.Connection, cid: int) -> None:
    rs = reglas(con, cid)
    print("\nREGLAS ACTUALES")
    print("-" * 78)
    if not rs:
        print("No hay reglas configuradas.")
        return
    for r in rs:
        print(
            f"ID {r['id']:>3} | {r['parte_nombre']} | prioridad={r['prioridad']} | "
            f"temario_parte={r['temario_parte'] or '*'} | "
            f"tipo={r['tipo_contenido'] or '*'} | "
            f"teorica_practica={r['teorica_practica'] or '*'} | "
            f"tema_no_juridico={r['tema_no_juridico'] or '*'}"
        )


def elegir_parte(con: sqlite3.Connection, cid: int) -> sqlite3.Row | None:
    ps = partes(con, cid)
    print("\nPARTES")
    print("-" * 78)
    for n,p in enumerate(ps,1):
        print(f"{n}. {p['nombre']} ({p['numero_preguntas']} preguntas) [id={p['id']}]")
    print("0. Cancelar")
    while True:
        v=input("Parte: ").strip()
        if v=="0": return None
        if v.isdigit() and 1 <= int(v) <= len(ps): return ps[int(v)-1]
        print("Opción no válida.")


def opciones_temario(con: sqlite3.Connection, cid: int, campo: str) -> list[str]:
    if campo not in {"parte","tipo_contenido","tema_no_juridico"}:
        raise ValueError(campo)
    rows=con.execute(
        f"""
        SELECT DISTINCT TRIM(COALESCE(tt.{campo},'')) AS v
        FROM temario_temas tt
        JOIN temarios t ON t.id=tt.temario_id
        WHERE t.convocatoria_id=? AND TRIM(COALESCE(tt.{campo},''))<>''
        ORDER BY v COLLATE NOCASE
        """, (cid,)
    ).fetchall()
    return [str(r['v']) for r in rows]


def elegir_valor(titulo: str, valores: list[str], permitir_cualquiera: bool=True) -> str | None:
    print(f"\n{titulo}")
    print("-"*78)
    inicio=1
    if permitir_cualquiera:
        print("0. CUALQUIERA (*)")
    for i,v in enumerate(valores,inicio): print(f"{i}. {v}")
    while True:
        x=input("Opción: ").strip()
        if permitir_cualquiera and x=="0": return None
        if x.isdigit() and 1 <= int(x) <= len(valores): return valores[int(x)-1]
        print("Opción no válida.")


def pedir_regla(con: sqlite3.Connection, cid: int) -> dict:
    temario_parte=elegir_valor("PARTE DEL TEMARIO", opciones_temario(con,cid,"parte"))
    tipo=elegir_valor("TIPO DE CONTENIDO", opciones_temario(con,cid,"tipo_contenido"))
    tp=elegir_valor("TEÓRICA / PRÁCTICA", ["TEORICA","PRACTICA"])
    tema = None
    if str(tipo or "").upper() in {"NO_JURIDICO", "INFORMATICA"}:
        valor = input("Tema no jurídico concreto [INTRO = cualquiera]: ").strip()
        tema = valor or None
    while True:
        p=input("Prioridad [10]: ").strip() or "10"
        if p.isdigit() and int(p)>0: prioridad=int(p); break
        print("Debe ser un entero positivo.")
    return dict(prioridad=prioridad,temario_parte=temario_parte,
                tipo_contenido=tipo,teorica_practica=tp,tema_no_juridico=tema)


def cobertura(con: sqlite3.Connection, cid: int, regla: dict) -> tuple[int,int]:
    cond=["t.convocatoria_id=?"]
    par=[cid]
    mapa={"temario_parte":"parte","tipo_contenido":"tipo_contenido","tema_no_juridico":"tema_no_juridico"}
    for k,col in mapa.items():
        if regla[k] is not None:
            cond.append(f"UPPER(TRIM(COALESCE(tt.{col},'')))=UPPER(?)")
            par.append(regla[k])
    row=con.execute(
        f"""
        SELECT COUNT(DISTINCT tt.id) temas, COUNT(tr.id) refs
        FROM temarios t JOIN temario_temas tt ON tt.temario_id=t.id
        LEFT JOIN temario_referencias tr ON tr.tema_id=tt.id
        WHERE {' AND '.join(cond)}
        """, par
    ).fetchone()
    return int(row['temas']),int(row['refs'])


def confirmar(texto: str) -> bool:
    return input(texto+" [s/N]: ").strip().lower() in {"s","si","sí","y","yes"}


def guardar_regla(con: sqlite3.Connection, db: Path, parte_id: int, regla: dict, reemplazar: bool) -> None:
    if reemplazar:
        actuales=con.execute("SELECT COUNT(*) FROM convocatoria_parte_reglas WHERE convocatoria_parte_id=?",(parte_id,)).fetchone()[0]
        print(f"Se eliminarán {actuales} regla(s) actuales de esta parte antes de insertar la nueva.")
    if not confirmar("¿Guardar esta regla?"):
        print("Sin cambios."); return
    copia=backup(db)
    try:
        con.execute("BEGIN")
        if reemplazar:
            con.execute("DELETE FROM convocatoria_parte_reglas WHERE convocatoria_parte_id=?",(parte_id,))
        con.execute(
            """INSERT INTO convocatoria_parte_reglas
               (convocatoria_parte_id,prioridad,temario_parte,tipo_contenido,theorica_practica,tema_no_juridico)
               VALUES (?,?,?,?,?,?)""".replace("theorica_practica","teorica_practica"),
            (parte_id,regla['prioridad'],regla['temario_parte'],regla['tipo_contenido'],regla['teorica_practica'],regla['tema_no_juridico'])
        )
        con.commit()
    except Exception:
        con.rollback(); raise
    print(f"Backup: {copia}")
    print("Regla guardada correctamente.")


def eliminar_regla(con: sqlite3.Connection, db: Path, cid: int) -> None:
    mostrar_reglas(con,cid)
    v=input("ID de regla a eliminar [0=cancelar]: ").strip()
    if v=="0": return
    if not v.isdigit(): print("ID no válido."); return
    row=con.execute(
        """SELECT r.id FROM convocatoria_parte_reglas r JOIN convocatoria_partes cp ON cp.id=r.convocatoria_parte_id
           WHERE r.id=? AND cp.convocatoria_id=?""",(int(v),cid)).fetchone()
    if row is None: print("La regla no pertenece a esta convocatoria."); return
    if not confirmar("¿Eliminar esta regla?"): return
    copia=backup(db)
    con.execute("DELETE FROM convocatoria_parte_reglas WHERE id=?",(int(v),)); con.commit()
    print(f"Backup: {copia}")
    print("Regla eliminada.")


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",type=Path,default=DB_DEFECTO)
    args=ap.parse_args()
    with conectar(args.db) as con:
        conv=elegir_convocatoria(con)
        if conv is None: return 0
        cid=int(conv['id'])
        while True:
            print("\n"+"="*78)
            print(f"REGLAS DE PARTES | {conv['codigo']} | {conv['puesto']}")
            print("="*78)
            mostrar_reglas(con,cid)
            print("\n1. Añadir una regla")
            print("2. Reemplazar las reglas de una parte por una regla nueva")
            print("3. Eliminar una regla")
            print("0. Volver")
            op=input("Opción: ").strip()
            if op=="0": return 0
            if op in {"1","2"}:
                parte=elegir_parte(con,cid)
                if parte is None: continue
                regla=pedir_regla(con,cid)
                temas,refs=cobertura(con,cid,regla)
                print("\nREGLA PROPUESTA")
                print("-"*78)
                print(f"Parte examen:       {parte['nombre']}")
                print(f"Prioridad:          {regla['prioridad']}")
                print(f"Parte temario:      {regla['temario_parte'] or '*'}")
                print(f"Tipo contenido:     {regla['tipo_contenido'] or '*'}")
                print(f"Teórica/práctica:   {regla['teorica_practica'] or '*'}")
                print(f"Tema no jurídico:   {regla['tema_no_juridico'] or '*'}")
                print(f"Cobertura temario:  {temas} temas | {refs} referencias")
                if temas==0:
                    print("ERROR: la regla no cubre ningún tema del temario. No se guardará.")
                    continue
                guardar_regla(con,args.db,int(parte['id']),regla,op=="2")
            elif op=="3":
                eliminar_regla(con,args.db,cid)
            else:
                print("Opción no válida.")

if __name__ == "__main__":
    raise SystemExit(main())
