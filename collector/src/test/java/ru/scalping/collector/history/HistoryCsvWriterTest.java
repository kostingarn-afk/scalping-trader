package ru.scalping.collector.history;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import ru.scalping.collector.instrument.InstrumentMeta;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class HistoryCsvWriterTest {

    private static final Instant END = Instant.parse("2026-11-13T07:01:00Z");
    private static final InstrumentMeta SBER =
            new InstrumentMeta("BBG004730N88", "SBER", "TQBR", "Сбербанк", 10, 0.01, "rub", true, 1, 0);

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final HistoryCsvWriter writer = new HistoryCsvWriter(objectMapper);

    @TempDir
    Path tempDir;

    @Test
    void write_singleCandle_writesHeaderRowAndMeta() throws IOException {
        List<HistoryCandle> candles = List.of(
                new HistoryCandle(END, 270.0, 270.5, 269.5, 270.2, 120));

        Path csv = writer.write(tempDir, SBER, HistoryInterval.ONE_MIN, "tinvest:GetCandles", candles);

        assertThat(csv).isEqualTo(tempDir.resolve("SBER_1min.csv"));
        List<String> lines = Files.readAllLines(csv);
        assertThat(lines).hasSize(2);
        assertThat(lines.get(0)).isEqualTo(HistoryCsvWriter.HEADER);
        assertThat(lines.get(1)).isEqualTo("2026-11-13T07:01:00Z,270,270.5,269.5,270.2,120");

        JsonNode meta = objectMapper.readTree(tempDir.resolve("SBER_1min.meta.json").toFile());
        assertThat(meta.get("ticker").asText()).isEqualTo("SBER");
        assertThat(meta.get("class_code").asText()).isEqualTo("TQBR");
        assertThat(meta.get("lot_size").asInt()).isEqualTo(10);
        assertThat(meta.get("interval_minutes").asInt()).isEqualTo(1);
        assertThat(meta.get("bars").asInt()).isEqualTo(1);
        assertThat(meta.get("source").asText()).isEqualTo("tinvest:GetCandles");
        assertThat(meta.get("fetched_at").asText()).isNotBlank();
    }

    @Test
    void write_hourInterval_usesMinutesInFileName() throws IOException {
        Path csv = writer.write(tempDir, SBER, HistoryInterval.HOUR, "tinvest:GetCandles",
                List.of(new HistoryCandle(END, 100.0, 101.0, 99.0, 100.5, 10)));

        assertThat(csv.getFileName().toString()).isEqualTo("SBER_60min.csv");
        assertThat(tempDir.resolve("SBER_60min.meta.json")).exists();
    }

    @Test
    void write_twice_overwritesPreviousContent() throws IOException {
        writer.write(tempDir, SBER, HistoryInterval.ONE_MIN, "tinvest:GetCandles",
                List.of(new HistoryCandle(END, 1.0, 1.0, 1.0, 1.0, 1)));

        Path csv = writer.write(tempDir, SBER, HistoryInterval.ONE_MIN, "tinvest:GetCandles",
                List.of(new HistoryCandle(END, 2.0, 2.0, 2.0, 2.0, 2),
                        new HistoryCandle(END.plusSeconds(60), 3.0, 3.0, 3.0, 3.0, 3)));

        assertThat(Files.readAllLines(csv)).hasSize(3);
        assertThat(objectMapper.readTree(tempDir.resolve("SBER_1min.meta.json").toFile())
                .get("bars").asInt()).isEqualTo(2);
    }

    @Test
    void write_emptyCandles_writesHeaderOnlyAndZeroBars() throws IOException {
        Path csv = writer.write(tempDir, SBER, HistoryInterval.ONE_MIN, "tinvest:GetCandles", List.of());

        assertThat(Files.readAllLines(csv)).containsExactly(HistoryCsvWriter.HEADER);
        assertThat(objectMapper.readTree(tempDir.resolve("SBER_1min.meta.json").toFile())
                .get("bars").asInt()).isZero();
    }

    @Test
    void write_fractionalPrice_noScientificNotation() throws IOException {
        Path csv = writer.write(tempDir, SBER, HistoryInterval.ONE_MIN, "tinvest:GetCandles",
                List.of(new HistoryCandle(END, 0.1, 0.0000123, 0.0000001, 270.20, 1)));

        assertThat(Files.readAllLines(csv).get(1))
                .isEqualTo("2026-11-13T07:01:00Z,0.1,0.0000123,0.0000001,270.2,1");
    }
}
