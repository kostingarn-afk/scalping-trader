package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.offset;

class KellyEstimatorTest {

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
    void computesFractionFromWinRateAndPayoff() {
        // p = 0.5, b = 100/50 = 2 → f = 0.5 − 0.5/2 = 0.25
        assertThat(KellyEstimator.fraction(trades(15, 100.0, 15, 50.0)))
                .hasValueSatisfying(f -> assertThat(f).isCloseTo(0.25, offset(1e-9)));
    }

    @Test
    void negativeEdgeIsNegative() {
        // p = 0.3, b = 1 → f = 0.3 − 0.7 = −0.4: преимущества нет
        assertThat(KellyEstimator.fraction(trades(6, 100.0, 14, 100.0)))
                .hasValueSatisfying(f -> assertThat(f).isCloseTo(-0.4, offset(1e-9)));
    }

    @Test
    void returnsEmptyBelowMinimumSample() {
        assertThat(KellyEstimator.fraction(trades(10, 100.0, 9, 50.0))).isEmpty();
    }

    @Test
    void returnsEmptyForOneSidedSample() {
        assertThat(KellyEstimator.fraction(trades(25, 100.0, 0, 0.0))).isEmpty();
        assertThat(KellyEstimator.fraction(trades(0, 0.0, 25, 100.0))).isEmpty();
    }

    @Test
    void ignoresZeroAndMissingResults() {
        List<Double> pnls = new ArrayList<>(trades(15, 100.0, 15, 50.0));
        pnls.add(0.0);
        pnls.add(null);

        assertThat(KellyEstimator.fraction(pnls)).isPresent();
    }

    @Test
    void handlesEmptyInput() {
        assertThat(KellyEstimator.fraction(Collections.emptyList())).isEmpty();
        assertThat(KellyEstimator.fraction(null)).isEmpty();
    }
}
