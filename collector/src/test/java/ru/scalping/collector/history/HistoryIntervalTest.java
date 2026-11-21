package ru.scalping.collector.history;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class HistoryIntervalTest {

    @ParameterizedTest
    @CsvSource({"1min,1", "5min,5", "15min,15", "1h,60"})
    void fromCode_knownCode_returnsInterval(String code, int expectedMinutes) {
        HistoryInterval interval = HistoryInterval.fromCode(code);

        assertThat(interval.minutes()).isEqualTo(expectedMinutes);
        assertThat(interval.barDuration()).isEqualTo(Duration.ofMinutes(expectedMinutes));
    }

    @Test
    void fromCode_uppercaseCode_isAccepted() {
        assertThat(HistoryInterval.fromCode("1H")).isEqualTo(HistoryInterval.HOUR);
    }

    @Test
    void fromCode_unknownCode_throwsWithAvailableCodes() {
        assertThatThrownBy(() -> HistoryInterval.fromCode("7min"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("1min")
                .hasMessageContaining("1h");
    }

    @Test
    void maxPageSpan_oneMinute_isOneDay() {
        assertThat(HistoryInterval.ONE_MIN.maxPageSpan()).isEqualTo(Duration.ofDays(1));
    }

    /**
     * Окно одного запроса не должно превышать документированный максимум T-Invest:
     * 1 мин — 1 день, 5 мин — неделя, 15 мин — 3 недели, 1 час — 3 месяца.
     *
     * <p>Ровно 3 недели у 15-минутки — исправление дефекта: стояло 30 дней, и такой запрос
     * API отклоняет.
     */
    @Test
    void maxPageSpan_doesNotExceedApiLimits() {
        assertThat(HistoryInterval.ONE_MIN.maxPageSpan()).isEqualTo(Duration.ofDays(1));
        assertThat(HistoryInterval.FIVE_MIN.maxPageSpan()).isEqualTo(Duration.ofDays(7));
        assertThat(HistoryInterval.FIFTEEN_MIN.maxPageSpan()).isEqualTo(Duration.ofDays(21));
        assertThat(HistoryInterval.HOUR.maxPageSpan()).isLessThanOrEqualTo(Duration.ofDays(90));
    }
}
