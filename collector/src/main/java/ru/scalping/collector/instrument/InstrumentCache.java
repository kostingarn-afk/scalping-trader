package ru.scalping.collector.instrument;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import ru.scalping.collector.config.CollectorProperties;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Кэш метаданных инструментов (задача 2.3).
 *
 * <p>Метаданные (lot, min_price_increment, trading_status, api_trade_available_flag)
 * загружаются через {@code InstrumentsService.GetInstrumentBy} при старте и
 * обновляются по расписанию (daily refresh). Запись в таблицу {@code instruments}
 * выполняется через {@link InstrumentRepository}.
 *
 * <p>Абстрагирован от конкретного SDK: реальная загрузка делегирована {@link InstrumentLoader}.
 */
@Service
public class InstrumentCache {

    private static final Logger log = LoggerFactory.getLogger(InstrumentCache.class);

    private final CollectorProperties properties;
    private final InstrumentLoader loader;
    private final InstrumentRepository repository;
    private final Map<String, InstrumentMeta> cache = new ConcurrentHashMap<>();

    public InstrumentCache(CollectorProperties properties,
                           InstrumentLoader loader,
                           InstrumentRepository repository) {
        this.properties = properties;
        this.loader = loader;
        this.repository = repository;
    }

    /** Загрузка при старте. */
    public void init() {
        refresh();
    }

    /** Daily refresh по cron из конфигурации. */
    @Scheduled(cron = "${collector.instrument-refresh-cron:0 0 3 * * *}")
    public void refresh() {
        for (String ticker : properties.instruments()) {
            try {
                InstrumentMeta meta = loader.loadByTicker(ticker);
                cache.put(meta.figi(), meta);
                repository.upsert(meta);
                log.info("Loaded instrument meta: {}", meta);
            } catch (Exception e) {
                log.error("Failed to load instrument {}: {}", ticker, e.getMessage());
            }
        }
    }

    public InstrumentMeta get(String figi) {
        return cache.get(figi);
    }

    public int tradingStatus(String figi) {
        InstrumentMeta m = cache.get(figi);
        return m == null ? -1 : m.tradingStatus();
    }
}
