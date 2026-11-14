package ru.scalping.collector.stream;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import ru.scalping.collector.config.CollectorProperties;
import ru.scalping.collector.converter.TinvestConverter;
import ru.scalping.collector.instrument.InstrumentLoader;
import ru.scalping.collector.metrics.CollectorMetrics;
import ru.scalping.proto.OrderBookItem;
import ru.scalping.proto.RawMarketData;
import ru.tinkoff.piapi.contract.v1.MarketDataResponse;
import ru.tinkoff.piapi.contract.v1.Quotation;
import ru.tinkoff.piapi.contract.v1.SubscriptionInterval;
import ru.tinkoff.piapi.core.InvestApi;
import ru.tinkoff.piapi.core.stream.MarketDataSubscriptionService;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Pattern;

/**
 * Привязка bi-directional gRPC-стрима T-Invest ({@code MarketDataStreamService})
 * к абстракции {@link MarketStreamService}.
 *
 * <p>{@link #subscribe(List, MarketDataListener)} блокирует вызывающий поток до {@link #close()}
 * либо до ошибки стрима — на этом построен reconnect-loop {@link StreamSubscriptionManager}.
 * Ошибка стрима сохраняется в {@link AtomicReference} и выбрасывается из {@code subscribe}
 * после разблокировки, чтобы менеджер выполнил переподключение с backoff.
 */
@Service
public class TinkoffMarketStreamService implements MarketStreamService {

    private static final Logger log = LoggerFactory.getLogger(TinkoffMarketStreamService.class);

    private static final String STREAM_NAME = "collector-market-data";
    /** FIGI — 12 символов [A-Z0-9]; всё остальное считается тикером. */
    private static final Pattern FIGI_PATTERN = Pattern.compile("[A-Z0-9]{12}");
    private static final ru.scalping.proto.Quotation ZERO_QUOTATION = TinvestConverter.toQuotation(0, 0);

    private final InvestApi investApi;
    private final InstrumentLoader instrumentLoader;
    private final CollectorProperties properties;
    private final CollectorMetrics metrics;

    private final AtomicReference<MarketDataSubscriptionService> subscription = new AtomicReference<>();
    private final AtomicReference<RuntimeException> streamError = new AtomicReference<>();
    private volatile CountDownLatch awaitLatch = new CountDownLatch(1);
    private volatile boolean closed;

    public TinkoffMarketStreamService(InvestApi investApi,
                                      InstrumentLoader instrumentLoader,
                                      CollectorProperties properties,
                                      CollectorMetrics metrics) {
        this.investApi = investApi;
        this.instrumentLoader = instrumentLoader;
        this.properties = properties;
        this.metrics = metrics;
    }

    @Override
    public void subscribe(List<String> figis, MarketDataListener listener) {
        if (figis == null || figis.isEmpty()) {
            throw new IllegalArgumentException("Instrument list for market data stream must not be empty");
        }
        if (closed) {
            // закрытие пришло раньше, чем reconnect-цикл успел открыть новый стрим
            log.info("Stream service is closed, skipping subscribe");
            return;
        }
        CountDownLatch latch = new CountDownLatch(1);
        this.awaitLatch = latch;
        this.streamError.set(null);

        List<String> instruments = resolveFigis(figis);
        SubscriptionInterval interval = resolveInterval();

        MarketDataSubscriptionService stream = investApi.getMarketDataStreamService()
                .newStream(STREAM_NAME, response -> handleResponse(response, listener), this::onStreamError);
        subscription.set(stream);
        try {
            stream.subscribeCandles(instruments, interval);
            stream.subscribeOrderbook(instruments, properties.orderBookDepth());
            stream.subscribeTrades(instruments);
            stream.subscribeInfo(instruments);
            log.info("Market data stream opened: instruments={}, interval={}, depth={}",
                    instruments.size(), interval, properties.orderBookDepth());
            latch.await();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.warn("Market data stream interrupted, closing");
            cancelQuietly(stream);
            return;
        } catch (RuntimeException e) {
            cancelQuietly(stream);
            throw e;
        }
        subscription.compareAndSet(stream, null);

        RuntimeException error = streamError.get();
        if (error != null) {
            throw error;
        }
    }

    @Override
    public void close() {
        closed = true;
        MarketDataSubscriptionService stream = subscription.getAndSet(null);
        if (stream != null) {
            cancelQuietly(stream);
        }
        awaitLatch.countDown();
        log.info("Market data stream closed");
    }

