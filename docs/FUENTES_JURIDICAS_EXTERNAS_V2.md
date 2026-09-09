# Constructor Temario V2 — Fuentes jurídicas externas

Estado: DISEÑO PARA PILOTO. Sin cambios de esquema ni escrituras en BD.

## 1. Problema

Una unidad jurídica doctrinal o transversal puede requerir respaldos que no son artículos de una norma consolidada: jurisprudencia del TJUE, jurisprudencia constitucional, declaraciones anexas a los Tratados, documentos institucionales u otras fuentes oficiales.

Estas fuentes no deben incorporarse a `normas` / `articulos_fuente` fingiendo que son legislación articulada.

## 2. Principio de separación

Se mantienen dos capas distintas:

- **Corpus normativo oficial**: `normas -> norma_fuentes -> articulos_fuente`.
- **Fuentes jurídicas externas**: documentos o resoluciones oficiales con identidad, procedencia y unidades propias.

Una pseudonorma puede apoyarse simultáneamente en ambas capas.

## 3. Tipos de fuente externa

Tipos iniciales:

- `FUENTE_OFICIAL_UE`: declaraciones, protocolos u otros documentos oficiales de la UE no tratados como artículos de una norma local.
- `JURISPRUDENCIA_TJUE`: sentencias/autos del Tribunal de Justicia de la Unión Europea.
- `JURISPRUDENCIA_CONSTITUCIONAL`: sentencias, declaraciones y autos del Tribunal Constitucional.
- `JURISPRUDENCIA_OTRA`: reservado para otras resoluciones judiciales cuando el temario las requiera.
- `FUENTE_INSTITUCIONAL`: material oficial explicativo cuando sea jurídicamente pertinente, siempre subordinado a las fuentes primarias.
- `DOCTRINA_DE_APOYO`: material doctrinal auxiliar; nunca sustituye una fuente primaria.

## 4. Identidad y trazabilidad

Cada fuente debe conservar, como mínimo:

- `tipo_fuente`
- `identificador_oficial`
- `titulo_oficial`
- `organismo`
- `fecha`
- `url_oficial` o localizador oficial equivalente
- `texto` o documento oficial recuperado
- `hash_texto`
- `fecha_recuperacion`
- `estado_validacion`
- datos de versión cuando proceda

Identificadores preferentes:

- TJUE: ECLI cuando exista.
- Tribunal Constitucional: identificador oficial/BOE de la resolución.
- UE: identificador CELEX/EUR-Lex u otro identificador oficial estable.

La URL nunca es la identidad primaria si existe un identificador oficial estable.

## 5. Estados

Una fuente recuperada no se acepta automáticamente.

Estados conceptuales:

- `CANDIDATA`
- `RECUPERADA`
- `VERIFICADA`
- `RECHAZADA`
- `OBSOLETA`

Un respaldo de pseudoartículo mantiene además su propia evaluación de pertinencia:

- `CANDIDATO`
- `PERTINENTE`
- `DESCARTADO`
- `PENDIENTE`

`VERIFICADA` significa que la fuente es auténtica y correctamente identificada; no significa que sea pertinente para cualquier tema.

## 6. Flujo general

```text
unidad del temario
  -> detectar necesidad de fuente externa
  -> formular fuente requerida con identidad esperada
  -> localizar exclusivamente en proveedor oficial
  -> recuperar metadatos + contenido
  -> verificar identidad
  -> verificar integridad/versionado
  -> guardar artefacto de revisión
  -> evaluar pertinencia jurídica para la unidad
  -> revisión humana
  -> sólo después: incorporación persistente
```

La recuperación y la pertinencia son pasos separados.

## 7. Proveedores oficiales

El constructor debe usar adaptadores independientes por proveedor, no lógica específica de una convocatoria:

- BOE para resoluciones/publicaciones españolas disponibles allí.
- Tribunal Constitucional para contenido oficial del TC cuando sea necesario.
- EUR-Lex para Derecho de la UE y documentos oficiales asociados.
- CURIA/ECLI para jurisprudencia del TJUE.

Cada adaptador debe devolver una representación común y conservar los metadatos específicos del proveedor.

No se usarán buscadores generales ni fuentes secundarias como mecanismo de aceptación automática.

## 8. P05 como prueba de regresión

`A1 / ESPECIAL / tema 1 / P05` exige:

1. Constitución Española, art. 93 — ya pertenece al corpus normativo local.
2. Declaración n.º 17 relativa a la primacía — fuente oficial UE externa.
3. Costa/ENEL, asunto 6/64, `ECLI:EU:C:1964:66` — jurisprudencia TJUE.
4. Simmenthal, asunto 106/77, `ECLI:EU:C:1978:49` — jurisprudencia TJUE.
5. Declaración 1/2004 del Tribunal Constitucional, `BOE-T-2005-111` — jurisprudencia constitucional.

El piloto se considera técnicamente correcto sólo si recupera exactamente estas identidades desde fuentes oficiales y no confunde ninguna de ellas con un artículo legislativo.

## 9. Relación con pseudonormas

Un pseudoartículo no copia sin más una fuente externa. Debe almacenar referencias a sus respaldos y una síntesis jurídicamente delimitada.

Ejemplo conceptual:

```text
P05
  texto_temario: "La primacía del Derecho europeo respecto de la Constitución..."
  respaldos:
    REAL: CE art. 93
    EXTERNA: Declaración 17
    EXTERNA: Costa/ENEL
    EXTERNA: Simmenthal
    EXTERNA: TC Declaración 1/2004
  sintesis: pendiente de validación
```

La respuesta del futuro RAG debe citar la fuente real (CE, TJUE, TC, EUR-Lex), no presentar `P05` como autoridad jurídica.

## 10. Persistencia — todavía no decidida

No se crea todavía ninguna tabla.

Antes de diseñar el esquema persistente hay que validar que una representación común funciona al menos con:

- una fuente EUR-Lex no jurisprudencial;
- una sentencia TJUE;
- una resolución del Tribunal Constitucional.

Sólo después se decidirá si conviene una tabla común de documentos jurídicos externos con unidades, tablas especializadas por proveedor o reutilización controlada de `documentos_corpus`.

## 11. Requisitos para automatización futura

El procedimiento final de alta de convocatoria deberá poder:

1. detectar fuentes externas necesarias durante la revisión del temario;
2. comprobar si ya existen localmente por identificador estable;
3. reutilizarlas entre convocatorias;
4. recuperar las ausentes desde la fuente oficial;
5. bloquear ambigüedades o discrepancias;
6. presentar la propuesta para revisión;
7. incorporar sólo fuentes verificadas y respaldos aceptados;
8. mantener idempotencia y trazabilidad.

No debe requerir que el operador conozca previamente qué script ejecutar para cada fuente.

## 12. Próximo piloto

Construir un recuperador **sin escrituras BD** para P05 que genere JSON y pruebe tres familias distintas:

- Declaración 17 (EUR-Lex),
- una resolución TJUE por ECLI,
- Declaración 1/2004 del TC/BOE.

Primero se validará recuperación e identidad. La redacción automática de la pseudonorma y la persistencia quedan fuera de ese piloto.
