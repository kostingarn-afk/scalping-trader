package ru.scalping.trader.signal;

import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Instant;

/**
 * Проверка устаревания сигнала и торгового статуса (задача 4.3).
 */
@Component
public class SignalValidator {

    private final Clock clock;

    public SignalValidator(Clock clock) {
        this.clock = clock;
    }

    public SignalValidator() {
        this(Clock.systemUTC());
    }

    /** @return true если сигнал устарел (expires_at < now). */
    public boolean isExpired(Instant expiresAt) {
        return expiresAt != null && expiresAt.isBefore(clock.instant());
    }

    /** @return true если торговый статус NORMAL_TRADING (2). */
    public boolean isTradable(int tradingStatus) {
        return tradingStatus == 2; // SECURITY_TRADING_STATUS_NORMAL_TRADING
    }
}
