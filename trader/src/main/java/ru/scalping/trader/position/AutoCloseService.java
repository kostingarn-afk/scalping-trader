package ru.scalping.trader.position;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import ru.scalping.trader.execution.OrderExecutionService;
import ru.scalping.trader.execution.OrderResult;
import ru.scalping.trader.metrics.TraderMetrics;
import ru.scalping.trader.publish.TradeResultPublisher;
import ru.scalping.trader.risk.RiskConfig;
import ru.scalping.trader.risk.RiskManager;
import ru.scalping.proto.TradeEventType;
import ru.scalping.proto.TradeResult;

import java.time.Duration;
import java.time.Instant;
import java.util.UUID;

/**
 * Auto-close по таймауту удержания (задача 4.8, spec trade-execution).
 *
 * <p>Позиции, удерживаемые дольше {@code max_holding_time} (10 минут),
 * закрываются ордером, публикуется событие TIMEOUT_CLOSED.
 */
@Component
public class AutoCloseService {

    private static final Logger log = LoggerFactory.getLogger(AutoCloseService.class);

    private final PositionStore positionStore;
    private final OrderExecutionService executionService;
    private final TradeResultPublisher publisher;
    private final RiskManager riskManager;
    private final TraderMetrics metrics;

    public AutoCloseService(PositionStore positionStore,
                            OrderExecutionService executionService,
                            TradeResultPublisher publisher,
                            RiskManager riskManager,
                            TraderMetrics metrics) {
        this.positionStore = positionStore;
        this.executionService = executionService;
        this.publisher = publisher;
        this.riskManager = riskManager;
        this.metrics = metrics;
    }

    /** Проверка каждые 30 секунд. */
    @Scheduled(fixedDelay = 30_000)
    public void checkStalePositions() {
        RiskConfig cfg = riskManager.config();
        Duration maxHolding = Duration.ofMinutes(cfg.maxHoldingTimeMinutes());
        for (Position p : positionStore.findStale(maxHolding)) {
            closeOnTimeout(p);
        }
    }

    void closeOnTimeout(Position p) {
        log.info("Auto-closing stale position: figi={}, openedAt={}", p.figi(), p.openedAt());
        UUID orderId = UUID.randomUUID();
        OrderResult result = executionService.emergencyClose(p.figi(), Math.abs(p.quantityLots()), orderId);
        if (result.success()) {
            positionStore.remove(p.figi());
            TradeResult event = TradeResult.newBuilder()
                    .setTradeId(p.tradeId().toString())
                    .setFigi(p.figi())
                    .setEventType(TradeEventType.TIMEOUT_CLOSED)
                    .setTs(com.google.protobuf.Timestamp.newBuilder().setSeconds(Instant.now().getEpochSecond()).build())
                    .setOrderId(orderId.toString())
                    .setExchangeOrderId(result.exchangeOrderId())
                    .setQuantityLots(p.quantityLots())
                    .build();
            publisher.publish(event);
        } else {
            log.error("Failed to auto-close position figi={}: {}", p.figi(), result.rejectReason());
            metrics.orderRejected();
        }
    }
}
