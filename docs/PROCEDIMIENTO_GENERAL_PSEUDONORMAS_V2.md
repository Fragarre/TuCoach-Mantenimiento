# Constructor Temario V2 — Procedimiento general de pseudonormas

## Estado

`DISEÑO GENERAL DERIVADO DEL PILOTO P05`

Este documento generaliza el procedimiento validado en el piloto A1 Especial 1 P05. No modifica la base de datos, el corpus, `temario.csv`, `temario_referencias`, `lote_preguntas` ni los bancos.

## 1. Objetivo

Representar de forma rigurosa contenidos jurídicos del temario que no pueden reducirse de manera exhaustiva y defendible a una relación `norma + artículo`, manteniendo siempre trazabilidad hacia fuentes jurídicas reales.

Una pseudonorma es una estructura interna de conocimiento y estudio. No es legislación, no se incorpora al catálogo canónico `normas` como si fuera una norma vigente y no puede citarse al usuario como autoridad jurídica.

## 2. Ámbito inicial

El procedimiento automático se aplica inicialmente a temas o segmentos clasificados como:

- `JURIDICO_DOCTRINAL_TRANSVERSAL`.

En una fase posterior podrá utilizarse de forma complementaria en `JURIDICO_DELIMITABLE` cuando exista una parte del contenido correctamente representada mediante normas/artículos reales y otra parte relevante que no pueda delimitarse de ese modo.

No se utiliza para:

- `JURIDICO_EXPLICITO` cuando las referencias reales permiten representar rigurosamente el contenido;
- `NO_JURIDICO`;
- contenido cuya clasificación jurídica permanezca en `REVISION`.

## 3. Principios invariantes

1. El texto literal del temario oficial es la fuente primaria para determinar el alcance de lo que debe estudiarse.
2. La pseudonorma nunca amplía silenciosamente el temario.
3. Toda proposición jurídica sustantiva debe conservar trazabilidad hacia una o más fuentes reales.
4. Una coincidencia léxica nunca convierte automáticamente una fuente en pertinente.
5. Verificar la identidad de una fuente y validar su pertinencia son operaciones distintas.
6. La recuperación correcta de un documento tampoco implica que respalde una proposición.
7. Ninguna unidad nace aceptada: el estado inicial es `REVISION`.
8. El catálogo canónico `normas` continúa reservado a normas jurídicas reales.
9. Las fuentes oficiales tienen prioridad sobre fuentes institucionales y doctrinales.
10. La generación IA y el RAG deben poder distinguir siempre contenido normativo real, jurisprudencial, síntesis y material didáctico.

## 4. Flujo general

```text
PDF oficial
    ↓
extracción literal del tema
    ↓
clasificación de alcance
    ↓
JURIDICO_DOCTRINAL_TRANSVERSAL
    ↓
segmentación conservadora
    ↓
unidades pseudonormativas
    ↓
proposiciones jurídicas candidatas
    ↓
búsqueda de fuentes reales
    ↓
verificación de identidad
    ↓
recuperación del texto oficial
    ↓
localización de evidencia candidata
    ↓
validación de pertinencia
    ↓
redacción/síntesis trazable
    ↓
auditoría final
    ↓
pseudonorma ACEPTADA o REVISION
```

## 5. Identidad

Identificador estable propuesto:

`PSEUDO:<PARTE>_<NUMERO_TEMA>_<SLUG>`

Cada pseudonorma contiene unidades estables `P01`, `P02`, etc. La numeración identifica unidades conceptuales y no pretende imitar artículos legales.

La identidad debe permitir reutilización futura sin hacer depender la pseudonorma de un nombre de fichero o de una ejecución concreta.

## 6. Segmentación

La segmentación parte exclusivamente del texto oficial del tema.

Reglas:

- conservar el orden del temario;
- dividir por conceptos jurídicos materialmente diferenciables;
- evitar unidades demasiado amplias que mezclen instituciones sin relación suficiente;
- evitar fragmentación artificial en microconceptos;
- no introducir conceptos ausentes del temario solo porque aparezcan en una fuente encontrada;
- conservar el fragmento literal del temario que justifica cada unidad.

Salida mínima de cada unidad:

