package ru.scalping.collector.history;

import com.google.protobuf.Timestamp;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import ru.tinkoff.piapi.contract.v1.CandleInterval;
import ru.tinkoff.piapi.contract.v1.HistoricCandle;
import ru.tinkoff.piapi.contract.v1.Quotation;
import ru.tinkoff.piapi.core.InvestApi;

import java.time.Instant;
import java.util.List;

/**
 * Выгрузка исторических свечей через T-Invest {@code MarketDataService.GetCandles}.
 *
 * <p>Семантика SDK: {@code HistoricCandle.getTime()} — время <b>открытия</b> свечи, поэтому
 * время закрытия вычисляется как {@code time + interval}. Незавершённые свечи отбрасываются:
 * контракт выгрузки содержит только закрытые бары.
 */
@Component
public class TinkoffHistoryCandleSource implements HistoryCandleSource {

    private static final Logger log = LoggerFactory.getLogger(TinkoffHistoryCandleSource.class);

    private final InvestApi investApi;

    public TinkoffHistoryCandleSource(InvestApi investApi) {
        this.investApi = investApi;
    }

    @Override
    public List<HistoryCandle> fetch(String figi, Instant from, Instant to, HistoryInterval interval) {
        if (!from.isBefore(to)) {
            throw new IllegalArgumentException("History range must satisfy from < to");
        }
        List<HistoricCandle> candles = investApi.getMarketDataService()
                .getCandlesSync(figi, from, to, toSdkInterval(interval));
        log.debug("GetCandles figi={} {} [{}..{}) returned {} bars",
                figi, interval.code(), from, to, candles.size());
        return candles.stream()
                .filter(HistoricCandle::getIsComplete)
                .map(candle -> toCandle(candle, interval))
                .sorted((left, right) -> left.end().compareTo(right.end()))
                .toList();
    }

    private static HistoryCandle toCandle(HistoricCandle candle, HistoryInterval interval) {
        Instant begin = instant(candle.getTime());
        return new HistoryCandle(
                begin.plus(interval.barDuration()),
                toDouble(candle.getOpen()),
                toDouble(candle.getHigh()),
                toDouble(candle.getLow()),
                toDouble(candle.getClose()),
                candle.getVolume());
    }

    private static Instant instant(Timestamp timestamp) {
        return Instant.ofEpochSecond(timestamp.getSeconds(), timestamp.getNanos());
    }

    /** {@code Quotation} → {@code double}: {@code units + nano / 1e9}. */
    private static double toDouble(Quotation quotation) {
        return quotation.getUnits() + quotation.getNano() / 1_000_000_000d;
    }

    private static CandleInterval toSdkInterval(HistoryInterval interval) {
        return switch (interval) {
            case ONE_MIN -> CandleInterval.CANDLE_INTERVAL_1_MIN;
            case FIVE_MIN -> CandleInterval.CANDLE_INTERVAL_5_MIN;
            case FIFTEEN_MIN -> CandleInterval.CANDLE_INTERVAL_15_MIN;
            case HOUR -> CandleInterval.CANDLE_INTERVAL_HOUR;
        };
    }
}
