package ru.scalping.collector.metrics;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

/**
 * Метрики Collector (задача 2.7):
 * messages_published_total, stream_reconnects_total, api_errors_total.
 */
@Component
public class CollectorMetrics {

    private final Counter messagesPublished;
    private final Counter streamReconnects;
    private final Counter apiErrors;

    public CollectorMetrics(MeterRegistry registry) {
        this.messagesPublished = Counter.builder("messages_published_total")
                .description("Total messages published to Kafka raw-market-data")
                .register(registry);
        this.streamReconnects = Counter.builder("stream_reconnects_total")
                .description("Total stream reconnect attempts")
                .register(registry);
        this.apiErrors = Counter.builder("api_errors_total")
                .description("Total T-Invest API errors")
                .register(registry);
    }

    public void messagePublished() { messagesPublished.increment(); }
    public void streamReconnect()  { streamReconnects.increment(); }
    public void apiError()         { apiErrors.increment(); }
}
