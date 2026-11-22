package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

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

    // --- Лимит номинала и комиссионный фильтр (V4) -----------------------------------

    @Test
    void notionalLimitCutsQuantityInsteadOfRejecting() {
        RiskManager rm = new RiskManager();
        // Риск 1 % = 2000 ₽ при стопе 100 → 90 даёт 200 лотов = 20 000 ₽ номинала.
        // Лимит 5 % депозита (10 000 ₽) должен урезать объём до 100 лотов.
        int lots = rm.capByNotional(200, 100.0, 1, 200_000, withNotionalPct(5.0));

        assertThat(lots).isEqualTo(100);
    }

    @Test
    void notionalLimitRejectsWhenEvenOneLotDoesNotFit() {
        RiskManager rm = new RiskManager();
        rm.updateConfig(withNotionalPct(10.0));
        // Цена 30 000 при стопе 29 000 → 2 лота = 60 000 ₽, а лимит 10 % = 20 000 ₽.
        RiskCheckResult result = rm.check("SBER", 30_000.0, 29_000.0, 1, 200_000, 0.0);

        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("notional limit");
    }

    @Test
    void commissionFilterRejectsThinTargetOnInvestorTariff() {
        RiskManager rm = new RiskManager();
        // Ставка «Инвестора» задана явно: дефолт — «Трейдер» 0.0005 (шаг 1.9).
        // Цель 0.5 % при ставке 0.3 % за сторону: круг 0.6 %, ratio 3 → нужно 1.8 %.
        rm.updateConfig(withCommissionRate(0.003));
        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 100.5, 1, 200_000, 0.0);

        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("commission filter");
    }

    @Test
    void negativeExpectedValueRejectsSignal() {
        RiskManager rm = new RiskManager();
        rm.updateExpectedValue(-12.5);

        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 102.0, 1, 200_000, 0.0);

        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).isEqualTo("negative EV");
    }

    @Test
    void zeroExpectedValueRejectsSignal() {
        RiskManager rm = new RiskManager();
        rm.updateExpectedValue(0.0);

        assertThat(rm.check("SBER", 100.0, 99.0, 102.0, 1, 200_000, 0.0).allowed()).isFalse();
    }

    @Test
    void positiveExpectedValueAllowsSignal() {
        RiskManager rm = new RiskManager();
        rm.updateExpectedValue(35.0);

        assertThat(rm.check("SBER", 100.0, 99.0, 102.0, 1, 200_000, 0.0).allowed()).isTrue();
    }

    @Test
    void missingExpectedValueKeepsPreviousBehaviour() {
        RiskManager rm = new RiskManager();

        assertThat(rm.check("SBER", 100.0, 99.0, 102.0, 1, 200_000, 0.0).allowed()).isTrue();
    }

    @Test
    void commissionFilterRejectsTargetBehindEntry() {
        RiskManager rm = new RiskManager();
        // SL ниже входа (покупка), TP тоже ниже: цель позади входа, прибыли «до TP» нет.
        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 98.0, 1, 200_000, 0.0);

        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("commission filter");
    }

    @Test
    void commissionFilterAcceptsShortGeometry() {
        RiskManager rm = new RiskManager();
        // SL выше входа, TP ниже — короткая позиция; порядок уровней согласован.
        assertThat(rm.check("SBER", 100.0, 101.0, 98.0, 1, 200_000, 0.0).allowed()).isTrue();
    }

    @Test
    void commissionFilterCountsRoundTripOnBothLegs() {
        RiskManager rm = new RiskManager();
        // Расстояние 0.4 % при ставке 0.05 %: круг = 0.1 % от суммы, ratio 3 → нужно 0.3 %.
        // 0.4 % проходит; расчёт «только по входу» дал бы тот же ответ, поэтому проверяем
        // границу, где выход заметно дороже входа: цель 101.0 при входе 100.0.
        assertThat(rm.check("SBER", 100.0, 99.0, 100.4, 1, 200_000, 0.0).allowed()).isTrue();
        assertThat(rm.check("SBER", 100.0, 99.0, 100.2, 1, 200_000, 0.0).allowed()).isFalse();
    }

    @Test
    void evMinTradesOutsideRangeIsRejected() {
        assertThatThrownBy(() -> new RiskConfig(1.0, 3.0, 3.0, 3, 10, 0.8, 10, 660, 0.0005,
                100.0, 1.0, 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("ev_min_trades");
        assertThat(RiskConfig.fromMap(java.util.Map.of("ev_min_trades", "25")).evMinTrades())
                .isEqualTo(25);
    }

    @Test
    void commissionFilterPassesSameTargetOnTraderTariff() {
        RiskManager rm = new RiskManager();
        rm.updateConfig(withCommissionRate(0.0005));

        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 100.5, 1, 200_000, 0.0);

        assertThat(result.allowed()).isTrue();
    }

    @Test
    void commissionFilterSkippedWithoutTarget() {
        RiskManager rm = new RiskManager();

        // Перегрузка без TP: фильтр не применяется, иначе сигналы без цели отсекались бы все.
        assertThat(rm.check("SBER", 100.0, 99.0, 1, 200_000, 0.0).allowed()).isTrue();
    }

    private static RiskConfig withNotionalPct(double notionalPct) {
        RiskConfig d = RiskConfig.defaults();
        return new RiskConfig(d.maxLossPerTradePct(), d.pnlCircuitBreakerPct(),
                d.commissionProfitRatio(), d.maxOpenPositions(), d.maxHoldingTimeMinutes(),
                d.correlationThreshold(), d.maxTradesPerMinute(), d.entryAfterMskMinutes(),
                d.commissionRatePerSide(), notionalPct, d.kellyCapPct(), d.evMinTrades());
    }

    private static RiskConfig withCommissionRate(double ratePerSide) {
        RiskConfig d = RiskConfig.defaults();
        return new RiskConfig(d.maxLossPerTradePct(), d.pnlCircuitBreakerPct(),
                d.commissionProfitRatio(), d.maxOpenPositions(), d.maxHoldingTimeMinutes(),
                d.correlationThreshold(), d.maxTradesPerMinute(), d.entryAfterMskMinutes(),
                ratePerSide, d.maxPositionNotionalPct(), d.kellyCapPct(), d.evMinTrades());
    }
}
