package ru.scalping.collector.publish;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import ru.scalping.proto.PayloadType;
import ru.scalping.proto.RawMarketData;

/**
 * Фильтрация по торговому статусу (задача 2.5, spec market-data-collection):
 * данные Candle/OrderBook/Trades публикуются только при {@code NORMAL_TRADING},
 * Info публикуется всегда.
 *
 * <p>{@code NORMAL_TRADING = 2} в {@code SecurityTradingStatus} T-Invest API.
 */
@Component
public class TradingStatusFilter {

    private static final Logger log = LoggerFactory.getLogger(TradingStatusFilter.class);

    /** SecurityTradingStatus.SECURITY_TRADING_STATUS_NORMAL_TRADING */
    public static final int NORMAL_TRADING = 2;

    /**
     * @param data           сырые данные
     * @param tradingStatus  текущий торговый статус инструмента
     * @return true — данные нужно публиковать; false — отброшено фильтром
     */
    public boolean shouldPublish(RawMarketData data, int tradingStatus) {
        PayloadType type = data.getPayloadType();
        if (type == PayloadType.INFO) {
            return true;
        }
        if (tradingStatus != NORMAL_TRADING) {
            log.debug("Filtering {} for figi={} — trading_status={}", type, data.getFigi(), tradingStatus);
            return false;
        }
        return true;
    }
}
