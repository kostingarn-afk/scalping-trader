package ru.scalping.trader.risk;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Risk Manager (задача 4.4, spec risk-management).
 *
 * <p>Проверки:
 * <ul>
 *   <li>Динамический размер позиции: убыток на сделку ≤ 1% депозита</li>
 *   <li>max_open_positions (3)</li>
 *   <li>Корреляционный лимит (correlation > threshold → reject)</li>
 *   <li>max_trades_per_minute (10) — self-rate-limit</li>
 * </ul>
 *
 * <p>Параметры из {@link RiskConfig}, подхватываются без рестарта через {@link #updateConfig}.
 */
@Component
public class RiskManager {

    private static final Logger log = LoggerFactory.getLogger(RiskManager.class);

    private volatile RiskConfig config = RiskConfig.defaults();
    private final Clock clock;
    private final Deque<Instant> recentOrders = new ArrayDeque<>();
    private final Map<String, String> openPositions = new ConcurrentHashMap<>(); // figi → tradeId
    private volatile boolean pnlCircuitBreakerActive = false;

    public RiskManager(Clock clock) {
        this.clock = clock;
    }

    public RiskManager() {
        this(Clock.systemUTC());
    }

    public void updateConfig(RiskConfig config) {
        this.config = config;
        log.info("Risk config updated: {}", config);
    }

    public RiskConfig config() {
        return config;
    }

    public void setOpenPositions(java.util.Set<String> figis) {
        openPositions.clear();
        for (String figi : figis) openPositions.put(figi, "recovered");
    }

    public void onPositionOpened(String figi, String tradeId) {
        openPositions.put(figi, tradeId);
    }

    public void onPositionClosed(String figi) {
        openPositions.remove(figi);
    }

    public void setPnlCircuitBreakerActive(boolean active) {
        this.pnlCircuitBreakerActive = active;
    }

    public boolean isPnlCircuitBreakerActive() {
        return pnlCircuitBreakerActive;
    }

    /**
     * Проверка сигнала перед исполнением.
     *
     * @param figi          инструмент
     * @param suggestedPrice цена входа
     * @param suggestedSl   Stop-Loss
     * @param lotSize       лотность инструмента
     * @param deposit       текущий депозит
     * @param correlation   корреляция с открытыми позициями (0 если нет)
     */
    public RiskCheckResult check(String figi, double suggestedPrice, double suggestedSl,
                                 int lotSize, double deposit, double correlation) {
        RiskConfig cfg = config;

        if (pnlCircuitBreakerActive) {
            return RiskCheckResult.rejected("pnl_circuit_breaker active");
        }

        // 1. Корреляционный лимит
        if (correlation > cfg.correlationThreshold()) {
            return RiskCheckResult.rejected("correlation limit");
        }

        // 2. Лимит одновременных позиций
        if (openPositions.size() >= cfg.maxOpenPositions() && !openPositions.containsKey(figi)) {
            return RiskCheckResult.rejected("max_open_positions reached");
        }

        // 3. Rate-limit
        if (!checkRateLimit()) {
            return RiskCheckResult.rejected("rate_limit exceeded");
        }

        // 4. Динамический размер позиции
        int quantityLots = calculatePositionSize(suggestedPrice, suggestedSl, lotSize, deposit, cfg);
        if (quantityLots < 1) {
            return RiskCheckResult.rejected("position_size too small");
        }

        return RiskCheckResult.allowed(quantityLots);
    }

    /**
     * Расчёт объёма: maxLoss = deposit * pct%; volume = maxLoss / |entry - sl| / lotSize.
     */
    int calculatePositionSize(double entry, double sl, int lotSize, double deposit, RiskConfig cfg) {
        double maxLoss = deposit * cfg.maxLossPerTradePct() / 100.0;
        double priceDiff = Math.abs(entry - sl);
        if (priceDiff <= 0) return 0;
        double rawVolume = maxLoss / priceDiff / lotSize;
        return (int) Math.floor(rawVolume);
    }

    private boolean checkRateLimit() {
        Instant now = clock.instant();
        Instant oneMinuteAgo = now.minus(Duration.ofMinutes(1));
        synchronized (recentOrders) {
            while (!recentOrders.isEmpty() && recentOrders.peekFirst().isBefore(oneMinuteAgo)) {
                recentOrders.pollFirst();
            }
            if (recentOrders.size() >= config.maxTradesPerMinute()) {
                return false;
            }
            recentOrders.addLast(now);
        }
        return true;
    }

    public int openPositionsCount() {
        return openPositions.size();
    }
}
