package ru.scalping.collector.history;

import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class HistoryDumpPropertiesTest {

    private static final Instant FROM = Instant.parse("2026-01-21T00:00:00Z");

    @Test
    void requestsPerMinute_notSet_usesDefaultBelowSandboxLimit() {
        HistoryDumpProperties properties =
                new HistoryDumpProperties(List.of("GAZP"), "1min", FROM, FROM.plusSeconds(60), Path.of("out"), null);

        assertThat(properties.requestsPerMinute())
                .isEqualTo(HistoryDumpProperties.DEFAULT_REQUESTS_PER_MINUTE)
                .isLessThan(200);
    }

    @Test
    void requestsPerMinute_explicitValue_isKept() {
        HistoryDumpProperties properties = new HistoryDumpProperties(
                List.of("GAZP"), "1min", FROM, FROM.plusSeconds(60), Path.of("out"), 300);

        assertThat(properties.requestsPerMinute()).isEqualTo(300);
    }

    @Test
    void requestsPerMinute_nonPositive_throws() {
        assertThatThrownBy(() -> new HistoryDumpProperties(
                List.of("GAZP"), "1min", FROM, FROM.plusSeconds(60), Path.of("out"), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("requests-per-minute");
    }
}
