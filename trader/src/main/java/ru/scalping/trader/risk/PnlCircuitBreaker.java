package ru.scalping.trader.risk;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Instant;

/**
 * PnL Circuit Breaker (задача 4.9, spec risk-management).
 *
 * <p>Отслеживает суммарный дневной PnL. При убытке ≥ {@code pnlCircuitBreakerPct}% депозита
 * (6000 руб при 3% от 200к) — close all + блок до след. сессии.
 * Сброс — при наступлении новой торговой сессии (через {@link #resetForNewSession}).
 */
@Component
public class PnlCircuitBreaker {

    private static final Logger log = LoggerFactory.getLogger(PnlCircuitBreaker.class);

    private final Clock clock;
    private volatile double dailyPnl = 0.0;
    private volatile boolean active = false;
    private volatile Instant lastSessionReset;

    public PnlCircuitBreaker(Clock clock) {
        this.clock = clock;
        this.lastSessionReset = clock.instant();
    }

    public PnlCircuitBreaker() {
        this(Clock.systemUTC());
    }

    /**
     * Регистрация PnL закрытой сделки.
     *
     * @return true если CB сработал (достигнут порог)
     */
    public boolean registerPnl(double pnl, double deposit, double breakerPct) {
        dailyPnl += pnl;
        double threshold = -deposit * breakerPct / 100.0;
        if (!active && dailyPnl <= threshold) {
            active = true;
            log.warn("PnL Circuit Breaker triggered: dailyPnl={}, threshold={}", dailyPnl, threshold);
            return true;
        }
        return false;
    }

    public boolean isActive() {
        return active;
    }

    public double dailyPnl() {
        return dailyPnl;
    }

    /** Сброс блокировки на новую торговую сессию. */
    public void resetForNewSession() {
        log.info("PnL Circuit Breaker reset for new session (was active={}, dailyPnl={})", active, dailyPnl);
        active = false;
        dailyPnl = 0.0;
        lastSessionReset = clock.instant();
    }

    public Instant lastSessionReset() {
        return lastSessionReset;
    }
}
