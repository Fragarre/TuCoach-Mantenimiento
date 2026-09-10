# Constructor Temario V2 — Generalización

## Estado

`FASE DE PILOTOS CERRADA — GENERALIZACIÓN EN CURSO`

El Constructor Temario V2 ha superado la fase en la que el procedimiento de pseudonormas dependía de un único caso piloto.

Casos utilizados para validar la generalización:

- A1 Especial 1, incluyendo unidades de naturaleza normativa y mixta;
- A1 Especial 3 P03;
- A1 Especial 53, pseudonorma multifuente finalmente `ACEPTADA` tras revisión humana y auditoría formal 7/7.

La generalización se realiza todavía en modo seguro: no implica persistencia automática de pseudonormas en la base de datos, modificación del corpus, modificación de `temario.csv` ni construcción automática del banco.

## 1. Objetivo de esta fase

Transformar el conjunto de scripts V2 ya validados en un procedimiento reutilizable por convocatoria, tema y unidad, sin introducir reglas específicas de los pilotos.

El operador no debe necesitar conocer qué scripts ejecutar ni construir manualmente las rutas de todos los artefactos intermedios.

El Constructor debe:

1. partir del documento oficial del temario;
2. conservar literalmente el texto oficial;
3. clasificar cada tema por naturaleza;
4. encaminarlo al tratamiento adecuado;
5. producir candidatos y evidencia trazable;
6. detenerse cuando exista una decisión jurídica que requiera revisión humana;
7. continuar a partir de esa decisión sin reinterpretarla;
8. auditar mecánicamente el resultado;
9. entregar únicamente resultados formalmente aceptables a una futura fase de persistencia.

## 2. Caminos del Constructor

### 2.1. `NO_JURIDICO`

No se inventan normas ni artículos. El alcance procede del texto oficial y de la categorización temática consolidada.

### 2.2. `JURIDICO_EXPLICITO`

Se resuelven las referencias jurídicas que aparecen literalmente en el temario y se contrastan contra fuentes oficiales y catálogo canónico.

### 2.3. `JURIDICO_DELIMITABLE`

La automatización completa `texto del tema -> norma -> bloque/artículos` no se considera validada.

El Constructor puede:

- clasificar el tema como delimitable;
- proponer normas candidatas para revisión;
- mostrar la estructura jurídica oficial disponible de una norma seleccionada;
- materializar automáticamente los artículos contenidos en los bloques que el revisor seleccione;
- permitir ajustes artículo a artículo cuando el alcance oficial no coincida exactamente con un bloque estructural completo.

La selección de norma, bloque o artículos inferidos requiere decisión humana explícita. Un delimitable no revisado debe permanecer en estado `PENDIENTE_REVISION_HUMANA`.

### 2.4. `JURIDICO_DOCTRINAL_TRANSVERSAL`

Se utiliza el procedimiento general de pseudonormas:

`texto oficial → unidades → proposiciones → fuentes → identidad → recuperación → evidencia candidata → pertinencia humana → evidencia materializada → síntesis → auditoría final`.

## 3. Contrato de seguridad

Mientras dure esta fase de generalización, el Constructor V2:

- NO modifica `db/oposiciones.sqlite3`;
- NO modifica `normas`;
- NO modifica `temario_referencias`;
- NO modifica `lote_preguntas`;
- NO modifica `banco_preguntas`;
- NO modifica el corpus;
- NO modifica `temario.csv`;
- NO acepta automáticamente una relación jurídica por similitud, puntuación o respuesta de IA.

Las salidas son artefactos auditables de trabajo.

## 4. Contrato de decisión humana

La automatización puede localizar, ordenar y materializar evidencia, pero no sustituye la decisión jurídica.

Una decisión humana de pertinencia debe quedar expresada de forma explícita y estructurada. El sistema posterior debe limitarse a comprobar coherencia y reproducir la evidencia asociada.

Estados admitidos para una relación revisada:

