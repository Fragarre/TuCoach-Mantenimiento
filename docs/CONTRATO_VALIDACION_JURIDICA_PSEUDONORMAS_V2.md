# Constructor Temario V2 — contrato de validación jurídica de pseudonormas

Estado: DISEÑO VALIDADO PARA PILOTO. Sin cambios de BD, corpus, temario.csv ni bancos.

## 1. Problema que resuelve

La recuperación léxica sólo produce candidatos. Una coincidencia alta no implica pertinencia jurídica. La capa de validación debe decidir, para cada pseudoartículo, qué fuentes reales respaldan materialmente el contenido y qué contenido requiere síntesis doctrinal o jurisprudencial.

## 2. Entradas

Por unidad pseudonormativa:
- identidad estable del borrador;
- código Pxx;
- texto literal del temario;
- candidatos recuperados del corpus oficial local;
- fuentes oficiales adicionales localizadas cuando el corpus local no basta.

## 3. Tipos de fuente

- FUENTE_PRIMARIA_OFICIAL: Constitución, ley, reglamento, tratado, declaración oficial u otro texto normativo/institucional primario.
- JURISPRUDENCIA: resoluciones de TJUE, Tribunal Constitucional, Tribunal Supremo u órgano pertinente.
- FUENTE_INSTITUCIONAL: material explicativo oficial de una institución pública cuando no sustituye a la fuente primaria.
- DOCTRINA_DE_APOYO: material doctrinal usado para estructurar o explicar, nunca para desplazar una fuente primaria disponible.

## 4. Estados de cada respaldo

- CANDIDATO: localizado automáticamente o por búsqueda dirigida, sin juicio de pertinencia.
- PERTINENTE: la fuente respalda materialmente una parte identificable de la unidad.
- DESCARTADO: coincidencia no pertinente para el objeto del pseudoartículo.
- PENDIENTE: requiere comprobación adicional.

Ningún algoritmo de similitud puede convertir por sí solo CANDIDATO en PERTINENTE.

## 5. Resultado por pseudoartículo

Cada unidad debe terminar con:

- `texto_temario`: literal, inmutable.
- `tipo_contenido`: NORMATIVO, JURISPRUDENCIAL, DOCTRINAL o MIXTO.
- `respaldos_pertinentes`: lista trazable de fuentes reales.
- `respaldos_descartados`: candidatos relevantes para auditoría de falsos positivos.
- `lagunas`: aspectos del texto del temario todavía sin respaldo suficiente.
- `sintesis`: sólo después de verificar los respaldos; nunca se presenta como texto legal literal.
- `estado`: REVISION, LISTO_PARA_REVISION_HUMANA o ACEPTADO.

## 6. Criterio de suficiencia

Una unidad puede pasar a LISTO_PARA_REVISION_HUMANA sólo cuando:
1. todo concepto jurídico afirmado en la síntesis puede rastrearse a un respaldo pertinente;
2. las reglas normativas se apoyan preferentemente en fuente primaria oficial;
3. los criterios jurisprudenciales se apoyan en la resolución correspondiente o fuente oficial equivalente;
4. se distinguen claramente literalidad, síntesis y explicación;
5. las lagunas pendientes están vacías o declaradas expresamente;
6. no se ha incorporado contenido ajeno al alcance razonable del texto oficial del temario.

ACEPTADO requiere revisión humana posterior.

## 7. Pilotos de control

### P02 — aplicación y eficacia general de las normas

Patrón esperado: fuerte componente normativo. Deben priorizarse Código Civil, Título Preliminar, y Constitución Española en los puntos constitucionales correspondientes. La pseudonorma estructura y explica; no sustituye los preceptos reales.

### P05 — primacía del Derecho de la Unión respecto de la Constitución

Patrón esperado: contenido mixto normativo-jurisprudencial-doctrinal. La coincidencia léxica con una ley presupuestaria u otra norma incidental debe descartarse. La validación exige fuentes de la UE sobre primacía, jurisprudencia del TJUE y el encaje constitucional español pertinente.

Estos dos pilotos son controles deliberadamente distintos: P02 prueba una unidad casi reducible a normas reales; P05 prueba una unidad que no puede representarse correctamente sólo como norma+artículo.

## 8. Regla de automatización

La automatización futura puede:
- recuperar candidatos;
- buscar fuentes oficiales por categorías y términos jurídicos;
- comprobar identificadores, artículos y metadatos;
- proponer relaciones entre fragmentos y fuentes;
- señalar contradicciones, ausencia de respaldo y falsos positivos.

No puede aceptar automáticamente una pseudonorma ni convertir una coincidencia textual en verdad jurídica.

## 9. Persistencia

Esta fase no define todavía tablas definitivas. Primero deben validarse varios pilotos y estabilizarse:
- identidad de pseudonorma/pseudoartículo;
- tipos de fuente;
- relación fragmento-respaldo;
- estados y ciclo de revisión;
- reutilización entre convocatorias.

Sólo después se diseñará persistencia compatible con el sistema existente.
