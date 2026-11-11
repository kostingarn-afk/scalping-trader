package ru.scalping.trader.position;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class PositionStoreTest {

    private Position pos(String figi, Instant opened) {
        return new Position(figi, 1, 100, 100, 0, opened, UUID.randomUUID(), 99, 101, "sl", "tp");
    }

    @Test
    void findStaleReturnsOldPositions() {
        Clock fixed = Clock.fixed(Instant.parse("2026-11-10T12:10:01Z"), ZoneOffset.UTC);
        PositionStore store = new PositionStore(fixed);
        store.put(pos("SBER", Instant.parse("2026-11-10T12:00:00Z"))); // 10 мин 1 сек — stale
        store.put(pos("BR", Instant.parse("2026-11-10T12:05:00Z")));   // 5 мин — не stale

        var stale = store.findStale(Duration.ofMinutes(10));
        assertThat(stale).hasSize(1);
        assertThat(stale.iterator().next().figi()).isEqualTo("SBER");
    }

    @Test
    void removeDeletesPosition() {
        PositionStore store = new PositionStore();
        store.put(pos("SBER", Instant.now()));
        assertThat(store.count()).isEqualTo(1);
        store.remove("SBER");
        assertThat(store.count()).isZero();
    }
}
