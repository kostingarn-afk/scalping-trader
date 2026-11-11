package ru.scalping.collector.stream;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import ru.scalping.collector.config.CollectorProperties;
import ru.scalping.collector.instrument.InstrumentCache;
import ru.scalping.collector.metrics.CollectorMetrics;
import ru.scalping.collector.publish.MarketDataPublisher;
import ru.scalping.collector.publish.TradingStatusFilter;
import ru.scalping.proto.RawMarketData;

import java.util.List;

/**
 * Оркестрация подписки на gRPC-стрим (задачи 2.2, 2.4, 2.5, 2.6).
 *
 * <p>При старте открывает стрим, выставляет 4 типа подписок на каждый инструмент,
 * направляет события в publisher с учётом фильтрации по торговому статусу.
 * При разрыве — переподключение с exponential backoff (1с→30с потолок).
 */
@Component
public class StreamSubscriptionManager {

    private static final Logger log = LoggerFactory.getLogger(StreamSubscriptionManager.class);

    private final MarketStreamService streamService;
    private final MarketDataPublisher publisher;
    private final TradingStatusFilter statusFilter;
    private final InstrumentCache instrumentCache;
    private final CollectorProperties properties;
    private final CollectorMetrics metrics;
    private final List<String> figis;

    public StreamSubscriptionManager(MarketStreamService streamService,
                                     MarketDataPublisher publisher,
                                     TradingStatusFilter statusFilter,
                                     InstrumentCache instrumentCache,
                                     CollectorProperties properties,
                                     CollectorMetrics metrics) {
        this.streamService = streamService;
        this.publisher = publisher;
        this.statusFilter = statusFilter;
        this.instrumentCache = instrumentCache;
        this.properties = properties;
        this.metrics = metrics;
        this.figis = List.copyOf(properties.instruments());
    }

    public void start() {
        subscribeWithReconnect();
    }

    private void subscribeWithReconnect() {
        long delay = properties.reconnectInitialMs();
        while (!Thread.currentThread().isInterrupted()) {
            try {
                log.info("Opening market data stream for {} instruments", figis.size());
                streamService.subscribe(figis, this::handle);
                // subscribe блокирует до разрыва; если вышел без исключения — выходим
                return;
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                return;
            } catch (Exception e) {
                metrics.streamReconnect();
                metrics.apiError();
                log.error("Stream error, reconnecting in {} ms: {}", delay, e.getMessage());
                try {
                    Thread.sleep(delay);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return;
                }
                delay = Math.min(delay * 2, properties.reconnectMaxMs());
            }
        }
    }

    void handle(RawMarketData data) {
        int status = instrumentCache.tradingStatus(data.getFigi());
        if (statusFilter.shouldPublish(data, status)) {
            publisher.publish(data);
        }
    }

    public void stop() {
        streamService.close();
    }
}
