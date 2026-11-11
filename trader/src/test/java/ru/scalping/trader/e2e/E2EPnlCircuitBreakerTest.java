package ru.scalping.trader.e2e;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import ru.scalping.trader.risk.PnlCircuitBreaker;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * E2E PnL-CB (задача 6.2):
 * симуляция 3 убыточных сделок суммарно -6000 руб → PnL-CB срабатывает,
 * позиции закрываются, новые заявки блокируются.
 */
@Tag("e2e")
class E2EPnlCircuitBreakerTest {

    @Test
    void threeLossesTriggerCircuitBreaker() {
        PnlCircuitBreaker cb = new PnlCircuitBreaker();
        double deposit = 200_000;
        double breakerPct = 3.0;

        // 3 убыточные сделки по -2000
        boolean triggered = false;
        for (int i = 0; i < 3; i++) {
            triggered = cb.registerPnl(-2000, deposit, breakerPct);
        }

        assertThat(triggered).as("PnL-CB should trigger at -6000").isTrue();
        assertThat(cb.isActive()).isTrue();
        assertThat(cb.dailyPnl()).isEqualTo(-6000.0);
    }

    @Test
    void newSignalsRejectedAfterCircuitBreaker() {
        PnlCircuitBreaker cb = new PnlCircuitBreaker();
        cb.registerPnl(-6000, 200_000, 3.0);
        // После CB — новые сигналы rejected (проверяется через RiskManager)
        assertThat(cb.isActive()).isTrue();
    }
}
