package ru.scalping.collector.instrument;

/**
 * Метаданные инструмента (подмножество полей таблицы {@code instruments}).
 */
public record InstrumentMeta(
        String figi,
        String ticker,
        String classCode,
        String name,
        int lot,
        double minPriceIncrement,
        String currency,
        boolean apiTradeAvailable,
        int tradingStatus,
        int tier
) {}
