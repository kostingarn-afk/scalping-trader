package ru.scalping.collector.history;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
import ru.scalping.collector.instrument.InstrumentLoader;
import ru.scalping.collector.instrument.InstrumentMeta;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NavigableSet;
import java.util.TreeSet;
import java.util.stream.Collectors;

/**
 * Выгрузка истории в CSV для Analyzer'а: страницы запроса, дедупликация, запись файлов.
 *
 * <p>Свечи на стыках страниц могут повторяться (граница запроса включительна), поэтому результат
 * сводится по времени закрытия: дубликат заменяется последней версией, порядок — по возрастанию
 * времени. Пустой ответ не создаёт файл: Analyzer не должен видеть выгрузку без данных.
 */
@Service
@Profile("history-dump")
public class HistoryDumpService {

    private static final Logger log = LoggerFactory.getLogger(HistoryDumpService.class);
    static final String SOURCE = "tinvest:GetCandles";

    /** Сколько «пустых» дней перечислять в WARN до сокращения списка. */
    private static final int MAX_LOGGED_GAPS = 10;

    private final InstrumentLoader instrumentLoader;
    private final HistoryCandleSource candleSource;
    private final HistoryCsvWriter csvWriter;
    private final RequestPacer requestPacer;

    public HistoryDumpService(InstrumentLoader instrumentLoader,
                              HistoryCandleSource candleSource,
                              HistoryCsvWriter csvWriter,
                              RequestPacer requestPacer) {
        this.instrumentLoader = instrumentLoader;
        this.candleSource = candleSource;
        this.csvWriter = csvWriter;
        this.requestPacer = requestPacer;
    }

    /**
     * Выгрузить историю по всем инструментам конфигурации.
     *
     * @throws IOException при ошибке записи файлов
     */
    public List<DumpReport> dump(HistoryDumpProperties properties) throws IOException {
        HistoryInterval interval = HistoryInterval.fromCode(properties.interval());
        List<DumpReport> reports = new ArrayList<>();
        for (String reference : properties.instruments()) {
            reports.add(dumpInstrument(reference, interval, properties));
        }
        return reports;
    }

    private DumpReport dumpInstrument(String reference, HistoryInterval interval,
                                      HistoryDumpProperties properties) throws IOException {
        InstrumentMeta instrument = instrumentLoader.loadByTicker(reference);
        List<HistoryRange.Page> pages = HistoryRange.split(properties.from(), properties.to(),
                interval.maxPageSpan());
        List<HistoryCandle> candles = new ArrayList<>();
        for (HistoryRange.Page page : pages) {
            requestPacer.await();
            candles.addAll(candleSource.fetch(instrument.figi(), page.from(), page.to(), interval));
        }
        List<HistoryCandle> unique = dedupeAndSort(candles);
        if (unique.isEmpty()) {
            log.warn("No history for {}@{} {} in [{}..{})",
                    instrument.ticker(), instrument.classCode(), interval.code(),
                    properties.from(), properties.to());
            return new DumpReport(instrument.ticker(), instrument.classCode(), 0, null);
        }
        List<LocalDate> gaps = missingDays(unique);
        if (!gaps.isEmpty()) {
            log.warn("History gaps {}@{} {}: {} day(s) without bars between {} and {}: {}",
                    instrument.ticker(), instrument.classCode(), interval.code(), gaps.size(),
                    gaps.get(0), gaps.get(gaps.size() - 1), describeGaps(gaps));
        }
        Path path = csvWriter.write(properties.outDir(), instrument, interval, SOURCE, unique);
        log.info("History dump {}@{} {}: {} bars, pages={} → {}",
                instrument.ticker(), instrument.classCode(), interval.code(), unique.size(),
                pages.size(), path);
        return new DumpReport(instrument.ticker(), instrument.classCode(), unique.size(), path);
    }

    /** Дедупликация по времени закрытия с сохранением порядка. */
    static List<HistoryCandle> dedupeAndSort(List<HistoryCandle> candles) {
        Map<Instant, HistoryCandle> byEnd = new LinkedHashMap<>();
        candles.stream()
                .sorted((left, right) -> left.end().compareTo(right.end()))
                .forEach(candle -> byEnd.put(candle.end(), candle));
        return List.copyOf(byEnd.values());
    }

    /**
     * Календарные дни (UTC), за которые внутри периода выгрузки нет ни одного бара.
     *
     * <p>Крайние дни не проверяются: {@code from}/{@code to} могут приходиться на середину
     * торгового дня, и отсутствие баров до первого или после последнего дня — не пропуск данных,
     * а граница запроса. Метод нужен, чтобы пропуски в источнике (например, выходной день без
     * торгов у фьючерса) не терялись молча.
     */
    static List<LocalDate> missingDays(List<HistoryCandle> candles) {
        if (candles.isEmpty()) {
            return List.of();
        }
        NavigableSet<LocalDate> present = candles.stream()
                .map(candle -> candle.end().atZone(ZoneOffset.UTC).toLocalDate())
                .collect(Collectors.toCollection(TreeSet::new));
        List<LocalDate> missing = new ArrayList<>();
        for (LocalDate day = present.first().plusDays(1); day.isBefore(present.last()); day = day.plusDays(1)) {
            if (!present.contains(day)) {
                missing.add(day);
            }
        }
        return List.copyOf(missing);
    }

    private static String describeGaps(List<LocalDate> gaps) {
        if (gaps.size() <= MAX_LOGGED_GAPS) {
            return gaps.toString();
        }
        return gaps.subList(0, MAX_LOGGED_GAPS) + " … (" + gaps.size() + " total)";
    }
}