    /** Колбэк SDK: любая ошибка стрима разблокирует {@link #subscribe} и уйдёт в reconnect. */
    private void onStreamError(Throwable error) {
        metrics.apiError();
        String message = error == null ? "unknown" : error.getMessage();
        RuntimeException wrapped = error instanceof RuntimeException runtime
                ? runtime
                : new RuntimeException("Market data stream error: " + message, error);
        if (streamError.compareAndSet(null, wrapped)) {
            log.error("Market data stream error: {}", message);
        }
        awaitLatch.countDown();
    }

    private void handleResponse(MarketDataResponse response, MarketDataListener listener) {
        try {
            RawMarketData data = convert(response);
            if (data != null) {
                listener.onNext(data);
            }
        } catch (RuntimeException e) {
            log.error("Failed to convert market data payload {}: {}", response.getPayloadCase(), e.getMessage());
        }
    }

    private RawMarketData convert(MarketDataResponse response) {
        if (response.hasCandle()) {
            var candle = response.getCandle();
            if (candle.getFigi().isBlank()) {
                return null;
            }
            Instant begin = instant(candle.getTime());
            Instant end = begin.plus(candleDuration(candle.getInterval()));
            // В стриме SDK v1.8 нет признака завершённости свечи: считаем её закрытой,
            // когда истёк интервал. Analyzer обрабатывает только is_complete=true.
            boolean complete = !end.isAfter(Instant.now());
            var payload = TinvestConverter.candleValue(
                    TinvestConverter.toTimestamp(begin),
                    TinvestConverter.toTimestamp(end),
                    quotation(candle.getOpen()),
                    quotation(candle.getClose()),
                    quotation(candle.getHigh()),
                    quotation(candle.getLow()),
                    candle.getVolume(),
                    complete);
            return TinvestConverter.candle(candle.getFigi(), begin, payload);
        }
        if (response.hasOrderbook()) {
            var book = response.getOrderbook();
            if (book.getFigi().isBlank()) {
                return null;
            }
            Instant ts = book.hasTime() ? instant(book.getTime()) : Instant.now();
            int depth = Math.max(1, properties.orderBookDepth());
            var payload = TinvestConverter.orderBook(
                    levels(book.getBidsList(), depth),
                    levels(book.getAsksList(), depth),
                    book.hasLimitUp() ? quotation(book.getLimitUp()) : ZERO_QUOTATION,
                    book.hasLimitDown() ? quotation(book.getLimitDown()) : ZERO_QUOTATION);
            return TinvestConverter.orderBook(book.getFigi(), ts, payload);
        }
        if (response.hasTrade()) {
            var trade = response.getTrade();
            if (trade.getFigi().isBlank()) {
                return null;
            }
            Instant ts = trade.hasTime() ? instant(trade.getTime()) : Instant.now();
            var payload = TinvestConverter.trade(
                    quotation(trade.getPrice()),
                    trade.getQuantity(),
                    // идентификатора сделки в стриме T-Invest v1.8 нет
                    "",
                    TinvestConverter.tradeDirection(trade.getDirectionValue()));
            return TinvestConverter.trade(trade.getFigi(), ts, payload);
        }
        if (response.hasTradingStatus()) {
            var status = response.getTradingStatus();
            if (status.getFigi().isBlank()) {
                return null;
            }
            Instant ts = status.hasTime() ? instant(status.getTime()) : Instant.now();
            var payload = TinvestConverter.status(
                    status.getTradingStatusValue(),
                    status.getLimitOrderAvailableFlag() ? 1 : 0,
                    status.getMarketOrderAvailableFlag() ? 1 : 0);
            return TinvestConverter.info(status.getFigi(), ts, payload);
        }
        return null;
    }

