# Arquitectura del Constructor de Temarios V2

## 1. Objetivo

Construir un procedimiento reutilizable para incorporar futuras convocatorias a OpoCoach/Tu Coach partiendo del temario oficial, conservando la estructura y los contratos actuales del proyecto.

El objetivo no es inferir automáticamente todo el contenido de un temario sin supervisión. El objetivo es automatizar únicamente aquello que pueda determinarse con evidencia suficiente, registrar los casos dudosos y facilitar una revisión humana limitada y trazable.

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
- `equivalencias_temas_no_juridicos` y demás estructuras consolidadas utilizadas por el mantenimiento del banco.

El nuevo sistema actúa antes de estas estructuras: ayuda a construir y revisar la representación del temario y, una vez aprobada, entrega datos compatibles con el flujo actual.

## 3. Dos clases de puntos del temario

El Constructor V2 no debe asumir que todos los puntos de una convocatoria están sometidos a una norma jurídica.

Cada punto debe conservar su `tipo_contenido` y seguir uno de los dos caminos consolidados.

### 3.1. Puntos jurídicos

Son los puntos cuyo contenido se delimita mediante normas y artículos.

Relación principal:

`tema -> norma -> artículo`

La validación de preguntas para el banco se realiza por las propiedades de la pregunta y por la coincidencia entre:

`lote_preguntas.norma_id_normalizada + articulo_normalizado`

y las referencias jurídicas aprobadas del temario.

### 3.2. Puntos no jurídicos

Son puntos que no se delimitan por una relación `norma + artículo`, por ejemplo determinados contenidos de informática.

No deben crearse normas ni artículos ficticios para representarlos.

Su definición parte del texto oficial del punto y de su ámbito temático. Las preguntas pueden proceder de generación IA o de otras fuentes y se incorporan primero a `lote_preguntas`.

La vinculación posterior con un banco se realiza por el mecanismo no jurídico consolidado: categoría/equivalencia temática y correspondencia de contenido con un punto del temario.

Relación conceptual:

`texto oficial del tema -> categoría/ámbito temático -> preguntas compatibles -> banco`

El Constructor V2 debe conservar ambos caminos y no convertir un problema no jurídico en uno jurídico.

## 4. Productos distintos de una convocatoria

Una convocatoria necesita varios resultados relacionados, pero no intercambiables.

### 4.1. Mapa jurídico del temario

Sólo para puntos jurídicos:

`tema -> norma -> artículo`

Debe representar los artículos que realmente forman parte del temario, aunque actualmente no existan preguntas sobre ellos.

Sirve para:

- construir `temario_referencias`;
- generar el corpus preciso utilizado como contexto de generación IA jurídica;
- determinar qué preguntas jurídicas pueden entrar en el banco.

### 4.2. Mapa no jurídico del temario

Para puntos no jurídicos:

`tema -> texto oficial -> categoría/ámbito temático`

Sirve para:

- generar preguntas IA basadas en el contenido real del punto;
- clasificar preguntas importadas de otras fuentes;
- mantener las equivalencias necesarias para vincular preguntas no jurídicas con el punto correcto.

No utiliza `norma + artículo` como criterio de pertenencia.

### 4.3. Banco de preguntas

`lote_preguntas` es el repositorio maestro y acumulativo de preguntas disponibles. No pertenece a una convocatoria concreta y no debe modificarse como efecto colateral de construir o sincronizar un banco.

Todas las preguntas, incluidas las generadas por IA, deben incorporarse primero a `lote_preguntas` con sus propiedades y normalizaciones correspondientes.

Después, el mantenimiento consolidado decide si una pregunta es apta para un banco concreto.

Camino jurídico:

`temario_referencias aprobadas -> propiedades/normalización de lote_preguntas -> banco_preguntas`

Camino no jurídico:

`equivalencias temáticas aprobadas -> propiedades/categoría de lote_preguntas -> banco_preguntas`

