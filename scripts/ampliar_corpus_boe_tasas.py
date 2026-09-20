"""Planifica la ampliación por bloques de la Ley 20/2017 de tasas."""
from __future__ import annotations
import argparse, hashlib, re, shutil, sqlite3, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; sys.path.insert(0,str(Path(__file__).resolve().parent))
from boe_api import obtener_texto_completo,nombre_etiqueta,_texto_version,normalizar
SOURCE="BOE-A-2018-1870"; HEAD=re.compile(r"^(Artículo\s+(\d+(?:\.\d+)*-\d+)(?:\s+\[sic\])?)\b",re.I)
def build_plan():
 root=obtener_texto_completo(SOURCE); raw=[]
 for b in root.iter():
  if nombre_etiqueta(b)!="bloque": continue
  v=[x for x in b if nombre_etiqueta(x)=="version"]
  if len(v)!=1: continue
  text=_texto_version(v[0]); m=HEAD.match(text)
  if m: raw.append((normalizar(m.group(1).replace("[sic]","")),m.group(2),re.sub(r"\D","",v[0].attrib.get("fecha_publicacion","")),text))
 if len(raw)!=364 or len({(x[0],x[2]) for x in raw})!=364: raise RuntimeError("Inventario estructurado inesperado")
 import ampliar_corpus_chat as g
 index={}
 for x in g.candidatos_indice(SOURCE):
  m=HEAD.match(x.titulo)
  if m: index[(normalizar(m.group(1).replace("[sic]","")),x.fecha_actualizacion)]=x
 out=[]
 for key,art,date,text in raw:
  x=index.get((key,date))
  if not x: raise RuntimeError(f"Sin identidad BOE verificable: {key}/{date}")
  out.append((x.id_bloque,art,x.titulo,text,hashlib.sha256(text.encode()).hexdigest()))
 return out
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',default=str(ROOT/'db'/'oposiciones.sqlite3'));p.add_argument('--aplicar',action='store_true');a=p.parse_args();items=build_plan()
 con=sqlite3.connect(a.db); existing={r[0] for r in con.execute('select id_bloque from articulos_fuente where id_boe=?',(SOURCE,))};dept=con.execute('select departamento from articulos_fuente where id_boe=? limit 1',(SOURCE,)).fetchone()[0];con.close(); add=[x for x in items if x[0] not in existing]
 print(f"Bloques oficiales vigentes: {len(items)} | existentes: {len(items)-len(add)} | insertar: {len(add)}")
 if not a.aplicar: print("PLAN OK: no se modifica la base.");return
 backup=Path(a.db).with_name(f"oposiciones_antes_tasas_{datetime.now():%Y%m%d_%H%M%S}.sqlite3");shutil.copy2(a.db,backup)
 with sqlite3.connect(a.db) as con:
  con.execute('pragma foreign_keys=on')
  for bid,art,title,text,h in add: con.execute('insert into articulos_fuente(id_boe,id_bloque,articulo_boe,titulo_bloque,departamento,texto,hash_texto) values(?,?,?,?,?,?,?)',(SOURCE,bid,art,title,dept,text,h))
  if con.execute('pragma foreign_key_check').fetchall(): raise RuntimeError('foreign_key_check')
 print(f"APLICACIÓN OK | copia: {backup} | insertados: {len(add)}")
if __name__=='__main__': main()
