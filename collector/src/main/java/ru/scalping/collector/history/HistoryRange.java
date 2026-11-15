package ru.scalping.collector.history;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

/**
 * Нарезка периода выгрузки на страницы, укладывающиеся в лимит одного запроса.
 *
 * <p>Страницы идут подряд без пропусков и пересечений: конец страницы {@code to} исключителен,
 * следующий запрос начинается ровно с него.
 */
public final class HistoryRange {

    /** Страница запроса: {@code from} включительно, {@code to} исключительно. */
    public record Page(Instant from, Instant to) {}

    private HistoryRange() {}

    /**
     * Разбить период на страницы не длиннее {@code maxSpan}.
     *
     * @throws IllegalArgumentException если {@code from >= to} или {@code maxSpan <= 0}
     */
    public static List<Page> split(Instant from, Instant to, Duration maxSpan) {
        if (from == null || to == null) {
            throw new IllegalArgumentException("History range bounds must not be null");
        }
        if (!from.isBefore(to)) {
            throw new IllegalArgumentException("History range must satisfy from < to");
        }
        if (maxSpan == null || maxSpan.isZero() || maxSpan.isNegative()) {
            throw new IllegalArgumentException("Page span must be positive");
        }
        List<Page> pages = new ArrayList<>();
        Instant cursor = from;
        while (cursor.isBefore(to)) {
            Instant pageEnd = cursor.plus(maxSpan);
            if (pageEnd.isAfter(to)) {
                pageEnd = to;
            }
            pages.add(new Page(cursor, pageEnd));
            cursor = pageEnd;
        }
        return pages;
    }
}
