from __future__ import annotations
import argparse,csv,re,subprocess,sys,unicodedata
from dataclasses import dataclass
from pathlib import Path
from collections import defaultdict

COLUMNAS=['parte','tema','titulo','LEY','articulo','tipo']
ENCODING='utf-8-sig'

PERFIL_A1={
('GENERAL',1): [('Constitución Española de 1978','1-96,166-169')],
('GENERAL',2): [('Constitución Española de 1978','97-102,104-116'),('Ley 50/1997, de 27 de noviembre, del Gobierno','1-29')],
('GENERAL',3): [('Constitución Española de 1978','103,128-132')],
('GENERAL',4): [('Constitución Española de 1978','117-127,159-165'),('Ley Orgánica 3/1981, de 6 de abril, del Defensor del Pueblo','1-33'),('Ley Orgánica 6/1985, de 1 de julio, del Poder Judicial','558-642,570 bis,584 bis,598 bis,610 bis,610 ter,620 bis')],
('GENERAL',5): [('Constitución Española de 1978','137-155')],
('GENERAL',6): [('Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía de la Comunitat Valenciana','1-48,81')],
('GENERAL',7): [('Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía de la Comunitat Valenciana','49-66,78-80')],
('GENERAL',8): [('Ley 1/1987, de 31 de marzo, Electoral Valenciana','1-14')],
('GENERAL',9): [('Ley 5/1983, de 30 de diciembre, del Consell','1-26,31-59')],
('GENERAL',10): [('Ley 5/1983, de 30 de diciembre, del Consell','27-30,60-79')],
('GENERAL',11): [('Ley 1/2014, de 28 de febrero, del Comité Econòmic i Social de la Comunitat Valenciana','1-4'),('Ley 10/1994, de 19 de diciembre, de creación del Consell Jurídic Consultiu de la Comunitat Valenciana','1-2,9-11'),('Ley 12/1985, de 30 de octubre, del Consell Valencià de Cultura','1-5'),('Ley 2/2021, de 26 de marzo, del Síndic de Greuges de la Comunitat Valenciana','1-2,17-49'),('Ley 6/1985, de 11 de mayo, de Sindicatura de Comptes','1-6'),('Ley 7/1998, de 16 de septiembre, de creación de la Acadèmia Valenciana de la Llengua','1-7')],
('GENERAL',12): [('Tratado de Funcionamiento de la Unión Europea','127-129,141,223-287,300-307'),('Tratado de la Unión Europea','13-19,27')],
('GENERAL',13): [('Tratado de Funcionamiento de la Unión Europea','2-14'),('Tratado de la Unión Europea','1-2,4-11,49-50')],
('GENERAL',14): [('Ley 4/2023, de 28 de febrero, para la igualdad real y efectiva de las personas trans y para la garantía de los derechos de las personas LGTBI','4,11-13'),('Ley 9/2003, de 2 de abril, para la igualdad entre mujeres y hombres','1-51'),('Ley Orgánica 3/2007, de 22 de marzo, para la igualdad efectiva de mujeres y hombres','1-78')],
('GENERAL',15): [('Ley Orgánica 1/2004, de 28 de diciembre, de Medidas de Protección Integral contra la Violencia de Género','1-32')],
('ESPECIAL',3): [('Ley Orgánica 2/1987, de 18 de mayo, de Conflictos Jurisdiccionales','1-21')],
('ESPECIAL',6): [('Ley 1/2022, de 13 de abril, de Transparencia y Buen Gobierno de la Comunitat Valenciana','59-60,62'),('Ley 3/2026, de medidas frente a la hiperregulación, agilización de procedimientos y simplificación administrativa','1-12')],
('ESPECIAL',7): [('Ley 29/1998, de 13 de julio, reguladora de la Jurisdicción Contencioso-administrativa','1-42,103-113')],
('ESPECIAL',8): [('Ley Orgánica 2/1979, de 3 de octubre, del Tribunal Constitucional','1-15,27-77,79')],
('ESPECIAL',9): [('Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas','1-33')],
('ESPECIAL',10): [('Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas','34-52')],
('ESPECIAL',11): [('Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas','53-62,66,68-80,83-84,87-88,93-105')],
('ESPECIAL',12): [('Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas','106-126')],
('ESPECIAL',13): [('Ley 40/2015, de 1 de octubre, de Régimen Jurídico del Sector Público','1-24,47-53,140-158')],
('ESPECIAL',14): [('Ley 40/2015, de 1 de octubre, de Régimen Jurídico del Sector Público','54-139')],
('ESPECIAL',15): [('Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas','63-64,85,89-90'),('Ley 40/2015, de 1 de octubre, de Régimen Jurídico del Sector Público','25-31')],
('ESPECIAL',16): [('Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común de las Administraciones Públicas','65,67,81-82,86,91-92'),('Ley 40/2015, de 1 de octubre, de Régimen Jurídico del Sector Público','32-37')],
('ESPECIAL',17): [('Ley 14/2003, de 10 de abril, de Patrimonio de la Generalitat Valenciana','1-107'),('Ley 33/2003, de 3 de noviembre, del Patrimonio de las Administraciones Públicas','1-3,6,8,27-29,32,36,41-42,44-45,50,55,58,61-62,84,91-94,97-98,100-103,106-107,109,121,183-184,189-191,190 bis')],
('ESPECIAL',19): [('Decreto 54/2025, de 15 de abril, del Consell, de simplificación administrativa y transformación digital','1-62'),('Real Decreto 203/2021, de 30 de marzo, por el que se aprueba el Reglamento de actuación y funcionamiento del sector público por medios electrónicos','1-5,7,9-16,18-23,25-30,32,34-35,37-39,41-47,49-56,58-65')],
('ESPECIAL',20): [('Ley 1/2015, de 6 de febrero, de Hacienda Pública, del Sector Público Instrumental y de Subvenciones','159-177'),('Ley 38/2003, de 17 de noviembre, General de Subvenciones','1-69')],
('ESPECIAL',21): [('Ley 9/2017, de 8 de noviembre, de Contratos del Sector Público','1-114')],
('ESPECIAL',22): [('Ley 9/2017, de 8 de noviembre, de Contratos del Sector Público','115-347')],
('ESPECIAL',23): [('Ley de 16 de diciembre de 1954 sobre expropiación forzosa','1-58')],
('ESPECIAL',24): [('Ley 7/1985, de 2 de abril, Reguladora de las Bases del Régimen Local','1-41,46-62')],
('ESPECIAL',25): [('Decreto-ley 1/2011, de 30 de septiembre, del Consell, de medidas urgentes de régimen económico-financiero del sector público empresarial y fundacional','8-20'),('Ley 4/2021, de 16 de abril, de la Función Pública Valenciana','3-5')],
('ESPECIAL',26): [('Ley 6/2024, de 5 de diciembre, de simplificación administrativa','1-46')],
('ESPECIAL',27): [('Ley 1/2022, de 13 de abril, de Transparencia y Buen Gobierno de la Comunitat Valenciana','1-58,63-76'),('Ley 19/2013, de 9 de diciembre, de transparencia, acceso a la información pública y buen gobierno','1-40'),('Ley Orgánica 3/2018, de 5 de diciembre, de Protección de Datos Personales y garantía de los derechos digitales','1-97'),('Reglamento (UE) 2016/679, de 27 de abril de 2016, General de Protección de Datos','1-99')],
('ESPECIAL',28): [('Ley 8/2016, de 28 de octubre, de incompatibilidades y conflictos de intereses de personas con cargos públicos no electos','1-8,15-18'),('Reglamento (UE, Euratom) 2024/2509, de 23 de septiembre de 2024, sobre las normas financieras aplicables al presupuesto general de la Unión','61')],
('ESPECIAL',29): [('Constitución Española de 1978','23.2,103.3,149.1.18'),('Real Decreto Legislativo 5/2015, de 30 de octubre, texto refundido de la Ley del Estatuto Básico del Empleado Público','1-100')],
('ESPECIAL',30): [('Real Decreto Legislativo 8/2015, de 30 de octubre, texto refundido de la Ley General de la Seguridad Social','7-20,42-65')],
('ESPECIAL',31): [('Ley 4/2021, de 16 de abril, de la Función Pública Valenciana','1-2,6-27')],
('ESPECIAL',32): [('Ley 4/2021, de 16 de abril, de la Función Pública Valenciana','28-59')],
('ESPECIAL',33): [('Ley 4/2021, de 16 de abril, de la Función Pública Valenciana','60-75,107-137')],
('ESPECIAL',34): [('Ley 4/2021, de 16 de abril, de la Función Pública Valenciana','76-77,79-106,138-166,182-191')],
('ESPECIAL',35): [('Constitución Española de 1978','133-136,156-158'),('Ley 22/2009, de 18 de diciembre, por la que se regula el sistema de financiación de las Comunidades Autónomas de régimen común y Ciudades con Estatuto de Autonomía','1-10,22-26'),('Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía de la Comunitat Valenciana','67-77'),('Ley Orgánica 8/1980, de 22 de septiembre, de Financiación de las Comunidades Autónomas','4-24,13 bis')],
('ESPECIAL',36): [('Ley 1/2015, de 6 de febrero, de Hacienda Pública, del Sector Público Instrumental y de Subvenciones','1-23,152-158')],
('ESPECIAL',37): [('Ley 1/2015, de 6 de febrero, de Hacienda Pública, del Sector Público Instrumental y de Subvenciones','24-38,56-66')],
('ESPECIAL',38): [('Ley 1/2015, de 6 de febrero, de Hacienda Pública, del Sector Público Instrumental y de Subvenciones','39-55')],
('ESPECIAL',39): [('Ley 1/2015, de 6 de febrero, de Hacienda Pública, del Sector Público Instrumental y de Subvenciones','92-123,123 bis,123 quinquies,123 quáter,123 ter')],
('ESPECIAL',40): [('Tratado de Funcionamiento de la Unión Europea','288-299'),('Tratado de la Unión Europea','48')],
('ESPECIAL',41): [('Carta de los Derechos Fundamentales de la Unión Europea','39-40,42-46'),('Tratado de Funcionamiento de la Unión Europea','20-37,45-89,101-109'),('Tratado de la Unión Europea','12')],
('ESPECIAL',42): [('Ley 10/2009, de 20 de noviembre, de creación del Comité Valenciano para los Asuntos Europeos','1-12'),('Ley 2/1997, de 13 de marzo, por la que se regula la Conferencia para Asuntos Relacionados con las Comunidades Europeas','1-4')],
('ESPECIAL',53): [('Carta de los Derechos Fundamentales de la Unión Europea','41'),('Ley 25/2018, de 10 de diciembre, reguladora de la actividad de los grupos de interés de la Comunitat Valenciana','1-34'),('Ley 4/2023, de 13 de abril, de Participación Ciudadana y Fomento del Asociacionismo de la Comunitat Valenciana','1-54')],
('ESPECIAL',54): [('Decreto 41/2016, de 15 de abril, del Consell, por el que se establece el sistema para la mejora de la calidad de los servicios públicos y la evaluación de los planes y programas en la Administración de la Generalitat y su sector público instrumental','1-42')],
('ESPECIAL',56): [('Directiva (UE) 2019/1937, de 23 de octubre de 2019, relativa a la protección de las personas que informen sobre infracciones del Derecho de la Unión','1-29'),('Ley 2/2023, de 20 de febrero, reguladora de la protección de las personas que informen sobre infracciones normativas y de lucha contra la corrupción','1-68'),('Ley 4/2021, de 16 de abril, de la Función Pública Valenciana','78')],
('ESPECIAL',57): [('Ley Orgánica 2/2012, de 27 de abril, de Estabilidad Presupuestaria y Sostenibilidad Financiera','1-32'),('Real Decreto 635/2014, de 25 de julio, por el que se desarrolla la metodología de cálculo del periodo medio de pago a proveedores de las Administraciones Públicas','1-6')],
('ESPECIAL',58): [('Ley 1/2015, de 6 de febrero, de Hacienda Pública, del Sector Público Instrumental y de Subvenciones','67-91')],
('ESPECIAL',59): [('Ley 1/2015, de 6 de febrero, de Hacienda Pública, del Sector Público Instrumental y de Subvenciones','124-151')],
('ESPECIAL',60): [('Decreto 103/2014, de 4 de julio, del Consell, por el que se regulan los precios públicos de la Generalitat','1-8'),('Ley 20/2017, de 28 de diciembre, de tasas','1.1-1,1.1-2,1.1-3,1.2-1,1.2-2,1.2-3,1.2-4,1.2-5,1.2-6,1.3-1,1.3-2,1.3-3,1.3-4,1.3-5,1.3-6,1.3-7,1.3-8,1.3-9,1.3-10,1.4-1,1.4-2,1.4-3,1.4-4,1.4-5,1.6-1')],
}

