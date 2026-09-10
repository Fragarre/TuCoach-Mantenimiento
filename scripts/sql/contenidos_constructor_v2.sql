-- Constructor Temario V2: extensión aditiva del esquema contenidos.
-- SOLO DDL. No inserta, actualiza ni elimina datos existentes.

CREATE TABLE IF NOT EXISTS contenidos.temario_pseudonormas (
    id bigint PRIMARY KEY,
    tema_id bigint NOT NULL REFERENCES contenidos.temario_temas(id) ON DELETE CASCADE,
    codigo text NOT NULL,
    estado text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tema_id, codigo)
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudonormas_tema
    ON contenidos.temario_pseudonormas(tema_id);

CREATE TABLE IF NOT EXISTS contenidos.temario_pseudoarticulos (
    id bigint PRIMARY KEY,
    pseudonorma_id bigint NOT NULL REFERENCES contenidos.temario_pseudonormas(id) ON DELETE CASCADE,
    codigo text NOT NULL,
    orden bigint NOT NULL,
    texto_temario text NOT NULL,
    tipo_contenido text NOT NULL,
    sintesis text,
    estado text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pseudonorma_id, codigo),
    UNIQUE (pseudonorma_id, orden)
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudoarticulos_pseudonorma
    ON contenidos.temario_pseudoarticulos(pseudonorma_id);

CREATE TABLE IF NOT EXISTS contenidos.temario_pseudoarticulo_proposiciones (
    id bigint PRIMARY KEY,
    pseudoarticulo_id bigint NOT NULL REFERENCES contenidos.temario_pseudoarticulos(id) ON DELETE CASCADE,
    codigo text NOT NULL,
    texto text NOT NULL,
    esencial bigint NOT NULL CHECK (esencial IN (0,1)),
    tipo text NOT NULL,
    estado text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pseudoarticulo_id, codigo)
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudoarticulo_prop_pa
    ON contenidos.temario_pseudoarticulo_proposiciones(pseudoarticulo_id);

CREATE TABLE IF NOT EXISTS contenidos.fuentes_conocimiento (
    id bigint PRIMARY KEY,
    tipo_fuente text NOT NULL,
    organismo text NOT NULL,
    nombre text NOT NULL,
    identificador_oficial text,
    localizador_oficial text,
    estado_verificacion text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tipo_fuente, organismo, nombre, identificador_oficial)
);
CREATE INDEX IF NOT EXISTS idx_fuentes_conocimiento_tipo
    ON contenidos.fuentes_conocimiento(tipo_fuente);
CREATE INDEX IF NOT EXISTS idx_fuentes_conocimiento_identificador
    ON contenidos.fuentes_conocimiento(identificador_oficial);

CREATE TABLE IF NOT EXISTS contenidos.temario_pseudoarticulo_respaldos (
    id bigint PRIMARY KEY,
    pseudoarticulo_id bigint NOT NULL REFERENCES contenidos.temario_pseudoarticulos(id) ON DELETE CASCADE,
    proposicion_id bigint REFERENCES contenidos.temario_pseudoarticulo_proposiciones(id) ON DELETE CASCADE,
    articulo_fuente_id bigint REFERENCES contenidos.articulos_fuente(id) ON DELETE NO ACTION,
    fuente_conocimiento_id bigint REFERENCES contenidos.fuentes_conocimiento(id) ON DELETE NO ACTION,
    pertinencia text NOT NULL,
    estado text NOT NULL,
    motivo text,
    evidencia_texto text,
    evidencia_localizador text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (articulo_fuente_id IS NOT NULL AND fuente_conocimiento_id IS NULL)
        OR
        (articulo_fuente_id IS NULL AND fuente_conocimiento_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_pa
    ON contenidos.temario_pseudoarticulo_respaldos(pseudoarticulo_id);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_prop
    ON contenidos.temario_pseudoarticulo_respaldos(proposicion_id);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_articulo
    ON contenidos.temario_pseudoarticulo_respaldos(articulo_fuente_id);
CREATE INDEX IF NOT EXISTS idx_temario_pseudo_resp_fuente
    ON contenidos.temario_pseudoarticulo_respaldos(fuente_conocimiento_id);

CREATE TABLE IF NOT EXISTS contenidos.pregunta_contextos_v2 (
    id bigint PRIMARY KEY,
    pregunta_id bigint NOT NULL REFERENCES contenidos.lote_preguntas(id) ON DELETE CASCADE,
    tema_id bigint NOT NULL REFERENCES contenidos.temario_temas(id) ON DELETE CASCADE,
    tipo_contexto text NOT NULL CHECK (tipo_contexto IN ('REAL','PSEUDO','MIXTA')),
    pseudoarticulo_id bigint REFERENCES contenidos.temario_pseudoarticulos(id) ON DELETE NO ACTION,
    temario_referencia_id bigint REFERENCES contenidos.temario_referencias(id) ON DELETE NO ACTION,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (tipo_contexto = 'REAL' AND pseudoarticulo_id IS NULL AND temario_referencia_id IS NOT NULL)
        OR
        (tipo_contexto = 'PSEUDO' AND pseudoarticulo_id IS NOT NULL AND temario_referencia_id IS NULL)
        OR
        (tipo_contexto = 'MIXTA' AND pseudoarticulo_id IS NOT NULL AND temario_referencia_id IS NOT NULL)
    ),
    UNIQUE (pregunta_id, tema_id)
);
CREATE INDEX IF NOT EXISTS idx_pregunta_contextos_v2_pregunta
    ON contenidos.pregunta_contextos_v2(pregunta_id);
CREATE INDEX IF NOT EXISTS idx_pregunta_contextos_v2_tema
    ON contenidos.pregunta_contextos_v2(tema_id);
CREATE INDEX IF NOT EXISTS idx_pregunta_contextos_v2_pseudoarticulo
    ON contenidos.pregunta_contextos_v2(pseudoarticulo_id);
CREATE INDEX IF NOT EXISTS idx_pregunta_contextos_v2_referencia
    ON contenidos.pregunta_contextos_v2(temario_referencia_id);
