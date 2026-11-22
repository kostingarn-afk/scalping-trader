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
    /** Оценка Келли (доля капитала) по закрытым сделкам; {@code null} — оценки нет. */
    private volatile Double kellyFraction;
    /** Ожидаемая доходность сделки, ₽; {@code null} — оценки нет (мало закрытых сделок). */
    private volatile Double expectedValue;

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

    /** Обновить оценку Келли; {@code null} — статистики нет, поведение прежнее. */
    public void updateKellyFraction(Double fraction) {
        this.kellyFraction = fraction;
        log.info("Kelly fraction updated: {}", fraction);
    }

    public Double kellyFraction() {
        return kellyFraction;
    }

    /** Обновить ожидаемую доходность; {@code null} — статистики нет, гейт не действует. */
    public void updateExpectedValue(Double ev) {
        this.expectedValue = ev;
        log.info("Expected value updated: {}", ev);
    }

    public Double expectedValue() {
        return expectedValue;
    }

    /**
     * Риск на сделку в % депозита: {@code min(max_loss, kelly_cap_pct, Келли·100)}.
     *
     * <p>Без оценки Келли действует только {@code max_loss_per_trade_pct}: ноль остановил бы
     * торговлю до накопления статистики, а потолок без оценки ограничивать нечего.
     */
    double effectiveRiskPct(RiskConfig cfg) {
        double maxLoss = cfg.maxLossPerTradePct();
        Double kelly = kellyFraction;
        if (kelly == null || !Double.isFinite(kelly)) {
            return maxLoss;
        }
        return Math.min(maxLoss, Math.min(cfg.kellyCapPct(), kelly * 100.0));
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
        return check(figi, suggestedPrice, suggestedSl, 0.0, lotSize, deposit, correlation);
    }

    /**
     * Полная проверка сигнала.
     *
     * <p>{@code suggestedTp} нужен комиссионному фильтру: без цели нельзя оценить, покроет ли
     * сделка ratio комиссий. Значение 0 (или меньше) отключает проверку — так работает
     * перегрузка без TP.
     *
     * @param figi          инструмент
     * @param suggestedPrice цена входа
     * @param suggestedSl   Stop-Loss
     * @param suggestedTp   Take-Profit
     * @param lotSize       лотность инструмента
     * @param deposit       текущий депозит
     * @param correlation   корреляция с открытыми позициями (0 если нет)
     */
    public RiskCheckResult check(String figi, double suggestedPrice, double suggestedSl,
                                 double suggestedTp, int lotSize, double deposit,
                                 double correlation) {
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

        // 4. Ожидаемая доходность: при отрицательном EV сделка не нужна, а размер считать
        // незачем. Оценки нет (мало закрытых сделок) — правило не действует.
        if (expectedValue != null && Double.isFinite(expectedValue) && expectedValue <= 0) {
            return RiskCheckResult.rejected("negative EV");
        }

        // 5. Динамический размер позиции
        int quantityLots = calculatePositionSize(suggestedPrice, suggestedSl, lotSize, deposit, cfg);
        if (quantityLots < 1) {
            return RiskCheckResult.rejected("position_size too small");
        }

        // 6. Лимит объёма: номинал, а не риск. Объём урезается, а не отклоняется — при риске
        // 1 % и узком стопе расчётный объём легко превышает депозит.
        quantityLots = capByNotional(quantityLots, suggestedPrice, lotSize, deposit, cfg);
        if (quantityLots < 1) {
            return RiskCheckResult.rejected("notional limit");
        }

        // 8. Комиссионный фильтр: ожидаемая прибыль до TP должна покрыть ratio комиссий круга.
        if (suggestedTp > 0
                && !passesCommissionFilter(
                        suggestedPrice, suggestedSl, suggestedTp, quantityLots, lotSize, cfg)) {
            return RiskCheckResult.rejected("commission filter");
        }

        return RiskCheckResult.allowed(quantityLots);
    }

    /** Урезать объём под лимит номинала (в % депозита). */
    int capByNotional(int lots, double price, int lotSize, double deposit, RiskConfig cfg) {
        double notionalLimit = deposit * cfg.maxPositionNotionalPct() / 100.0;
        if (price <= 0 || lotSize <= 0 || notionalLimit <= 0) {
            return 0;
        }
        double lotsByNotional = notionalLimit / (price * lotSize);
        return (int) Math.min(lots, Math.floor(lotsByNotional));
    }

    /**
     * Ожидаемая прибыль до TP покрывает {@code commission_profit_ratio} комиссий круга.
     *
     * <p>Ставка берётся из конфигурации, поэтому смена тарифа брокера не требует правки кода:
     * на «Инвесторе» (0.3 % за сторону) фильтр отсекает почти всё, на «Трейдере» — единицы.
     *
     * <p>Направление выводится из положения стопа: цель должна лежать впереди входа
     * ({@code sl < entry < tp} для покупки, {@code tp < entry < sl} для продажи). Если порядок
     * нарушен (гэп, устаревший сигнал), сделка отклоняется: прибыли «до TP» у неё нет.
     * Круговая комиссия считается по обеим ногам — входу и цели, как в бэктесте.
     */
    boolean passesCommissionFilter(
            double entry, double sl, double tp, int lots, int lotSize, RiskConfig cfg) {
        boolean longSide = sl < entry;
        boolean targetAhead = longSide ? tp > entry : tp < entry;
        if (!targetAhead) {
            return false;
        }
        double units = (double) lots * lotSize;
        double profit = Math.abs(tp - entry) * units;
        double roundTripCost = cfg.commissionRatePerSide() * (entry + tp) * units;
        return profit >= cfg.commissionProfitRatio() * roundTripCost;
    }

    /**
     * Расчёт объёма: maxLoss = deposit * riskPct%; volume = maxLoss / |entry - sl| / lotSize.
     *
     * <p>riskPct — минимум из max_loss, потолка Келли и самой оценки Келли.
     */
    int calculatePositionSize(double entry, double sl, int lotSize, double deposit, RiskConfig cfg) {
        double maxLoss = deposit * effectiveRiskPct(cfg) / 100.0;
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
