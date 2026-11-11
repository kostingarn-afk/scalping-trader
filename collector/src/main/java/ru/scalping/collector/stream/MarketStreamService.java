package ru.scalping.collector.stream;

/**
 * Абстракция над bi-directional gRPC-стримом {@code MarketDataStreamService}.
 * Реализация привязана к T-Invest SDK и подставляется через профиль.
 *
 * <p>Метод {@link #subscribe} открывает стрим, выставляет подписки
 * (Candles, OrderBook, Trades, Info) и направляет события в {@link MarketDataListener}.
 */
public interface MarketStreamService {
    void subscribe(java.util.List<String> figis, MarketDataListener listener);
    void close();
}
