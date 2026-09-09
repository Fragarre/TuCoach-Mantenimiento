# Piloto pseudonormativo — A1 Especial 1

## Estado

`BORRADOR_DE_ESTRUCTURA`

Este documento no es legislación ni una pseudonorma aceptada. Define el primer caso piloto para validar la arquitectura antes de modificar base de datos, importadores, bancos o corpus.

## Tema oficial

**A1 — Parte Especial — Tema 1**

> Las fuentes del derecho administrativo (I): concepto de fuentes del derecho. Las normas jurídicas: aplicación y eficacia general. El derecho de la Unión Europea. Los tratados internacionales. La primacía del Derecho europeo respecto de la Constitución y sus consecuencias para el sistema de fuentes. La Constitución. La ley: sus clases. Los principios de legalidad, reserva de ley, de jerarquía normativa y de competencia. Los estatutos de autonomía. Las leyes de las comunidades autónomas. Referencia a la costumbre y principios generales del derecho.

Clasificación V2:

`JURIDICO_DOCTRINAL_TRANSVERSAL`

## Material de apoyo existente

En la carpeta jurídica aportada existe el documento:

`Fuentes de dercho I.pdf`

Su contenido sigue de forma bastante próxima la secuencia del tema oficial y demuestra que una representación pseudonormativa es viable.

Sin embargo, **el documento no se acepta como fuente jurídica validada por el mero hecho de existir**. Contiene síntesis doctrinales, referencias legales y formulaciones que deben contrastarse una a una antes de formar parte del corpus definitivo.

## Identidad técnica propuesta

`PSEUDO:A1_FUENTES_DERECHO_ADMINISTRATIVO_I`

La identidad definitiva debería ser reutilizable entre convocatorias si el contenido material es equivalente. El prefijo `A1_` se mantiene aquí sólo durante el piloto para evitar decisiones prematuras sobre reutilización global.

## Pseudoartículos candidatos

### `P01` — Concepto y clasificación de las fuentes del Derecho administrativo

Cobertura:

- concepto de fuente del Derecho;
- fuentes directas e indirectas;
- fuentes primarias y subsidiarias;
- posición de las fuentes en Derecho administrativo.

Tipo principal: `SINTESIS_DOCTRINAL`

Estado: `PENDIENTE_VERIFICACION`

### `P02` — Aplicación, eficacia y vigencia de las normas jurídicas

Cobertura:

- entrada en vigor;
- derogación y pérdida de vigencia;
- irretroactividad;
- aplicación general de las normas.

Tipo principal: `MIXTO_NORMATIVO_DOCTRINAL`

Fuentes a verificar, entre otras:

- Código Civil;
- Constitución Española.

Estado: `PENDIENTE_VERIFICACION`

**Control especial:** no atribuir a una norma una regla que proceda de otra. El material de apoyo existente mezcla en este punto referencias al Código Civil y a la Ley 39/2015, por lo que debe reconstruirse desde las fuentes primarias antes de aceptarlo.

### `P03` — Derecho de la Unión Europea: estructura del ordenamiento

Cobertura:

- Derecho originario;
- Derecho derivado;
- reglamentos, directivas, decisiones, recomendaciones y dictámenes;
- efecto directo cuando proceda.

Tipo principal: `MIXTO_NORMATIVO_DOCTRINAL`

Fuentes a verificar:

- TUE;
- TFUE;
- Carta de Derechos Fundamentales cuando proceda.

Estado: `PENDIENTE_VERIFICACION`

### `P04` — Tratados internacionales en el ordenamiento español

Cobertura:

- celebración e integración;
- publicación;
- posición en el ordenamiento;
- modificación, derogación o suspensión conforme a su régimen propio.

Tipo principal: `NORMATIVO_CON_EXPLICACION`

Fuentes a verificar:

- Constitución Española, especialmente el régimen constitucional de tratados;
- normativa complementaria cuando sea necesaria.

Estado: `PENDIENTE_VERIFICACION`

### `P05` — Primacía del Derecho de la Unión Europea y relación con la Constitución

Cobertura:

- primacía;
- supremacía constitucional;
- inaplicación de la norma interna incompatible;
- consecuencias para el sistema de fuentes;
- papel del TJUE y del Tribunal Constitucional.

Tipo principal: `JURISPRUDENCIAL_DOCTRINAL`

Fuentes a verificar:

- TUE/TFUE cuando proceda;
- Constitución Española;
- jurisprudencia TJUE;
- jurisprudencia y doctrina constitucional española relevante.

Estado: `PENDIENTE_VERIFICACION`

### `P06` — La Constitución como norma superior del ordenamiento

Cobertura:

- vinculación de ciudadanos y poderes públicos;
- supremacía constitucional;
- control de constitucionalidad;
- reforma constitucional únicamente en cuanto sea necesaria para explicar su posición en el sistema de fuentes.

Tipo principal: `NORMATIVO_CON_EXPLICACION`

