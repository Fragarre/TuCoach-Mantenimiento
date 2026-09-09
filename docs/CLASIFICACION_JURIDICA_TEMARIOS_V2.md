# Clasificación jurídica del Constructor de Temarios V2

## Objetivo

Antes de resolver artículos, el Constructor V2 debe distinguir qué grado de precisión jurídica permite realmente el texto oficial de cada tema.

No todo tema jurídico admite una transformación exhaustiva y objetiva a `tema -> norma -> artículo`.

Esta clasificación es de trabajo y no modifica por sí misma las estructuras consolidadas de la aplicación.

## Categorías

### 1. `JURIDICO_EXPLICITO`

El propio temario oficial identifica de forma literal una o varias normas o una estructura normativa suficientemente concreta.

Ejemplos de evidencia válida:

- nombre o número de una ley, decreto, reglamento, tratado u otra norma;
- título, capítulo, sección o artículos de una norma identificada;
- denominación oficial inequívoca que pueda resolverse contra el catálogo canónico `normas`.

Tratamiento:

1. Resolver la norma contra `normas` / `norma_fuentes`.
2. Determinar los artículos mediante la estructura citada y `articulos_fuente`.
3. Marcar para revisión cualquier expansión no demostrada por el texto oficial.

Una cita explícita de una norma no implica por sí sola que toda la norma forme parte del temario.

### 2. `JURIDICO_DELIMITABLE`

El tema es jurídico y no cita necesariamente la norma de forma literal, pero su materia permite identificar con evidencia suficientemente fuerte una norma nuclear y un ámbito normativo razonablemente acotable.

Ejemplos típicos:

- órganos administrativos, competencia, delegación, avocación;
- situaciones administrativas, provisión, movilidad o régimen disciplinario cuando el ámbito legal aplicable es identificable;
- materias presupuestarias o de control cuya regulación principal es reconocible y puede contrastarse con texto oficial.

Fuentes admisibles para proponer normas, por orden de fiabilidad:

1. contenido oficial del tema;
2. contenido oficial de las normas (`norma_fuentes` + `articulos_fuente`);
3. mapeos previamente validados de otras convocatorias realmente equivalentes;
4. `lote_preguntas` como evidencia auxiliar;
5. similitud semántica o IA sólo como propuesta.

Tratamiento:

- las normas se proponen con trazabilidad;
- ninguna norma inferida se acepta automáticamente sólo por similitud;
- los artículos se proponen únicamente cuando exista correspondencia demostrable con el contenido del tema;
- si el alcance completo no puede delimitarse, queda en `REVISION`.

### 3. `JURIDICO_DOCTRINAL_TRANSVERSAL`

El tema es jurídico, pero su contenido es doctrinal, transversal o mezcla varias fuentes y principios de forma que no existe una lista única y objetivamente deducible de normas y artículos que represente exhaustivamente el tema.

Ejemplos:

- teoría general de las fuentes del Derecho;
- relaciones entre Constitución, Derecho de la Unión Europea, tratados, estatutos, leyes, costumbre y principios generales;
- principios generales como legalidad, jerarquía normativa, reserva de ley o competencia cuando aparecen como materia doctrinal transversal;
- temas que combinan instituciones, conceptos y fuentes jurídicas heterogéneas sin delimitación normativa expresa.

Tratamiento:

1. El texto oficial literal del tema es la referencia primaria.
2. Pueden asociarse `normas_de_apoyo` cuando estén justificadas.
3. Pueden proponerse artículos concretos cuando exista correspondencia clara, pero no se exige ni se presume una lista exhaustiva.
4. El sistema debe distinguir entre:
   - artículos explícitamente o claramente cubiertos;
   - normas de apoyo;
   - contenido doctrinal no reducible a artículos.
5. La ausencia de una lista exhaustiva de artículos no se considera un fallo del constructor.

Para generación IA, el contexto de estos temas no debe reducirse artificialmente a los artículos asociados. El texto oficial del tema y los apoyos jurídicos validados deben conservarse como contexto complementario.

## Relación con la clasificación jurídica/no jurídica existente

La primera decisión sigue siendo:

- `NO_JURIDICO`
- `JURIDICO`
- `REVISION` cuando ni siquiera esa distinción sea segura.

Sólo después, para un tema jurídico, se determina su `alcance_juridico_v2`:

- `JURIDICO_EXPLICITO`
- `JURIDICO_DELIMITABLE`
- `JURIDICO_DOCTRINAL_TRANSVERSAL`
- `REVISION`

No se sustituye todavía `temario_temas.tipo_contenido` ni se modifica el esquema de la base de datos.

## Regla de seguridad para artículos

El Constructor V2 no debe fabricar precisión.

Por tanto:

- no se exige una cobertura exhaustiva de artículos para temas doctrinales/transversales;
- no se transforma una norma relacionada en una norma definitiva sin evidencia suficiente;
- no se incorpora un artículo sólo porque contiene palabras parecidas al tema;
- no se utiliza el banco actual de preguntas para definir el alcance completo del temario;
- no se usa la verdad consolidada de la convocatoria patrón como entrada durante una prueba de regresión.

## Resultado esperado antes de resolver artículos

Para cada tema jurídico, el diagnóstico deberá mostrar al menos:

- parte y número de tema;
- texto oficial literal;
- clasificación jurídica básica;
- `alcance_juridico_v2`;
- evidencia que justifica esa clasificación;
- normas explícitas, si existen;
- normas candidatas/de apoyo, si procede;
- si es razonable intentar resolver artículos automáticamente o debe mantenerse revisión humana.

## Validación con C1 y C2

C1 y C2 se mantienen inmutables y sólo actúan como patrón de regresión.

El proceso V2 debe producir su clasificación independientemente y compararla después con la representación consolidada.

Un desacuerdo debe corregirse en el algoritmo o quedar en revisión; nunca se modifica C1/C2 para acomodarlo al algoritmo.

## Aplicación a A1

A1 será la primera convocatoria construida realmente con V2.

Antes de resolver artículos de A1, cada tema jurídico debe pasar por esta clasificación de alcance.

Los temas `JURIDICO_DOCTRINAL_TRANSVERSAL` no serán obligados a producir una relación exhaustiva `tema -> norma -> artículo`. Para ellos se conservará el texto oficial como núcleo y las normas/artículos se tratarán como apoyos trazables y revisables.
