-- V1__init_schema.sql — начальная схема для scalping-trading-system.
-- 7 таблиц из ТЗ раздел 5. Владелец миграций — Java-модуль Trader.

-- 5.1. Справочник торговых инструментов.
CREATE TABLE instruments (
    figi                  VARCHAR(32)   NOT NULL,
    uid                   UUID,
    ticker                VARCHAR(16),
    class_code            VARCHAR(16),
    name                  VARCHAR(128),
    instrument_type       VARCHAR(16),
    lot                   INT,
    min_price_increment   DECIMAL(20, 9),
    currency              VARCHAR(8),
    api_trade_available   BOOLEAN,
    trading_status        SMALLINT,
    tier                  SMALLINT,
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_instruments PRIMARY KEY (figi)
);

-- 5.2. Результаты анализа (пишется Python Analyzer).
CREATE TABLE analysis_results (
    signal_id             UUID          NOT NULL,
    ts                    TIMESTAMPTZ   NOT NULL,
    instrument_id         VARCHAR(32)   NOT NULL,
    candle_ts             TIMESTAMPTZ,
    candle_interval       VARCHAR(8),
    strategy_id           VARCHAR(32),
    analyzer_version      VARCHAR(16),
    indicators            JSONB,
    signal                VARCHAR(8),
    confidence            DECIMAL(5, 4),
    suggested_price       DECIMAL(20, 9),
    suggested_sl          DECIMAL(20, 9),
    suggested_tp          DECIMAL(20, 9),
    position_size_hint    INT,
    expires_at            TIMESTAMPTZ,
    CONSTRAINT pk_analysis_results PRIMARY KEY (signal_id),
    CONSTRAINT fk_analysis_instrument FOREIGN KEY (instrument_id)
        REFERENCES instruments (figi)
);

CREATE INDEX idx_analysis_instr_ts ON analysis_results (instrument_id, ts DESC);
CREATE INDEX idx_analysis_signal_ts ON analysis_results (signal, ts DESC);
CREATE INDEX idx_analysis_expires   ON analysis_results (expires_at);

-- 5.3. История торговых операций.
CREATE TABLE trade_results (
    trade_id              UUID          NOT NULL,
    signal_id             UUID,
    instrument_id         VARCHAR(32)   NOT NULL,
    direction             VARCHAR(4),
    quantity_lots         INT,
    order_id              VARCHAR(36),
    exchange_order_id     VARCHAR(64),
    price_entry           DECIMAL(20, 9),
    price_exit            DECIMAL(20, 9),
    commission            DECIMAL(20, 9),
    pnl                   DECIMAL(20, 9),
    sl_price              DECIMAL(20, 9),
    tp_price              DECIMAL(20, 9),
    sl_order_id           VARCHAR(64),
    tp_order_id           VARCHAR(64),
    status                VARCHAR(16),
    reject_reason         VARCHAR(256),
    opened_at             TIMESTAMPTZ,
    closed_at             TIMESTAMPTZ,
    CONSTRAINT pk_trade_results PRIMARY KEY (trade_id),
    CONSTRAINT fk_trade_signal FOREIGN KEY (signal_id)
        REFERENCES analysis_results (signal_id),
    CONSTRAINT fk_trade_instrument FOREIGN KEY (instrument_id)
        REFERENCES instruments (figi)
);

CREATE INDEX idx_trade_instr_opened ON trade_results (instrument_id, opened_at DESC);
CREATE INDEX idx_trade_status       ON trade_results (status);
CREATE INDEX idx_trade_signal       ON trade_results (signal_id);

-- 5.4. Текущие открытые позиции.
CREATE TABLE positions (
    instrument_id         VARCHAR(32)   NOT NULL,
    quantity_lots         INT,
    avg_price             DECIMAL(20, 9),
    current_price         DECIMAL(20, 9),
    unrealized_pnl        DECIMAL(20, 9),
    opened_at             TIMESTAMPTZ,
    trade_id              UUID,
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_positions PRIMARY KEY (instrument_id),
    CONSTRAINT fk_positions_trade FOREIGN KEY (trade_id)
        REFERENCES trade_results (trade_id),
    CONSTRAINT fk_positions_instrument FOREIGN KEY (instrument_id)
        REFERENCES instruments (figi)
);

-- 5.5. История заявок.
CREATE TABLE orders (
    order_id              VARCHAR(36)   NOT NULL,
    exchange_order_id     VARCHAR(64),
    signal_id             UUID,
    instrument_id         VARCHAR(32)   NOT NULL,
    direction             VARCHAR(4),
    order_type            VARCHAR(8),
    quantity_lots         INT,
    price                 DECIMAL(20, 9),
    status                VARCHAR(24),
    lots_executed         INT,
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_orders PRIMARY KEY (order_id),
    CONSTRAINT fk_orders_signal FOREIGN KEY (signal_id)
        REFERENCES analysis_results (signal_id),
    CONSTRAINT fk_orders_instrument FOREIGN KEY (instrument_id)
        REFERENCES instruments (figi)
);

CREATE INDEX idx_orders_status ON orders (status);

-- 5.6. Параметры риск-менеджмента (настраивается без передеплоя).
CREATE TABLE risk_config (
    key                   VARCHAR(64)   NOT NULL,
    value                 VARCHAR(128),
    description           VARCHAR(256),
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_by            VARCHAR(64),
    CONSTRAINT pk_risk_config PRIMARY KEY (key)
);

-- 5.7. Дедупликация обработанных сигналов (TTL 1 час — см. V2).
CREATE TABLE processed_signals (
    signal_id             UUID          NOT NULL,
    processed_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    result                VARCHAR(16),
    reject_reason         VARCHAR(256),
    CONSTRAINT pk_processed_signals PRIMARY KEY (signal_id)
);

CREATE INDEX idx_processed_at ON processed_signals (processed_at);