Una pregunta que no pertenezca a una convocatoria permanece en `lote_preguntas` y puede ser válida para otra convocatoria presente o futura.

### 4.4. Corpus RAG de convocatoria

Incluye las normas completas que pertenecen al temario jurídico, aunque sólo algunos artículos sean objeto directo de examen.

Relación:

`normas aprobadas para la convocatoria -> documentos completos -> convocatoria_documentos_corpus`

Este corpus no debe depender de que todos los artículos de una norma pertenezcan al temario ni de que existan preguntas sobre ellos.

## 5. Catálogo de normas

El catálogo `normas` continúa siendo la fuente canónica.

Reglas:

1. Ningún proceso nuevo debe crear una norma duplicada por una variante textual de su nombre.
2. Toda norma candidata debe intentar resolverse primero contra el catálogo existente.
3. Si no existe una correspondencia segura, el sistema debe marcarla para revisión en vez de crear una norma automáticamente.
4. La normalización de nombres debe ser trazable.
5. Estas reglas sólo se aplican a puntos jurídicos. Un punto no jurídico no debe recibir una norma artificial para encajar en este modelo.

## 6. Flujo de construcción

El flujo general previsto es:

`temario oficial -> temas -> clasificación jurídica/no jurídica -> tratamiento específico -> revisión -> temario definitivo -> flujo actual`

### Fase A. Extraer temas

Se extraen del documento oficial, sin interpretación adicional:

- parte;
- número de tema;
- texto literal del tema;
- normas citadas explícitamente;
- títulos, capítulos, secciones u otras estructuras mencionadas literalmente.

La extracción debe conservar el texto original para poder auditar cualquier decisión posterior.

### Fase B. Clasificar el tipo de contenido

Cada punto debe identificarse como jurídico o no jurídico utilizando la información del temario y las reglas ya consolidadas cuando existan.

Una clasificación dudosa se marca para revisión. No se fuerza una clasificación jurídica para poder aplicar normas y artículos.

### Fase C. Resolver normas candidatas de puntos jurídicos

Las normas se clasifican por origen.

- `PDF_EXPLICITO`: la norma aparece literalmente en el temario.
- `REUTILIZADO`: existe una asociación ya validada en otra convocatoria claramente equivalente.
- `INFERIDO`: la materia permite proponer una norma, pero no está citada literalmente.
- `MANUAL`: asociación introducida o confirmada por revisión humana.

Regla crítica: una norma `INFERIDO` nunca debe pasar automáticamente a definitiva sólo por una respuesta de IA.

### Fase D. Resolver artículos candidatos de puntos jurídicos

Se utilizan, por orden de fiabilidad:

1. estructuras jurídicas explícitas del temario;
2. mapeos ya revisados en otras convocatorias cuando la equivalencia sea comprobable;
3. texto oficial de `articulos_fuente`;
4. preguntas disponibles en `lote_preguntas` como evidencia auxiliar;
5. análisis semántico/IA únicamente como propuesta.

La IA puede ayudar a encontrar candidatos. No puede convertir por sí sola una asociación dudosa en una referencia definitiva.

### Fase E. Resolver puntos no jurídicos

Para un punto no jurídico se conserva como fuente principal el texto literal del temario.

El proceso debe permitir:

- identificar o mantener su categoría temática;
- reutilizar equivalencias no jurídicas ya validadas cuando corresponda;
- proponer equivalencias por similitud de contenido únicamente como candidatos;
- utilizar el texto del punto como contexto para generación IA;
- revisar manualmente casos ambiguos.

No se generan filas ficticias de `temario_referencias` con normas o artículos inexistentes.

## 7. Estados de trabajo

Los candidatos deben tener como mínimo estos estados:

- `PROPUESTO`;
- `ACEPTADO`;
- `RECHAZADO`;
- `REVISION`.

No se escribirá en estructuras definitivas ninguna asociación que no esté en estado `ACEPTADO`.

