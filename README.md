# OpoCoach --- Mantenimiento y actualización de preguntas

Este documento describe el procedimiento operativo para incorporar
nuevas preguntas a **OpoCoach**, generar preguntas mediante IA, mantener
los bancos por convocatoria, realizar comprobaciones y actualizar la
base de datos utilizada por la aplicación **OpoCoach (Streamlit)**.

> **Principio general:** `OpoCoach-Mantenimiento` es la base maestra. La
> aplicación Streamlit **no realiza tareas de mantenimiento**: consume
> una copia ya validada de `db/oposiciones.sqlite3`.

## 1. Proyectos

``` text
OpoCoach-Mantenimiento/
    db/oposiciones.sqlite3      ← base maestra
    menu_mantenimiento.py
    scripts/
    data_examenes/
    data_preguntas/
    data_academia/
    data_academia_texto/
    data_informatica/
    logs/
    auditorias/
    registros/

OpoCoach/
    db/oposiciones.sqlite3      ← copia utilizada por Streamlit
    ...
```

El punto normal de entrada al mantenimiento es:

``` powershell
python menu_mantenimiento.py
```

El menú estable se organiza en seis bloques:

1.  **Flujo habitual**
2.  **Convocatorias, temarios y corpus**
3.  **Preguntas e importaciones avanzadas**
4.  **Bancos de preguntas --- avanzado**
5.  **Auditorías y diagnóstico**
6.  **Administración**

Las opciones avanzadas son herramientas de diagnóstico o intervención
concreta. **No sustituyen al Flujo habitual.**

------------------------------------------------------------------------

## 2. Incorporar nuevas preguntas desde ficheros

### 2.1. Carpeta según el origen

Depositar cada fichero en la carpeta correspondiente:

  -----------------------------------------------------------------------
  Carpeta                             Contenido
  ----------------------------------- -----------------------------------
  `data_examenes/`                    Exámenes oficiales o de apoyo

  `data_preguntas/`                   Tests visuales PDF/PNG (incluido
                                      GoFullPage)

  `data_academia/`                    Tests de academia en formato
                                      estructurado

  `data_academia_texto/`              Tests de academia con explicación o
                                      texto auxiliar

  `data_informatica/`                 Preguntas/fuentes específicas de
                                      informática
  -----------------------------------------------------------------------

No es necesario ejecutar individualmente el importador de cada carpeta.

### 2.2. Proceso normal

Desde el menú:

``` text
1. FLUJO HABITUAL
   └─ 1. Mantenimiento completo de preguntas
```

Esta es la operación ordinaria para incorporar nuevas preguntas.

El proceso completo realiza, en cadena:

``` text
IMPORTACIÓN DE TODAS LAS FUENTES
        ↓
DEPURACIÓN DE DUPLICADOS
        ↓
NORMALIZACIÓN / CLASIFICACIÓN
        ↓
CATÁLOGO DE NORMAS Y ENLACES
        ↓
AUDITORÍA DE LA BASE
        ↓
SINCRONIZACIÓN DE TODOS LOS BANCOS
        ↓
VALIDACIÓN COMPLETA
```

Si una fase falla, el proceso se detiene.

### 2.3. `lote_preguntas` y bancos

`lote_preguntas` es el repositorio maestro de preguntas.

Los bancos de las convocatorias **no son colecciones independientes de
preguntas**: vinculan las preguntas válidas de `lote_preguntas` que
cumplen las reglas de cada convocatoria y de su temario.

Por tanto:

-   una pregunta puede pertenecer a una convocatoria, a varias o a
    ninguna;
-   una jurídica incompleta puede conservarse en `lote_preguntas`, pero
    no debe incorporarse a un banco;
-   las preguntas obsoletas que deban excluirse no deben permanecer en
    los bancos;
-   después del mantenimiento no hay que actualizar C1, C2, etc.
    manualmente.

La sincronización de todos los bancos utiliza una **única lógica común
de selección**.

------------------------------------------------------------------------

## 3. Generar preguntas mediante IA

La generación IA se realiza también desde **Flujo habitual**.

### 3.1. Preguntas jurídicas

``` text
1. FLUJO HABITUAL
   └─ 3. Generar preguntas jurídicas IA
```

Las preguntas jurídicas parten de referencias **norma + artículo** del
temario. Las preguntas aprobadas se publican en `lote_preguntas`.

Después, automáticamente:

``` text
GENERACIÓN Y VALIDACIÓN IA
        ↓
PUBLICACIÓN EN lote_preguntas
        ↓
SINCRONIZACIÓN COMÚN DE TODOS LOS BANCOS
        ↓
VALIDACIÓN COMPLETA
```

La generación puede utilizar el modelo de examen, un tema concreto o el
conjunto de temas jurídicos según las opciones disponibles.

Que una pregunta haya sido generada no implica por sí solo que
pertenezca a un banco: la pertenencia sigue dependiendo de las reglas
del temario y de la selección del banco.

### 3.2. Preguntas de informática

``` text
1. FLUJO HABITUAL
   └─ 4. Generar preguntas de informática IA
```

