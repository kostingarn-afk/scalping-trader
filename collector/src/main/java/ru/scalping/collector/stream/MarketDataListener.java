package ru.scalping.collector.stream;

import ru.scalping.proto.RawMarketData;

/** Приёмник событий из gRPC-стрима. */
@FunctionalInterface
public interface MarketDataListener {
    void onNext(RawMarketData data);
}