    /**
     * Приведение списка идентификаторов к FIGI.
     *
     * <p>{@link StreamSubscriptionManager} передаёт {@code collector.instruments} (тикеры),
     * а gRPC-стрим T-Invest принимает только FIGI/instrument_uid. Тикеры разрешаются через
     * {@link InstrumentLoader}; если разрешить не удалось — идентификатор передаётся как есть.
     */
    private List<String> resolveFigis(List<String> instruments) {
        List<String> resolved = new ArrayList<>(instruments.size());
        for (String instrument : instruments) {
            if (instrument == null || instrument.isBlank()) {
                throw new IllegalArgumentException("Instrument identifier must not be blank");
            }
            if (FIGI_PATTERN.matcher(instrument).matches()) {
                resolved.add(instrument);
                continue;
            }
            try {
                String figi = instrumentLoader.loadByTicker(instrument).figi();
                log.info("Resolved instrument '{}' to figi {}", instrument, figi);
                resolved.add(figi);
            } catch (RuntimeException e) {
                log.warn("Cannot resolve instrument '{}' to figi ({}), passing to stream as is",
                        instrument, e.getMessage());
                resolved.add(instrument);
            }
        }
        return List.copyOf(resolved);
    }

    private SubscriptionInterval resolveInterval() {
        String raw = properties.candleInterval() == null
                ? "1min"
                : properties.candleInterval().trim().toLowerCase(Locale.ROOT);
        return switch (raw) {
            case "1min", "1m", "min" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_MINUTE;
            case "2min", "2m" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_2_MIN;
            case "3min", "3m" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_3_MIN;
            case "5min", "5m" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_FIVE_MINUTES;
            case "10min", "10m" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_10_MIN;
            case "15min", "15m" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_FIFTEEN_MINUTES;
            case "30min", "30m" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_30_MIN;
            case "1h", "hour", "1hour" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_HOUR;
            case "2h" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_2_HOUR;
            case "4h" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_4_HOUR;
            case "1d", "day", "1day" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_DAY;
            case "week", "1w" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_WEEK;
            case "month", "1month" -> SubscriptionInterval.SUBSCRIPTION_INTERVAL_MONTH;
            default -> throw new IllegalArgumentException(
                    "Unsupported collector.candle-interval: " + properties.candleInterval());
        };
    }

    /** Длительность интервала для расчёта {@code end} свечи (месяц — приближение 30 дней). */
    private static Duration candleDuration(SubscriptionInterval interval) {
        return switch (interval) {
            case SUBSCRIPTION_INTERVAL_ONE_MINUTE -> Duration.ofMinutes(1);
            case SUBSCRIPTION_INTERVAL_2_MIN -> Duration.ofMinutes(2);
            case SUBSCRIPTION_INTERVAL_3_MIN -> Duration.ofMinutes(3);
            case SUBSCRIPTION_INTERVAL_FIVE_MINUTES -> Duration.ofMinutes(5);
            case SUBSCRIPTION_INTERVAL_10_MIN -> Duration.ofMinutes(10);
            case SUBSCRIPTION_INTERVAL_FIFTEEN_MINUTES -> Duration.ofMinutes(15);
            case SUBSCRIPTION_INTERVAL_30_MIN -> Duration.ofMinutes(30);
            case SUBSCRIPTION_INTERVAL_ONE_HOUR -> Duration.ofHours(1);
            case SUBSCRIPTION_INTERVAL_2_HOUR -> Duration.ofHours(2);
            case SUBSCRIPTION_INTERVAL_4_HOUR -> Duration.ofHours(4);
            case SUBSCRIPTION_INTERVAL_ONE_DAY -> Duration.ofDays(1);
            case SUBSCRIPTION_INTERVAL_WEEK -> Duration.ofDays(7);
            case SUBSCRIPTION_INTERVAL_MONTH -> Duration.ofDays(30);
            default -> Duration.ZERO;
        };
    }

    private static List<OrderBookItem> levels(List<ru.tinkoff.piapi.contract.v1.Order> source, int limit) {
        return source.stream()
                .limit(limit)
                .map(level -> TinvestConverter.item(quotation(level.getPrice()), level.getQuantity()))
                .toList();
    }

    private static ru.scalping.proto.Quotation quotation(Quotation sdkQuotation) {
        return sdkQuotation == null
                ? ZERO_QUOTATION
                : TinvestConverter.toQuotation(sdkQuotation.getUnits(), sdkQuotation.getNano());
    }

    private static Instant instant(com.google.protobuf.Timestamp timestamp) {
        return Instant.ofEpochSecond(timestamp.getSeconds(), timestamp.getNanos());
    }

    private void cancelQuietly(MarketDataSubscriptionService stream) {
        try {
            stream.cancel();
        } catch (RuntimeException e) {
            log.warn("Failed to cancel market data subscription: {}", e.getMessage());
        }
    }
}
