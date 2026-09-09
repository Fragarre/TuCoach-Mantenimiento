# Constructor automático de pseudonormas V2

## Objetivo

Extender el Constructor de Temarios V2 para representar con rigor los temas jurídicos que no admiten una traducción exhaustiva y fiable a `norma -> artículo`.

El sistema no debe inventar una falsa precisión jurídica. Cuando el temario sea doctrinal o transversal, se construirá una **pseudonorma de estudio** compuesta por unidades internas trazables, siempre distinguida de la legislación oficial.

## Alcance inicial

Primera fase: sólo temas `JURIDICO_DOCTRINAL_TRANSVERSAL`.

Segunda fase, únicamente si se valida: permitir que determinados `JURIDICO_DELIMITABLE` pasen a solución híbrida cuando la cobertura normativa real resulte insuficiente.

Los temas `JURIDICO_EXPLICITO` continúan por el flujo de normas y artículos reales.

## Principio de seguridad

Una pseudonorma:

- NO es legislación;
- NO entra automáticamente en `normas`;
- NO utiliza numeración que pueda confundirse con artículos oficiales;
- NO sustituye fuentes oficiales cuando éstas existen;
- NO pasa a corpus de producción sin revisión;
- NO se utiliza para modificar bancos durante la fase de validación.

Toda unidad nace en estado `REVISION`.

## Flujo

`PDF oficial -> extracción literal -> clasificación de alcance -> tema doctrinal -> segmentación conservadora -> borrador pseudonormativo -> búsqueda de respaldos -> redacción -> validación -> corpus`

La segmentación inicial no redacta contenido jurídico. Sólo convierte el texto oficial en unidades de cobertura.

## Identidad

Identidad de borrador:

`PSEUDO:<PARTE>_<NUMERO>_<SLUG>`

Ejemplo:

`PSEUDO:ESPECIAL_1_FUENTES_DERECHO_ADMINISTRATIVO_I`

Durante el piloto la identidad depende del tema concreto. La reutilización entre convocatorias se resolverá después de validar equivalencia material.

## Unidad pseudonormativa

Cada pseudonorma contendrá unidades `P01`, `P02`, ...

Cada unidad debe conservar como mínimo:

- `codigo`;
- `texto_temario`;
- `titulo_provisional`;
- `estado`;
- `tipo_contenido`;
- `sintesis`;
- `respaldos`;
- `notas_revision`.

En el borrador automático:

- `estado = REVISION`;
- `sintesis = null`;
- `respaldos = []`;
- `tipo_contenido = PENDIENTE_CLASIFICACION`.

## Segmentación

La primera implementación será deliberadamente conservadora.

Se separarán unidades por límites explícitos del texto del temario:

- punto;
- punto y coma;
- dos puntos cuando introduzcan una enumeración claramente separable;
- conectores enumerativos evidentes.

No se utilizará IA para fusionar, completar o reinterpretar unidades en esta fase.

Si la segmentación deja una unidad demasiado corta o ambigua, permanece igualmente en `REVISION`.

## Construcción jurídica posterior

Cada unidad seguirá cuatro capas:

1. `TEXTO_TEMARIO`: fragmento literal que debe cubrirse.
2. `SINTESIS`: explicación jurídica redactada para el opositor.
3. `RESPALDOS`: normas, artículos, jurisprudencia o fuentes institucionales que sustentan cada afirmación relevante.
4. `NOTAS_DIDACTICAS`: explicación auxiliar claramente separada del fundamento jurídico.

No se permite que una afirmación jurídica relevante quede únicamente respaldada por otra pseudonorma.

## Tipos de respaldo

Tipos previstos:

- `NORMA_ARTICULO`;
- `NORMA_ESTRUCTURA`;
- `JURISPRUDENCIA_TJUE`;
- `JURISPRUDENCIA_TC`;
- `JURISPRUDENCIA_OTRA`;
- `FUENTE_INSTITUCIONAL`;
- `DOCTRINA_APOYO`.

La doctrina de apoyo nunca sustituye una fuente primaria cuando ésta existe.

## Estados

Pseudonorma:

- `BORRADOR`;
- `REVISION`;
- `VALIDADA`;
- `OBSOLETA`.

Unidad:

- `REVISION`;
- `VALIDADA`;
- `RECHAZADA`;
- `REQUIERE_FUENTE`.

## Cobertura

La cobertura debe medirse contra el texto oficial, no contra el número de fuentes localizadas.

Métricas conceptuales:

- `cobertura_temario`: partes del texto oficial representadas por alguna unidad;
- `cobertura_respaldos`: unidades con respaldo jurídico suficiente;
- `cobertura_validada`: unidades aprobadas tras revisión.

Una pseudonorma no puede declararse `VALIDADA` con huecos de cobertura del temario sin señalarlos expresamente.

## Relación con el CSV

No se modifica todavía el contrato de `temario.csv`.

La representación futura podrá usar una identidad técnica diferenciada, por ejemplo:

`PSEUDO:ESPECIAL_1_FUENTES_DERECHO_ADMINISTRATIVO_I ; P05`

pero esta decisión se pospone hasta probar que:

- el constructor genera unidades estables;
- la validación jurídica funciona;
- la incorporación al corpus no confunde fuentes oficiales y pseudonormativas;
- el mantenimiento del banco puede consumirlas sin degradar el comportamiento actual.

## Integración con corpus

Las pseudonormas se incorporarán como documentos de apoyo jurídicos, nunca como legislación oficial.

El RAG debe poder distinguir metadatos como:

- `naturaleza = PSEUDONORMA`;
- `estado = VALIDADA`;
- `tema_origen`;
- `version`;
- `fecha_revision`;
- `respaldos`.

Cuando el usuario solicite fundamento jurídico, el sistema debe priorizar los respaldos oficiales.

## Política de generación de preguntas

Las preguntas derivadas podrán ser:

- `REAL`: respuesta respaldada directamente por norma/artículo real;
- `PSEUDO`: pregunta doctrinal cuya respuesta procede de una unidad pseudonormativa validada;
- `MIXTA`: necesita comprensión doctrinal y además posee respaldo normativo real.

Las preguntas `PSEUDO` no deben presentar `P01`, `P02`, etc. como artículos legales.

## Fase actual

La implementación inicial es `SOLO LECTURA` y sólo genera borradores de estructura.

No modifica:

- base de datos;
- `temario_referencias`;
- `normas`;
- corpus;
- `lote_preguntas`;
- bancos;
- C1/C2.

## Criterio de validación

La fase se considerará superada cuando, sobre A1:

1. todos los temas `JURIDICO_DOCTRINAL_TRANSVERSAL` produzcan una estructura trazable;
2. ninguna unidad introduzca contenido ausente del texto oficial;
3. la segmentación sea suficientemente estable para revisión humana;
4. P02 y P05 puedan representarse dentro del mismo modelo;
5. las fuentes jurídicas reales puedan añadirse después sin alterar la identidad de las unidades salvo revisión explícita.
