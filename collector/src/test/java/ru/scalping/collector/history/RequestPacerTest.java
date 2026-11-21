package ru.scalping.collector.history;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RequestPacerTest {

    /** Часы, которые двигаются только вручную — тест не зависит от реального времени. */
    private final AtomicLong clock = new AtomicLong(0);
    private final List<Long> sleeps = new ArrayList<>();

    private RequestPacer pacer(int requestsPerMinute) {
        return RequestPacer.perMinute(requestsPerMinute, clock::get, nanos -> {
            sleeps.add(nanos);
            clock.addAndGet(nanos);
        });
    }

    @Test
    void await_firstCall_doesNotSleep() {
        RequestPacer pacer = pacer(120);

        pacer.await();

        assertThat(sleeps).isEmpty();
    }

    @Test
    void await_burstCalls_sleepsBetweenRequests() {
        RequestPacer pacer = pacer(120);

        for (int i = 0; i < 4; i++) {
            pacer.await();
        }

        assertThat(sleeps).containsExactly(500_000_000L, 500_000_000L, 500_000_000L);
    }

    @Test
    void await_slowRequest_doesNotSleepAndDoesNotBankCredit() {
        RequestPacer pacer = pacer(120);

        pacer.await();
        clock.addAndGet(2_000_000_000L);
        pacer.await();
        assertThat(sleeps).isEmpty();

        clock.addAndGet(10_000_000L);
        pacer.await();

        assertThat(sleeps).containsExactly(490_000_000L);
    }

    @Test
    void unlimited_neverSleeps() {
        RequestPacer pacer = RequestPacer.unlimited();

        for (int i = 0; i < 5; i++) {
            pacer.await();
        }

        assertThat(sleeps).isEmpty();
    }

    @Test
    void perMinute_nonPositiveLimit_throws() {
        assertThatThrownBy(() -> RequestPacer.perMinute(0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("requestsPerMinute");
    }
}
