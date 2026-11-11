package ru.scalping.trader.risk;

import java.util.Map;

/**
 * Риск-параметры из таблицы {@code risk_config} (задача 4.4).
 * Подхватываются без рестарта (poll).
 */
public record RiskConfig(
        double maxLossPerTradePct,
        double pnlCircuitBreakerPct,
        double commissionProfitRatio,
        int maxOpenPositions,
        int maxHoldingTimeMinutes,
        double correlationThreshold,
        int maxTradesPerMinute
) {
    /** Десериализация из Map<key,value> (значения — строки). */
    public static RiskConfig fromMap(Map<String, String> m) {
        return new RiskConfig(
                Double.parseDouble(m.getOrDefault("max_loss_per_trade_pct", "1.0")),
                Double.parseDouble(m.getOrDefault("pnl_circuit_breaker_pct", "3.0")),
                Double.parseDouble(m.getOrDefault("commission_profit_ratio", "3.0")),
                Integer.parseInt(m.getOrDefault("max_open_positions", "3")),
                Integer.parseInt(m.getOrDefault("max_holding_time_minutes", "10")),
                Double.parseDouble(m.getOrDefault("correlation_threshold", "0.8")),
                Integer.parseInt(m.getOrDefault("max_trades_per_minute", "10"))
        );
    }

    /** Дефолтные значения (ТЗ раздел 5.6). */
    public static RiskConfig defaults() {
        return new RiskConfig(1.0, 3.0, 3.0, 3, 10, 0.8, 10);
    }
}
