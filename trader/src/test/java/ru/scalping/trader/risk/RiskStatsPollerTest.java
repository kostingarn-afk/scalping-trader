package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;
import ru.scalping.trader.repository.TradeRepository;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.offset;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RiskStatsPollerTest {

    /** 30 прибылей по 100 и 30 убытков по 50: Келли 0.25, EV = 0.5·100 − 0.5·50 = 25. */
    private static List<Double> results() {
        List<Double> pnls = new ArrayList<>();
        for (int i = 0; i < 30; i++) {
            pnls.add(100.0);
            pnls.add(-50.0);
        }
        return pnls;
    }

    @Test
    void updatesKellyAndExpectedValueFromRepository() {
        TradeRepository repository = mock(TradeRepository.class);
        when(repository.recentClosedPnls(RiskStatsPoller.SAMPLE_SIZE)).thenReturn(results());
        RiskManager riskManager = new RiskManager();

        new RiskStatsPoller(riskManager, repository).poll();

        assertThat(riskManager.kellyFraction()).isNotNull();
        assertThat(riskManager.kellyFraction()).isCloseTo(0.25, offset(1e-9));
        assertThat(riskManager.expectedValue()).isCloseTo(25.0, offset(1e-9));
    }

    @Test
    void keepsPreviousValuesOnRepositoryFailure() {
        TradeRepository repository = mock(TradeRepository.class);
        when(repository.recentClosedPnls(RiskStatsPoller.SAMPLE_SIZE))
                .thenThrow(new RuntimeException("db down"));
        RiskManager riskManager = new RiskManager();
        riskManager.updateKellyFraction(0.1);
        riskManager.updateExpectedValue(0.0);

        new RiskStatsPoller(riskManager, repository).poll();

        assertThat(riskManager.kellyFraction()).isEqualTo(0.1);
        assertThat(riskManager.expectedValue()).isEqualTo(0.0);
    }

    @Test
    void clearsEstimateWhenSampleIsTooSmall() {
        TradeRepository repository = mock(TradeRepository.class);
        when(repository.recentClosedPnls(RiskStatsPoller.SAMPLE_SIZE)).thenReturn(List.of(100.0));
        RiskManager riskManager = new RiskManager();
        riskManager.updateExpectedValue(-5.0);

        new RiskStatsPoller(riskManager, repository).poll();

        assertThat(riskManager.expectedValue()).isNull();
    }
}
