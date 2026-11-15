package ru.scalping.collector.history;

import java.time.Instant;
import java.util.List;

/**
 * Источник исторических свечей.
 *
 * <p>Абстракция отделяет выгрузку от T-Invest SDK: бизнес-логика (пагинация, дедупликация,
 * запись файлов) не зависит от gRPC-типов.
 */
public interface HistoryCandleSource {

    /**
     * Запросить свечи за интервал.
     *
     * @param figi     идентификатор инструмента
     * @param from     начало периода (включительно)
     * @param to       конец периода (исключительно)
     * @param interval таймфрейм; период должен укладываться в {@link HistoryInterval#maxPageSpan()}
     * @return завершённые свечи, отсортированные по времени закрытия
     */
    List<HistoryCandle> fetch(String figi, Instant from, Instant to, HistoryInterval interval);
}