- `PERTINENTE`;
- `DESCARTADO`;
- `PENDIENTE`.

Una pseudonorma no puede quedar aceptada mientras exista una relación esencial `PENDIENTE` o una proposición esencial sin respaldo material suficiente.

## 5. Cierre formal generalizado

El cierre de una pseudonorma ya no depende de comandos específicos de un piloto.

El script:

`scripts/cerrar_pseudonorma_v2.py`

encadena de forma genérica:

1. `materializar_evidencia_humana_v2.py`;
2. `auditar_pseudonorma_final_v2.py`.

Entradas conceptuales:

- síntesis revisada;
- auditoría de pertinencia;
- decisiones humanas.

Salidas:

- evidencia humana materializada;
- auditoría final formal.

El estado formal final es `ACEPTABLE_FORMALMENTE` únicamente cuando se superan los siete controles V2. Esto no equivale por sí mismo a persistir el contenido en la BD o incorporarlo al corpus.

## 6. Siete controles invariantes

El Constructor conserva como condición de aceptación:

1. texto oficial del temario conservado;
2. proposiciones esenciales pertinentes;
3. identidad de fuentes verificada;
4. evidencia material que respalda las proposiciones;
5. síntesis dentro del alcance del temario y de las fuentes;
6. límites negativos identificados;
7. trazabilidad completa `tema → unidad → proposición → fuente → evidencia`.

Estos controles son generales y no contienen conocimiento jurídico específico de Especial 1, Especial 3 o Especial 53.

## 7. Artefactos de un caso V2

Un caso general debe poder reconstruirse a partir de artefactos explícitos, sin depender del historial de una sesión.

Como mínimo:

- identificación estable del caso y pseudonorma;
- texto oficial del tema/unidad;
- proposiciones;
- fuentes candidatas y verificadas;
- auditoría de pertinencia;
- decisiones humanas;
- evidencia aceptada materializada;
- síntesis final;
- auditoría final.

Los nombres físicos de los ficheros no constituyen la identidad jurídica del caso.

## 8. Reproducibilidad

Dadas las mismas entradas y decisiones humanas, las operaciones mecánicas posteriores deben producir el mismo resultado sustantivo.

La generalización debe evitar:

- reglas codificadas para números de tema concretos;
- respaldos aceptados automáticamente por pertenecer a un piloto conocido;
- dependencias de nombres locales de ficheros;
- aceptar una fuente por coincidencia léxica;
- introducir conocimiento no contenido en los artefactos de entrada.

Los controles piloto históricos pueden conservarse exclusivamente como pruebas de regresión, nunca como reglas productivas.

## 9. Flujo general objetivo

```text
CONVOCATORIA / PDF OFICIAL
        ↓
extraer estructura literal
        ↓
clasificar alcance de cada tema
        ↓
┌───────────────────────────────┬──────────────────────────────┐
│ jurídico explícito/delimitable│ doctrinal transversal       │
│                               │                              │
│ explícito: resolver citas     │ construir pseudonorma       │
│ delimitable: revisión humana  │ proponer fuentes             │
│ de norma/bloques/artículos    │ recuperar evidencia          │
│ verificar fuente oficial      │ revisión humana pertinencia  │
└───────────────────────────────┴──────────────────────────────┘
        ↓
auditoría de cada resultado
        ↓
RESULTADO V2 APROBADO PARA PERSISTENCIA
        ↓
[fase futura y separada]
        ↓
temario.csv / temario_referencias / corpus / banco
```

## 10. Frontera con la persistencia

La persistencia continúa siendo una fase distinta.

No se debe conectar automáticamente la salida V2 con las tablas consolidadas hasta disponer de:

- contrato de salida común para todos los tipos de tema;
- auditoría global de convocatoria;
- operación de escritura explícita y reversible;
- validación previa de que la salida reproduce los contratos actuales.

Las pseudonormas no se almacenarán en `normas` como si fueran legislación real.

