"""
Auditoría conservadora de posibles objetos obsoletos de oposiciones.sqlite3.

SOLO LECTURA respecto de la base de datos. No elimina, altera ni migra nada.
Genera informes en auditorias/esquema_obsoleto/ por defecto.

La clasificación es deliberadamente provisional: una tabla nunca se marca como
"ELIMINAR" automáticamente. El objetivo es reunir evidencia antes de cualquier
limpieza física del esquema.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sqlite3
import shutil
import sys
import warnings
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

EXTENSIONES_CODIGO = {".py"}
PATRONES_HISTORICOS = (
    "_old", "old_", "_antes_", "antes_", "corregir_", "migrar_",
    "reparar_", "actualizar_resolvedor", "estrategia2", "estrategia3",
)

@dataclass
class RefCodigo:
    proyecto: str
    archivo: str
    linea: int
    texto: str
    ambito: str  # activo / auxiliar / historico / desconocido
    contexto_bd: str  # maestra / usuario / indeterminado

@dataclass
class ObjetoAuditado:
    nombre: str
    tipo: str
    tabla_base: str | None
    filas: int | None
    bytes_aprox: int | None
    fk_salientes: int
    fk_entrantes: int
    indices_asociados: int
    triggers_asociados: int
    refs_activas_mantenimiento: int
    refs_historicas_mantenimiento: int
    refs_opocoach_maestra: int
    refs_opocoach_usuario: int
    refs_opocoach_indeterminadas: int
    clasificacion: str
    motivo: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audita posibles objetos obsoletos sin modificar la BD.")
    p.add_argument("--db", default="db/oposiciones.sqlite3", help="Ruta a oposiciones.sqlite3")
    p.add_argument("--raiz-mantenimiento", default=None,
                   help="Raíz OpoCoach-Mantenimiento. Por defecto se deduce desde el script.")
    p.add_argument("--raiz-opocoach", default=None,
                   help="Raíz del proyecto OpoCoach. Si se omite, intenta ../OpoCoach.")
    p.add_argument("--menu", default=None,
                   help="Ruta a menu_mantenimiento.py; por defecto <raiz-mantenimiento>/menu_mantenimiento.py")
    p.add_argument("--salida", default=None,
                   help="Carpeta de informes. Por defecto auditorias/esquema_obsoleto")
    return p.parse_args()


def resolver_rutas(args: argparse.Namespace) -> tuple[Path, Path, Path | None, Path, Path]:
    script = Path(__file__).resolve()
    raiz_mant = Path(args.raiz_mantenimiento).expanduser().resolve() if args.raiz_mantenimiento else script.parent.parent.resolve()
    db = Path(args.db).expanduser()
    if not db.is_absolute():
        db = (raiz_mant / db).resolve()
    raiz_opo: Path | None
    if args.raiz_opocoach:
        raiz_opo = Path(args.raiz_opocoach).expanduser().resolve()
    else:
        candidato = (raiz_mant.parent / "OpoCoach").resolve()
        raiz_opo = candidato if candidato.is_dir() else None
    menu = Path(args.menu).expanduser().resolve() if args.menu else (raiz_mant / "menu_mantenimiento.py").resolve()
    if args.salida:
        salida = Path(args.salida).expanduser()
        if not salida.is_absolute():
            salida = (raiz_mant / salida).resolve()
    else:
        salida = raiz_mant / "auditorias" / "esquema_obsoleto"
        if salida.exists():
            shutil.rmtree(salida)
    return raiz_mant, db, raiz_opo, menu, salida


def leer_texto(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            pass
    return path.read_text(encoding="utf-8", errors="replace")


def es_historico(path: Path) -> bool:
    n = path.name.casefold()
    return any(p in n for p in PATRONES_HISTORICOS)


def scripts_menu(menu: Path, scripts_dir: Path) -> set[Path]:
    if not menu.is_file():
        return set()
    txt = leer_texto(menu)
    nombres = set(re.findall(r'["\']([A-Za-z0-9_\-]+\.py)["\']', txt))
    return {(scripts_dir / n).resolve() for n in nombres if (scripts_dir / n).is_file()}


def imports_locales(path: Path, scripts_dir: Path) -> set[Path]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(leer_texto(path), filename=str(path))
    except Exception:
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    out = set()
    for m in mods:
        p = scripts_dir / f"{m}.py"
        if p.is_file():
            out.add(p.resolve())
    return out


def construir_scripts_activos(menu: Path, scripts_dir: Path) -> set[Path]:
    activos = scripts_menu(menu, scripts_dir)
    q = deque(activos)
    while q:
        p = q.popleft()
        deps = set(imports_locales(p, scripts_dir))
        # Los orquestadores llaman otros scripts por nombre mediante subprocess.
        # Se incluyen también esos destinos para no confundirlos con auxiliares.
        try:
            txt = leer_texto(p)
            for n in re.findall(r'["\']([A-Za-z0-9_\-]+\.py)["\']', txt):
                qpath = (scripts_dir / n).resolve()
                if qpath.is_file():
                    deps.add(qpath)
        except Exception:
            pass
        for dep in deps:
            if dep not in activos:
                activos.add(dep)
                q.append(dep)
    return activos


def archivos_py(raiz: Path | None) -> list[Path]:
    if raiz is None or not raiz.is_dir():
        return []
    excl = {".venv", "venv", "__pycache__", ".git", "historico_scripts"}
    out = []
    for p in raiz.rglob("*.py"):
        if any(part in excl for part in p.parts):
            continue
        out.append(p)
    return out


def contexto_bd_opocoach(path: Path, texto: str, linea: int) -> str:
    """Intenta identificar la conexión usada por la función que contiene la referencia."""
    lineas = texto.splitlines()
    bloque = ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(texto, filename=str(path))
        candidatos = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ini = getattr(node, "lineno", 0)
                fin = getattr(node, "end_lineno", ini)
                if ini <= linea <= fin:
                    candidatos.append((fin-ini, ini, fin))
        if candidatos:
            _, ini, fin = min(candidatos)
            bloque = "\n".join(lineas[ini-1:fin]).casefold()
    except Exception:
        pass
    if not bloque:
        ini = max(0, linea - 100)
        fin = min(len(lineas), linea + 100)
        bloque = "\n".join(lineas[ini:fin]).casefold()

    if ("conectar_usuario" in bloque or "con_usuario" in bloque or
            "usuario.sqlite3" in bloque or "turso" in bloque):
        return "usuario"
    if "conectar()" in bloque or "oposiciones.sqlite3" in bloque:
        return "maestra"
    return "indeterminado"


def _strings_ast(path: Path, texto: str) -> list[tuple[int, str]]:
    """Devuelve literales de texto del código; evita confundir variables/comentarios con tablas."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(texto, filename=str(path))
    except Exception:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((getattr(node, "lineno", 1), node.value))
        elif isinstance(node, ast.JoinedStr):
            partes = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    partes.append(v.value)
            if partes:
                out.append((getattr(node, "lineno", 1), "".join(partes)))
    return out


