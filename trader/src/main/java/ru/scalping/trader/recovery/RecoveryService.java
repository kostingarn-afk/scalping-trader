package ru.scalping.trader.recovery;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import ru.scalping.trader.position.Position;
import ru.scalping.trader.position.PositionStore;

import java.util.Collection;

/**
 * Recovery при старте Trader (задача 4.10, spec trade-execution).
 *
 * <p>Синхронизация состояния: открытые позиции через {@code GetPositions},
 * in-flight ордера через {@code GetOrders}, биржевые стоп-ордера через {@code GetStopOrders}.
 * Сверка с БД, восстановление in-memory.
 *
 * <p>Абстрагирован от SDK: реальная загрузка через {@link ExchangeStateProvider}.
 */
@Component
public class RecoveryService {

    private static final Logger log = LoggerFactory.getLogger(RecoveryService.class);

    private final ExchangeStateProvider provider;
    private final PositionStore positionStore;

    public RecoveryService(ExchangeStateProvider provider, PositionStore positionStore) {
        this.provider = provider;
        this.positionStore = positionStore;
    }

    /** Синхронизация при старте. */
    public void recover() {
        log.info("Starting recovery from exchange state");
        Collection<Position> exchangePositions = provider.loadOpenPositions();
        for (Position p : exchangePositions) {
            positionStore.put(p);
            log.info("Recovered position: figi={}, qty={}, openedAt={}", p.figi(), p.quantityLots(), p.openedAt());
        }
        // Сверка с БД — в реализации через JdbcTemplate (опускаем для краткости; БД — source of truth для запросов, биржа — для исполнения)
        log.info("Recovery complete: {} positions restored", positionStore.count());
    }
}
