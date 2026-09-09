# Capa pseudonormativa del Constructor de Temarios V2

## 1. Motivación

Las convocatorias modernas pueden definir materias jurídicas mediante conceptos, instituciones, principios o ámbitos sectoriales sin citar de forma exhaustiva las normas y artículos que deben estudiarse.

El Constructor V2 no debe responder a esa ambigüedad fabricando una precisión normativa que el temario oficial no contiene.

Cuando un tema no pueda representarse con rigor suficiente mediante una relación exhaustiva:

`tema -> norma oficial -> artículo oficial`

se podrá construir una **pseudonorma de apoyo**, expresamente diferenciada de la legislación oficial, que represente de forma estructurada el contenido jurídico exigible por el texto oficial del tema.

La pseudonorma es una unidad interna de conocimiento y estudio. No es una fuente del Derecho, no tiene vigencia normativa y nunca debe presentarse al opositor como legislación.

## 2. Objetivo

La capa pseudonormativa pretende cubrir cuatro necesidades sin degradar la calidad jurídica:

1. representar temas doctrinales o transversales cuyo alcance no cabe en una lista cerrada de artículos;
2. proporcionar contexto preciso para generación IA de preguntas;
3. permitir incorporar preguntas al banco mediante unidades de conocimiento trazables;
4. alimentar el corpus RAG/chat con material explicativo estructurado y respaldado por fuentes jurídicas reales.

## 3. Principio de doble verdad

El sistema debe conservar dos planos separados.

### 3.1. Verdad normativa oficial

Procede exclusivamente de normas y fuentes oficiales:

`normas -> norma_fuentes -> articulos_fuente`

Sus artículos conservan su identidad jurídica real.

### 3.2. Representación pseudonormativa

Es una síntesis estructurada construida específicamente para un punto del temario.

Relación conceptual:

`tema oficial -> pseudonorma -> pseudoartículos -> fuentes reales de respaldo`

La pseudonorma nunca sustituye ni altera la fuente oficial. Su función es representar el ámbito de estudio cuando la convocatoria no permite derivarlo exhaustivamente de una sola norma o conjunto inequívoco de artículos.

## 4. Cuándo procede

### `JURIDICO_EXPLICITO`

Destino preferente:

`normas reales + artículos reales`

No debe crearse una pseudonorma si las referencias oficiales cubren adecuadamente el tema.

### `JURIDICO_DELIMITABLE`

Primero se intentará resolver normas y artículos reales.

Puede utilizarse pseudonorma si, tras la revisión, quedan contenidos relevantes del tema que no puedan adscribirse con suficiente seguridad a un conjunto exhaustivo de artículos oficiales.

Se admite por tanto una solución híbrida:

`artículos oficiales + pseudonorma complementaria`

### `JURIDICO_DOCTRINAL_TRANSVERSAL`

Es el candidato natural a pseudonorma.

No se exigirá una lista exhaustiva de artículos oficiales. La pseudonorma podrá constituir la representación principal del contenido, acompañada por normas y artículos de respaldo allí donde exista correspondencia clara.

### `NO_JURIDICO`

No utiliza pseudonormas jurídicas. Mantiene el camino temático no jurídico consolidado.

## 5. Estructura conceptual

Una pseudonorma debe tener una identidad estable, por ejemplo:

`PSEUDO:FUENTES_DERECHO_ADMINISTRATIVO_I`

Y dividirse en pseudoartículos o unidades de conocimiento:

- `P01` Concepto y clasificación de las fuentes.
- `P02` Aplicación y eficacia de las normas.
- `P03` Derecho originario y derivado de la Unión Europea.
- `P04` Tratados internacionales.
- `P05` Primacía del Derecho de la Unión Europea.
- `P06` Constitución y ley.
- `P07` Legalidad, jerarquía, reserva de ley y competencia.
- `P08` Estatutos y leyes autonómicas.
- `P09` Costumbre y principios generales del Derecho.

Los identificadores son técnicos. No deben presentarse como artículos legales.

## 6. Regla crítica de trazabilidad

Cada pseudoartículo debe registrar las fuentes que respaldan su contenido.

Ejemplo conceptual:

`P05 Primacía del Derecho de la Unión Europea`

Fuentes de respaldo:

- disposiciones concretas de TUE/TFUE cuando proceda;
- Constitución Española cuando proceda;
- jurisprudencia TJUE/TC cuando sea necesaria;
- otra fuente jurídica oficial o doctrinal previamente aceptada.

La relación debe permitir distinguir:

- `FUENTE_PRIMARIA_OFICIAL`;
- `JURISPRUDENCIA`;
- `FUENTE_INSTITUCIONAL`;
- `DOCTRINA_DE_APOYO`.

Una afirmación sin respaldo suficiente queda en revisión y no debe convertirse automáticamente en contenido aceptado.

## 7. Calidad jurídica de las pseudonormas

Los documentos de apoyo existentes pueden servir como materia prima, pero no se consideran automáticamente correctos por estar ya en la carpeta de fuentes jurídicas.

Antes de incorporarlos como pseudonorma deben pasar por un proceso de verificación:

1. segmentar el contenido según el texto literal del tema;
2. identificar las afirmaciones jurídicas relevantes;
3. localizar y asociar fuentes de respaldo;
4. comprobar que las referencias citadas respaldan realmente la afirmación;
5. corregir o eliminar formulaciones no verificables;
6. registrar fecha y versión de la revisión;
7. aprobar manualmente la pseudonorma antes de publicarla.

