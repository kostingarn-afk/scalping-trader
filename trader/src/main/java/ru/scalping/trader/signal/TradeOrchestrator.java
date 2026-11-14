package ru.scalping.trader.signal;

import com.google.protobuf.Timestamp;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Component;
import ru.scalping.proto.Quotation;
import ru.scalping.proto.SignalType;
import ru.scalping.proto.TradeDirection;
import ru.scalping.proto.TradeEventType;
import ru.scalping.proto.TradeResult;
import ru.scalping.proto.TradingSignal;
import ru.scalping.trader.execution.OrderExecutionService;
import ru.scalping.trader.execution.OrderRequest;
import ru.scalping.trader.execution.OrderResult;
import ru.scalping.trader.execution.OrderSide;
import ru.scalping.trader.execution.OrderType;
import ru.scalping.trader.metrics.TraderMetrics;
import ru.scalping.trader.position.Position;
import ru.scalping.trader.position.PositionStore;
import ru.scalping.trader.publish.TradeResultPublisher;
import ru.scalping.trader.recovery.ExchangeStateProvider;
import ru.scalping.trader.repository.TradeRepository;
import ru.scalping.trader.risk.RiskCheckResult;
import ru.scalping.trader.risk.RiskManager;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.UUID;

/**
 * Оркестратор сделки по входящему сигналу (spec trade-execution).
 *
 * <p>Порядок: валидация (дедупликация → устаревание → HOLD) → торговый статус →
 * risk-check → вход LIMIT → запись {@code trade_results}/{@code positions} → SL/TP →
 * публикация {@code TradeResult}. Типы T-Invest SDK сюда не попадают: работа идёт через
 * {@link OrderExecutionService} и {@link ExchangeStateProvider}.
 */
@Component
public class TradeOrchestrator {

    private static final Logger log = LoggerFactory.getLogger(TradeOrchestrator.class);

    private static final String STATUS_OPEN = "open";
    private static final String STATUS_REJECTED = "rejected";

    private final SignalProcessor signalProcessor;
    private final SignalValidator signalValidator;
    private final SignalDeduplicator deduplicator;
    private final RiskManager riskManager;
    private final TradeRepository tradeRepository;
    private final OrderExecutionService executionService;
    private final ExchangeStateProvider exchangeStateProvider;
    private final TradeResultPublisher publisher;
    private final PositionStore positionStore;
    private final TraderMetrics metrics;
    private final double deposit;

    public TradeOrchestrator(SignalProcessor signalProcessor,
                             SignalValidator signalValidator,
                             SignalDeduplicator deduplicator,
                             RiskManager riskManager,
                             TradeRepository tradeRepository,
                             OrderExecutionService executionService,
                             ExchangeStateProvider exchangeStateProvider,
                             TradeResultPublisher publisher,
                             PositionStore positionStore,
                             TraderMetrics metrics,
                             @Value("${trader.deposit:200000}") double deposit) {
        this.signalProcessor = signalProcessor;
        this.signalValidator = signalValidator;
        this.deduplicator = deduplicator;
        this.riskManager = riskManager;
        this.tradeRepository = tradeRepository;
        this.executionService = executionService;
        this.exchangeStateProvider = exchangeStateProvider;
        this.publisher = publisher;
        this.positionStore = positionStore;
        this.metrics = metrics;
        this.deposit = deposit;
    }

    /**
     * Обработка сигнала.
     *
     * <p>Инфраструктурный сбой БД пробрасывается как {@link RetryableSignalException} —
     * consumer тогда не коммитит offset и сообщение будет перечитано. Ошибка данных
     * (нарушение FK) и прочие сбои логируются: повтор их не исправит, сообщение пропускается.
     */
    public void handle(TradingSignal signal) {
        try {
            process(signal);
        } catch (DataIntegrityViolationException e) {
            log.error("Signal rejected by data constraints (poison message): figi={}, signalId={}: {}",
                    signal.getFigi(), signal.getSignalId(), e.getMessage());
        } catch (DataAccessException e) {
            throw new RetryableSignalException(
                    "Signal processing postponed, database unavailable: " + e.getMessage(), e);
        } catch (RuntimeException e) {
            log.error("Signal processing failed: figi={}, signalId={}: {}",
                    signal.getFigi(), signal.getSignalId(), e.getMessage(), e);
        }
    }

