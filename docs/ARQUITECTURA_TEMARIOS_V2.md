# Arquitectura del Constructor de Temarios V2

## 1. Objetivo

Construir un procedimiento reutilizable para incorporar futuras convocatorias a OpoCoach/Tu Coach partiendo del temario oficial, conservando la estructura y los contratos actuales del proyecto.

El objetivo no es inferir automáticamente todo el contenido jurídico de un temario sin supervisión. El objetivo es automatizar únicamente aquello que pueda determinarse con evidencia suficiente, registrar los casos dudosos y facilitar una revisión humana limitada y trazable.

## 2. Principio de compatibilidad

El Constructor V2 no sustituye las estructuras consolidadas que ya consume la aplicación.

Se mantienen como contratos finales:

- `normas`: catálogo canónico de normas.
- `temarios`.
- `temario_temas`.
- `temario_referencias`.
- `norma_fuentes`.
- `articulos_fuente`.
- `documentos_corpus`.
- `convocatoria_documentos_corpus`.
- `lote_preguntas`.
- `banco_preguntas`.

El nuevo sistema actúa antes de estas estructuras: ayuda a construir y revisar las referencias del temario y, una vez aprobadas, entrega datos compatibles con el flujo actual.

## 3. Tres productos distintos

Una convocatoria necesita tres resultados diferentes y no deben confundirse.

### 3.1. Mapa jurídico del temario

Relación:

`tema -> norma -> artículo`

Debe representar los artículos que realmente forman parte del temario, aunque actualmente no existan preguntas sobre ellos.

Sirve para:

- construir `temario_referencias`;
- generar el corpus preciso utilizado como contexto de generación IA;
- determinar qué preguntas jurídicas pueden entrar en el banco.

### 3.2. Banco de preguntas

Se obtiene cruzando el mapa jurídico aprobado con `lote_preguntas`.

El contenido de `lote_preguntas` no define el temario. Sólo permite saber qué preguntas disponibles coinciden con las referencias jurídicas ya aprobadas.

Relación:

`temario_referencias aprobadas -> lote_preguntas -> banco_preguntas`

### 3.3. Corpus RAG de convocatoria

Incluye las normas completas que pertenecen al temario, aunque sólo algunos artículos sean objeto directo de examen.

Relación:

`normas aprobadas para la convocatoria -> documentos completos -> convocatoria_documentos_corpus`

Este corpus no debe depender de que todos los artículos de una norma pertenezcan al temario.

## 4. Catálogo de normas

El catálogo `normas` continúa siendo la fuente canónica.

Reglas:

1. Ningún proceso nuevo debe crear una norma duplicada por una variante textual de su nombre.
2. Toda norma candidata debe intentar resolverse primero contra el catálogo existente.
3. Si no existe una correspondencia segura, el sistema debe marcarla para revisión en vez de crear una norma automáticamente.
4. La normalización de nombres debe ser trazable.

## 5. Flujo de construcción

El flujo previsto es:

`temario oficial -> temas -> normas candidatas -> artículos candidatos -> revisión -> temario definitivo -> flujo actual`

### Fase A. Extraer temas

Se extraen del documento oficial, sin interpretación jurídica adicional:

- parte;
- número de tema;
- texto literal del tema;
- normas citadas explícitamente;
- títulos, capítulos, secciones u otras estructuras mencionadas literalmente.

La extracción debe conservar el texto original para poder auditar cualquier decisión posterior.

### Fase B. Resolver normas candidatas

Las normas se clasifican por origen.

- `PDF_EXPLICITO`: la norma aparece literalmente en el temario.
- `REUTILIZADO`: existe una asociación ya validada en otra convocatoria claramente equivalente.
- `INFERIDO`: la materia permite proponer una norma, pero no está citada literalmente.
- `MANUAL`: asociación introducida o confirmada por revisión humana.

Regla crítica: una norma `INFERIDO` nunca debe pasar automáticamente a definitiva sólo por una respuesta de IA.

### Fase C. Resolver artículos candidatos

Se utilizan, por orden de fiabilidad:

1. estructuras jurídicas explícitas del temario (título, capítulo, sección, rango claramente indicado);
2. mapeos ya revisados en otras convocatorias cuando la equivalencia del contenido sea comprobable;
3. texto oficial de `articulos_fuente`;
4. preguntas disponibles en `lote_preguntas` como evidencia auxiliar;
5. análisis semántico/IA únicamente como propuesta, nunca como fuente jurídica definitiva.

La IA puede ayudar a encontrar candidatos. No puede convertir por sí sola una asociación dudosa en una referencia definitiva.

## 6. Estados de trabajo

Los candidatos deben tener como mínimo estos estados:

- `PROPUESTO`;
- `ACEPTADO`;
- `RECHAZADO`;
- `REVISION`.

No se escribirá en `temario_referencias` ninguna asociación que no esté en estado `ACEPTADO`.

## 7. Evidencia y trazabilidad

Cada candidato debe registrar por qué existe.

Campos conceptuales mínimos:

- convocatoria;
- tema;
- norma;
- artículo cuando proceda;
- origen;
- estado;
- evidencia o justificación breve;
- fecha de decisión;
- decisión automática o manual.