La IA puede redactar, estructurar y proponer respaldos, pero no puede aprobar por sí sola el contenido jurídico final.

## 8. Separación entre texto oficial y explicación

Dentro de una pseudonorma deben distinguirse al menos:

- **contenido normativo literal**, cuando se reproduzca o parafrasee una fuente oficial identificada;
- **explicación o síntesis**, cuando se integre información procedente de varias fuentes;
- **criterio jurisprudencial**, cuando dependa de jurisprudencia;
- **nota didáctica**, cuando sea una ayuda de estudio y no una proposición normativa.

Esta separación es necesaria tanto para el RAG como para la generación de preguntas.

## 9. Generación IA de preguntas

Para un tema con pseudonorma, el contexto de generación puede componerse de:

`texto oficial del tema + pseudoartículos aceptados + fuentes oficiales enlazadas`

La pregunta generada debe conservar el tipo de evidencia de la que procede.

Cuando una pregunta dependa directamente de un artículo real, debe seguir normalizándose contra la norma y artículo reales.

Cuando dependa de contenido doctrinal integrado, podrá vincularse al pseudoartículo correspondiente, sin presentarlo al usuario como una referencia legal.

## 10. Banco de preguntas

La pseudonorma no debe modificar el principio de que `lote_preguntas` es el repositorio maestro acumulativo.

La futura integración deberá permitir que una pregunta tenga, según proceda:

- referencia normativa real;
- referencia pseudonormativa;
- ambas, cuando la pregunta doctrinal se apoye además en uno o varios artículos reales.

No se implementa todavía este cambio. Antes debe diseñarse y probarse el contrato de compatibilidad con el mantenimiento consolidado.

## 11. Corpus RAG/chat

Las pseudonormas pueden incorporarse al corpus, pero deben estar etiquetadas inequívocamente como:

`FUENTE JURIDICA DE APOYO / PSEUDONORMA`

Nunca como `NORMA VIGENTE`.

El RAG debe poder recuperar conjuntamente:

- la pseudonorma para explicar el tema;
- las fuentes oficiales enlazadas para verificar o citar jurídicamente la respuesta.

Siempre que el usuario solicite fundamento normativo, la respuesta debe priorizar y citar la fuente real, no el identificador pseudoarticular.

## 12. Relación con `temario.csv`

Se pretende conservar el contrato conceptual de referencia por unidades, pero no se decide todavía el formato definitivo.

Una representación posible sería:

`ESPECIAL;1;...;PSEUDO:FUENTES_DERECHO_ADMINISTRATIVO_I;P01;JURIDICO_PSEUDO`

Esta sintaxis es sólo una propuesta de diseño. No debe incorporarse al importador actual hasta verificar que no rompe el catálogo canónico, `temario_referencias`, el banco ni los corpus.

## 13. No contaminar el catálogo `normas`

Regla de arquitectura:

**una pseudonorma no debe introducirse en `normas` fingiendo que es una norma oficial.**

El catálogo `normas` conserva exclusivamente identidades normativas reales.

Si finalmente se necesita persistencia estructurada, la pseudonorma deberá disponer de identidad y almacenamiento diferenciados, aunque los consumidores puedan recibir una interfaz homogénea de `fuente + unidad`.

## 14. Ciclo de vida y versionado

Una pseudonorma debe ser versionable porque depende de:

- el temario oficial de la convocatoria;
- legislación y jurisprudencia vigentes;
- revisión jurídica posterior.

Estados conceptuales mínimos:

- `BORRADOR`;
- `REVISION`;
- `ACEPTADA`;
- `OBSOLETA`.

Debe conservarse:

- identificador estable;
- versión;
- fecha de revisión;
- convocatoria/temas a los que aplica;
- fuentes utilizadas;
- motivo de cualquier modificación relevante.

## 15. Reutilización entre convocatorias

Una pseudonorma puede reutilizarse cuando dos temas sean materialmente equivalentes, pero no sólo porque tengan títulos similares.

La reutilización puede ser:

- completa;
- parcial por pseudoartículos;
- mediante una nueva versión derivada.

Esto permite construir progresivamente una biblioteca jurídica didáctica propia sin perder trazabilidad.

## 16. Ventaja funcional buscada

La finalidad no es crear más contenido, sino representar mejor el alcance real de convocatorias cada vez menos explícitas.

Una solución robusta debe ser capaz de combinar:

- literalidad normativa cuando existe;
- inferencia jurídica controlada cuando la materia es delimitable;
- síntesis pseudonormativa trazable cuando el temario es doctrinal o transversal;
- contenido temático no jurídico cuando no procede ninguna norma.

La competencia del sistema depende de no confundir estos cuatro casos.

## 17. Fase actual de implementación

En esta fase:

- no se modifica la base de datos;
- no se modifica `temario_referencias`;
- no se modifica `normas`;
- no se modifica `lote_preguntas`;
- no se modifica el mantenimiento del banco;
- no se modifica el importador de `temario.csv`.

El siguiente paso será construir y auditar **una pseudonorma piloto** sobre un tema doctrinal real de A1 utilizando los documentos jurídicos de apoyo existentes y las fuentes oficiales enlazables. Sólo después se diseñará la persistencia definitiva.