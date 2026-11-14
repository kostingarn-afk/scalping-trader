package ru.scalping.trader.recovery;

import ru.scalping.trader.position.Position;

import java.util.Collection;

/**
 * Провайдер состояния биржи для recovery (задача 4.10).
 * Реализация привязана к T-Invest SDK.
 */
public interface ExchangeStateProvider {
    /** Открытые позиции через {@code GetPositions}. */
    Collection<Position> loadOpenPositions();

    /**
     * Текущий {@code SecurityTradingStatus} инструмента (2 — {@code NORMAL_TRADING}).
     *
     * @return значение статуса либо {@code -1}, если статус недоступен
     */
    int tradingStatus(String figi);
}
