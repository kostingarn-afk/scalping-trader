-- V5__kelly_cap_pct.sql
-- Потолок риска по Келли (решение 4, to_do.md §5.2; шаг 1.7).
-- Фактический риск на сделку = min(Келли, kelly_cap_pct, max_loss_per_trade_pct).
-- Потолок выше 1.0 действует только после поднятия max_loss_per_trade_pct: min вернёт меньший.
-- Диапазон 0.1–5.0 проверяет RiskConfig при чтении; в БД значение хранится строкой.

INSERT INTO risk_config (key, value, description, updated_by) VALUES
    ('kelly_cap_pct', '1.0',
     'Потолок риска по Келли, % депозита (0.1–5.0); фактический риск — минимум с max_loss_per_trade_pct',
     'system')
ON CONFLICT (key) DO NOTHING;
