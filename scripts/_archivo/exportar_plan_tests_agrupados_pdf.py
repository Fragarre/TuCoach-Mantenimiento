from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak


DESTINO = Path(r"C:\Users\fraga\OneDrive\Desktop\output\pdf\plan_tests_leyes_temas_agrupados_tucoach.pdf")


def p(text, style):
    return Paragraph(text, style)


def encabezado(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#d8e3f5"))
    canvas.line(1.6 * cm, 1.35 * cm, A4[0] - 1.6 * cm, 1.35 * cm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#65738a"))
    canvas.drawString(1.6 * cm, 0.88 * cm, "TuCoach | Plan de implementación")
    canvas.drawRightString(A4[0] - 1.6 * cm, 0.88 * cm, f"Página {doc.page}")
    canvas.restoreState()


def main():
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleTC", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=colors.HexColor("#12356b"), spaceAfter=10))
    styles.add(ParagraphStyle(name="SubTC", parent=styles["Normal"], fontSize=10.5, leading=15, textColor=colors.HexColor("#52627a"), spaceAfter=16))
    styles.add(ParagraphStyle(name="H1TC", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=colors.HexColor("#12356b"), spaceBefore=10, spaceAfter=7))
    styles.add(ParagraphStyle(name="H2TC", parent=styles["Heading3"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=colors.HexColor("#1f5db8"), spaceBefore=7, spaceAfter=4))
    styles.add(ParagraphStyle(name="BodyTC", parent=styles["BodyText"], fontSize=9.2, leading=13, spaceAfter=6))
    styles.add(ParagraphStyle(name="SmallTC", parent=styles["BodyText"], fontSize=8.3, leading=11.2))
    styles.add(ParagraphStyle(name="CalloutTC", parent=styles["BodyText"], fontSize=9.3, leading=13, textColor=colors.HexColor("#17345e"), backColor=colors.HexColor("#edf4ff"), borderColor=colors.HexColor("#b9d0f5"), borderWidth=0.6, borderPadding=8, spaceBefore=4, spaceAfter=10))
    doc = SimpleDocTemplate(str(DESTINO), pagesize=A4, rightMargin=1.6*cm, leftMargin=1.6*cm, topMargin=1.55*cm, bottomMargin=1.7*cm)
    story = []
    story += [p("Plan de implementación: tests por leyes y temas", styles["TitleTC"]), p("Alcance: restringir las preguntas a los artículos del temario y presentar los resultados agrupados, sin modificar todavía la aplicación.", styles["SubTC"])]
    story += [p("Resultado que debe quedar garantizado", styles["H1TC"]), p("Cuando se seleccionen una o varias leyes, el test solo podrá contener preguntas de esas leyes y, dentro de ellas, únicamente de artículos que estén referenciados por el temario de la convocatoria elegida. Cuando se seleccionen varios temas o varias leyes, las preguntas quedarán contiguas por grupo; nunca se intercalarán grupos distintos.", styles["CalloutTC"])]
    data = [[p("Situación actual comprobada", styles["SmallTC"]), p("Cambio objetivo", styles["SmallTC"])],
            [p("La interfaz ya permite seleccionar múltiples temas o normas.", styles["SmallTC"]), p("Conservar la selección múltiple y hacer explícito el orden de grupos.", styles["SmallTC"])],
            [p("La selección por tema ya se apoya en el vínculo principal pregunta-tema.", styles["SmallTC"]), p("Mantener ese alcance; ordenar por tema en vez de mezclar al final.", styles["SmallTC"])],
            [p("La selección por norma filtra por banco y norma, pero no cruza las referencias del temario.", styles["SmallTC"]), p("Cruzar norma + artículo de la pregunta con norma + artículo permitido por el temario.", styles["SmallTC"])],
            [p("crear_test ejecuta random.shuffle(elegidas_total).", styles["SmallTC"]), p("Eliminar la mezcla global; conservar aleatoriedad solo dentro de cada grupo.", styles["SmallTC"])]]
    table = Table(data, colWidths=[8.4*cm, 8.4*cm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#12356b")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("VALIGN", (0,0), (-1,-1), "TOP"), ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cbd8e8")), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7faff")]), ("LEFTPADDING", (0,0), (-1,-1), 7), ("RIGHTPADDING", (0,0), (-1,-1), 7), ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
    story += [table, Spacer(1, 8)]
    story += [p("1. Decisiones funcionales a fijar antes de programar", styles["H1TC"]), p("1.1 Orden de grupos. Usar el orden visible del selector: para temas, parte GENERAL, ESPECIAL y después número/título; para leyes, el orden alfabético canónico mostrado. Si se desea respetar el orden exacto de clic del usuario, debe añadirse una posición de selección en el cliente; el plan recomienda el orden visible, estable y reproducible.", styles["BodyTC"]), p("1.2 Pertenencia de artículo. La regla normal será igualdad entre norma_id_normalizada y articulo_normalizado de la pregunta y de temario_referencias. Antes de activarla se auditarán formatos de artículo (por ejemplo, 12, 12.1, 12 bis y disposiciones) y se definirá una normalización común. Las preguntas sin norma o artículo normalizado no entrarán en un test por ley.", styles["BodyTC"]), p("1.3 Artículos repetidos en varios temas. En modo ley se considera permitido si aparece en cualquier tema de la convocatoria; el grupo sigue siendo la ley. En modo tema se conserva el tema principal ya asociado a la pregunta.", styles["BodyTC"])]
    story += [p("2. Diseño de datos y consultas", styles["H1TC"]), p("2.1 Crear una función interna de alcance permitido para ley: recibe convocatoria_id y normas seleccionadas; devuelve pares únicos (norma_id, articulo_normalizado) desde temarios -> temario_temas -> temario_referencias con estado COMPLETADO y campos normalizados. No debe depender solo de que exista una pregunta en banco.", styles["BodyTC"]), p("2.2 Adaptar obtener_normas_test para listar exclusivamente leyes con al menos una candidata que pase la intersección anterior. La cifra disponibles debe reflejar las preguntas realmente utilizables, no el total histórico de la ley.", styles["BodyTC"]), p("2.3 En _cargar_datos_creacion_test, rama NORMA: unir/correlacionar lote_preguntas con los pares permitidos de la convocatoria. Aplicar simultáneamente filtros actuales: convocatoria, INCLUIDA, fuentes, exclusión informática y clave de norma seleccionada.", styles["BodyTC"]), p("2.4 Mantener la rama TEMA y su unión banco_preguntas_temas. Añadir un orden de grupo calculado y trazable, sin ampliar su ámbito.", styles["BodyTC"])]
    story += [p("3. Selección y ordenación", styles["H1TC"]), p("3.1 Conservar el reparto proporcional actual por elemento, limitado por disponibilidad. Se calcula después de aplicar el nuevo filtro de artículos, para que nunca asigne preguntas inexistentes.", styles["BodyTC"]), p("3.2 Para cada grupo, usar _seleccionar para evitar repeticiones recientes y después mezclar solo las preguntas de ese mismo grupo. No se hará random.shuffle sobre el conjunto completo.", styles["BodyTC"]), p("3.3 Concatenar los grupos en orden canónico. Guardar esa secuencia como orden en public.simulacro_preguntas; las APIs, la pantalla y los PDFs ya recuperan por ese orden, por lo que no requieren una segunda reordenación.", styles["BodyTC"]), p("3.4 Si un grupo se queda sin candidatas válidas, no incorporar preguntas de otra ley o tema. Informar en avisos que la cantidad total se ha reducido y nombrar el grupo afectado.", styles["BodyTC"])]
    story += [p("4. Contrato API y experiencia de usuario", styles["H1TC"]), p("4.1 Mantener CrearTestRequest y sus listas temas_seleccionados/normas_seleccionadas para preservar compatibilidad. Opcionalmente añadir agrupacion: 'ORDEN_CANONICO' como valor documentado, sin exponer alternativas hasta que exista necesidad real.", styles["BodyTC"]), p("4.2 Mostrar en cada selector la disponibilidad ya restringida. En leyes, el texto ayudará a evitar una expectativa errónea: 'preguntas de artículos incluidos en este temario'.", styles["BodyTC"]), p("4.3 Tras crear el test, mostrar un resumen breve de bloques: Ley X (n preguntas), Ley Y (n preguntas), o Tema n (n preguntas). No se deben insertar cabeceras dentro del examen salvo que se acuerde expresamente; la agrupación queda garantizada por la numeración consecutiva.", styles["BodyTC"]), p("4.4 Mantener el límite de 10 preguntas para prueba gratuita y el resto de reglas de suscripción. No afectar simulacros, corrección, historial ni análisis de rendimiento.", styles["BodyTC"])]
    story += [p("5. Pruebas automatizadas obligatorias", styles["H1TC"])]
    tests = [["Caso", "Comprobación"], ["Una ley", "Cada pregunta tiene la ley seleccionada y un par norma-artículo presente en temario_referencias."], ["Varias leyes", "Ninguna pregunta pertenece a ley no seleccionada; las posiciones de cada ley forman un intervalo continuo."], ["Varios temas", "Ninguna pregunta pertenece a un tema no seleccionado; cada tema forma un intervalo continuo."], ["Ley con artículos fuera del temario", "Esas preguntas no aparecen y disponibilidad/aviso reflejan el máximo real."], ["Artículo duplicado en temas", "En modo ley aparece una sola vez por pregunta, sin duplicados."], ["SQLite/Postgres", "Las listas, candidatas y orden de grupo son equivalentes en ambos orígenes."], ["Regresión", "Simulacros y tests unitarios existentes continúan pasando; los endpoints no exponen la respuesta correcta."]]
    tt = Table([[p(c, styles["SmallTC"]) for c in row] for row in tests], colWidths=[4.4*cm, 12.4*cm], repeatRows=1)
    tt.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#12356b")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#cbd8e8")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7faff")]), ("LEFTPADDING", (0,0), (-1,-1), 7), ("RIGHTPADDING", (0,0), (-1,-1), 7), ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
    story += [tt]
    story += [PageBreak(), p("6. Secuencia de implementación", styles["H1TC"]), p("Fase A - Auditoría técnica: consultar cobertura norma-artículo por convocatoria, inventariar nulos y formatos irregulares, y decidir los casos de disposiciones. Sin escritura.", styles["BodyTC"]), p("Fase B - Backend: implementar la consulta de alcance, corregir listado/disponibilidad, selección y concatenación ordenada; añadir avisos precisos.", styles["BodyTC"]), p("Fase C - Frontend: actualizar ayuda, disponibilidad y resumen de bloques, conservando los controles existentes.", styles["BodyTC"]), p("Fase D - Validación: pruebas unitarias y duales SQLite/Postgres, prueba manual autenticada de dos leyes y dos temas, revisión del PDF de preguntas y de la pantalla.", styles["BodyTC"]), p("Fase E - Publicación: revisión de diff, commit separado y despliegue del backend/frontend; comprobación posterior en la web privada con un test nuevo. Los tests ya guardados no se alteran.", styles["BodyTC"])]
    story += [p("7. Criterios de aceptación y cierre", styles["H1TC"]), p("La tarea estará terminada cuando: (a) toda pregunta de un test por ley cumpla la intersección ley-artículo del temario de su convocatoria; (b) los grupos de leyes o temas no se mezclen; (c) las disponibilidades coincidan con el filtro real; (d) las pruebas cubran los casos anteriores en ambos orígenes de contenidos; y (e) el flujo se verifique tras desplegar sin regresiones en simulacros.", styles["CalloutTC"])]
    story += [p("8. Mapa de cambio técnico", styles["H1TC"])]
    mapa = [["Componente", "Responsabilidad durante la implementación"], ["backend/app/tests_tucoach.py", "Fuente única de verdad: alcance norma-artículo, disponibilidad, reparto, ordenación por grupos y avisos."], ["backend/app/schemas.py y main.py", "Mantener el contrato de creación; documentar el comportamiento y validar errores de selección."], ["frontend/app/page.tsx", "Explicar el alcance limitado al temario, mostrar disponibilidad válida y resumen de los bloques creados."], ["backend/scripts/test_tests_tucoach_dual.py", "Ampliar la comparación SQLite/Postgres con dos normas, dos temas, artículos excluidos y orden de grupos."], ["PDF de preguntas / API de preguntas", "Verificar que respetan el campo orden persistido; no incorporar lógica de reordenación duplicada."]]
    mt = Table([[p(c, styles["SmallTC"]) for c in row] for row in mapa], colWidths=[5.1*cm, 11.7*cm], repeatRows=1)
    mt.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#12356b")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#cbd8e8")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7faff")]), ("LEFTPADDING", (0,0), (-1,-1), 7), ("RIGHTPADDING", (0,0), (-1,-1), 7), ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
    story += [mt, p("Riesgo principal a controlar: datos heredados sin artículo normalizado. La implementación debe excluirlos del modo ley y mostrar una disponibilidad menor, antes que permitir una pregunta cuya pertenencia al temario no se pueda demostrar.", styles["CalloutTC"])]
    doc.build(story, onFirstPage=encabezado, onLaterPages=encabezado)
    print(DESTINO)


if __name__ == "__main__":
    main()
