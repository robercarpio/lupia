-- =============================================================================
-- IndesIAhack 2026 - RBE Sales Anomaly Detection
-- Database Contract (Azure Database for PostgreSQL 15/16 & Databricks UC Mirror)
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS rbe;
SET search_path TO rbe, public;

-- -----------------------------------------------------------------------------
-- 1. Model execution metadata (MLflow traceability & synthetic benchmark metrics)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rbe.model_runs (
    run_id                      VARCHAR(64) PRIMARY KEY,
    model_version               VARCHAR(32) NOT NULL,
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    eval_start_date             DATE NOT NULL DEFAULT DATE '2023-08-05',
    eval_end_date               DATE NOT NULL DEFAULT DATE '2026-09-30',
    total_candidate_rows        INTEGER,
    total_alerts                INTEGER,
    total_caidas                INTEGER,
    total_subidas               INTEGER,
    f1_synthetic                NUMERIC(6,4),
    precision_synthetic         NUMERIC(6,4),
    recall_synthetic            NUMERIC(6,4),
    precision_at_100_synthetic  NUMERIC(6,4),
    params_json                 JSONB
);

-- -----------------------------------------------------------------------------
-- 2. Restaurant master dimension (541 Burger King Spain units)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rbe.restaurants_dim (
    restaurante_id              VARCHAR(4) PRIMARY KEY
                                CHECK (restaurante_id ~ '^R(00[1-9]|0[1-9][0-9]|[1-4][0-9]{2}|5[0-4][0-9])$'),
    marca                       VARCHAR(32) NOT NULL DEFAULT 'Burger King',
    pais                        VARCHAR(32) NOT NULL DEFAULT 'España',
    comunidad_autonoma          VARCHAR(64) NOT NULL DEFAULT 'desconocido',
    provincia                   VARCHAR(64) NOT NULL DEFAULT 'desconocido',
    provincia_inferida          VARCHAR(64),                   -- Inferred via holiday correlation for 11 orphan units
    is_orphan_master            BOOLEAN NOT NULL DEFAULT FALSE,
    tipologia                   VARCHAR(16) NOT NULL DEFAULT 'desconocido',
    tamano                      VARCHAR(16) NOT NULL DEFAULT 'desconocido',
    ubicacion                   VARCHAR(32) NOT NULL DEFAULT 'desconocido',
    imagen                      VARCHAR(32) NOT NULL DEFAULT 'desconocido',
    terraza                     VARCHAR(16) NOT NULL DEFAULT 'desconocido',
    tiene_autoking              BOOLEAN NOT NULL DEFAULT FALSE,
    tiene_domicilio             BOOLEAN NOT NULL DEFAULT FALSE, -- Derived from actual sales (distancia_reparto='-' can still sell)
    distancia_reparto           VARCHAR(16) NOT NULL DEFAULT 'desconocido',
    estado                      VARCHAR(32) NOT NULL DEFAULT 'desconocido',
    primera_venta               DATE NOT NULL,
    ultima_venta                DATE NOT NULL,
    mediana_ventas_diaria_eur   NUMERIC(12,2),
    total_alertas               INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT chk_rest_dates   CHECK (ultima_venta >= primera_venta)
);

CREATE INDEX IF NOT EXISTS idx_rest_geo
    ON rbe.restaurants_dim (comunidad_autonoma, provincia);
CREATE INDEX IF NOT EXISTS idx_rest_segment
    ON rbe.restaurants_dim (tipologia, ubicacion, tamano);

