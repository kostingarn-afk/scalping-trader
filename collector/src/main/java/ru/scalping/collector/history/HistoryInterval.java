package ru.scalping.collector.history;

import java.time.Duration;
import java.util.Arrays;
import java.util.Locale;
import java.util.Optional;

/**
 * Таймфрейм выгрузки истории и лимит одного запроса {@code MarketDataService.GetCandles}.
 *
 * <p>Один запрос возвращает не больше фиксированного интервала-лимита (для 1 мин — сутки),
 * поэтому период режется на страницы ({@link HistoryRange}).
 *
 * @param code        код в конфигурации Collector'а: {@code 1min}, {@code 5min}, {@code 15min}, {@code 1h}
 * @param minutes     длительность свечи в минутах
 * @param maxPageSpan максимальный период одного запроса
 */
public enum HistoryInterval {
    ONE_MIN("1min", 1, Duration.ofDays(1)),
    FIVE_MIN("5min", 5, Duration.ofDays(7)),
    FIFTEEN_MIN("15min", 15, Duration.ofDays(30)),
    HOUR("1h", 60, Duration.ofDays(30));

    private final String code;
    private final int minutes;
    private final Duration maxPageSpan;

    HistoryInterval(String code, int minutes, Duration maxPageSpan) {
        this.code = code;
        this.minutes = minutes;
        this.maxPageSpan = maxPageSpan;
    }

    public String code() {
        return code;
    }

    public int minutes() {
        return minutes;
    }

    public Duration maxPageSpan() {
        return maxPageSpan;
    }

    /** Длительность одной свечи — прибавляется ко времени начала, чтобы получить время закрытия. */
    public Duration barDuration() {
        return Duration.ofMinutes(minutes);
    }

    /**
     * Разобрать код таймфрейма из конфигурации.
     *
     * @throws IllegalArgumentException если код неизвестен
     */
    public static HistoryInterval fromCode(String code) {
        String normalized = code == null ? "" : code.trim().toLowerCase(Locale.ROOT);
        Optional<HistoryInterval> match = Arrays.stream(values())
                .filter(interval -> interval.code.equals(normalized))
                .findFirst();
        return match.orElseThrow(() -> new IllegalArgumentException(
                "Unknown history interval '" + code + "', expected one of: "
                        + String.join(", ", Arrays.stream(values()).map(HistoryInterval::code).toList())));
    }
}
