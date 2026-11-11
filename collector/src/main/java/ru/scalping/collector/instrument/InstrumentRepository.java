package ru.scalping.collector.instrument;

/**
 * Репозиторий для upsert метаданных в таблицу {@code instruments}.
 */
public interface InstrumentRepository {
    void upsert(InstrumentMeta meta);
}