No se utilizará una puntuación numérica de confianza como sustituto de la evidencia. Si se añade una puntuación, será sólo auxiliar.

## 8. Calidad de la generación IA

La generación IA debe seguir apoyándose en textos jurídicos oficiales, no en resúmenes inventados por el constructor.

El Constructor V2 debe intentar conseguir un mapa jurídico suficientemente completo del tema, incluyendo artículos aunque no tengan todavía preguntas en `lote_preguntas`.

Debe distinguirse:

- `cobertura_corpus`: grado de representación jurídica del tema;
- `cobertura_banco`: grado de disponibilidad de preguntas para esas referencias.

Una baja cobertura de banco no implica una baja cobertura del corpus.

## 9. Uso de `lote_preguntas`

`lote_preguntas` se utiliza para:

- localizar pares `norma + artículo` ya presentes;
- conocer cuántas preguntas disponibles existen por referencia;
- contrastar decisiones sobre artículos;
- incorporar al banco las preguntas que coincidan con referencias aprobadas.

No se utiliza para definir por sí solo el alcance jurídico de un tema.

## 10. Reutilización entre convocatorias

La reutilización es deseable, pero debe ser explícita.

Se puede reutilizar una relación `tema -> norma -> artículo` cuando:

- el contenido del nuevo tema sea equivalente o más amplio de forma comprobable;
- la relación de origen ya esté validada;
- se conserve la procedencia de la reutilización.

No se debe asumir equivalencia sólo por similitud verbal del título del tema.

## 11. Modo de seguridad

La primera implementación será exclusivamente `SOLO LECTURA`.

No podrá:

- modificar `temario_referencias`;
- modificar `normas`;
- modificar corpus;
- modificar bancos;
- modificar `lote_preguntas`.

Sólo generará informes/candidatos para comparación.

Las operaciones de escritura se añadirán únicamente después de validar el comportamiento sobre convocatorias existentes.

## 12. Validación antes de usar A1

El primer banco de pruebas será una convocatoria existente con temario ya revisado, inicialmente C1.

El Constructor V2 recibirá el documento oficial y las fuentes disponibles, pero su resultado se comparará posteriormente con el temario consolidado ya existente.

Se medirán al menos:

- referencias existentes reproducidas correctamente;
- referencias existentes no detectadas;
- referencias nuevas propuestas;
- falsos positivos;
- casos enviados correctamente a revisión.

No se fijará de antemano un porcentaje de éxito que obligue a aceptar resultados incorrectos. Los tipos de error serán más importantes que una cifra global.

Después se repetirá con C2.

Sólo tras estas pruebas se utilizará el procedimiento para construir A1.

## 13. Integración futura en el menú

El Constructor V2 se integrará en `menu_mantenimiento.py`, pero no en la primera fase.

La secuencia prevista será aproximadamente:

1. Analizar temario oficial.
2. Proponer normas.
3. Proponer artículos.
4. Revisar candidatos.
5. Generar `temario.csv` compatible.
6. Aplicar el flujo actual de alta/importación.
7. Construir/verificar corpus.
8. Mantener banco con el procedimiento consolidado.

El menú debe dejar claro qué operaciones son sólo lectura y cuáles escriben en la BD.

## 14. Papel de `temario.csv`

`temario.csv` se conserva.

Deja de ser necesariamente un fichero elaborado a mano y pasa a poder ser el producto exportable/auditable del Constructor V2.

Debe continuar siendo compatible con el importador actual mientras no exista una razón validada para modificar ese contrato.

## 15. Regla contra inferencias no verificadas

Ante cualquier ambigüedad:

- no completar huecos con conocimiento general;
- no asumir que una norma es aplicable por plausibilidad;
- no expandir automáticamente una materia a artículos no demostrados;
- no interpretar silencio del temario como autorización para incluir contenido;
- marcar `REVISION` y mostrar la evidencia disponible.

Es preferible dejar una referencia pendiente a introducir una referencia falsa.

## 16. Desarrollo por fases

### Fase 1

Sólo lectura: inventario técnico y reconstrucción de C1.

### Fase 2

Propuestas de normas, todavía sin escritura.

### Fase 3

Propuestas de artículos, todavía sin escritura.

### Fase 4

Comparación sistemática contra C1 y C2 consolidados.

### Fase 5

Interfaz de revisión y exportación de `temario.csv`.

### Fase 6

Integración controlada con los procesos existentes de corpus y bancos.

### Fase 7

Alta de A1 mediante el nuevo procedimiento.

Cada fase debe poder detenerse sin afectar al funcionamiento actual de Mantenimiento ni de Tu Coach.

## 17. Primer paso de implementación

El primer script nuevo no intentará resolver normas ni artículos.

Su única función será auditar, en solo lectura, la información disponible para una convocatoria consolidada (C1):

- temas oficiales existentes en BD;
- referencias actuales por tema;
- catálogo de normas utilizado;
- artículos enlazados;
- preguntas disponibles por referencia;
- corpus asociados.

Ese inventario servirá como línea base antes de implementar cualquier inferencia.
