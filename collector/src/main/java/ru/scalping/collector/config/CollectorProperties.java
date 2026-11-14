package ru.scalping.collector.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

/**
 * Конфигурация Collector.
 *
 * @param instruments   список инструментов для подписки: {@code TICKER} или {@code TICKER@CLASS}
 *                      (например, {@code SBER}, {@code SiZ6@SPBFUT})
 * @param candleInterval интервал свечей (1min)
 * @param orderBookDepth глубина стакана
 * @param reconnectInitialMs начальная задержка переподключения (1000)
 * @param reconnectMaxMs    потолок задержки переподключения (30000)
 * @param instrumentRefreshCron cron обновления метаданных инструментов
 */
@ConfigurationProperties(prefix = "collector")
public record CollectorProperties(
        List<String> instruments,
        String candleInterval,
        int orderBookDepth,
        long reconnectInitialMs,
        long reconnectMaxMs,
        String instrumentRefreshCron
) {
    public CollectorProperties {
        if (instruments == null || instruments.isEmpty()) {
            throw new IllegalArgumentException("collector.instruments must not be empty");
        }
        if (candleInterval == null) candleInterval = "1min";
        if (orderBookDepth <= 0) orderBookDepth = 10;
        if (reconnectInitialMs <= 0) reconnectInitialMs = 1000L;
        if (reconnectMaxMs <= 0) reconnectMaxMs = 30_000L;
        if (instrumentRefreshCron == null) instrumentRefreshCron = "0 0 3 * * *";
    }
}
