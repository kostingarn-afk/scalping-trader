package ru.scalping.trader.risk;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;

class TradingWindowTest {

    private static final ZoneId MSK = ZoneId.of("Europe/Moscow");
    private static final RiskConfig DEFAULT = RiskConfig.defaults();

    private TradingWindow windowAt(Instant moment) {
        return new TradingWindow(Clock.fixed(moment, ZoneOffset.UTC), MSK);
    }

    private static RiskConfig withThreshold(int minutes) {
        return new RiskConfig(
                DEFAULT.maxLossPerTradePct(),
                DEFAULT.pnlCircuitBreakerPct(),
                DEFAULT.commissionProfitRatio(),
                DEFAULT.maxOpenPositions(),
                DEFAULT.maxHoldingTimeMinutes(),
                DEFAULT.correlationThreshold(),
                DEFAULT.maxTradesPerMinute(),
                minutes,
                DEFAULT.commissionRatePerSide(),
                DEFAULT.maxPositionNotionalPct()
        );
    }

    @Test
    void firstSessionHourIsBlocked() {
        // 07:30 UTC = 10:30 МСК — первый час основной сессии
        TradingWindow window = windowAt(Instant.parse("2026-11-13T07:30:00Z"));

        assertThat(window.isEntryAllowed(DEFAULT)).isFalse();
    }

    @Test
    void entryAllowedExactlyAtThreshold() {
        // 08:00 UTC = 11:00 МСК — граница включена
        TradingWindow window = windowAt(Instant.parse("2026-11-13T08:00:00Z"));

        assertThat(window.isEntryAllowed(DEFAULT)).isTrue();
    }

    @Test
    void eveningSessionIsAllowed() {
        TradingWindow window = windowAt(Instant.parse("2026-11-13T18:30:00Z"));

        assertThat(window.isEntryAllowed(DEFAULT)).isTrue();
    }

    @Test
    void zeroThresholdDisablesTheRule() {
        TradingWindow window = windowAt(Instant.parse("2026-11-13T07:05:00Z"));

        assertThat(window.isEntryAllowed(withThreshold(0))).isTrue();
    }

    @Test
    void thresholdIsReadFromConfigNotFromCode() {
        TradingWindow window = windowAt(Instant.parse("2026-11-13T06:00:00Z")); // 09:00 МСК

        assertThat(window.isEntryAllowed(withThreshold(540))).isTrue();
        assertThat(window.isEntryAllowed(withThreshold(600))).isFalse();
    }

    @Test
    void configMappingUsesDatabaseKey() {
        RiskConfig config = RiskConfig.fromMap(java.util.Map.of("entry_after_msk_minutes", "720"));

        assertThat(config.entryAfterMskMinutes()).isEqualTo(720);
    }

    @Test
    void configMappingDefaultsWhenKeyMissing() {
        RiskConfig config = RiskConfig.fromMap(java.util.Map.of());

        assertThat(config.entryAfterMskMinutes()).isEqualTo(660);
    }
}
