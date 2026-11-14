package ru.scalping.trader.execution;

/**
 * Направление входа в позицию.
 *
 * <p>Отдельный тип, чтобы бизнес-логика не зависела от {@code OrderDirection} T-Invest SDK
 * и от порядка аргументов {@code postOrderSync}.
 */
public enum OrderSide {
    BUY,
    SELL
}
