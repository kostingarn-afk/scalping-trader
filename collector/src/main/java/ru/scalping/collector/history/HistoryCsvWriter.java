package ru.scalping.collector.history;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;
import ru.scalping.collector.instrument.InstrumentMeta;

import java.io.BufferedWriter;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Запись выгрузки истории по контракту Analyzer'а ({@code analyzer/backtest/loader.py}).
 *
 * <p>Файлы: {@code {ticker}_{minutes}min.csv} с заголовком
 * {@code ts,open,high,low,close,volume} и рядом {@code .meta.json} с обязательными полями
 * {@code ticker}, {@code class_code}, {@code lot_size}, {@code interval_minutes}.
 * Существующие файлы перезаписываются целиком — выгрузка идемпотентна.
 */
@Component
public class HistoryCsvWriter {

    static final String HEADER = "ts,open,high,low,close,volume";

    private final ObjectMapper objectMapper;

    public HistoryCsvWriter(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /**
     * Записать свечи в каталог выгрузки.
     *
     * @return путь к CSV-файлу
     * @throws IOException при ошибке записи
     */
    public Path write(Path outDir, InstrumentMeta instrument, HistoryInterval interval,
                      String source, List<HistoryCandle> candles) throws IOException {
        Files.createDirectories(outDir);
        String base = instrument.ticker() + "_" + interval.minutes() + "min";
        Path csvPath = outDir.resolve(base + ".csv");
        try (BufferedWriter writer = Files.newBufferedWriter(csvPath, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING)) {
            writer.write(HEADER);
            writer.newLine();
            for (HistoryCandle candle : candles) {
                writer.write(row(candle));
                writer.newLine();
            }
        }

        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("ticker", instrument.ticker());
        meta.put("class_code", instrument.classCode());
        meta.put("lot_size", instrument.lot());
        // Шаг цены нужен офлайн-исследованию: барьеры, издержки и требуемое движение
        // считаются в тиках (docs/buy_frame.md §2). Без поля шаг выводится по сетке цен
        // и помечается UNVERIFIED.
        meta.put("min_price_increment", instrument.minPriceIncrement());
        meta.put("interval_minutes", interval.minutes());
        meta.put("bars", candles.size());
        meta.put("fetched_at", Instant.now().truncatedTo(ChronoUnit.SECONDS).toString());
        meta.put("source", source);
        objectMapper.writerWithDefaultPrettyPrinter()
                .writeValue(outDir.resolve(base + ".meta.json").toFile(), meta);
        return csvPath;
    }

    private static String row(HistoryCandle candle) {
        return String.join(",",
                candle.end().toString(),
                price(candle.open()),
                price(candle.high()),
                price(candle.low()),
                price(candle.close()),
                Long.toString(candle.volume()));
    }

    /** Цена без экспоненты и лишних нулей: {@code 270.2}, а не {@code 2.702E+2}. */
    private static String price(double value) {
        return BigDecimal.valueOf(value).stripTrailingZeros().toPlainString();
    }
}