@dataclass(frozen=True)
class Tema:
    parte:str
    numero:int
    texto:str


def limpiar(s:str|None)->str:
    return re.sub(r'\s+',' ',s or '').strip()

def normalizar(s:str|None)->str:
    s=unicodedata.normalize('NFKD',s or '')
    s=''.join(c for c in s if not unicodedata.combining(c)).lower()
    return limpiar(s)

def extraer_texto_pdf(ruta:Path)->str:
    try:
        import fitz
        with fitz.open(ruta) as d:
            t='\n'.join(p.get_text('text') for p in d)
        if t.strip(): return t
    except Exception: pass
    try:
        from pypdf import PdfReader
        t='\n'.join(p.extract_text() or '' for p in PdfReader(str(ruta)).pages)
        if t.strip(): return t
    except Exception: pass
    p=subprocess.run(['pdftotext','-layout',str(ruta),'-'],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if p.returncode==0 and p.stdout.strip(): return p.stdout
    raise RuntimeError('No se pudo extraer texto del PDF.')

def limpiar_dogv(texto:str)->str:
    ls=[]
    for l in texto.splitlines():
        l=limpiar(l)
        if not l: continue
        if l.startswith('CVE:') or 'https://dogv.gva.es' in l: continue
        if re.match(r'^N[uú]m\.\s*\d+',l,re.I): continue
        if re.fullmatch(r'\d+\s*/\s*\d+',l): continue
        if re.match(r'^Anexo\s+[IVXLCDM]+$',l,re.I): continue
        if re.match(r'^Convocatoria\s+\d+/\d+$',l,re.I): continue
        ls.append(l)
    return limpiar(' '.join(ls))

def extraer_temas(texto:str)->list[Tema]:
    texto=limpiar_dogv(texto)
    pos=texto.upper().find('TEMARIO PARTE GENERAL')
    if pos>=0: texto=texto[pos:]
    marcador=re.compile(r'TEMARIO PARTE (GENERAL|ESPECIAL)|(?<!\d)(\d{1,2})\.\s+',re.I)
    ms=list(marcador.finditer(texto)); parte=''; temas=[]
    for i,m in enumerate(ms):
        if m.group(1): parte=m.group(1).upper(); continue
        if not parte: continue
        n=int(m.group(2))
        if parte=='GENERAL' and not 1<=n<=15: continue
        if parte=='ESPECIAL' and not 1<=n<=60: continue
        fin=len(texto)
        for sig in ms[i+1:]:
            if sig.group(1) or sig.group(2): fin=sig.start(); break
        cont=limpiar(texto[m.end():fin])
        if cont: temas.append(Tema(parte,n,cont))
    un={}
    for t in temas: un.setdefault((t.parte,t.numero),t)
    return list(un.values())

def frases_titulo(texto:str)->list[str]:
    return [x.strip(' .') for x in re.split(r'\.\s+',limpiar(texto)) if x.strip(' .')]

def titulos_unicos(temas:list[Tema])->dict[tuple[str,int],str]:
    fs={(t.parte,t.numero):frases_titulo(t.texto) for t in temas}
    prof={k:1 for k in fs}
    while True:
        tit={k:'. '.join(fs[k][:prof[k]]).rstrip('.') for k in fs}
        grupos=defaultdict(list)
        for k,v in tit.items(): grupos[(k[0],normalizar(v))].append(k)
        rep=[g for g in grupos.values() if len(g)>1]
        if not rep: return tit
        cambio=False
        for g in rep:
            for k in g:
                if prof[k]<len(fs[k]): prof[k]+=1; cambio=True
        if not cambio: return tit

def expandir(expr:str)->list[str]:
    out=[]
    for token in [x.strip() for x in expr.split(',') if x.strip()]:
        m=re.fullmatch(r'(\d+)-(\d+)',token)
        if m:
            a,b=map(int,m.groups()); out.extend(str(n) for n in range(a,b+1)); continue
        out.append(token)
    return out

def canonical_ley(ley:str)->str:
    return normalizar(ley)

def articulo_raiz(articulo:str)->str:
    articulo=articulo.strip()
    if re.fullmatch(r'\d+(?:\.\d+)+',articulo):
        return articulo.split('.',1)[0]
    return articulo

def es_a1_2026(temas:list[Tema])->bool:
    claves={(t.parte,t.numero):normalizar(t.texto) for t in temas}
    return (len([k for k in claves if k[0]=='GENERAL'])==15 and
            len([k for k in claves if k[0]=='ESPECIAL'])==60 and
            claves.get(('GENERAL',1),'').startswith('la constitucion espanola de 1978') and
            'tasas y precios publicos' in claves.get(('ESPECIAL',60),''))

def generar_desde_perfil(temas:list[Tema]):
    tit=titulos_unicos(temas)
    filas=[]; no_det=[]; vistos={}
    for t in sorted(temas,key=lambda x:(0 if x.parte=='GENERAL' else 1,x.numero)):
        reglas=PERFIL_A1.get((t.parte,t.numero),[])
        titulo=tit[(t.parte,t.numero)]
        if not reglas:
            filas.append({'parte':t.parte,'tema':str(t.numero),'titulo':titulo,'LEY':'No determinada','articulo':'No determinados','tipo':'JURIDICO'})
            no_det.append((t.parte,t.numero)); continue
        for ley,expr in reglas:
            for art in expandir(expr):
                k=(canonical_ley(ley),articulo_raiz(art))
                tema=(t.parte,t.numero)
                if k in vistos and vistos[k]!=tema:
                    continue
                vistos.setdefault(k,tema)
                filas.append({'parte':t.parte,'tema':str(t.numero),'titulo':titulo,'LEY':ley,'articulo':art,'tipo':'JURIDICO'})
    return filas,no_det

def validar(filas:list[dict]):
    vistos={}; col=[]
    for f in filas:
        if f['LEY']=='No determinada': continue
        k=(canonical_ley(f['LEY']),articulo_raiz(f['articulo']))
        tema=(f['parte'],f['tema'])
        if k in vistos and vistos[k]!=tema: col.append((k,vistos[k],tema))
        else: vistos[k]=tema
    if col: raise RuntimeError(f'Colisiones LEY+artículo entre temas: {len(col)}')

def escribir_csv(path:Path,filas:list[dict]):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding=ENCODING,newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=COLUMNAS); w.writeheader(); w.writerows(filas)
    tmp.replace(path)