-- -----------------------------------------------------------------------------
-- 3. Daily restaurant-channel series & counterfactual baseline (1,377,194 rows)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rbe.series_summary (
    restaurante_id              VARCHAR(4) NOT NULL
                                REFERENCES rbe.restaurants_dim(restaurante_id),
    canal                       VARCHAR(16) NOT NULL
                                CHECK (canal IN ('sala', 'domicilio', 'autoking')),
    fecha                       DATE NOT NULL
                                CHECK (fecha BETWEEN DATE '2023-07-01' AND DATE '2026-09-30'),

    -- Observed metrics (raw values; ticket_medio is not reconstructed from rounded products)
    ventas                      NUMERIC(12,2) NOT NULL CHECK (ventas > 0),
    tickets                     INTEGER NOT NULL CHECK (tickets >= 1),
    ticket_medio                NUMERIC(10,2) NOT NULL CHECK (ticket_medio > 0),

    -- Counterfactual baseline (OOF LightGBM Quantile + robust bilateral LOO)
    ventas_esperadas            NUMERIC(12,2),
    ventas_p05                  NUMERIC(12,2),
    ventas_p95                  NUMERIC(12,2),
    tickets_esperados           NUMERIC(10,2),
    tickets_p05                 NUMERIC(10,2),
    tickets_p95                 NUMERIC(10,2),
    ticket_medio_esperado       NUMERIC(10,2),

    -- Diagnostic residuals & peer/cross-channel signals
    impacto_estimado_eur        NUMERIC(12,2),                 -- ventas - ventas_esperadas
    desviacion_ventas_pct       NUMERIC(8,2),
    desviacion_tickets_pct      NUMERIC(8,2),
    z_ventas                    NUMERIC(8,3),
    z_tickets                   NUMERIC(8,3),
    z_ticket_medio              NUMERIC(8,3),
    z_peer_diff                 NUMERIC(8,3),                  -- Local residual minus CCAA/province peer median
    z_cross_channel             NUMERIC(8,3),                  -- Channel residual minus active sibling channels

    -- Calendar context & deterministic guardrails
    dia_semana                  SMALLINT NOT NULL CHECK (dia_semana BETWEEN 1 AND 7),
    tipo_festivo                VARCHAR(32) NOT NULL DEFAULT '',
    vispera_festivo             BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_especial              VARCHAR(32) NOT NULL DEFAULT '',
    navidad                     BOOLEAN NOT NULL DEFAULT FALSE,
    semana_santa                BOOLEAN NOT NULL DEFAULT FALSE,
    is_eligible_for_alert       BOOLEAN NOT NULL DEFAULT TRUE,
    exclusion_reason            VARCHAR(48),                   -- sin_historico | fecha_especial | festivo | vispera_festivo | apertura | cierre | reforma | canal_pequeno | dia_neutro
    is_systemic_day             BOOLEAN NOT NULL DEFAULT FALSE,

    -- Final anomaly flag
    is_anomaly                  BOOLEAN NOT NULL DEFAULT FALSE,
    anomaly_tipo                VARCHAR(8) CHECK (anomaly_tipo IN ('caida', 'subida')),

    PRIMARY KEY (restaurante_id, canal, fecha)
);

CREATE INDEX IF NOT EXISTS idx_series_rest_fecha
    ON rbe.series_summary (restaurante_id, fecha);
CREATE INDEX IF NOT EXISTS idx_series_fecha_canal
    ON rbe.series_summary (fecha, canal);
CREATE INDEX IF NOT EXISTS idx_series_anomalies_only
    ON rbe.series_summary (fecha, restaurante_id, canal)
    WHERE is_anomaly = TRUE;

