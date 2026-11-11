package ru.scalping.collector.converter;

import org.junit.jupiter.api.Test;
import ru.scalping.proto.PayloadType;
import ru.scalping.proto.Quotation;
import ru.scalping.proto.RawMarketData;

import static org.assertj.core.api.Assertions.assertThat;

class TinvestConverterTest {

    @Test
    void quotationFromDouble() {
        Quotation q = TinvestConverter.toQuotation(100.25);
        assertThat(q.getUnits()).isEqualTo(100);
        assertThat(q.getNano()).isEqualTo(250_000_000);
    }

    @Test
    void quotationNegativePrice() {
        Quotation q = TinvestConverter.toQuotation(-1.5);
        assertThat(q.getUnits()).isEqualTo(-2);
        assertThat(q.getNano()).isEqualTo(500_000_000);
    }

    @Test
    void candleHasCorrectPayloadType() {
        RawMarketData data = TinvestConverter.candle(
                "SBER",
                java.time.Instant.now(),
                ru.scalping.proto.Candle.newBuilder().build()
        );
        assertThat(data.getPayloadType()).isEqualTo(PayloadType.CANDLE);
        assertThat(data.getFigi()).isEqualTo("SBER");
    }

    @Test
    void infoHasCorrectPayloadType() {
        RawMarketData data = TinvestConverter.info(
                "SBER", java.time.Instant.now(),
                TinvestConverter.status(2, 1, 1)
        );
        assertThat(data.getPayloadType()).isEqualTo(PayloadType.INFO);
        assertThat(data.getInfo().getTradingStatus()).isEqualTo(2);
    }
}