    private void process(TradingSignal signal) {
        final UUID signalId;
        try {
            signalId = UUID.fromString(signal.getSignalId());
        } catch (IllegalArgumentException e) {
            log.warn("Invalid signal_id='{}', signal skipped", signal.getSignalId());
            return;
        }

        String figi = signal.getFigi();
        TradeDirection direction = directionOf(signal.getSignal());

        SignalProcessor.ValidationResult validation = signalProcessor.validate(signal);
        switch (validation.status()) {
            case DUPLICATE -> {
                log.warn("Duplicate signal ignored: signalId={}", signalId);
                return;
            }
            case REJECTED -> {
                if (signal.getSignal() == SignalType.HOLD) {
                    // HOLD — не попытка сделки: событие в Kafka не публикуется (design D2).
                    deduplicator.markRejected(signalId, "hold signal");
                    log.debug("HOLD signal not executed: signalId={}, figi={}", signalId, figi);
                    return;
                }
                log.warn("Signal rejected before execution: signalId={}, reason={}",
                        signalId, validation.rejectReason());
                publishReject(UUID.randomUUID(), signalId, figi, direction, 0, null,
                        validation.rejectReason());
                return;
            }
            case OK -> {
            }
        }

        int tradingStatus = exchangeStateProvider.tradingStatus(figi);
        if (!signalValidator.isTradable(tradingStatus)) {
            reject(signalId, figi, direction, "trading_status not normal");
            return;
        }

        double price = toDouble(signal.getSuggestedPrice());
        double sl = toDouble(signal.getSuggestedSl());
        double tp = toDouble(signal.getSuggestedTp());
        if (sl <= 0 || tp <= 0) {
            reject(signalId, figi, direction, "no sl/tp in signal");
            return;
        }

        RiskCheckResult risk = riskManager.check(figi, price, sl, tradeRepository.lotSize(figi), deposit, 0.0);
        if (!risk.allowed()) {
            reject(signalId, figi, direction, risk.rejectReason());
            return;
        }
        int quantityLots = risk.quantityLots();
        OrderSide side = sideOf(signal.getSignal());
        // order_id детерминирован от signal_id: повтор после сбоя не создаст второй ордер (D3).
        UUID orderId = UUID.nameUUIDFromBytes(("order:" + signalId).getBytes(StandardCharsets.UTF_8));

        OrderResult result = executionService.placeOrder(
                new OrderRequest(figi, quantityLots, price, OrderType.LIMIT, side, orderId, signalId));

        UUID tradeId = UUID.randomUUID();
        // Первая запись до positions: positions.trade_id ссылается на trade_results (FK).
        tradeRepository.insertTrade(tradeId, signalId, figi, side.name(), quantityLots,
                orderId.toString(), result.exchangeOrderId(), result.executedPrice(), sl, tp,
                null, null, result.success() ? STATUS_OPEN : STATUS_REJECTED);

        if (!result.success()) {
            log.warn("Entry order rejected: signalId={}, figi={}, reason={}",
                    signalId, figi, result.rejectReason());
            tradeRepository.updateRejectReason(tradeId, result.rejectReason());
            deduplicator.markRejected(signalId, result.rejectReason());
            publishReject(tradeId, signalId, figi, direction, quantityLots, orderId.toString(),
                    result.rejectReason());
            metrics.orderRejected();
            return;
        }

        // SL/TP — best-effort: в песочнице SDK запрещает стоп-ордера, позиция закрывается AutoCloseService.
        // Объём стопов — фактически исполненный, с фолбэком на расчётный.
        int stopLots = result.executedLots() > 0 ? result.executedLots() : quantityLots;
        String slOrderId = placeStopLoss(figi, sl, stopLots, orderId);
        String tpOrderId = placeTakeProfit(figi, tp, stopLots, orderId);

        double executedPrice = result.executedPrice();
        tradeRepository.insertTrade(tradeId, signalId, figi, side.name(), quantityLots,
                orderId.toString(), result.exchangeOrderId(), executedPrice, sl, tp,
                slOrderId, tpOrderId, STATUS_OPEN);
        tradeRepository.upsertPosition(figi, quantityLots, executedPrice, tradeId);

        positionStore.put(new Position(figi, quantityLots, price, executedPrice, 0.0, Instant.now(),
                tradeId, sl, tp, slOrderId, tpOrderId));
        riskManager.onPositionOpened(figi, tradeId.toString());
        metrics.orderPlaced();
        metrics.positionsOpen(positionStore.count());

        publishOpened(tradeId, signalId, figi, direction, quantityLots, orderId.toString(),
                result.exchangeOrderId(), executedPrice);
    }

