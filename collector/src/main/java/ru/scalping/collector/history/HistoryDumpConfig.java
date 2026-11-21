package ru.scalping.collector.history;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * Конфигурация режима выгрузки истории.
 *
 * <p>Активна только в профиле {@code history-dump}: в обычном режиме Collector параметры
 * {@code collector.history.*} не требуются.
 */
@Configuration
@Profile("history-dump")
@EnableConfigurationProperties(HistoryDumpProperties.class)
public class HistoryDumpConfig {

    /**
     * Ограничитель частоты запросов к API.
     *
     * <p>Без него выгрузка минутных свечей за месяцы упирается в лимит брокера
     * ({@code RESOURCE_EXHAUSTED}) уже на второй бумаге.
     */
    @Bean
    RequestPacer historyRequestPacer(HistoryDumpProperties properties) {
        return RequestPacer.perMinute(properties.requestsPerMinute());
    }
}