Fuente principal:

- Constitución Española.

Estado: `PENDIENTE_VERIFICACION`

### `P07` — La ley y sus clases

Cobertura:

- ley orgánica;
- ley ordinaria;
- decreto legislativo;
- decreto-ley;
- límites y relaciones esenciales entre estas figuras.

Tipo principal: `NORMATIVO_CON_EXPLICACION`

Fuente principal:

- Constitución Española.

Estado: `PENDIENTE_VERIFICACION`

### `P08` — Principios de legalidad, reserva de ley, jerarquía normativa y competencia

Cobertura:

- principio de legalidad;
- reserva de ley;
- jerarquía normativa;
- principio de competencia;
- diferencia entre jerarquía y competencia cuando resulte necesaria.

Tipo principal: `MIXTO_NORMATIVO_DOCTRINAL`

Fuentes a verificar:

- Constitución Española;
- legislación administrativa general cuando proceda;
- jurisprudencia si una conclusión depende de ella.

Estado: `PENDIENTE_VERIFICACION`

### `P09` — Estatutos de autonomía y leyes de las comunidades autónomas

Cobertura:

- naturaleza de los estatutos;
- función institucional;
- potestad legislativa autonómica;
- relación competencial entre legislación estatal y autonómica.

Tipo principal: `MIXTO_NORMATIVO_DOCTRINAL`

Fuentes a verificar:

- Constitución Española;
- Estatuto de Autonomía de la Comunitat Valenciana para ejemplos o concreciones aplicables a la convocatoria.

Estado: `PENDIENTE_VERIFICACION`

### `P10` — Costumbre y principios generales del Derecho

Cobertura:

- papel de la costumbre;
- subsidiariedad;
- principios generales del Derecho como fuente, criterio interpretativo e informador;
- particularidades de su utilización en Derecho administrativo.

Tipo principal: `SINTESIS_DOCTRINAL_CON_RESPALDO`

Fuentes a verificar:

- Código Civil;
- legislación y jurisprudencia administrativa cuando sean necesarias para afirmaciones específicamente administrativas.

Estado: `PENDIENTE_VERIFICACION`

## Regla de construcción del contenido

Cada pseudoartículo aceptado deberá almacenar o poder reconstruir cuatro capas distintas:

1. `TEXTO_TEMARIO`: fragmento del temario oficial que debe cubrir.
2. `SINTESIS`: explicación jurídica redactada para el opositor.
3. `RESPALDOS`: fuentes reales que sustentan cada proposición jurídica relevante.
4. `NOTAS_DIDACTICAS`: ejemplos o aclaraciones que no deben confundirse con normas.

## Regla de validación

Un pseudoartículo no pasa a `ACEPTADO` hasta que:

- cubra una parte identificable del tema oficial;
- todas sus afirmaciones jurídicas relevantes tengan respaldo localizado;
- las referencias normativas hayan sido comprobadas contra la fuente real;
- las afirmaciones jurisprudenciales se hayan contrastado con la resolución correspondiente o con una fuente institucional fiable;
- no presente como literal una síntesis doctrinal;
- no introduzca materia ajena al alcance razonable del tema.

## Uso previsto en preguntas

Una futura pregunta derivada de este piloto podrá clasificarse de tres formas:

- `REAL`: responde directamente a una norma/artículo real identificado;
- `PSEUDO`: responde a una síntesis doctrinal integrada en un pseudoartículo;
- `MIXTA`: la formulación necesita comprensión doctrinal, pero su respuesta puede respaldarse además en uno o varios artículos reales.

La categoría técnica nunca debe mostrarse como si `P01`, `P02`, etc. fueran artículos jurídicos oficiales.

## Uso previsto en RAG/chat

El RAG podrá recuperar el pseudoartículo para explicar la materia y, simultáneamente, sus respaldos oficiales para fundamentar jurídicamente la respuesta.

Prioridad de respuesta:

1. fuente oficial cuando el usuario pide fundamento jurídico;
2. síntesis pseudonormativa para explicación y preparación del tema;
3. señalización clara cuando una cuestión sea doctrinal o jurisprudencial y no pueda reducirse a un único artículo.

## Resultado esperado del piloto

El piloto se considerará satisfactorio si permite:

- representar completamente el texto oficial del Tema 1 sin fingir una lista exhaustiva de artículos;
- mantener trazabilidad desde cada unidad de estudio hasta sus fuentes reales;
- generar preguntas útiles y jurídicamente controlables;
- integrar el material en el corpus sin confundirlo con legislación;
- demostrar que el mismo mecanismo puede reutilizarse para otros temas `JURIDICO_DOCTRINAL_TRANSVERSAL`.

## Siguiente paso

Construir el contenido verificable de `P01`–`P10` a partir de las fuentes disponibles, comenzando por un pseudoartículo piloto y auditando después su precisión jurídica antes de automatizar el resto.