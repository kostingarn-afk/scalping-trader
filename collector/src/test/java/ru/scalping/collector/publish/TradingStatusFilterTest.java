package ru.scalping.collector.publish;

import org.junit.jupiter.api.Test;
import ru.scalping.proto.PayloadType;
import ru.scalping.proto.RawMarketData;
import ru.scalping.proto.TradingStatusInfo;

import static org.assertj.core.api.Assertions.assertThat;

class TradingStatusFilterTest {

    private final TradingStatusFilter filter = new TradingStatusFilter();

    private RawMarketData info(String figi) {
        return RawMarketData.newBuilder()
                .setFigi(figi)
                .setPayloadType(PayloadType.INFO)
                .setInfo(TradingStatusInfo.newBuilder().setTradingStatus(5).build())
                .build();
    }

    private RawMarketData candle(String figi) {
        return RawMarketData.newBuilder()
                .setFigi(figi)
                .setPayloadType(PayloadType.CANDLE)
                .build();
    }

    @Test
    void infoPublishedRegardlessOfStatus() {
        assertThat(filter.shouldPublish(info("SBER"), 5)).isTrue();
        assertThat(filter.shouldPublish(info("SBER"), 2)).isTrue();
    }

    @Test
    void candlePublishedOnlyOnNormalTrading() {
        assertThat(filter.shouldPublish(candle("SBER"), TradingStatusFilter.NORMAL_TRADING)).isTrue();
    }

    @Test
    void candleFilteredOnBreakInTrading() {
        // BREAK_IN_TRADING = 7 в SecurityTradingStatus
        assertThat(filter.shouldPublish(candle("SBER"), 7)).isFalse();
    }

    @Test
    void candleFilteredOnOpeningPeriod() {
        // OPENING_PERIOD = 4
        assertThat(filter.shouldPublish(candle("SBER"), 4)).isFalse();
    }
}
