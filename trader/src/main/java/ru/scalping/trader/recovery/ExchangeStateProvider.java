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
}
