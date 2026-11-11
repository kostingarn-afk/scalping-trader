package ru.scalping.collector.instrument;

/**
 * Загрузчик метаданных инструмента через T-Invest {@code InstrumentsService}.
 * Реализация привязана к SDK и подставляется через конфигурацию профиля.
 */
public interface InstrumentLoader {
    InstrumentMeta loadByTicker(String ticker);
}