def escanear_referencias(tablas: Iterable[str], raiz: Path | None, proyecto: str,
                         activos_mant: set[Path] | None = None) -> dict[str, list[RefCodigo]]:
    refs: dict[str, list[RefCodigo]] = defaultdict(list)
    if raiz is None:
        return refs
    tablas_ord = sorted(tablas, key=len, reverse=True)
    patrones = {t: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(t)}(?![A-Za-z0-9_])", re.I) for t in tablas_ord}
    for p in archivos_py(raiz):
        txt = leer_texto(p)
        lines = txt.splitlines()
        if proyecto == "mantenimiento":
            rp = p.resolve()
            if activos_mant is not None and rp in activos_mant:
                amb = "activo"
            elif es_historico(p):
                amb = "historico"
            else:
                amb = "auxiliar"
        else:
            amb = "activo" if any(part in {"lib", "pages"} for part in p.parts) else "auxiliar"

        encontrados: set[tuple[str,int]] = set()
        for linea, literal in _strings_ast(p, txt):
            upper = literal.upper()
            sqlish = any(k in upper for k in (
                "SELECT", " FROM ", "\nFROM ", " JOIN ", "\nJOIN ",
                "INSERT", "UPDATE", "DELETE", "CREATE TABLE",
                "ALTER TABLE", "DROP TABLE", "PRAGMA", "SQLITE_MASTER"
            ))
            for t, pat in patrones.items():
                # Se contabilizan literales que forman parte de SQL/DDL. Esto evita
                # falsos positivos por documentación, variables o textos de interfaz.
                if sqlish and pat.search(literal) and (t, linea) not in encontrados:
                    encontrados.add((t, linea))
                    ctx = "maestra" if proyecto == "mantenimiento" else contexto_bd_opocoach(p, txt, linea)
                    try:
                        rel = str(p.relative_to(raiz))
                    except ValueError:
                        rel = str(p)
                    rawline = lines[linea-1].strip()[:300] if 1 <= linea <= len(lines) else literal[:300]
                    refs[t].append(RefCodigo(proyecto, rel, linea, rawline, amb, ctx))
    return refs


