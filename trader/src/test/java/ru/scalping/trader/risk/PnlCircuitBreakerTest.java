package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class PnlCircuitBreakerTest {

    @Test
    void triggersAt3PercentLoss() {
        PnlCircuitBreaker cb = new PnlCircuitBreaker();
        // 3% от 200000 = 6000
        boolean triggered = cb.registerPnl(-6000, 200_000, 3.0);
        assertThat(triggered).isTrue();
        assertThat(cb.isActive()).isTrue();
    }

    @Test
    void doesNotTriggerBelowThreshold() {
        PnlCircuitBreaker cb = new PnlCircuitBreaker();
        boolean triggered = cb.registerPnl(-5000, 200_000, 3.0);
        assertThat(triggered).isFalse();
        assertThat(cb.isActive()).isFalse();
    }

    @Test
    void blocksAfterTrigger() {
        PnlCircuitBreaker cb = new PnlCircuitBreaker();
        cb.registerPnl(-6000, 200_000, 3.0);
        assertThat(cb.isActive()).isTrue();
    }

    @Test
    void resetsOnNewSession() {
        PnlCircuitBreaker cb = new PnlCircuitBreaker();
        cb.registerPnl(-6000, 200_000, 3.0);
        assertThat(cb.isActive()).isTrue();
        cb.resetForNewSession();
        assertThat(cb.isActive()).isFalse();
        assertThat(cb.dailyPnl()).isZero();
    }

    @Test
    void accumulatesPnl() {
        PnlCircuitBreaker cb = new PnlCircuitBreaker();
        cb.registerPnl(-2000, 200_000, 3.0);
        cb.registerPnl(-2000, 200_000, 3.0);
        boolean triggered = cb.registerPnl(-2000, 200_000, 3.0);
        assertThat(triggered).isTrue();
    }
}