- `codigo_unidad`;
- `titulo`;
- `texto_temario`;
- `estado=REVISION`;
- `tipo_contenido`.

## 7. Proposiciones jurídicas

Antes de buscar evidencia, cada unidad se descompone en proposiciones jurídicas comprobables.

Una proposición debe ser:

- suficientemente concreta para poder ser respaldada o rechazada;
- necesaria para explicar el fragmento del temario;
- neutral respecto de la fuente que finalmente la respalde;
- no más amplia que el contenido exigido por el temario.

No se admite como proposición una afirmación vaga cuya veracidad solo pueda evaluarse por semejanza temática.

Estados iniciales:

- `CANDIDATA`;
- posteriormente `PERTINENTE`, `DESCARTADA` o `PENDIENTE`.

## 8. Fuentes

Tipos generales:

- `FUENTE_PRIMARIA_OFICIAL`;
- `FUENTE_OFICIAL_UE`;
- `JURISPRUDENCIA_TJUE`;
- `JURISPRUDENCIA_CONSTITUCIONAL`;
- `JURISPRUDENCIA_OTRA`;
- `FUENTE_INSTITUCIONAL`;
- `DOCTRINA_DE_APOYO`.

Orden de preferencia:

1. norma oficial aplicable;
2. jurisprudencia oficial cuando la proposición sea jurisprudencial;
3. fuente institucional oficial;
4. doctrina de apoyo únicamente cuando sea necesaria para explicación y quede claramente identificada como tal.

La doctrina de apoyo no debe utilizarse para corregir, sustituir o reinterpretar silenciosamente una fuente primaria.

## 9. Verificación de identidad

Para cada fuente candidata se registra, cuando exista:

- tipo;
- identificador jurídico estable;
- identificador técnico de recuperación;
- título;
- órgano/emisor;
- fecha;
- URL oficial;
- estado de recuperación;
- hash del texto recuperado;
- fecha de recuperación.

Estados de fuente:

- `CANDIDATA`;
- `RECUPERADA`;
- `VERIFICADA`;
- `RECHAZADA`;
- `OBSOLETA`.

`VERIFICADA` significa que la identidad y procedencia del documento han sido comprobadas. No significa que la fuente sea pertinente para una proposición concreta.

## 10. Evidencia y pertinencia

El sistema localiza ventanas de evidencia mediante anclas jurídicas y otros mecanismos de recuperación, pero el resultado se denomina siempre `fragmento_candidato`.

La selección debe priorizar:

- densidad de conceptos relevantes;
- texto sustantivo frente a cabeceras, índices y metadatos;
- fundamentos jurídicos y parte dispositiva cuando corresponda;
- idioma oficial útil para la convocatoria;
- contexto suficiente para no alterar el significado.

Estados de pertinencia:

- `CANDIDATO`;
- `PERTINENTE`;
- `DESCARTADO`;
- `PENDIENTE`.

La pertinencia solo puede quedar aceptada cuando la evidencia respalda materialmente la proposición. La presencia de palabras clave, una puntuación o una similitud semántica nunca bastan por sí solas.

## 11. Síntesis pseudonormativa

Solo se redacta una síntesis final a partir de proposiciones `PERTINENTE`.

Debe separar explícitamente:

- `TEXTO_TEMARIO`: literal oficial;
- `SINTESIS`: explicación estructurada;
- `RESPALDOS`: fuentes reales y evidencia;
- `NOTAS_DIDACTICAS`: ejemplos o aclaraciones no normativas, si existen.

La síntesis:

- no crea reglas nuevas;
- no elimina matices relevantes de las fuentes;
- no convierte jurisprudencia en texto legal;
- no presenta doctrina como norma;
- incorpora límites negativos cuando sean necesarios para evitar interpretaciones previsiblemente erróneas.

## 12. Auditoría final

Una unidad puede pasar a `ACEPTADA` únicamente si:

1. conserva el texto oficial que delimita su alcance;
2. todas las proposiciones esenciales están `PERTINENTE`;
3. las fuentes tienen identidad verificada;
4. la evidencia seleccionada respalda materialmente las proposiciones;
5. la síntesis no excede el alcance del temario ni de sus fuentes;
6. están identificadas las afirmaciones que no deben deducirse;
7. existe trazabilidad completa `tema → unidad → proposición → fuente → evidencia`.

