package ru.scalping.collector.stream;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import ru.scalping.collector.instrument.InstrumentCache;

/**
 * Старт Collector после поднятия Spring-контекста:
 * 1) загрузка метаданных инструментов (задача 2.3);
 * 2) открытие gRPC-стрима (задача 2.2).
 */
@Component
public class CollectorStartup {

    private static final Logger log = LoggerFactory.getLogger(CollectorStartup.class);

    private final InstrumentCache instrumentCache;
    private final StreamSubscriptionManager streamManager;

    public CollectorStartup(InstrumentCache instrumentCache,
                            StreamSubscriptionManager streamManager) {
        this.instrumentCache = instrumentCache;
        this.streamManager = streamManager;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void onReady() {
        log.info("Collector ready — loading instruments and opening stream");
        instrumentCache.init();
        streamManager.start();
    }
}