El proceso genera, valida y publica las preguntas y después ejecuta la
sincronización común y la validación completa.

Para las preguntas no jurídicas, `norma` y `artículo` no son aplicables.

------------------------------------------------------------------------

## 4. Recuperar preguntas `PENDIENTE`

Las preguntas ya existentes que no pudieron quedar correctamente
clasificadas o normalizadas pueden revisarse mediante:

``` text
1. FLUJO HABITUAL
   └─ 2. Recuperar preguntas PENDIENTE
```

Después de la recuperación se ejecutan automáticamente las fases
necesarias:

``` text
RECUPERACIÓN
    ↓
NORMALIZACIÓN / CLASIFICACIÓN
    ↓
CATÁLOGO DE NORMAS
    ↓
ENLACES
    ↓
AUDITORÍA DE LA BASE
    ↓
SINCRONIZACIÓN DE TODOS LOS BANCOS
    ↓
VALIDACIÓN COMPLETA
```

Esta operación puede utilizar IA y, por tanto, generar coste de API.

------------------------------------------------------------------------

## 5. Sincronización manual de bancos

Normalmente **no es necesaria** después de una importación o generación
IA completa.

Cuando se necesite comprobar o sincronizar expresamente todos los
bancos:

``` text
1. FLUJO HABITUAL
   └─ 5. Sincronizar todos los bancos
```

El procedimiento es seguro en dos fases:

1.  revisión de **todas** las convocatorias;
2.  aplicación, solo después de confirmación.

Si se aplica, termina ejecutando la validación completa.

Para intervenir únicamente sobre una convocatoria existen herramientas
en:

``` text
4. BANCOS DE PREGUNTAS — AVANZADO
```

Estas opciones se reservan para diagnóstico o correcciones concretas.

------------------------------------------------------------------------

## 6. Validación y auditorías

### 6.1. Validación completa

Es la prueba de regresión principal:

``` text
5. AUDITORÍAS Y DIAGNÓSTICO
   └─ 1. Validación completa
```

También puede ejecutarse directamente:

``` powershell
python scripts/validacion_completa.py
```

Es una operación de **solo lectura**.

Comprueba, entre otros puntos:

-   integridad SQLite;
-   claves foráneas;
-   duplicados en bancos;
-   partes de convocatoria nulas;
-   normalización/vigencia jurídica;
-   preguntas IA;
-   constructores de todas las convocatorias en modo revisión;
-   selección de bancos;
-   modelos de examen;
-   auditoría general de la base.

El resultado esperado es:

``` text
RESULTADO FINAL....................... CORRECTO
```

En los flujos ordinarios de escritura esta validación ya se ejecuta
automáticamente. No es necesario repetirla manualmente si el proceso ha
terminado correctamente.

### 6.2. Auditorías auxiliares

El bloque **Auditorías y diagnóstico** contiene herramientas de solo
lectura para investigar problemas concretos:

  -----------------------------------------------------------------------
  Auditoría               Para qué sirve          Cuándo usarla
  ----------------------- ----------------------- -----------------------
  **Auditoría general de  Revisa estructura,      Cuando la validación
  la base**               importaciones, datos    general avisa de una
                          anómalos y trazabilidad incidencia o se desea
                                                  una revisión global

  **Auditoría de          Comprueba que la        Si faltan/sobran
  selección de bancos**   selección realizada por preguntas o se
                          los constructores es    modifican reglas de
                          coherente               selección

  **Auditoría funcional   Revisa una convocatoria Ante una incidencia
  de un banco**           concreta                localizada en C1, C2 u
                                                  otra convocatoria

  **Auditoría global lote Contrasta el            Si se sospechan
  ↔ banco**               repositorio maestro con vinculaciones
                          los bancos              incorrectas o preguntas
                                                  que deberían
                                                  entrar/salir

  **Auditoría de          Revisa la estructura    Ante problemas de
  estructura del banco**  interna de los bancos   partes, temas o
                                                  relaciones

  **Auditoría del         Comprueba referencias y Tras cambios de
  corpus/temario**        corpus jurídico         temario/corpus o ante
                                                  incidencias BOE

  **Auditar posibles      Busca estructuras u     Mantenimiento técnico
  objetos obsoletos**     objetos potencialmente  del esquema
                          antiguos                

  **Inventariar           Detecta variantes de    Problemas de
  denominaciones de       nombres de normas       normalización
  normas**                                        

  **Buscar norma por      Diagnóstico asistido    Solo para casos
  respuesta correcta**    por IA                  jurídicos concretos
                                                  difíciles de
                                                  identificar
  -----------------------------------------------------------------------

Estas auditorías **no forman una secuencia obligatoria después de cada
importación**. Se utilizan cuando la validación completa detecta un
problema o cuando se realiza una intervención específica.

------------------------------------------------------------------------

## 7. Herramientas avanzadas de preguntas

En:

``` text
3. PREGUNTAS E IMPORTACIONES AVANZADAS
```

existen operaciones individuales para:

