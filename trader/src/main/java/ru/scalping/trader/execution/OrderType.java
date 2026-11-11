package ru.scalping.trader.execution;

/**
 * Тип ордера (задача 4.5, D4).
 * MARKET запрещён на вход — только для emergency-close (PnL-CB).
 */
public enum OrderType {
    LIMIT,
    BESTPRICE,
    STOP_LOSS,
    TAKE_PROFIT,
    MARKET  // только для emergency-close
}
