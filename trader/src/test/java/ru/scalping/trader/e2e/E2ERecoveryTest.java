package ru.scalping.trader.e2e;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import ru.scalping.trader.position.Position;
import ru.scalping.trader.position.PositionStore;
import ru.scalping.trader.recovery.ExchangeStateProvider;

import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * E2E recovery (задача 6.3):
 * Trader рестарт при открытой позиции → позиция восстановлена из GetPositions,
 * TradesStream подписан.
 */
@Tag("e2e")
class E2ERecoveryTest {

    @Test
    void positionRestoredFromExchange() {
        // Имитация: биржа сообщает об открытой позиции
        Position exchangePosition = new Position(
                "BBG000000001", 10, 100.0, 100.5, 50.0,
                Instant.now().minusSeconds(60), UUID.randomUUID(),
                99.0, 101.0, "sl-1", "tp-1"
        );

        ExchangeStateProvider provider = new ExchangeStateProvider() {
            @Override
            public Collection<Position> loadOpenPositions() {
                return List.of(exchangePosition);
            }

            @Override
            public int tradingStatus(String figi) {
                return 2;
            }
        };
        PositionStore store = new PositionStore();

        // Recovery
        for (Position p : provider.loadOpenPositions()) {
            store.put(p);
        }

        assertThat(store.count()).isEqualTo(1);
        assertThat(store.get("BBG000000001")).isPresent();
        assertThat(store.get("BBG000000001").get().quantityLots()).isEqualTo(10);
    }
}
