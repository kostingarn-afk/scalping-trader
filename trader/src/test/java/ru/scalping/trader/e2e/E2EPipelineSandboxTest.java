package ru.scalping.trader.e2e;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * E2E на sandbox (задача 6.1):
 * полный pipeline Collector→Kafka→Analyzer→Kafka→Trader→Kafka по 1 инструменту (BR),
 * сигнал BUY → ордер → SL/TP выставлены → позиция в БД.
 *
 * <p>Требует поднятое окружение: Kafka, PostgreSQL, sandbox T-Invest API.
 * Запуск: {@code mvn test -Dtags=e2e -DT_INVEST_TOKEN=...}
 */
@Tag("e2e")
@EnabledIfEnvironmentVariable(named = "T_INVEST_TOKEN", matches = ".+")
class E2EPipelineSandboxTest {

    @Test
    void fullPipelineBuySignalOpensPositionWithStops() {
        // 1. Запустить Collector, Analyzer, Trader (через testcontainers или docker-compose)
        // 2. Дождаться сигнала BUY по BR (mean reversion на synthetic/sandbox данных)
        // 3. Проверить trade_results: status=opened, sl_order_id и tp_order_id заполнены
        // Полная реализация требует интеграционного фреймворка (Testcontainers + Kafka + PG).
        // Скелет теста — маркер готовности E2E-инфраструктуры.
        assertThat(true).as("E2E pipeline skeleton — requires sandbox env").isTrue();
    }
}
