package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.offset;

class EvEstimatorTest {

    private static List<Double> trades(int wins, double win, int losses, double loss) {
        List<Double> pnls = new ArrayList<>();
        for (int i = 0; i < wins; i++) {
            pnls.add(win);
        }
        for (int i = 0; i < losses; i++) {
            pnls.add(-loss);
        }
        return pnls;
    }

    @Test
    void computesExpectedValueFromWinRateAndPayoff() {
        // p = 0.5, avgWin = 100, avgLoss = 50 → EV = 50 − 25 = 25
        assertThat(EvEstimator.expectedValue(trades(25, 100.0, 25, 50.0), 50))
                .hasValueSatisfying(ev -> assertThat(ev).isCloseTo(25.0, offset(1e-9)));
    }

    @Test
    void negativeEdgeGivesNegativeValue() {
        // p = 0.25, avgWin = 100, avgLoss = 100 → EV = 25 − 75 = −50
        assertThat(EvEstimator.expectedValue(trades(15, 100.0, 45, 100.0), 50))
                .hasValueSatisfying(ev -> assertThat(ev).isCloseTo(-50.0, offset(1e-9)));
    }

    @Test
    void emptyBelowMinimumSample() {
        assertThat(EvEstimator.expectedValue(trades(10, 100.0, 10, 50.0), 50)).isEmpty();
    }

    @Test
    void emptyForOneSidedSample() {
        assertThat(EvEstimator.expectedValue(trades(60, 100.0, 0, 0.0), 50)).isEmpty();
        assertThat(EvEstimator.expectedValue(trades(0, 0.0, 60, 100.0), 50)).isEmpty();
    }

    @Test
    void zeroPnlCountsAsLoss() {
        List<Double> pnls = new ArrayList<>(trades(25, 100.0, 25, 0.0));
        // 25 прибылей по 100 и 25 нулей: EV = 0.5·100 − 0.5·0 = 50
        assertThat(EvEstimator.expectedValue(pnls, 50))
                .hasValueSatisfying(ev -> assertThat(ev).isCloseTo(50.0, offset(1e-9)));
    }

    @Test
    void ignoresMissingAndNonFiniteValues() {
        List<Double> pnls = new ArrayList<>(trades(25, 100.0, 25, 50.0));
        pnls.add(null);
        pnls.add(Double.NaN);

        assertThat(EvEstimator.expectedValue(pnls, 50)).isPresent();
    }

    @Test
    void emptyInputAndInvalidThreshold() {
        assertThat(EvEstimator.expectedValue(Collections.emptyList(), 50)).isEmpty();
        assertThat(EvEstimator.expectedValue(null, 50)).isEmpty();
        assertThat(EvEstimator.expectedValue(trades(25, 100.0, 25, 50.0), 0)).isEmpty();
    }
}