Si falla una condición esencial, la unidad permanece en `REVISION`.

## 13. Ciclo de vida

Estados de pseudonorma:

- `BORRADOR`;
- `REVISION`;
- `ACEPTADA`;
- `OBSOLETA`.

Debe existir versionado. Una versión aceptada no se sobrescribe silenciosamente cuando cambia una fuente, el temario o la interpretación necesaria. La revisión debe conservar motivo, fecha y fuentes afectadas.

## 14. Reutilización

La reutilización entre convocatorias puede ser:

- completa;
- parcial por unidades;
- derivada mediante una nueva versión.

Solo procede si el alcance material del nuevo temario es equivalente. La similitud textual entre títulos de temas no basta.

La asociación con una nueva convocatoria debe conservar el fragmento literal de su propio temario para poder auditar la equivalencia.

## 15. Generación de preguntas IA

Tipos conceptuales futuros:

- `REAL`: basada directamente en norma/artículo o fuente oficial inequívoca;
- `MIXTA`: requiere una relación conceptual validada por la pseudonorma y mantiene trazabilidad a fuentes reales;
- `PSEUDO`: basada en contenido doctrinal estructurado. Solo se admite cuando el contenido esté aceptado y respaldado; nunca para inventar una regla jurídica.

La generación no debe utilizar como única verdad una síntesis pseudonormativa si existen fuentes reales recuperables. La pseudonorma organiza el contexto; las fuentes reales mantienen la autoridad jurídica.

## 16. RAG / chat jurídico

La pseudonorma puede entrar como capa estructurada de recuperación, claramente etiquetada como `FUENTE JURIDICA DE APOYO / PSEUDONORMA`.

Cuando se solicite fundamento jurídico:

- priorizar fuente real;
- citar norma, resolución o documento oficial;
- no citar `P01`, `P05`, etc. como si fueran artículos jurídicos;
- distinguir síntesis de texto normativo literal.

## 17. Persistencia — aún no decidida

El piloto no justifica todavía alterar el esquema existente. Antes de diseñar tablas o modificar importadores deben probarse varias pseudonormas de naturaleza distinta.

La futura persistencia debe preservar como mínimo:

- identidad y versión de pseudonorma;
- unidades;
- texto literal de temario asociado;
- proposiciones;
- fuentes;
- evidencia;
- decisiones de pertinencia;
- estado y auditoría;
- asociaciones con convocatorias/temas.

No se utilizará `normas` para almacenar pseudonormas.

## 18. Integración futura con Constructor de nueva convocatoria

Objetivo final del menú:

```text
NUEVA CONVOCATORIA
    ↓
seleccionar PDF oficial
    ↓
extraer estructura
    ↓
clasificar temas
    ├── NO_JURIDICO → flujo temático
    ├── JURIDICO_EXPLICITO → normas/artículos reales
    ├── JURIDICO_DELIMITABLE → propuesta y revisión de normas/artículos
    └── JURIDICO_DOCTRINAL_TRANSVERSAL → pseudonormas
                                      ↓
                           fuentes + evidencia + revisión
    ↓
auditoría conjunta
    ↓
aprobación
    ↓
temario / referencias / corpus
    ↓
construcción del banco desde lote_preguntas
```

La interfaz final debe ocultar al operador la sucesión de scripts técnicos. Debe presentar incidencias, elementos pendientes de revisión y resultados verificables, sin exigir que el usuario conozca de antemano qué fuente jurídica falta.

## 19. Próxima fase de desarrollo

Antes de persistir pseudonormas se realizarán al menos dos pilotos adicionales de naturaleza distinta a P05 para comprobar que el procedimiento no depende de reglas particulares sobre primacía del Derecho de la Unión.

Criterio de salida de esta fase:

- segmentación generalizable;
- búsqueda multifuente generalizable;
- recuperación oficial estable;
- evidencia suficientemente precisa;
- revisión humana limitada a decisiones jurídicas reales y no a corregir errores mecánicos del sistema;
- artefacto final reproducible y auditable.