## 11. Contrato común de salida V2

Cada tema debe producir un resultado estructurado común, independientemente del camino seguido.

Campos mínimos conceptuales:

- `parte`;
- `numero_tema`;
- `texto_oficial`;
- `clasificacion_v2`;
- `estado_v2`;
- `requiere_revision_humana`;
- `normas`;
- `articulos`;
- `fuentes`;
- `evidencia`;
- `incidencias`;
- `trazabilidad`.

Estados generales previstos:

- `RESUELTO_MECANICAMENTE`: el alcance procede de referencias explícitas verificadas y no existe una inferencia jurídica pendiente;
- `PENDIENTE_REVISION_HUMANA`: existe una decisión jurídica que el sistema no puede tomar con fiabilidad suficiente;
- `PENDIENTE_FUENTE`: falta recuperar o verificar una fuente necesaria;
- `PENDIENTE_SINTESIS`: la evidencia está disponible pero el camino doctrinal todavía no ha completado su síntesis;
- `ACEPTABLE_FORMALMENTE`: ha superado los controles aplicables de su camino y puede pasar a una futura fase separada de persistencia;
- `BLOQUEADO_INCIDENCIA`: existe una inconsistencia o carencia que impide continuar de forma segura.

Reglas por clasificación:

### `NO_JURIDICO`

- no inventa `normas` ni `articulos`;
- puede cerrar mecánicamente su clasificación si no existen incidencias estructurales;
- no se fuerza artificialmente a producir referencias jurídicas.

### `JURIDICO_EXPLICITO`

- las normas y artículos deben proceder de referencias literales del temario y de fuentes verificadas;
- si una referencia no puede resolverse inequívocamente, el tema no se declara resuelto y queda pendiente de revisión o fuente según la causa;
- una coincidencia léxica nunca sustituye una referencia jurídica comprobada.

### `JURIDICO_DELIMITABLE`

- no se acepta automáticamente una norma, bloque estructural o artículo inferido;
- las propuestas automáticas son auxiliares y no constituyen decisión jurídica;
- la salida inicial normal es `PENDIENTE_REVISION_HUMANA`;
- tras la decisión humana, el sistema puede materializar mecánicamente los artículos incluidos en los bloques seleccionados y permitir inclusiones/exclusiones individuales;
- la decisión humana debe quedar registrada de forma estructurada y trazable.

### `JURIDICO_DOCTRINAL_TRANSVERSAL`

- mantiene el contrato ya validado de pseudonormas;
- la pertinencia de relaciones y fuentes requiere revisión humana donde corresponda;
- sólo alcanza `ACEPTABLE_FORMALMENTE` después de superar su auditoría final.

La existencia de un contrato común NO implica que todos los tipos de tema tengan el mismo grado de automatización. El estado debe reflejar expresamente dónde termina la automatización fiable y dónde comienza la revisión humana.

Este contrato es conceptual mientras no se valide su representación concreta en el manifiesto general. No autoriza cambios de esquema ni escritura en las tablas consolidadas.

## 12. Siguiente incremento

El siguiente incremento de desarrollo es adaptar el **orquestador general de análisis** para que reciba una convocatoria/PDF y produzca un manifiesto de trabajo V2 conforme al contrato común anterior, reutilizando los scripts existentes.

Ese orquestador seguirá siendo de solo lectura y no tomará decisiones humanas ni ejecutará persistencia.

El manifiesto deberá permitir saber, para cada tema, si está:

- resuelto mecánicamente;
- pendiente de recuperación de fuente;
- pendiente de revisión humana;
- pendiente de síntesis;
- formalmente aceptable;
- bloqueado por incidencia.

Antes de integrar nada en `menu_mantenimiento.py` o diseñar la fase de escritura, deberá comprobarse con una convocatoria patrón que el manifiesto representa correctamente los cuatro caminos sin ocultar decisiones humanas pendientes.