-- 005_valuation.sql — cached model outputs (AVM estimates and market forecasts)
--
-- These tables cache DERIVED values. Every row records the method, the input
-- evidence count and the model version so a stale cache can never masquerade
-- as an observed transaction.

CREATE TABLE IF NOT EXISTS property_estimates (
    id                  bigserial PRIMARY KEY,
    property_id         bigint NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    valuation_date      date NOT NULL,        -- the date the value refers to
    price_type          price_type NOT NULL,  -- HISTORICAL_ESTIMATE | CURRENT_ESTIMATE
    estimated_price     numeric(14,2) NOT NULL,
    low_estimate        numeric(14,2) NOT NULL,
    high_estimate       numeric(14,2) NOT NULL,
    currency_code       char(3) NOT NULL,
    confidence          confidence_level NOT NULL,
    precision_level     precision_level NOT NULL,
    method              text NOT NULL,
    model_version       text NOT NULL,
    comparable_count    integer NOT NULL DEFAULT 0,
    evidence            jsonb NOT NULL DEFAULT '{}',   -- explainability payload (§38)
    source_keys         text[] NOT NULL DEFAULT '{}',
    computed_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (property_id, valuation_date, model_version)
);
CREATE INDEX IF NOT EXISTS property_estimates_lookup
    ON property_estimates (property_id, valuation_date DESC);

CREATE TABLE IF NOT EXISTS forecasts (
    id                  bigserial PRIMARY KEY,
    country_iso2        char(2) NOT NULL,
    area_code           text NOT NULL,
    area_name           text NOT NULL,
    segment             text NOT NULL DEFAULT 'all',
    origin_period       date NOT NULL,        -- last observed month used to fit
    target_period       date NOT NULL,        -- forecast month
    horizon_months      integer NOT NULL,
    index_ratio         numeric(10,6) NOT NULL,   -- target / origin index
    ratio_low           numeric(10,6) NOT NULL,
    ratio_high          numeric(10,6) NOT NULL,
    expected_price      numeric(14,2),
    price_low           numeric(14,2),
    price_high          numeric(14,2),
    currency_code       char(3) NOT NULL,
    confidence          confidence_level NOT NULL,
    model               text NOT NULL,
    model_version       text NOT NULL,
    diagnostics         jsonb NOT NULL DEFAULT '{}',
    computed_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (country_iso2, area_code, segment, origin_period, target_period, model_version)
);
CREATE INDEX IF NOT EXISTS forecasts_lookup
    ON forecasts (country_iso2, area_code, segment, target_period);

-- Stored evaluation results for the AVM and the forecaster, so the UI/README
-- can quote measured accuracy rather than claims (§13, §39).
CREATE TABLE IF NOT EXISTS model_evaluations (
    id                  bigserial PRIMARY KEY,
    model               text NOT NULL,
    model_version       text NOT NULL,
    country_iso2        char(2),
    evaluation_kind     text NOT NULL,        -- holdout | backtest
    train_period        text,
    test_period         text,
    sample_size         integer NOT NULL,
    metrics             jsonb NOT NULL,       -- {mae, medae, mape, rmse, ...}
    notes               text,
    computed_at         timestamptz NOT NULL DEFAULT now()
);