## 8. Evidencia y trazabilidad

Cada candidato debe registrar por qué existe.

Campos conceptuales mínimos:

- convocatoria;
- tema;
- tipo de contenido;
- norma y artículo cuando proceda;
- categoría/equivalencia cuando proceda;
- origen;
- estado;
- evidencia o justificación breve;
- fecha de decisión;
- decisión automática o manual.

No se utilizará una puntuación numérica de confianza como sustituto de la evidencia.

## 9. Calidad de la generación IA

### 9.1. Generación jurídica

Debe seguir apoyándose en textos jurídicos oficiales, no en resúmenes inventados por el constructor.

El Constructor V2 debe intentar conseguir un mapa jurídico suficientemente completo del tema, incluyendo artículos aunque no tengan todavía preguntas en `lote_preguntas`.

Debe distinguirse:

- `cobertura_corpus`: grado de representación jurídica del tema;
- `cobertura_banco`: grado de disponibilidad de preguntas para esas referencias.

Una baja cobertura de banco no implica una baja cobertura del corpus.

### 9.2. Generación no jurídica

Debe apoyarse en el texto oficial del punto del temario y en los apoyos de contenido que utilice actualmente la aplicación para ese tipo de materia.

La ausencia de norma o artículo no es una carencia que deba corregirse: es una propiedad legítima del punto no jurídico.

Las preguntas generadas se incorporan a `lote_preguntas` y posteriormente se someten al mismo mecanismo consolidado de clasificación y pertenencia al banco.

## 10. Uso de `lote_preguntas`

`lote_preguntas` es el repositorio maestro acumulativo de todas las preguntas disponibles.

Se utiliza para:

- almacenar preguntas procedentes de IA, exámenes, academias u otras fuentes;
- conservar sus propiedades y normalizaciones;
- localizar pares `norma + artículo` ya presentes en preguntas jurídicas;
- conocer disponibilidad de preguntas por referencia;
- contrastar decisiones sobre artículos como evidencia auxiliar;
- localizar preguntas no jurídicas por su categoría/contenido;
- incorporar a cada banco únicamente las preguntas que cumplan las reglas de esa convocatoria.

Reglas críticas:

1. El Constructor V2 no elimina preguntas de `lote_preguntas`.
2. El Constructor V2 no modifica una pregunta de `lote_preguntas` como efecto de decidir si pertenece o no a un temario.
3. Una pregunta fuera del temario de una convocatoria sigue disponible para otras convocatorias.
4. Las bajas, correcciones, cambios de vigencia o normalización de `lote_preguntas` son operaciones expresas de mantenimiento, independientes de la construcción de un banco.
5. `lote_preguntas` no define por sí sola el alcance de un temario jurídico ni no jurídico.

## 11. Reutilización entre convocatorias

La reutilización es deseable, pero debe ser explícita.

En puntos jurídicos se puede reutilizar una relación `tema -> norma -> artículo` cuando la equivalencia esté comprobada y la relación de origen ya esté validada.

En puntos no jurídicos se pueden reutilizar categorías o equivalencias temáticas validadas cuando el contenido oficial sea realmente equivalente.

No se debe asumir equivalencia sólo por similitud verbal del título del tema.

## 12. Modo de seguridad

La primera implementación será exclusivamente `SOLO LECTURA`.

No podrá:

- modificar `temario_referencias`;
- modificar `normas`;
- modificar corpus;
- modificar bancos;
- modificar `lote_preguntas`;
- modificar equivalencias no jurídicas.

Sólo generará informes/candidatos para comparación.

Las operaciones de escritura se añadirán únicamente después de validar el comportamiento sobre convocatorias existentes.

## 13. Validación antes de usar A1

El primer banco de pruebas será una convocatoria existente con temario ya revisado, inicialmente C1.

La validación debe comprobar por separado ambos caminos.

### Jurídico

