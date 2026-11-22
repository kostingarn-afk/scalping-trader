package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;
import ru.scalping.trader.repository.TradeRepository;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class KellyPollerTest {

    private static List<Double> results() {
        List<Double> pnls = new ArrayList<>();
        for (int i = 0; i < 15; i++) {
            pnls.add(100.0);
            pnls.add(-50.0);
        }
        return pnls;
    }

    @Test
    void updatesEstimateFromRepository() {
        TradeRepository repository = mock(TradeRepository.class);
        when(repository.recentClosedPnls(KellyPoller.SAMPLE_SIZE)).thenReturn(results());
        RiskManager riskManager = new RiskManager();

        new KellyPoller(riskManager, repository).poll();

        assertThat(riskManager.kellyFraction()).isNotNull();
        assertThat(riskManager.kellyFraction()).isCloseTo(0.25, org.assertj.core.api.Assertions.offset(1e-9));
    }

    @Test
    void keepsPreviousEstimateOnRepositoryFailure() {
        TradeRepository repository = mock(TradeRepository.class);
        when(repository.recentClosedPnls(KellyPoller.SAMPLE_SIZE))
                .thenThrow(new RuntimeException("db down"));
        RiskManager riskManager = new RiskManager();
        riskManager.updateKellyFraction(0.1);

        new KellyPoller(riskManager, repository).poll();

        assertThat(riskManager.kellyFraction()).isEqualTo(0.1);
    }
}