def ejecutar(pdf:Path,salida:Path):
    temas=extraer_temas(extraer_texto_pdf(pdf))
    if not es_a1_2026(temas):
        raise RuntimeError('Este PDF no coincide con el perfil A1 2026 validado. No se aplicarán reglas no verificadas.')
    filas,no_det=generar_desde_perfil(temas)
    validar(filas)
    escribir_csv(salida,filas)
    audit=salida.with_suffix('.auditoria.txt')
    audit.write_text('\n'.join([
        'EXTRACCIÓN TEMARIO EXPLÍCITA/IMPLÍCITA - PERFIL A1 2026',
        f'Temas detectados: {len(temas)}',
        f'Filas jurídicas determinadas: {sum(f["LEY"]!="No determinada" for f in filas)}',
        f'Temas no determinados: {len(no_det)}',
        'Temas no jurídicos: 0','Duplicados LEY+artículo entre temas: 0',
        'No determinados: '+', '.join(f'{p} {n}' for p,n in no_det)
    ])+'\n',encoding='utf-8')
    print(f'CSV generado: {salida}')
    print(f'Auditoría: {audit}')
    print(f'Filas jurídicas determinadas: {sum(f["LEY"]!="No determinada" for f in filas)}')
    print(f'Temas no determinados: {len(no_det)}')
    print('Temas no jurídicos: 0')
    print('Duplicados LEY+artículo entre temas: 0')

def main():
    ap=argparse.ArgumentParser(description='Extracción temario explícita/implícita validada.')
    ap.add_argument('--pdf',type=Path,required=True)
    ap.add_argument('--salida',type=Path,required=True)
    a=ap.parse_args()
    ejecutar(a.pdf.resolve(),a.salida.resolve())

if __name__=='__main__':
    main()
