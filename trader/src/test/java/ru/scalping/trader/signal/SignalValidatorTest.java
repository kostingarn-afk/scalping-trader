package ru.scalping.trader.signal;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;

class SignalValidatorTest {

    @Test
    void expiredSignalRejected() {
        Clock fixed = Clock.fixed(Instant.parse("2026-11-10T12:00:00Z"), ZoneOffset.UTC);
        SignalValidator v = new SignalValidator(fixed);
        Instant expired = Instant.parse("2026-11-10T11:59:50Z");
        assertThat(v.isExpired(expired)).isTrue();
    }

    @Test
    void freshSignalAccepted() {
        Clock fixed = Clock.fixed(Instant.parse("2026-11-10T12:00:00Z"), ZoneOffset.UTC);
        SignalValidator v = new SignalValidator(fixed);
        Instant fresh = Instant.parse("2026-11-10T12:00:30Z");
        assertThat(v.isExpired(fresh)).isFalse();
    }

    @Test
    void normalTradingTradable() {
        SignalValidator v = new SignalValidator();
        assertThat(v.isTradable(2)).isTrue();
    }

    @Test
    void breakInTradingNotTradable() {
        SignalValidator v = new SignalValidator();
        assertThat(v.isTradable(7)).isFalse();
    }
}
