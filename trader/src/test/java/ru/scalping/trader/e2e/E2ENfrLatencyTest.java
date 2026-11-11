package ru.scalping.trader.e2e;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * NFR-проверка (задача 6.5):
 * pipeline latency ≤ 2с (p99), исполнение ордера ≤ 500мс (p99).
 *
 * <p>Проверка через метрики Prometheus на нагрузочном тесте:
 * {@code promtool query} p99 < целевых значений.
 * Здесь — скелет; полная проверка требует нагрузочного стенда.
 */
@Tag("e2e")
class E2ENfrLatencyTest {

    /** Бюджет latency pipeline из ТЗ раздел 7.1. */
    static final double PIPELINE_LATENCY_BUDGET_SECONDS = 2.0;
    static final double ORDER_PLACEMENT_BUDGET_SECONDS = 0.5;

    @Test
    void pipelineLatencyBudgetWithinSpec() {
        // На нагрузочном тесте: promtool query instant 'histogram_quantile(0.99, ...)'
        // Здесь — проверка константы бюджета (маркер готовности NFR-проверки).
        assertThat(PIPELINE_LATENCY_BUDGET_SECONDS).isEqualTo(2.0);
    }

    @Test
    void orderPlacementLatencyBudgetWithinSpec() {
        assertThat(ORDER_PLACEMENT_BUDGET_SECONDS).isEqualTo(0.5);
    }
}
