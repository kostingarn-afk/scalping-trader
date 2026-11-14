package ru.scalping.collector.converter;

import com.google.protobuf.Timestamp;
import ru.scalping.proto.Candle;
import ru.scalping.proto.OrderBook;
import ru.scalping.proto.OrderBookItem;
import ru.scalping.proto.PayloadType;
import ru.scalping.proto.Quotation;
import ru.scalping.proto.RawMarketData;
import ru.scalping.proto.Trade;
import ru.scalping.proto.TradeDirection;
import ru.scalping.proto.TradingStatusInfo;

import java.time.Instant;
import java.util.List;

/**
 * Конвертация объектов T-Invest SDK в Protobuf-сообщения.
 * Изолирует маппинг, чтобы бизнес-логика не зависела от типов SDK напрямую.
 */
public final class TinvestConverter {

    private TinvestConverter() {}

    public static Quotation toQuotation(long units, int nano) {
        return Quotation.newBuilder().setUnits(units).setNano(nano).build();
    }

    public static Quotation toQuotation(double price) {
        long units = (long) price;
        int nano = (int) Math.round((price - units) * 1_000_000_000);
        if (nano >= 1_000_000_000) { units += 1; nano -= 1_000_000_000; }
        if (nano < 0) { units -= 1; nano += 1_000_000_000; }
        return toQuotation(units, nano);
    }

    public static Timestamp toTimestamp(Instant instant) {
        return Timestamp.newBuilder()
                .setSeconds(instant.getEpochSecond())
                .setNanos(instant.getNano())
                .build();
    }

    public static RawMarketData candle(String figi, Instant ts, Candle candle) {
        return base(figi, ts, PayloadType.CANDLE).setCandle(candle).build();
    }

    public static RawMarketData orderBook(String figi, Instant ts, OrderBook book) {
        return base(figi, ts, PayloadType.ORDER_BOOK).setOrderBook(book).build();
    }

    public static RawMarketData trade(String figi, Instant ts, Trade trade) {
        return base(figi, ts, PayloadType.TRADE).setTrade(trade).build();
    }

    public static RawMarketData info(String figi, Instant ts, TradingStatusInfo info) {
        return base(figi, ts, PayloadType.INFO).setInfo(info).build();
    }

    private static RawMarketData.Builder base(String figi, Instant ts, PayloadType type) {
        return RawMarketData.newBuilder()
                .setFigi(figi)
                .setTs(toTimestamp(ts))
                .setPayloadType(type);
    }

    public static OrderBookItem item(Quotation price, long quantity) {
        return OrderBookItem.newBuilder().setPrice(price).setQuantity(quantity).build();
    }

    /** Сборка свечи из уже сконвертированных частей (SDK-типы не проникают в конвертер). */
    public static Candle candleValue(Timestamp begin, Timestamp end, Quotation open, Quotation close,
                                     Quotation high, Quotation low, long volume, boolean isComplete) {
        return Candle.newBuilder()
                .setBegin(begin).setEnd(end)
                .setOpen(open).setClose(close)
                .setHigh(high).setLow(low)
                .setVolume(volume)
                .setIsComplete(isComplete)
                .build();
    }

    /**
     * Направление сделки по числовому значению {@code TradeDirection} T-Invest API
     * (0 — unspecified, 1 — buy, 2 — sell).
     */
    public static TradeDirection tradeDirection(int code) {
        return switch (code) {
            case 1 -> TradeDirection.TRADE_DIRECTION_BUY;
            case 2 -> TradeDirection.TRADE_DIRECTION_SELL;
            default -> TradeDirection.TRADE_DIRECTION_UNSPECIFIED;
        };
    }

    public static Trade trade(Quotation price, long quantity, String tradeId, TradeDirection dir) {
        return Trade.newBuilder()
                .setPrice(price).setQuantity(quantity)
                .setTradeId(tradeId).setDirection(dir)
                .build();
    }

    public static TradingStatusInfo status(int tradingStatus, int limitFlag, int marketFlag) {
        return TradingStatusInfo.newBuilder()
                .setTradingStatus(tradingStatus)
                .setLimitOrderAvailableFlag(limitFlag)
                .setMarketOrderAvailableFlag(marketFlag)
                .build();
    }

    /** Сборка OrderBook из списков bids/asks (до depth уровней). */
    public static OrderBook orderBook(List<OrderBookItem> bids, List<OrderBookItem> asks,
                                      Quotation limitUp, Quotation limitDown) {
        return OrderBook.newBuilder()
                .addAllBids(bids).addAllAsks(asks)
                .setLimitPriceUp(limitUp).setLimitPriceDown(limitDown)
                .build();
    }
}