def dbstat_bytes(con: sqlite3.Connection) -> dict[str, int]:
    try:
        return {r[0]: int(r[1] or 0) for r in con.execute(
            "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"
        )}
    except sqlite3.DatabaseError:
        return {}


def clasificar(nombre: str, filas: int, fk_in: int, refs: list[RefCodigo]) -> tuple[str, str]:
    mant_act = [r for r in refs if r.proyecto == "mantenimiento" and r.ambito == "activo"]
    mant_aux = [r for r in refs if r.proyecto == "mantenimiento" and r.ambito == "auxiliar"]
    mant_hist = [r for r in refs if r.proyecto == "mantenimiento" and r.ambito == "historico"]
    opo_master = [r for r in refs if r.proyecto == "opocoach" and r.ambito == "activo" and r.contexto_bd == "maestra"]
    opo_user = [r for r in refs if r.proyecto == "opocoach" and r.ambito == "activo" and r.contexto_bd == "usuario"]
    opo_ind = [r for r in refs if r.proyecto == "opocoach" and r.ambito == "activo" and r.contexto_bd == "indeterminado"]

    if mant_act or opo_master:
        return "ACTIVO", "Referenciado por código operativo contra la base maestra."
    if opo_ind:
        return "REVISAR_DEPENDENCIA", "OpoCoach lo referencia, pero el análisis estático no determina con seguridad qué conexión utiliza."
    if opo_user and not (mant_act or opo_master):
        base = "Las referencias operativas de OpoCoach parecen dirigirse a la base de usuario/Turso, no a la base maestra."
        if filas:
            return "CANDIDATO_HISTORICO_CON_DATOS", base + f" La tabla maestra conserva {filas} filas."
        return "CANDIDATO_OBSOLETO", base + " La tabla maestra está vacía."
    if mant_aux:
        return "REVISAR_AUXILIAR", "Solo aparece en utilidades no alcanzadas desde el menú actual; comprobar antes de retirar."
    if mant_hist:
        if filas:
            return "HISTORICO_CON_DATOS", f"Solo aparece en scripts históricos y conserva {filas} filas."
        return "CANDIDATO_OBSOLETO", "Solo aparece en scripts históricos y está vacío."
    if fk_in:
        return "REVISAR_DEPENDENCIA_BD", f"No hay referencias de código detectadas, pero otras tablas tienen {fk_in} claves foráneas hacia esta tabla."
    if filas:
        return "HISTORICO_SIN_USO_DETECTADO", f"No hay referencias de código detectadas y conserva {filas} filas; no borrar sin analizar su contenido."
    return "CANDIDATO_OBSOLETO", "No hay referencias de código detectadas, está vacío y no recibe claves foráneas."


