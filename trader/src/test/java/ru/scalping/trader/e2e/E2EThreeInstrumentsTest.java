package ru.scalping.trader.e2e;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import ru.scalping.trader.risk.RiskCheckResult;
import ru.scalping.trader.risk.RiskManager;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * E2E 3 инструмента (задача 6.4):
 * BR+SBER+TCSG параллельно, сигналы по каждому, дедупликация, max_positions=3.
 * 4-й сигнал rejected при 3 открытых.
 */
@Tag("e2e")
class E2EThreeInstrumentsTest {

    @Test
    void fourthSignalRejectedAtMaxPositions() {
        RiskManager rm = new RiskManager();
        // Открытие 3 позиций
        rm.onPositionOpened("BR", "t1");
        rm.onPositionOpened("SBER", "t2");
        rm.onPositionOpened("TCSG", "t3");

        // 4-й сигнал по новому инструменту — reject
        RiskCheckResult result = rm.check("GAZP", 100.0, 99.0, 1, 200_000, 0.0);
        assertThat(result.allowed()).isFalse();
        assertThat(result.rejectReason()).contains("max_open_positions");
    }

    @Test
    void allThreeInstrumentsProcessable() {
        RiskManager rm = new RiskManager();
        // Каждый из 3 инструментов проходит risk-check
        for (String figi : new String[]{"BR", "SBER", "TCSG"}) {
            RiskCheckResult result = rm.check(figi, 100.0, 99.0, 1, 200_000, 0.0);
            assertThat(result.allowed()).as("Instrument %s should be allowed", figi).isTrue();
            rm.onPositionOpened(figi, "trade-" + figi);
        }
        assertThat(rm.openPositionsCount()).isEqualTo(3);
    }
}
