from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

OUT = Path(__file__).resolve().parent.parent / 'output' / 'pdf' / 'plan_cobertura_materiales_tucoach.pdf'
OUT.parent.mkdir(parents=True, exist_ok=True)
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='TitleTC', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=colors.HexColor('#0b2b5b'), alignment=TA_CENTER, spaceAfter=12))
styles.add(ParagraphStyle(name='H1TC', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=colors.HexColor('#0b4fa3'), spaceBefore=12, spaceAfter=7))
styles.add(ParagraphStyle(name='BodyTC', parent=styles['BodyText'], fontName='Helvetica', fontSize=9.4, leading=13, spaceAfter=5))
styles.add(ParagraphStyle(name='SmallTC', parent=styles['BodyText'], fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#555555')))

def P(text, style='BodyTC'): return Paragraph(text, styles[style])
def table(rows, widths):
    t=Table([[P(str(x),'SmallTC') for x in row] for row in rows], colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#0b4fa3')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('GRID',(0,0),(-1,-1),0.25,colors.HexColor('#cbd5e1')),('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,1),(-1,-1),colors.HexColor('#f8fafc')),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
    return t

story=[P('Plan de cobertura completa de materiales de estudio','TitleTC'),P('Tu Coach - documento operativo para ejecución en otro entorno','SmallTC'),Spacer(1,8)]
story += [P('<b>Objetivo.</b> Pasar de 30 normas activas con material aprobado a 106 normas activas cubiertas, conservando trazabilidad jurídica, calidad didáctica, catálogo coherente y despliegue verificable.'),P('<b>Estado de partida.</b> Las referencias activas de las tres convocatorias están completadas; no hay cruces detectados entre norma y artículo. Existen 76 normas activas con corpus disponible y sin resumen. La Ley 6/2025 es material extra no usado por una convocatoria activa.')]
story += [P('1. Inventario y clasificación','H1TC'),table([['Grupo','Cantidad','Acción'],['Material aprobado y corpus coherente','30','No regenerar salvo cambio de corpus o revisión manual'],['Normas activas sin resumen','76','Generar por prioridad y lote'],['Material extra no usado','1','Mantener o retirar según política de producto']], [5*cm,2.2*cm,9.2*cm]),P('Generar un informe de solo lectura por norma con: ID, nombre, convocatorias y temas que la usan, fuente, número de artículos, estado de corpus, PDF, prioridad, riesgo y necesidad de respaldo local.')]
story += [P('2. Priorización','H1TC'),P('Orden: (1) normas compartidas por varias convocatorias; (2) normas troncales administrativas; (3) normas cortas de menos de 60 artículos; (4) normativa valenciana y UE; (5) normas extensas; (6) materiales doctrinales GEN con revisión humana previa.'),P('Fase rápida recomendada: Código Civil, Carta de Derechos Fundamentales de la UE, Ley 10/1994, Ley 12/1985, Ley 25/2014, Ley 50/1997, LO 2/1987, LO 3/1981, LO 8/1980 y RD 635/2014.')]
story += [P('3. Validación de fuentes','H1TC'),P('Para cada norma: auditar el corpus en solo lectura; usar la fuente canónica de mayor cobertura; si falla, localizar PDF oficial local, verificar tipo/número/año/título, sincronizar únicamente las filas distintas con copia de seguridad y repetir auditoría. No generar si hay fuente ambigua, artículo vacío/truncado, versiones históricas mezcladas o PDF no verificable.')]
story += [P('4. Generación por lotes','H1TC'),table([['Lote','Tamaño','Uso'],['Corto','5 a 8 normas / menos de 60 artículos','Cobertura rápida'],['Medio','3 a 5 normas / 60 a 150 artículos','Generación estándar'],['Extenso','1 norma / más de 150 artículos','Control reforzado'],['GEN','1 norma','Revisión humana obligatoria']], [3*cm,5*cm,8.4*cm]),P('Por norma: validar RAG, extraer hechos por bloques, validar cada hecho, reintentar JSON inválido, sintetizar mapa temático, validar síntesis, crear PDF, guardar backup, actualizar catálogo y registrar coste. El lote se detiene ante fuente no fiable, no ante una alerta meramente estilística.')]
story += [P('5. Criterios de calidad','H1TC'),P('Obligatorios: 4 a 12 secciones temáticas; al menos 8 ideas desarrolladas; mapa no basado en rangos genéricos; cero elipsis o texto truncado; cero menciones a OpoCoach; sin requisitos, plazos, órganos o excepciones inventados; cierre de repaso y recomendaciones; marca Tu Coach.'),P('Revisión jurídica: fuente única, artículos completos, hechos respaldados y sin mezcla de versiones. Revisión técnica: PDF abre, texto extraíble, catálogo y hash coherentes. Revisión visual: primera página de todos los PDFs y, en normas extensas, una página intermedia y la última.')]
story += [P('6. Publicación y despliegue','H1TC'),P('Tras aprobar un lote: ejecutar publicación en modo prueba, copiar solo PDFs aprobados a TuCoach-Web/backend/materiales/resumenes, actualizar catalogo_resumenes.json, excluir backups y base de datos, revisar git status, crear commit limitado, fetch/rebase si procede y push a main. Verificar en producción selección de convocatoria, selección de norma y descarga del PDF.')]
story += [P('7. Control de costes','H1TC'),table([['Tipo','Estimación USD por norma','Límite recomendado'],['Corta','0,01 a 0,05','Lote: 2 USD'],['Media','0,05 a 0,20','Lote: 3 USD'],['Extensa','0,20 a 0,80','Norma: 2 USD'],['Muy extensa / reintentos','hasta 1,50','Norma: 2 USD']], [4*cm,5*cm,7.4*cm]),P('Estimación inicial de las 76 normas: 15 a 35 USD. No autorizar todo el presupuesto de una vez: revisar coste, calidad y fallos tras cada lote.')]
story += [P('8. Indicadores y cierre','H1TC'),P('Actualizar tras cada lote: normas activas totales, corpus válidos, PDFs v3 aprobados, normas sin material, PDFs con error técnico, referencias rotas y fuentes ambiguas. Cierre solo cuando haya 106 normas activas auditadas, 106 con material aprobado o excepción explícita, cero referencias rotas, cero corpus ambiguos, catálogo local/web coherente y descarga verificada en producción.'),Spacer(1,8),P('Nota operativa: las normas doctrinales GEN no deben presentarse como legislación literal; se generan de una en una y se aprueban con una muestra humana inicial.','SmallTC')]

def footer(canvas, doc):
    canvas.saveState(); canvas.setFont('Helvetica',8); canvas.setFillColor(colors.HexColor('#64748b')); canvas.drawString(1.7*cm,1.15*cm,'Tu Coach - Plan de materiales'); canvas.drawRightString(19.3*cm,1.15*cm,f'Página {doc.page}'); canvas.restoreState()

SimpleDocTemplate(str(OUT),pagesize=A4,rightMargin=1.65*cm,leftMargin=1.65*cm,topMargin=1.55*cm,bottomMargin=1.7*cm,title='Plan de cobertura de materiales - Tu Coach').build(story,onFirstPage=footer,onLaterPages=footer)
print(OUT)
