package ru.scalping.collector.history;

import java.nio.file.Path;

/**
 * Итог выгрузки по одному инструменту.
 *
 * @param ticker    тикер инструмента
 * @param classCode класс инструмента
 * @param bars      число записанных свечей
 * @param path      путь к CSV или {@code null}, если данных не нашлось
 */
public record DumpReport(String ticker, String classCode, int bars, Path path) {

    public boolean isEmpty() {
        return bars == 0;
    }
}
