package ru.scalping.collector.history;

import java.time.Instant;

/**
 * Завершённая историческая свеча в терминах контракта выгрузки.
 *
 * <p>{@code end} — время <b>закрытия</b> свечи (UTC): именно оно попадает в колонку {@code ts}
 * CSV-выгрузки и ожидается Analyzer'ом ({@code analyzer/backtest/loader.py}).
 */
public record HistoryCandle(
        Instant end,
        double open,
        double high,
        double low,
        double close,
        long volume
) {}