def main() -> int:
    args = parse_args()
    raiz_mant, db, raiz_opo, menu, salida = resolver_rutas(args)
    scripts_dir = raiz_mant / "scripts"

    print("=" * 78)
    print("AUDITORÍA CONSERVADORA DEL ESQUEMA")
    print("=" * 78)
    print("Modo: SOLO LECTURA")
    print(f"Base: {db}")
    print(f"Mantenimiento: {raiz_mant}")
    print(f"OpoCoach: {raiz_opo if raiz_opo else '(no localizado)'}")

    if not db.is_file():
        print(f"ERROR: no existe la base: {db}")
        return 1

    uri = f"file:{db.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
        objetos = con.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        tablas = [r["name"] for r in objetos if r["type"] == "table"]
        sizes = dbstat_bytes(con)

        # relaciones FK entrantes
        fk_in: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        fk_out: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for t in tablas:
            for r in con.execute(f'PRAGMA foreign_key_list("{t}")'):
                destino, desde, hacia = r[2], r[3], r[4]
                fk_out[t].append((destino, desde, hacia))
                fk_in[destino].append((t, desde, hacia))

        activos_mant = construir_scripts_activos(menu, scripts_dir) if scripts_dir.is_dir() else set()
        refs_m = escanear_referencias(tablas, scripts_dir if scripts_dir.is_dir() else raiz_mant,
                                      "mantenimiento", activos_mant)
        refs_o = escanear_referencias(tablas, raiz_opo, "opocoach")

        indices_por_tabla = defaultdict(list)
        triggers_por_tabla = defaultdict(list)
        for r in objetos:
            if r["type"] == "index":
                indices_por_tabla[r["tbl_name"]].append(r["name"])
            elif r["type"] == "trigger":
                triggers_por_tabla[r["tbl_name"]].append(r["name"])

        auditados: list[ObjetoAuditado] = []
        refs_todas: dict[str, list[RefCodigo]] = {}
        for t in tablas:
            filas = int(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
            refs = list(refs_m.get(t, [])) + list(refs_o.get(t, []))
            refs_todas[t] = refs
            clas, motivo = clasificar(t, filas, len(fk_in.get(t, [])), refs)
            auditados.append(ObjetoAuditado(
                nombre=t, tipo="table", tabla_base=t, filas=filas,
                bytes_aprox=sizes.get(t), fk_salientes=len(fk_out.get(t, [])),
                fk_entrantes=len(fk_in.get(t, [])),
                indices_asociados=len(indices_por_tabla.get(t, [])),
                triggers_asociados=len(triggers_por_tabla.get(t, [])),
                refs_activas_mantenimiento=sum(r.proyecto=="mantenimiento" and r.ambito=="activo" for r in refs),
                refs_historicas_mantenimiento=sum(r.proyecto=="mantenimiento" and r.ambito=="historico" for r in refs),
                refs_opocoach_maestra=sum(r.proyecto=="opocoach" and r.ambito=="activo" and r.contexto_bd=="maestra" for r in refs),
                refs_opocoach_usuario=sum(r.proyecto=="opocoach" and r.ambito=="activo" and r.contexto_bd=="usuario" for r in refs),
                refs_opocoach_indeterminadas=sum(r.proyecto=="opocoach" and r.ambito=="activo" and r.contexto_bd=="indeterminado" for r in refs),
                clasificacion=clas, motivo=motivo,
            ))

        salida.mkdir(parents=True, exist_ok=True)

        # Inventario completo de objetos SQLite (incluye SQL de creación).
        with (salida / "objetos_sqlite.csv").open("w", newline="", encoding="utf-8-sig") as f:
            campos_obj = ["tipo", "nombre", "tabla_base", "sql"]
            w = csv.DictWriter(f, fieldnames=campos_obj)
            w.writeheader()
            for r in objetos:
                w.writerow({
                    "tipo": r["type"], "nombre": r["name"],
                    "tabla_base": r["tbl_name"], "sql": r["sql"] or "",
                })

        # Inventario de columnas. Deliberadamente no se clasifican como obsoletas
        # de forma automática: una columna exige análisis semántico antes de retirarla.
        columnas_detalle = []
        for t in tablas:
            for c in con.execute(f'PRAGMA table_info("{t}")'):
                columnas_detalle.append({
                    "tabla": t, "cid": c[0], "columna": c[1], "tipo": c[2],
                    "notnull": c[3], "default": c[4], "pk": c[5],
                })
        with (salida / "columnas.csv").open("w", newline="", encoding="utf-8-sig") as f:
            campos_col = ["tabla", "cid", "columna", "tipo", "notnull", "default", "pk"]
            w = csv.DictWriter(f, fieldnames=campos_col)
            w.writeheader()
            w.writerows(columnas_detalle)

        # CSV resumen
        with (salida / "objetos.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(auditados[0]).keys()))
            w.writeheader()
            for a in auditados:
                w.writerow(asdict(a))

        # CSV referencias
        with (salida / "referencias_codigo.csv").open("w", newline="", encoding="utf-8-sig") as f:
            campos = ["objeto", "proyecto", "archivo", "linea", "ambito", "contexto_bd", "texto"]
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            for t in tablas:
                for r in refs_todas[t]:
                    d = asdict(r); d["objeto"] = t
                    w.writerow({k:d[k] for k in campos})

        # JSON completo
        detalle = {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "db": str(db),
            "integrity_check": integrity,
            "foreign_key_errors": [list(r) for r in fk_errors],
            "raiz_mantenimiento": str(raiz_mant),
            "raiz_opocoach": str(raiz_opo) if raiz_opo else None,
            "menu": str(menu),
            "scripts_activos_mantenimiento": sorted(str(p) for p in activos_mant),
            "objetos": [asdict(a) for a in auditados],
            "objetos_sqlite": [dict(r) for r in objetos],
            "columnas": columnas_detalle,
            "foreign_keys_entrantes": {k:v for k,v in fk_in.items()},
            "foreign_keys_salientes": {k:v for k,v in fk_out.items()},
            "indices": dict(indices_por_tabla),
            "triggers": dict(triggers_por_tabla),
            "referencias_codigo": {t:[asdict(r) for r in refs_todas[t]] for t in tablas},
        }
        (salida / "detalle.json").write_text(json.dumps(detalle, ensure_ascii=False, indent=2), encoding="utf-8")

        # Markdown legible
        orden = {
            "ACTIVO": 0,
            "REVISAR_DEPENDENCIA": 1,
            "REVISAR_DEPENDENCIA_BD": 2,
            "REVISAR_AUXILIAR": 3,
            "HISTORICO_CON_DATOS": 4,
            "HISTORICO_SIN_USO_DETECTADO": 5,
            "CANDIDATO_HISTORICO_CON_DATOS": 6,
            "CANDIDATO_OBSOLETO": 7,
        }
        aud_ord = sorted(auditados, key=lambda a:(orden.get(a.clasificacion,99), a.nombre))
        md = []
        md += ["# Auditoría conservadora del esquema", "",
               f"- Fecha: {datetime.now().isoformat(timespec='seconds')}",
               f"- Base: `{db}`", f"- `integrity_check`: **{integrity}**",
               f"- Errores de claves foráneas: **{len(fk_errors)}**", "",
               "> Esta auditoría no autoriza borrados. Las clasificaciones son provisionales y deben validarse mediante pruebas de regresión sobre una copia.", "",
               "## Resumen", "",
               "| Tabla | Filas | Tamaño aprox. | Clasificación | Motivo |",
               "|---|---:|---:|---|---|"]
        for a in aud_ord:
            tam = f"{a.bytes_aprox:,}" if a.bytes_aprox is not None else "n/d"
            md.append(f"| `{a.nombre}` | {a.filas:,} | {tam} B | **{a.clasificacion}** | {a.motivo} |")
        md += ["", "## Evidencia por tabla", ""]
        for a in aud_ord:
            md += [f"### `{a.nombre}` — {a.clasificacion}", "",
                   f"- Filas: **{a.filas:,}**",
                   f"- Tamaño aproximado: **{a.bytes_aprox:,} B**" if a.bytes_aprox is not None else "- Tamaño aproximado: n/d",
                   f"- FK salientes: {a.fk_salientes}; FK entrantes: {a.fk_entrantes}",
                   f"- Índices asociados: {a.indices_asociados}; triggers asociados: {a.triggers_asociados}",
                   f"- Motivo: {a.motivo}", ""]
            rs = refs_todas[a.nombre]
            if not rs:
                md.append("No se detectaron referencias textuales en el código analizado.\n")
            else:
                md.append("Referencias detectadas (máximo 20 mostradas):\n")
                for r in rs[:20]:
                    texto_ref = r.texto.replace("|", r"\|")
                    md.append(
                        f"- `{r.proyecto}` · `{r.ambito}` · BD `{r.contexto_bd}` · "
                        f"`{r.archivo}:{r.linea}` — `{texto_ref}`"
                    )
                if len(rs) > 20:
                    md.append(f"- … {len(rs)-20} referencias adicionales en `referencias_codigo.csv`.")
                md.append("")
        md += ["## Columnas", "",
               "El fichero `columnas.csv` contiene el inventario completo de columnas (tipo, NOT NULL, valor por defecto y clave primaria).",
               "No se marca ninguna columna como eliminable automáticamente: retirar una columna requiere una segunda auditoría semántica y pruebas de regresión específicas.", "",
               "El fichero `objetos_sqlite.csv` contiene además todas las tablas, índices, triggers y vistas definidos en `sqlite_master`.", "",
               "## Criterio de uso", "",
               "- **ACTIVO**: existe evidencia de uso operativo contra la base maestra.",
               "- **REVISAR_***: la evidencia no permite concluir con seguridad.",
               "- **HISTORICO_***: no aparece en el flujo operativo, pero contiene datos o evidencia histórica.",
               "- **CANDIDATO_OBSOLETO**: candidato a pruebas de retirada en una copia; **no significa que pueda borrarse directamente**.", "",
               "## Siguiente paso recomendado", "",
               "Crear una copia experimental de la base y retirar **un solo grupo candidato cada vez**, ejecutando después la validación completa de Mantenimiento y las pruebas funcionales de OpoCoach."
              ]
        (salida / "INFORME.md").write_text("\n".join(md)+"\n", encoding="utf-8")

        print(f"integrity_check...................... {integrity}")
        print(f"foreign_key_check................... {len(fk_errors)}")
        print(f"Tablas revisadas.................... {len(tablas)}")
        conteos = defaultdict(int)
        for a in auditados: conteos[a.clasificacion]+=1
        for k in sorted(conteos):
            print(f"{k:.<38} {conteos[k]}")
        print(f"Informe.............................. {salida / 'INFORME.md'}")
        print("\nNo se ha modificado la base de datos.")
        return 0 if integrity == "ok" and not fk_errors else 1
    finally:
        con.close()

if __name__ == "__main__":
    raise SystemExit(main())