-- -----------------------------------------------------------------------------
-- 4. Prioritized anomaly alerts (1 row per restaurant, channel, day)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rbe.alerts_prioritized (
    alert_id                    BIGSERIAL PRIMARY KEY,

    -- Submission contract fields (must pass validar_entrega.py)
    restaurante_id              VARCHAR(4) NOT NULL
                                REFERENCES rbe.restaurants_dim(restaurante_id),
    canal                       VARCHAR(16) NOT NULL
                                CHECK (canal IN ('sala', 'domicilio', 'autoking')),
    fecha                       DATE NOT NULL
                                CHECK (fecha BETWEEN DATE '2023-08-05' AND DATE '2026-09-30'),
    tipo                        VARCHAR(8) NOT NULL
                                CHECK (tipo IN ('caida', 'subida')),
    score                       DOUBLE PRECISION NOT NULL
                                CHECK (score = score AND score > -1e300 AND score < 1e300), -- Reject NaN/Inf

    -- Multi-day episode grouping for UI/operations without breaking daily grain
    episode_id                  VARCHAR(32) NOT NULL,
    episode_start_date          DATE NOT NULL,
    episode_end_date            DATE NOT NULL,
    episode_days                SMALLINT NOT NULL DEFAULT 1 CHECK (episode_days >= 1),
    episode_day_index           SMALLINT NOT NULL DEFAULT 1 CHECK (episode_day_index >= 1),
    episode_total_impact_eur    NUMERIC(12,2),

    -- Denormalized dimensions for low-latency dashboard filtering
    comunidad_autonoma          VARCHAR(64) NOT NULL DEFAULT 'desconocido',
    provincia                   VARCHAR(64) NOT NULL DEFAULT 'desconocido',
    tipologia                   VARCHAR(16) NOT NULL DEFAULT 'desconocido',
    ubicacion                   VARCHAR(32) NOT NULL DEFAULT 'desconocido',

    -- Business prioritization & decomposition
    priority_score              NUMERIC(5,2) NOT NULL CHECK (priority_score BETWEEN 0 AND 100),
    prioridad_nivel             VARCHAR(2) NOT NULL CHECK (prioridad_nivel IN ('P1', 'P2', 'P3')),
    metrica_principal           VARCHAR(16) NOT NULL
                                CHECK (metrica_principal IN ('ventas', 'tickets', 'ambas', 'ticket_medio')),

    ventas_reales               NUMERIC(12,2) NOT NULL CHECK (ventas_reales > 0),
    ventas_esperadas            NUMERIC(12,2) NOT NULL CHECK (ventas_esperadas > 0),
    impacto_estimado_eur        NUMERIC(12,2) NOT NULL,
    impacto_abs_eur             NUMERIC(12,2) GENERATED ALWAYS AS (ABS(impacto_estimado_eur)) STORED,
    desviacion_ventas_pct       NUMERIC(8,2) NOT NULL,

    tickets_reales              INTEGER NOT NULL CHECK (tickets_reales >= 1),
    tickets_esperados           NUMERIC(10,2) NOT NULL CHECK (tickets_esperados > 0),
    desviacion_tickets_pct      NUMERIC(8,2) NOT NULL,

    ticket_medio_real           NUMERIC(10,2) NOT NULL CHECK (ticket_medio_real > 0),
    ticket_medio_esperado       NUMERIC(10,2) NOT NULL CHECK (ticket_medio_esperado > 0),
    contribucion_tickets_pct    NUMERIC(6,2),
    contribucion_tm_pct         NUMERIC(6,2),

    -- Statistical & peer evidence
    z_ventas                    NUMERIC(8,3),
    z_tickets                   NUMERIC(8,3),
    z_ticket_medio              NUMERIC(8,3),
    z_max                       NUMERIC(8,3) NOT NULL,
    z_peer_diff                 NUMERIC(8,3),
    peer_delta_pct              NUMERIC(8,2),
    z_cross_channel             NUMERIC(8,3),
    otros_canales_normales      BOOLEAN,

    -- Business explainability & grounded Copilot payload
    explicacion_negocio         TEXT NOT NULL,
    recomendacion_revision      TEXT,
    evidencia_json              JSONB,
    llm_cached                  BOOLEAN NOT NULL DEFAULT FALSE,

    -- Lineage
    run_id                      VARCHAR(64) REFERENCES rbe.model_runs(run_id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_alerts_rest_canal_fecha UNIQUE (restaurante_id, canal, fecha),
    CONSTRAINT chk_episode_dates CHECK (episode_end_date >= episode_start_date),
    CONSTRAINT chk_episode_day_idx CHECK (episode_day_index <= episode_days)
);

CREATE INDEX IF NOT EXISTS idx_alerts_score_desc
    ON rbe.alerts_prioritized (score DESC, alert_id ASC);
CREATE INDEX IF NOT EXISTS idx_alerts_priority_desc
    ON rbe.alerts_prioritized (priority_score DESC, impacto_abs_eur DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_filters
    ON rbe.alerts_prioritized (fecha DESC, canal, tipo, prioridad_nivel);
CREATE INDEX IF NOT EXISTS idx_alerts_geo
    ON rbe.alerts_prioritized (comunidad_autonoma, provincia, fecha DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_episode
    ON rbe.alerts_prioritized (episode_id, fecha ASC);
CREATE INDEX IF NOT EXISTS idx_alerts_rest
    ON rbe.alerts_prioritized (restaurante_id, fecha DESC);

-- -----------------------------------------------------------------------------
-- 5. Consumption & export views
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW rbe.v_anomalias_export AS
SELECT
    restaurante_id,
    canal,
    TO_CHAR(fecha, 'YYYY-MM-DD')   AS fecha,
    tipo,
    ROUND(score::NUMERIC, 6)       AS score,
    ROUND(impacto_estimado_eur, 2) AS impacto_estimado_eur,
    explicacion_negocio            AS explicacion
FROM rbe.alerts_prioritized
ORDER BY score DESC, impacto_abs_eur DESC, restaurante_id, canal, fecha;

CREATE OR REPLACE VIEW rbe.v_alert_episodes AS
SELECT
    episode_id,
    restaurante_id,
    canal,
    tipo,
    MIN(comunidad_autonoma)                 AS comunidad_autonoma,
    MIN(provincia)                          AS provincia,
    MIN(tipologia)                          AS tipologia,
    MIN(ubicacion)                          AS ubicacion,
    MIN(fecha)                              AS fecha_inicio,
    MAX(fecha)                              AS fecha_fin,
    COUNT(*)::INTEGER                       AS duracion_dias,
    MAX(score)                              AS max_score,
    MAX(priority_score)                     AS max_priority_score,
    MIN(prioridad_nivel)                    AS prioridad_nivel, -- Lexicographic order: P1 < P2 < P3
    SUM(impacto_estimado_eur)               AS impacto_total_eur,
    SUM(impacto_abs_eur)                    AS impacto_abs_total_eur,
    ROUND(AVG(desviacion_ventas_pct), 2)    AS desviacion_media_ventas_pct,
    ROUND(AVG(desviacion_tickets_pct), 2)   AS desviacion_media_tickets_pct,
    (ARRAY_AGG(explicacion_negocio ORDER BY score DESC))[1]    AS explicacion_principal,
    (ARRAY_AGG(recomendacion_revision ORDER BY score DESC))[1] AS recomendacion_principal
FROM rbe.alerts_prioritized
GROUP BY episode_id, restaurante_id, canal, tipo;

CREATE OR REPLACE VIEW rbe.v_kpi_global AS
SELECT
    COUNT(*)                                                                  AS total_alertas_dia,
    COUNT(DISTINCT episode_id)                                                AS total_episodios,
    COUNT(DISTINCT restaurante_id)                                            AS restaurantes_afectados,
    COUNT(*) FILTER (WHERE tipo = 'caida')                                    AS total_caidas,
    COUNT(*) FILTER (WHERE tipo = 'subida')                                   AS total_subidas,
    COUNT(*) FILTER (WHERE prioridad_nivel = 'P1')                            AS alertas_p1,
    COALESCE(SUM(impacto_estimado_eur) FILTER (WHERE tipo = 'caida'), 0)      AS impacto_caidas_eur,
    COALESCE(SUM(impacto_estimado_eur) FILTER (WHERE tipo = 'subida'), 0)     AS impacto_subidas_eur,
    COALESCE(SUM(impacto_abs_eur), 0)                                         AS impacto_bruto_total_eur
FROM rbe.alerts_prioritized;