    private String placeStopLoss(String figi, double sl, int quantityLots, UUID orderId) {
        try {
            OrderResult result = executionService.placeStopLoss(figi, sl, quantityLots, orderId);
            if (!result.success()) {
                log.warn("Stop-loss not placed: figi={}, sl={}: {}", figi, sl, result.rejectReason());
                return null;
            }
            return result.exchangeOrderId();
        } catch (RuntimeException e) {
            log.warn("Stop-loss failed: figi={}, sl={}: {}", figi, sl, e.getMessage());
            return null;
        }
    }

    private String placeTakeProfit(String figi, double tp, int quantityLots, UUID orderId) {
        try {
            OrderResult result = executionService.placeTakeProfit(figi, tp, quantityLots, orderId);
            if (!result.success()) {
                log.warn("Take-profit not placed: figi={}, tp={}: {}", figi, tp, result.rejectReason());
                return null;
            }
            return result.exchangeOrderId();
        } catch (RuntimeException e) {
            log.warn("Take-profit failed: figi={}, tp={}: {}", figi, tp, e.getMessage());
            return null;
        }
    }

    private void reject(UUID signalId, String figi, TradeDirection direction, String reason) {
        log.warn("Signal rejected: signalId={}, figi={}, reason={}", signalId, figi, reason);
        deduplicator.markRejected(signalId, reason);
        publishReject(UUID.randomUUID(), signalId, figi, direction, 0, null, reason);
    }

    private void publishReject(UUID tradeId, UUID signalId, String figi, TradeDirection direction,
                               int quantityLots, String orderId, String reason) {
        // Сделки нет, но key топика обязателен: trade_id генерируется и для отказа.
        TradeResult.Builder event = TradeResult.newBuilder()
                .setTradeId(tradeId.toString())
                .setSignalId(signalId.toString())
                .setFigi(figi)
                .setEventType(TradeEventType.REJECTED)
                .setTs(now())
                .setDirection(direction)
                .setQuantityLots(quantityLots)
                .setRejectReason(reason == null ? "" : reason);
        if (orderId != null) {
            event.setOrderId(orderId);
        }
        publisher.publish(event.build());
    }

    private void publishOpened(UUID tradeId, UUID signalId, String figi, TradeDirection direction,
                               int quantityLots, String orderId, String exchangeOrderId, double price) {
        TradeResult.Builder event = TradeResult.newBuilder()
                .setTradeId(tradeId.toString())
                .setSignalId(signalId.toString())
                .setFigi(figi)
                .setEventType(TradeEventType.OPENED)
                .setTs(now())
                .setDirection(direction)
                .setQuantityLots(quantityLots)
                .setOrderId(orderId)
                .setPrice(toQuotation(price));
        if (exchangeOrderId != null) {
            event.setExchangeOrderId(exchangeOrderId);
        }
        publisher.publish(event.build());
    }

    private static TradeDirection directionOf(SignalType signalType) {
        return switch (signalType) {
            case BUY -> TradeDirection.TRADE_DIRECTION_BUY;
            case SELL -> TradeDirection.TRADE_DIRECTION_SELL;
            default -> TradeDirection.TRADE_DIRECTION_UNSPECIFIED;
        };
    }

    private static OrderSide sideOf(SignalType signalType) {
        return signalType == SignalType.BUY ? OrderSide.BUY : OrderSide.SELL;
    }

    private static double toDouble(Quotation quotation) {
        return quotation.getUnits() + quotation.getNano() / 1_000_000_000.0;
    }

    private static Quotation toQuotation(double value) {
        long units = (long) value;
        int nano = (int) Math.round((value - units) * 1_000_000_000.0);
        return Quotation.newBuilder().setUnits(units).setNano(nano).build();
    }

    private static Timestamp now() {
        Instant instant = Instant.now();
        return Timestamp.newBuilder()
                .setSeconds(instant.getEpochSecond())
                .setNanos(instant.getNano())
                .build();
    }
}
