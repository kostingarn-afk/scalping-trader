package ru.scalping.collector.history;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.nio.file.Path;
import java.time.Instant;
import java.util.List;

/**
 * Параметры режима выгрузки истории ({@code collector.history.*}, профиль {@code history-dump}).
 *
 * @param instruments список инструментов в формате {@code TICKER} или {@code TICKER@CLASS}
 * @param interval    таймфрейм: {@code 1min}, {@code 5min}, {@code 15min}, {@code 1h}
 * @param from        начало периода, ISO-8601 UTC
 * @param to          конец периода, ISO-8601 UTC
 * @param outDir             каталог выгрузки (CSV + sidecar {@code .meta.json})
 * @param requestsPerMinute  предел запросов к API в минуту ({@code GetCandles} — 600,
 *                           песочница — 200); по умолчанию 120 — с запасом под динамический лимит
 */
@ConfigurationProperties(prefix = "collector.history")
public record HistoryDumpProperties(
        List<String> instruments,
        String interval,
        Instant from,
        Instant to,
        Path outDir,
        Integer requestsPerMinute
) {
    /** Значение по умолчанию: ниже документированного лимита песочницы (200/мин). */
    static final int DEFAULT_REQUESTS_PER_MINUTE = 120;

    public HistoryDumpProperties {
        if (instruments == null || instruments.isEmpty()) {
            throw new IllegalArgumentException("collector.history.instruments must not be empty");
        }
        if (requestsPerMinute == null) {
            requestsPerMinute = DEFAULT_REQUESTS_PER_MINUTE;
        }
        if (requestsPerMinute < 1) {
            throw new IllegalArgumentException(
                    "collector.history.requests-per-minute must be positive: " + requestsPerMinute);
        }
        if (interval == null || interval.isBlank()) {
            interval = "1min";
        }
        if (from == null || to == null) {
            throw new IllegalArgumentException(
                    "collector.history.from and collector.history.to must be set (ISO-8601 UTC)");
        }
        if (!from.isBefore(to)) {
            throw new IllegalArgumentException("collector.history.from must be before to");
        }
        if (outDir == null) {
            outDir = Path.of("data", "history");
        }
    }
}
