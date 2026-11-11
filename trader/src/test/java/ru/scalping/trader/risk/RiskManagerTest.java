package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class RiskManagerTest {

    private RiskConfig cfg = RiskConfig.defaults();

    @Test
    void calculatesPositionSizeFromDistance() {
        RiskManager rm = new RiskManager();
        // entry=100, sl=99, lot=1, deposit=200000, maxLoss=1%=2000
        // volume = 2000 / (100-99) / 1 = 2000 lots
        int size = rm.calculatePositionSize(100.0, 99.0, 1, 200_000, cfg);
        assertThat(size).isEqualTo(2000);
    }

    @Test
    void positionSizeTooSmallRejected() {
        RiskManager rm = new RiskManager();
        // entry=100, sl=99, lot=1, deposit=50 → допустимый убыток 1% = 0.5 руб → 0 лотов
        int size = rm.calculatePositionSize(100.0, 99.0, 1, 50, cfg);
        assertThat(size).isLessThan(1);
        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 1, 50, 0.0);
        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("position_size too small");
    }

    @Test
    void maxOpenPositionsReached() {
        RiskManager rm = new RiskManager();
        rm.onPositionOpened("BR", "t1");
        rm.onPositionOpened("SBER", "t2");
        rm.onPositionOpened("TCSG", "t3");
        // 4-й инструмент — reject
        RiskCheckResult result = rm.check("GAZP", 100.0, 99.0, 1, 200_000, 0.0);
        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("max_open_positions");
    }

    @Test
    void correlationLimitRejects() {
        RiskManager rm = new RiskManager();
        rm.onPositionOpened("BR", "t1");
        // correlation 0.85 > threshold 0.8
        RiskCheckResult result = rm.check("Si", 100.0, 99.0, 1, 200_000, 0.85);
        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("correlation");
    }

    @Test
    void pnlCircuitBreakerBlocks() {
        RiskManager rm = new RiskManager();
        rm.setPnlCircuitBreakerActive(true);
        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 1, 200_000, 0.0);
        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("pnl_circuit_breaker");
    }

    @Test
    void validSignalAllowed() {
        RiskManager rm = new RiskManager();
        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 1, 200_000, 0.0);
        assertThat(result.allowed()).isTrue();
        assertThat(result.quantityLots()).isPositive();
    }
}
