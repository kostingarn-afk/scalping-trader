package ru.scalping.collector.history;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class HistoryRangeTest {

    private static final Instant FROM = Instant.parse("2026-11-01T00:00:00Z");

    @Test
    void split_evenPeriod_returnsExactPages() {
        List<HistoryRange.Page> pages = HistoryRange.split(FROM, FROM.plus(Duration.ofHours(6)),
                Duration.ofHours(2));

        assertThat(pages).hasSize(3);
        assertThat(pages.get(0)).isEqualTo(new HistoryRange.Page(FROM, FROM.plus(Duration.ofHours(2))));
        assertThat(pages.get(2).to()).isEqualTo(FROM.plus(Duration.ofHours(6)));
    }

    @Test
    void split_remainder_lastPageIsShorter() {
        List<HistoryRange.Page> pages = HistoryRange.split(FROM, FROM.plus(Duration.ofHours(5)),
                Duration.ofHours(2));

        assertThat(pages).hasSize(3);
        assertThat(pages.get(2).to()).isEqualTo(FROM.plus(Duration.ofHours(5)));
        assertThat(Duration.between(pages.get(2).from(), pages.get(2).to()))
                .isEqualTo(Duration.ofHours(1));
    }

    @Test
    void split_contiguousPages_noGapsOrOverlaps() {
        List<HistoryRange.Page> pages = HistoryRange.split(FROM, FROM.plus(Duration.ofDays(1)),
                HistoryInterval.ONE_MIN.maxPageSpan().dividedBy(5));

        assertThat(pages).hasSize(5);
        for (int index = 1; index < pages.size(); index++) {
            assertThat(pages.get(index).from()).isEqualTo(pages.get(index - 1).to());
        }
        assertThat(pages.get(0).from()).isEqualTo(FROM);
        assertThat(pages.get(pages.size() - 1).to()).isEqualTo(FROM.plus(Duration.ofDays(1)));
    }

    @Test
    void split_periodShorterThanSpan_returnsSinglePage() {
        List<HistoryRange.Page> pages = HistoryRange.split(FROM, FROM.plusSeconds(60),
                Duration.ofDays(1));

        assertThat(pages).containsExactly(new HistoryRange.Page(FROM, FROM.plusSeconds(60)));
    }

    @Test
    void split_invalidRange_throws() {
        assertThatThrownBy(() -> HistoryRange.split(FROM, FROM, Duration.ofDays(1)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("from < to");
    }

    @Test
    void split_nonPositiveSpan_throws() {
        assertThatThrownBy(() -> HistoryRange.split(FROM, FROM.plusSeconds(60), Duration.ZERO))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Page span must be positive");
    }
}