-   importar solo una fuente;
-   revisar importaciones problemáticas;
-   depurar duplicados exactos;
-   normalizar/clasificar preguntas;
-   reconstruir catálogo y enlaces;
-   validar la normalización jurídica;
-   listar `PENDIENTE`;
-   buscar por norma/artículo;
-   modificar manualmente una pregunta;
-   auditar vigencia jurídica.

Estas herramientas son útiles para **diagnóstico y reparación**.

> Para una incorporación ordinaria de nuevos ficheros debe utilizarse
> **Mantenimiento completo**, no encadenar manualmente estas opciones.

------------------------------------------------------------------------

## 8. Actualizar la base de la aplicación OpoCoach (Streamlit)

Una vez terminado el mantenimiento y con la base maestra correcta:

``` text
1. FLUJO HABITUAL
   └─ 6. Actualizar BD de OpoCoach
```

La misma operación está disponible en:

``` text
6. ADMINISTRACIÓN
   └─ 1. Actualizar BD de OpoCoach
```

El proceso realiza:

``` text
VALIDACIÓN COMPLETA OBLIGATORIA
        ↓
BACKUP DE LA BASE ACTUAL DE OpoCoach
        ↓
COPIA DE LA BASE MAESTRA
        ↓
OpoCoach/db/oposiciones.sqlite3
```

Origen:

``` text
OpoCoach-Mantenimiento/db/oposiciones.sqlite3
```

Destino:

``` text
OpoCoach/db/oposiciones.sqlite3
```

Antes de sustituir una base existente se crea automáticamente una copia
en:

``` text
OpoCoach/db/copias_seguridad/
```

con un nombre del tipo:

``` text
oposiciones_antes_actualizacion_AAAAMMDD_HHMMSS.sqlite3
```

La copia **no se realiza** si `OpoCoach-Mantenimiento` no supera
previamente `validacion_completa.py`.

Al terminar también se comprueba que el fichero de destino existe y que
su tamaño coincide con el origen.

### Regla importante

**OpoCoach (Streamlit) utiliza datos ya preparados.**\
La importación, normalización, BOE, generación IA, corpus, auditorías y
construcción de bancos pertenecen a `OpoCoach-Mantenimiento`.

------------------------------------------------------------------------

## 9. Limpieza de temporales

Para evitar crecimiento indefinido de archivos auxiliares:

``` text
6. ADMINISTRACIÓN
   └─ 2. Limpiar logs/auditorías temporales
```

La limpieza comienza siempre en **vista previa**.

La política es conservadora:

-   no modifica la base de datos;
-   no toca las carpetas de datos de entrada;
-   no borra `cache_boe_v2/` por defecto;
-   no borra genéricamente los registros protegidos;
-   elimina únicamente temporales antiguos según sus reglas de
    retención;
-   puede recortar logs activos excesivamente grandes conservando su
    parte reciente.

Solo debe aplicarse después de revisar la vista previa.

------------------------------------------------------------------------

## 10. Circuitos operativos resumidos

### Nuevos ficheros

``` text
Copiar ficheros a data_*
        ↓
Flujo habitual
        ↓
Mantenimiento completo de preguntas
        ↓
Bancos sincronizados
        ↓
Validación completa
```

### Nuevas preguntas jurídicas IA

``` text
Flujo habitual
        ↓
Generar preguntas jurídicas IA
        ↓
lote_preguntas
        ↓
Bancos sincronizados
        ↓
Validación completa
```

### Nuevas preguntas de informática IA

``` text
Flujo habitual
        ↓
Generar preguntas de informática IA
        ↓
lote_preguntas
        ↓
Bancos sincronizados
        ↓
Validación completa
```

### Publicar los cambios en Streamlit

``` text
Base maestra validada
        ↓
Actualizar BD de OpoCoach
        ↓
Validación obligatoria
        ↓
Backup de la BD anterior
        ↓
Copia a OpoCoach/db/oposiciones.sqlite3
```

------------------------------------------------------------------------

## 11. Regla práctica

Para el mantenimiento cotidiano basta con recordar:

1.  **Añadir ficheros** → `Flujo habitual → Mantenimiento completo`.
2.  **Generar IA jurídica** →
    `Flujo habitual → Generar preguntas jurídicas IA`.
3.  **Generar IA informática** →
    `Flujo habitual → Generar preguntas de informática IA`.
4.  **Recuperar pendientes** →
    `Flujo habitual → Recuperar preguntas PENDIENTE`.
5.  **Publicar en Streamlit** →
    `Flujo habitual → Actualizar BD de OpoCoach`.

Los cuatro primeros procesos dejan los bancos sincronizados y terminan
con la validación correspondiente. Las auditorías específicas se
reservan para diagnóstico o intervenciones extraordinarias.

------------------------------------------------------------------------

## 12. Criterio de estabilidad

Una modificación del mantenimiento no debe considerarse cerrada hasta
que:

``` powershell
python scripts/validacion_completa.py
```

termine con:

``` text
RESULTADO FINAL....................... CORRECTO
```

Si no ocurre, debe revisarse la incidencia antes de copiar la base a
`OpoCoach`.

------------------------------------------------------------------------

**Estado de referencia:** versión estable consolidada de
`OpoCoach-Mantenimiento`, agosto de 2026.
