package ru.scalping.collector.stream;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import ru.scalping.collector.config.CollectorProperties;
import ru.scalping.collector.instrument.InstrumentCache;
import ru.scalping.collector.metrics.CollectorMetrics;
import ru.scalping.collector.publish.MarketDataPublisher;
import ru.scalping.collector.publish.TradingStatusFilter;
import ru.scalping.proto.PayloadType;
import ru.scalping.proto.RawMarketData;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * Тест подписки с reconnect (задача 2.6):
 * mock-разрыв соединения проверяет exponential backoff и resubscribe.
 */
@ExtendWith(MockitoExtension.class)
class StreamSubscriptionManagerTest {

    @Mock MarketDataPublisher publisher;
    @Mock TradingStatusFilter statusFilter;
    @Mock InstrumentCache instrumentCache;
    @Mock CollectorMetrics metrics;

    private CollectorProperties properties;

    @BeforeEach
    void setUp() {
        // Маленькие задержки для теста
        properties = new CollectorProperties(
                List.of("BBG000000001"), "1min", 10, 10L, 50L, "0 0 3 * * *"
        );
    }

    @Test
    void reconnectsOnStreamFailure() throws Exception {
        AtomicInteger subscribeCalls = new AtomicInteger();
        MarketStreamService streamService = new MarketStreamService() {
            @Override
            public void subscribe(List<String> figis, MarketDataListener listener) {
                int n = subscribeCalls.incrementAndGet();
                if (n < 3) {
                    throw new RuntimeException("stream broken");
                }
                // 3-я попытка — успех, имитируем одно событие и выход
                listener.onNext(RawMarketData.newBuilder()
                        .setFigi("BBG000000001")
                        .setPayloadType(PayloadType.INFO)
                        .build());
            }
            @Override
            public void close() {}
        };

        when(statusFilter.shouldPublish(any(), anyInt())).thenReturn(true);
        when(instrumentCache.tradingStatus(any())).thenReturn(2);

        StreamSubscriptionManager manager = new StreamSubscriptionManager(
                streamService, publisher, statusFilter, instrumentCache, properties, metrics);

        // Запускаем в потоке, чтобы не блокировать тест
        Thread t = new Thread(manager::start);
        t.start();
        t.join(2000);

        assertThat(subscribeCalls.get()).isGreaterThanOrEqualTo(3);
        verify(metrics, atLeast(2)).streamReconnect();
        verify(publisher, atLeastOnce()).publish(any());
    }
}
