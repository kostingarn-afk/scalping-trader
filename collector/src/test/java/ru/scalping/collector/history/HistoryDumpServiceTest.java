package ru.scalping.collector.history;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import ru.scalping.collector.instrument.InstrumentLoader;
import ru.scalping.collector.instrument.InstrumentMeta;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;

class HistoryDumpServiceTest {

    private static final Instant FROM = Instant.parse("2026-10-01T00:00:00Z");
    private static final InstrumentMeta SBER =
            new InstrumentMeta("FIGI-SBER", "SBER", "TQBR", "Сбербанк", 10, 0.01, "rub", true, 1, 0);
    private static final InstrumentMeta SI =
            new InstrumentMeta("FIGI-SI", "SiZ6", "SPBFUT", "Si-12.26", 1, 1, "rub", true, 1, 0);

    private final HistoryCsvWriter writer = new HistoryCsvWriter(new ObjectMapper());
    private final InstrumentLoader loader = ticker -> ticker.startsWith("Si") ? SI : SBER;

    @TempDir
    Path tempDir;

    private HistoryDumpProperties properties(Instant to, String... instruments) {
        return new HistoryDumpProperties(List.of(instruments), "1min", FROM, to, tempDir,
                HistoryDumpProperties.DEFAULT_REQUESTS_PER_MINUTE);
    }

    @Test
    void dump_singleInstrument_writesBarsAndMeta() throws IOException {
        HistoryCandleSource source = (figi, from, to, interval) ->
                List.of(new HistoryCandle(from.plus(interval.barDuration()), 100, 101, 99, 100.5, 5));
        HistoryDumpService service = new HistoryDumpService(loader, source, writer, RequestPacer.unlimited());

        List<DumpReport> reports = service.dump(properties(FROM.plusSeconds(180), "SBER"));

        assertThat(reports).hasSize(1);
        assertThat(reports.get(0).ticker()).isEqualTo("SBER");
        assertThat(reports.get(0).bars()).isEqualTo(1);
        assertThat(reports.get(0).isEmpty()).isFalse();
        assertThat(Files.readAllLines(reports.get(0).path())).hasSize(2);
        assertThat(reports.get(0).path()).isEqualTo(tempDir.resolve("SBER_1min.csv"));
    }

    @Test
    void dump_twoInstruments_writesFilePerInstrument() throws IOException {
        HistoryCandleSource source = (figi, from, to, interval) ->
                List.of(new HistoryCandle(from.plus(interval.barDuration()), 100, 101, 99, 100.5, 5));
        HistoryDumpService service = new HistoryDumpService(loader, source, writer, RequestPacer.unlimited());

        List<DumpReport> reports = service.dump(properties(FROM.plusSeconds(60), "SBER", "SiZ6@SPBFUT"));

        assertThat(reports).extracting(DumpReport::ticker).containsExactly("SBER", "SiZ6");
        assertThat(tempDir.resolve("SBER_1min.csv")).exists();
        assertThat(tempDir.resolve("SiZ6_1min.csv")).exists();
    }

    @Test
    void dump_periodLongerThanPageSpan_requestsContiguousPages() throws IOException {
        List<String> requestedRanges = new ArrayList<>();
        HistoryCandleSource source = (figi, from, to, interval) -> {
            requestedRanges.add(from + "/" + to);
            return List.of(new HistoryCandle(from.plus(interval.barDuration()), 100, 101, 99, 100.5, 5));
        };
        HistoryDumpService service = new HistoryDumpService(loader, source, writer, RequestPacer.unlimited());

        service.dump(properties(FROM.plusSeconds(3L * 24 * 3600), "SBER"));

        assertThat(requestedRanges).hasSize(3);
        assertThat(requestedRanges.get(0)).isEqualTo(FROM + "/" + FROM.plusSeconds(86_400));
        assertThat(requestedRanges.get(1)).isEqualTo(FROM.plusSeconds(86_400) + "/" + FROM.plusSeconds(172_800));
    }

    @Test
    void dump_emptyHistory_reportsEmptyAndWritesNoFile() throws IOException {
        HistoryCandleSource source = (figi, from, to, interval) -> List.of();
        HistoryDumpService service = new HistoryDumpService(loader, source, writer, RequestPacer.unlimited());

        List<DumpReport> reports = service.dump(properties(FROM.plusSeconds(60), "SBER"));

        assertThat(reports.get(0).isEmpty()).isTrue();
        assertThat(reports.get(0).path()).isNull();
        assertThat(tempDir.resolve("SBER_1min.csv")).doesNotExist();
    }

    /** Пейсер вызывается перед каждым запросом: иначе пачка страниц упирается в лимит API. */
    @Test
    void dump_severalPages_awaitsPacerBeforeEveryRequest() throws IOException {
        List<String> events = new ArrayList<>();
        HistoryCandleSource source = (figi, from, to, interval) -> {
            events.add("fetch");
            return List.of(new HistoryCandle(from.plus(interval.barDuration()), 100, 101, 99, 100.5, 5));
        };
        AtomicLong clock = new AtomicLong();
        RequestPacer pacer = RequestPacer.perMinute(60, clock::get, nanos -> {
            events.add("wait");
            clock.addAndGet(nanos);
        });
        HistoryDumpService service = new HistoryDumpService(loader, source, writer, pacer);

        service.dump(properties(FROM.plusSeconds(3L * 24 * 3600), "SBER"));

        assertThat(events).containsExactly("fetch", "wait", "fetch", "wait", "fetch");
    }

    @Test
    void dedupeAndSort_duplicateEnd_keepsLastVersion() {
        Instant end = FROM.plusSeconds(60);
        List<HistoryCandle> candles = List.of(
                new HistoryCandle(end, 1, 1, 1, 1, 1),
                new HistoryCandle(end, 2, 2, 2, 2, 2),
                new HistoryCandle(end.minusSeconds(60), 3, 3, 3, 3, 3));

        List<HistoryCandle> unique = HistoryDumpService.dedupeAndSort(candles);

        assertThat(unique).extracting(HistoryCandle::end)
                .containsExactly(end.minusSeconds(60), end);
        assertThat(unique.get(1).close()).isEqualTo(2.0);
    }

    @Test
    void missingDays_gapInTheMiddle_reportsOnlyInnerDays() {
        List<HistoryCandle> candles = List.of(
                candleAt("2026-10-01T10:00:00Z"),
                candleAt("2026-10-01T10:01:00Z"),
                candleAt("2026-10-04T10:00:00Z"),
                candleAt("2026-10-05T10:00:00Z"));

        assertThat(HistoryDumpService.missingDays(candles))
                .containsExactly(LocalDate.of(2026, 10, 2), LocalDate.of(2026, 10, 3));
    }

    @Test
    void missingDays_contiguousDays_returnsEmpty() {
        List<HistoryCandle> candles = List.of(
                candleAt("2026-10-01T10:00:00Z"),
                candleAt("2026-10-02T10:00:00Z"),
                candleAt("2026-10-03T10:00:00Z"));

        assertThat(HistoryDumpService.missingDays(candles)).isEmpty();
    }

    @Test
    void missingDays_emptyOrSingleDay_returnsEmpty() {
        assertThat(HistoryDumpService.missingDays(List.of())).isEmpty();
        assertThat(HistoryDumpService.missingDays(List.of(candleAt("2026-10-01T10:00:00Z")))).isEmpty();
    }

    private static HistoryCandle candleAt(String end) {
        return new HistoryCandle(Instant.parse(end), 100, 101, 99, 100.5, 5);
    }
}
