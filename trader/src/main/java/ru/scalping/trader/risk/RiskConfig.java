package ru.scalping.trader.risk;

import java.util.Map;

/**
 * Параметры риск-менеджмента из таблицы {@code risk_config} (задача 4.4).
 *
 * <p>Обновляются без перезапуска: {@link RiskConfigPoller} перечитывает их по расписанию (poll).
 */
public record RiskConfig(
        double maxLossPerTradePct,
        double pnlCircuitBreakerPct,
        double commissionProfitRatio,
        int maxOpenPositions,
        int maxHoldingTimeMinutes,
        double correlationThreshold,
        int maxTradesPerMinute,
        /** Запрет входов до HH:MM МСК в минутах от полуночи (0 — выключено). */
        int entryAfterMskMinutes,
        /** Ставка брокера за сторону сделки: «Инвестор» 0.003, «Трейдер» 0.0005. */
        double commissionRatePerSide,
        /** Лимит номинала позиции в % депозита: страховка от неисполнимых объёмов. */
        double maxPositionNotionalPct,
        /** Потолок риска по Келли, % депозита (решение 4: 0.1–5.0). */
        double kellyCapPct,
        /** Минимальная выборка закрытых сделок для гейта ожидаемой доходности (шаг 1.10). */
        int evMinTrades
) {
    public RiskConfig {
        if (kellyCapPct < 0.1 || kellyCapPct > 5.0) {
            throw new IllegalArgumentException(
                    "kelly_cap_pct must be in [0.1, 5.0], got " + kellyCapPct);
        }
        if (evMinTrades < 1) {
            throw new IllegalArgumentException(
                    "ev_min_trades must be at least 1, got " + evMinTrades);
        }
    }

    /** Собрать из Map<key,value> (источник — {@code risk_config}). */
    public static RiskConfig fromMap(Map<String, String> m) {
        return new RiskConfig(
                Double.parseDouble(m.getOrDefault("max_loss_per_trade_pct", "1.0")),
                Double.parseDouble(m.getOrDefault("pnl_circuit_breaker_pct", "3.0")),
                Double.parseDouble(m.getOrDefault("commission_profit_ratio", "3.0")),
                Integer.parseInt(m.getOrDefault("max_open_positions", "3")),
                Integer.parseInt(m.getOrDefault("max_holding_time_minutes", "10")),
                Double.parseDouble(m.getOrDefault("correlation_threshold", "0.8")),
                Integer.parseInt(m.getOrDefault("max_trades_per_minute", "10")),
                Integer.parseInt(m.getOrDefault("entry_after_msk_minutes", "660")),
                Double.parseDouble(m.getOrDefault("commission_rate_per_side", "0.0005")),
                Double.parseDouble(m.getOrDefault("max_position_notional_pct", "100.0")),
                Double.parseDouble(m.getOrDefault("kelly_cap_pct", "1.0")),
                Integer.parseInt(m.getOrDefault("ev_min_trades", "50"))
        );
    }

    /** Значения по умолчанию (синхронны с сидом V2, дополнены V3–V7). */
    public static RiskConfig defaults() {
        return new RiskConfig(1.0, 3.0, 3.0, 3, 10, 0.8, 10, 660, 0.0005, 100.0, 1.0, 50);
    }
}
