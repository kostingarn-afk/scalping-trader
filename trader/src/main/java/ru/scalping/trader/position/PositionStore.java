package ru.scalping.trader.position;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Collection;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * In-memory хранилище открытых позиций (задачи 4.7, 4.8, 4.10).
 *
 * <p>Синхронизируется с биржей (GetPositions) при старте и TradesStream в runtime.
 */
@Component
public class PositionStore {

    private static final Logger log = LoggerFactory.getLogger(PositionStore.class);

    private final Clock clock;
    private final Map<String, Position> positions = new ConcurrentHashMap<>();

    public PositionStore(Clock clock) {
        this.clock = clock;
    }

    public PositionStore() {
        this(Clock.systemUTC());
    }

    public void put(Position position) {
        positions.put(position.figi(), position);
        log.info("Position updated: figi={}, qty={}, pnl={}", position.figi(), position.quantityLots(), position.unrealizedPnl());
    }

    public Optional<Position> get(String figi) {
        return Optional.ofNullable(positions.get(figi));
    }

    public void remove(String figi) {
        positions.remove(figi);
        log.info("Position closed: figi={}", figi);
    }

    public Collection<Position> all() {
        return positions.values();
    }

    public int count() {
        return positions.size();
    }

    /**
     * Поиск позиций, удерживаемых дольше maxHoldingTime (задача 4.8).
     */
    public Collection<Position> findStale(Duration maxHoldingTime) {
        Instant now = clock.instant();
        return positions.values().stream()
                .filter(p -> Duration.between(p.openedAt(), now).compareTo(maxHoldingTime) > 0)
                .toList();
    }
}
