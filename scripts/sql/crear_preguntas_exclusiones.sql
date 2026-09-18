CREATE TABLE IF NOT EXISTS preguntas_exclusiones (
    pregunta_id INTEGER PRIMARY KEY
        REFERENCES lote_preguntas(id) ON DELETE RESTRICT,
    clasificacion TEXT NOT NULL
        CHECK (clasificacion IN ('ERROR_DEMOSTRADO', 'NO_DETERMINABLE')),
    estado TEXT NOT NULL DEFAULT 'CUARENTENA'
        CHECK (estado IN ('CUARENTENA', 'RETIRADA')),
    respuesta_almacenada TEXT NOT NULL
        CHECK (respuesta_almacenada IN ('A', 'B', 'C', 'D')),
    respuesta_demostrada TEXT
        CHECK (respuesta_demostrada IS NULL OR respuesta_demostrada IN ('A', 'B', 'C', 'D')),
    motivo TEXT NOT NULL,
    evidencia_ruta TEXT NOT NULL,
    evidencia_sha256 TEXT NOT NULL
        CHECK (length(evidencia_sha256) = 64),
    origen_auditoria TEXT NOT NULL,
    decision_usuario INTEGER NOT NULL DEFAULT 0
        CHECK (decision_usuario IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (clasificacion = 'ERROR_DEMOSTRADO' AND respuesta_demostrada IS NOT NULL)
        OR
        (clasificacion = 'NO_DETERMINABLE' AND respuesta_demostrada IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_preguntas_exclusiones_estado
    ON preguntas_exclusiones(estado, clasificacion);

CREATE TRIGGER IF NOT EXISTS impedir_inclusion_pregunta_excluida_insert
BEFORE INSERT ON banco_preguntas
WHEN NEW.estado = 'INCLUIDA'
 AND EXISTS (
     SELECT 1
     FROM preguntas_exclusiones AS pe
     WHERE pe.pregunta_id = NEW.pregunta_id
       AND pe.estado IN ('CUARENTENA', 'RETIRADA')
 )
BEGIN
    SELECT RAISE(ABORT, 'PREGUNTA_EXCLUIDA_NO_INCLUIBLE');
END;

CREATE TRIGGER IF NOT EXISTS impedir_inclusion_pregunta_excluida_update
BEFORE UPDATE OF pregunta_id, estado ON banco_preguntas
WHEN NEW.estado = 'INCLUIDA'
 AND EXISTS (
     SELECT 1
     FROM preguntas_exclusiones AS pe
     WHERE pe.pregunta_id = NEW.pregunta_id
       AND pe.estado IN ('CUARENTENA', 'RETIRADA')
 )
BEGIN
    SELECT RAISE(ABORT, 'PREGUNTA_EXCLUIDA_NO_INCLUIBLE');
END;
