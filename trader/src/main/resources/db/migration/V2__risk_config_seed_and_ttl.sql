-- V2__risk_config_seed_and_ttl.sql
-- Предопределённые ключи risk_config (ТЗ раздел 5.6) и TTL для processed_signals.

INSERT INTO risk_config (key, value, description, updated_by) VALUES
    ('max_loss_per_trade_pct',   '1.0', 'Максимальный убыток на сделку, % депозита', 'system'),
    ('pnl_circuit_breaker_pct',  '3.0', 'Дневной лимит убытка для PnL Circuit Breaker, % депозита', 'system'),
    ('commission_profit_ratio',  '3.0', 'Минимальное отношение прибыль/комиссия (вход+выход)', 'system'),
    ('max_open_positions',       '3',   'Максимум одновременных открытых позиций', 'system'),
    ('max_holding_time_minutes', '10',  'Максимальное время удержания позиции, минуты', 'system'),
    ('correlation_threshold',    '0.8', 'Порог корреляции для запрета открытия позиции', 'system'),
    ('max_trades_per_minute',    '10',  'Лимит сделок в минуту (self-rate-limit)', 'system')
ON CONFLICT (key) DO NOTHING;

-- TTL для processed_signals: записи старше 1 часа удаляются.
-- Реализовано через partition + pg_cron (если доступно) либо scheduled job в Trader.
-- Здесь — индекс для эффективной очистки; сама очистка — в Trader (scheduled task).
CREATE INDEX IF NOT EXISTS idx_processed_signals_cleanup
    ON processed_signals (processed_at);