- referencias existentes reproducidas correctamente;
- referencias existentes no detectadas;
- referencias nuevas propuestas;
- falsos positivos;
- casos enviados correctamente a revisión.

### No jurídico

- puntos no jurídicos correctamente identificados;
- categorías/equivalencias existentes reproducidas;
- preguntas que el proceso consolidado considera aptas;
- ausencia de falsas referencias `norma + artículo`.

La línea base del banco debe reproducir exactamente el universo que determina el mantenimiento consolidado, no una reimplementación aproximada de sus reglas.

Después se repetirá con C2.

Sólo tras estas pruebas se utilizará el procedimiento para construir A1.

## 14. Integración futura en el menú

El Constructor V2 se integrará en `menu_mantenimiento.py`, pero no en la primera fase.

La secuencia prevista será aproximadamente:

1. Analizar temario oficial.
2. Clasificar puntos jurídicos/no jurídicos.
3. Proponer normas para puntos jurídicos.
4. Proponer artículos para puntos jurídicos.
5. Proponer/mantener categorías y equivalencias para puntos no jurídicos.
6. Revisar candidatos.
7. Generar `temario.csv` compatible y los demás datos necesarios según el contrato actual.
8. Aplicar el flujo actual de alta/importación.
9. Construir/verificar corpus.
10. Mantener banco con el procedimiento consolidado.

El menú debe dejar claro qué operaciones son sólo lectura y cuáles escriben en la BD.

## 15. Papel de `temario.csv`

`temario.csv` se conserva.

Deja de ser necesariamente un fichero elaborado a mano y pasa a poder ser el producto exportable/auditable del Constructor V2.

Debe continuar siendo compatible con el importador actual mientras no exista una razón validada para modificar ese contrato.

Los puntos no jurídicos deben conservar la representación que actualmente espera el sistema, sin forzarlos al modelo jurídico.

## 16. Regla contra inferencias no verificadas

Ante cualquier ambigüedad:

- no completar huecos con conocimiento general;
- no asumir que una norma es aplicable por plausibilidad;
- no expandir automáticamente una materia a artículos no demostrados;
- no convertir un punto no jurídico en jurídico para simplificar el procesamiento;
- no interpretar silencio del temario como autorización para incluir contenido;
- marcar `REVISION` y mostrar la evidencia disponible.

Es preferible dejar una asociación pendiente a introducir una asociación falsa.

## 17. Desarrollo por fases

### Fase 1

Sólo lectura: inventario técnico y línea base consolidada de C1, incluyendo camino jurídico y no jurídico.

### Fase 2

Repetición de la línea base en C2.

### Fase 3

Extracción y clasificación de puntos del PDF, todavía sin escritura.

### Fase 4

Propuestas de normas y artículos jurídicos y equivalencias no jurídicas, todavía sin escritura.

### Fase 5

Comparación sistemática contra C1 y C2 consolidados.

### Fase 6

Interfaz de revisión y exportación compatible.

### Fase 7

Integración controlada con los procesos existentes de corpus y bancos.

### Fase 8

Alta de A1 mediante el nuevo procedimiento.

Cada fase debe poder detenerse sin afectar al funcionamiento actual de Mantenimiento ni de Tu Coach.

## 18. Línea base de referencia

La auditoría V2 no debe inventar reglas propias para reproducir el banco. Debe reutilizar las reglas consolidadas de `mantener_banco_preguntas.py`.

Para C1, la línea base validada es:

- 35 temas;
- 1.146 referencias jurídicas completas;
- 10.843 preguntas jurídicas aptas;
- 1.060 preguntas no jurídicas aptas;
- 11.903 preguntas aptas en total;
- 11.903 preguntas presentes en banco;
- 0 preguntas del banco fuera del universo actual;
- 0 preguntas aptas ausentes del banco.

Esta línea base sirve como prueba de regresión del Constructor V2. No redefine el funcionamiento de `lote_preguntas` ni del banco.
