package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.offset;

class KellySizingTest {

    private static RiskConfig config(double maxLossPct, double kellyCapPct) {
        return new RiskConfig(maxLossPct, 3.0, 3.0, 3, 10, 0.8, 10, 660, 0.003, 100.0, kellyCapPct, 50);
    }

    @Test
    void withoutKellyEstimateRiskEqualsMaxLoss() {
        RiskManager rm = new RiskManager();

        assertThat(rm.effectiveRiskPct(config(1.0, 0.5))).isCloseTo(1.0, offset(1e-12));
    }

    @Test
    void capLimitsRiskWhenKellyIsHigher() {
        RiskManager rm = new RiskManager();
        rm.updateKellyFraction(0.03); // 3 % по Келли

        assertThat(rm.effectiveRiskPct(config(2.0, 0.5))).isCloseTo(0.5, offset(1e-12));
    }

    @Test
    void kellyLimitsRiskWhenBelowCapAndMaxLoss() {
        RiskManager rm = new RiskManager();
        rm.updateKellyFraction(0.005); // 0.5 %

        assertThat(rm.effectiveRiskPct(config(2.0, 2.0))).isCloseTo(0.5, offset(1e-12));
    }

    @Test
    void capAboveMaxLossDoesNothingUntilMaxLossIsRaised() {
        RiskManager rm = new RiskManager();
        rm.updateKellyFraction(0.03);

        assertThat(rm.effectiveRiskPct(config(1.0, 5.0))).isCloseTo(1.0, offset(1e-12));
        assertThat(rm.effectiveRiskPct(config(2.0, 5.0))).isCloseTo(2.0, offset(1e-12));
    }

    @Test
    void negativeKellyRejectsTrade() {
        RiskManager rm = new RiskManager();
        rm.updateKellyFraction(-0.2);

        RiskCheckResult result = rm.check("SBER", 100.0, 99.0, 1, 200_000, 0.0);

        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("position_size too small");
    }

    @Test
    void kellyCapOutsideRangeIsRejected() {
        assertThatThrownBy(() -> config(1.0, 0.05))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("kelly_cap_pct");
        assertThatThrownBy(() -> config(1.0, 5.5))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("kelly_cap_pct");
    }

    @Test
    void riskConfigReadsKellyCapFromMap() {
        RiskConfig parsed = RiskConfig.fromMap(java.util.Map.of("kelly_cap_pct", "2.5"));

        assertThat(parsed.kellyCapPct()).isEqualTo(2.5);
        assertThat(RiskConfig.defaults().kellyCapPct()).isEqualTo(1.0);
    }
}
