package ru.scalping.trader.signal;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.dao.DataAccessResourceFailureException;
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
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class TradeOrchestratorTest {

    private static final String FIGI = "BBG004730N88";
    private static final double DEPOSIT = 200_000;
    private static final int QUANTITY_LOTS = 10;
    private static final UUID SIGNAL_ID = UUID.fromString("11111111-2222-3333-4444-555555555555");
    private static final int NORMAL_TRADING = 2;
    private static final int BREAK_IN_TRADING = 7;

    private final SignalProcessor signalProcessor = mock(SignalProcessor.class);
    private final SignalDeduplicator deduplicator = mock(SignalDeduplicator.class);
    private final RiskManager riskManager = mock(RiskManager.class);
    private final TradeRepository tradeRepository = mock(TradeRepository.class);
    private final OrderExecutionService executionService = mock(OrderExecutionService.class);
    private final ExchangeStateProvider exchangeStateProvider = mock(ExchangeStateProvider.class);
    private final TradeResultPublisher publisher = mock(TradeResultPublisher.class);
    private final PositionStore positionStore = mock(PositionStore.class);
    private final TraderMetrics metrics = mock(TraderMetrics.class);

    private TradeOrchestrator orchestrator;

    @BeforeEach
    void setUp() {
        orchestrator = new TradeOrchestrator(signalProcessor, new SignalValidator(), deduplicator,
                riskManager, tradeRepository, executionService, exchangeStateProvider, publisher,
                positionStore, metrics, DEPOSIT);
        when(signalProcessor.validate(any()))
                .thenReturn(SignalProcessor.ValidationResult.ok(SIGNAL_ID));
        when(exchangeStateProvider.tradingStatus(FIGI)).thenReturn(NORMAL_TRADING);
        when(tradeRepository.lotSize(FIGI)).thenReturn(1);
        when(riskManager.check(anyString(), anyDouble(), anyDouble(), anyInt(), anyDouble(), anyDouble()))
                .thenReturn(RiskCheckResult.allowed(QUANTITY_LOTS));
    }

    @Test
    void handle_duplicateSignal_noOrderPlaced() {
        when(signalProcessor.validate(any()))
                .thenReturn(SignalProcessor.ValidationResult.duplicate(SIGNAL_ID));

        orchestrator.handle(buySignal(SIGNAL_ID));

        verifyNoInteractions(executionService, publisher, tradeRepository);
    }

    @Test
    void handle_expiredSignal_publishesRejected() {
        when(signalProcessor.validate(any()))
                .thenReturn(SignalProcessor.ValidationResult.rejected(SIGNAL_ID, "expired"));

        orchestrator.handle(buySignal(SIGNAL_ID));

        TradeResult event = capturedReject();
        assertThat(event.getRejectReason()).isEqualTo("expired");
        assertThat(event.getSignalId()).isEqualTo(SIGNAL_ID.toString());
        verifyNoInteractions(executionService, tradeRepository);
    }

    @Test
    void handle_holdSignal_noOrderPlaced() {
        when(signalProcessor.validate(any()))
                .thenReturn(SignalProcessor.ValidationResult.rejected(SIGNAL_ID, "hold signal"));

        orchestrator.handle(holdSignal(SIGNAL_ID));

        verify(deduplicator).markRejected(SIGNAL_ID, "hold signal");
        // HOLD — не попытка сделки: событие в Kafka не публикуется
        verifyNoInteractions(executionService, tradeRepository, publisher);
    }

    @Test
    void handle_databaseUnavailable_throwsRetryableException() {
        when(tradeRepository.lotSize(FIGI)).thenThrow(new DataAccessResourceFailureException("db down"));

        assertThatThrownBy(() -> orchestrator.handle(buySignal(SIGNAL_ID)))
                .isInstanceOf(RetryableSignalException.class);
        verifyNoInteractions(executionService, publisher);
    }

    @Test
    void handle_exchangeReject_persistsRejectReason() {
        when(executionService.placeOrder(any())).thenReturn(OrderResult.rejected("insufficient funds"));

        orchestrator.handle(buySignal(SIGNAL_ID));

        verify(tradeRepository).updateRejectReason(any(), eq("insufficient funds"));
    }

    @Test
    void handle_partiallyFilledExecution_stopsUseExecutedLots() {
        when(executionService.placeOrder(any())).thenReturn(OrderResult.filled("ex-1", 100.5, 3));
        when(executionService.placeStopLoss(anyString(), anyDouble(), anyInt(), any()))
                .thenReturn(OrderResult.filled("sl-1", 99.0, 3));

        orchestrator.handle(buySignal(SIGNAL_ID));

        verify(executionService).placeStopLoss(eq(FIGI), anyDouble(), eq(3), any());
        verify(executionService).placeTakeProfit(eq(FIGI), anyDouble(), eq(3), any());
    }

    @Test
    void handle_tradingStatusNotNormal_rejectsSignal() {
        when(exchangeStateProvider.tradingStatus(FIGI)).thenReturn(BREAK_IN_TRADING);

        orchestrator.handle(buySignal(SIGNAL_ID));

        verify(deduplicator).markRejected(SIGNAL_ID, "trading_status not normal");
        TradeResult event = capturedReject();
        assertThat(event.getRejectReason()).isEqualTo("trading_status not normal");
        verifyNoInteractions(executionService, tradeRepository);
    }

    @Test
    void handle_riskRejected_rejectsSignal() {
        when(riskManager.check(anyString(), anyDouble(), anyDouble(), anyInt(), anyDouble(), anyDouble()))
                .thenReturn(RiskCheckResult.rejected("max_open_positions reached"));

        orchestrator.handle(buySignal(SIGNAL_ID));

        verify(deduplicator).markRejected(SIGNAL_ID, "max_open_positions reached");
        assertThat(capturedReject().getRejectReason()).isEqualTo("max_open_positions reached");
        verifyNoInteractions(executionService);
        verify(tradeRepository, never()).insertTrade(any(), any(), any(), any(), anyInt(),
                any(), any(), anyDouble(), anyDouble(), anyDouble(), any(), any(), any());
    }

    @Test
    void handle_successfulExecution_opensPositionAndPublishesOpened() {
        when(executionService.placeOrder(any())).thenReturn(OrderResult.filled("ex-1", 100.5, QUANTITY_LOTS));
        when(executionService.placeStopLoss(eq(FIGI), anyDouble(), eq(QUANTITY_LOTS), any()))
                .thenReturn(OrderResult.filled("sl-1", 99.0, QUANTITY_LOTS));
        when(executionService.placeTakeProfit(eq(FIGI), anyDouble(), eq(QUANTITY_LOTS), any()))
                .thenReturn(OrderResult.filled("tp-1", 102.0, QUANTITY_LOTS));

        orchestrator.handle(buySignal(SIGNAL_ID));

        ArgumentCaptor<OrderRequest> request = ArgumentCaptor.forClass(OrderRequest.class);
        verify(executionService).placeOrder(request.capture());
        UUID expectedOrderId = UUID.nameUUIDFromBytes(("order:" + SIGNAL_ID).getBytes(StandardCharsets.UTF_8));
        assertThat(request.getValue().figi()).isEqualTo(FIGI);
        assertThat(request.getValue().quantityLots()).isEqualTo(QUANTITY_LOTS);
        assertThat(request.getValue().price()).isEqualTo(100.0);
        assertThat(request.getValue().type()).isEqualTo(OrderType.LIMIT);
        assertThat(request.getValue().side()).isEqualTo(OrderSide.BUY);
        assertThat(request.getValue().orderId()).isEqualTo(expectedOrderId);
        assertThat(request.getValue().signalId()).isEqualTo(SIGNAL_ID);

        verify(tradeRepository, times(2)).insertTrade(any(), any(), anyString(), anyString(), anyInt(),
                anyString(), anyString(), anyDouble(), anyDouble(), anyDouble(), any(), any(), anyString());
        verify(tradeRepository).insertTrade(any(), any(), eq(FIGI), eq("BUY"), eq(QUANTITY_LOTS),
                eq(expectedOrderId.toString()), eq("ex-1"), eq(100.5), eq(99.0), eq(102.0),
                eq("sl-1"), eq("tp-1"), eq("open"));

        ArgumentCaptor<UUID> tradeId = ArgumentCaptor.forClass(UUID.class);
        verify(tradeRepository).upsertPosition(eq(FIGI), eq(QUANTITY_LOTS), eq(100.5), tradeId.capture());

        ArgumentCaptor<Position> position = ArgumentCaptor.forClass(Position.class);
        verify(positionStore).put(position.capture());
        assertThat(position.getValue().tradeId()).isEqualTo(tradeId.getValue());
        assertThat(position.getValue().slOrderId()).isEqualTo("sl-1");
        assertThat(position.getValue().tpOrderId()).isEqualTo("tp-1");

        verify(riskManager).onPositionOpened(FIGI, tradeId.getValue().toString());
        verify(metrics).orderPlaced();

        ArgumentCaptor<TradeResult> event = ArgumentCaptor.forClass(TradeResult.class);
        verify(publisher).publish(event.capture());
        assertThat(event.getValue().getEventType()).isEqualTo(TradeEventType.OPENED);
        assertThat(event.getValue().getTradeId()).isEqualTo(tradeId.getValue().toString());
        assertThat(event.getValue().getSignalId()).isEqualTo(SIGNAL_ID.toString());
        assertThat(event.getValue().getDirection()).isEqualTo(TradeDirection.TRADE_DIRECTION_BUY);
        assertThat(event.getValue().getQuantityLots()).isEqualTo(QUANTITY_LOTS);
        assertThat(event.getValue().getPrice().getUnits()).isEqualTo(100);
        assertThat(event.getValue().getPrice().getNano()).isEqualTo(500_000_000);
    }

    @Test
    void handle_exchangeReject_publishesRejected() {
        when(executionService.placeOrder(any())).thenReturn(OrderResult.rejected("insufficient funds"));

        orchestrator.handle(buySignal(SIGNAL_ID));

        verify(tradeRepository).insertTrade(any(), any(), eq(FIGI), eq("BUY"), eq(QUANTITY_LOTS),
                anyString(), isNull(), eq(0.0), eq(99.0), eq(102.0), isNull(), isNull(), eq("rejected"));
        verify(tradeRepository, never()).upsertPosition(anyString(), anyInt(), anyDouble(), any());
        verify(deduplicator).markRejected(SIGNAL_ID, "insufficient funds");
        verify(metrics).orderRejected();
        verify(positionStore, never()).put(any());
        assertThat(capturedReject().getRejectReason()).isEqualTo("insufficient funds");
    }

    @Test
    void handle_invalidSignalId_skipsSignal() {
        orchestrator.handle(TradingSignal.newBuilder()
                .setSignalId("not-a-uuid")
                .setFigi(FIGI)
                .setSignal(SignalType.BUY)
                .build());

        verifyNoInteractions(executionService, publisher, signalProcessor);
    }

    @Test
    void handle_stopLossRejected_stillOpensPosition() {
        when(executionService.placeOrder(any())).thenReturn(OrderResult.filled("ex-1", 100.5, QUANTITY_LOTS));
        when(executionService.placeStopLoss(eq(FIGI), anyDouble(), eq(QUANTITY_LOTS), any()))
                .thenThrow(new IllegalStateException("sandbox mode violation"));
        when(executionService.placeTakeProfit(eq(FIGI), anyDouble(), eq(QUANTITY_LOTS), any()))
                .thenReturn(OrderResult.rejected("sandbox mode violation"));

        orchestrator.handle(buySignal(SIGNAL_ID));

        // Обе записи идентичны: SL/TP не выставлены, статус open.
        verify(tradeRepository, times(2)).insertTrade(any(), any(), eq(FIGI), eq("BUY"), eq(QUANTITY_LOTS),
                anyString(), eq("ex-1"), eq(100.5), eq(99.0), eq(102.0), isNull(), isNull(), eq("open"));
        verify(positionStore).put(any(Position.class));
        verify(publisher).publish(argThat(e -> e.getEventType() == TradeEventType.OPENED));
    }

    private TradeResult capturedReject() {
        ArgumentCaptor<TradeResult> event = ArgumentCaptor.forClass(TradeResult.class);
        verify(publisher).publish(event.capture());
        assertThat(event.getValue().getEventType()).isEqualTo(TradeEventType.REJECTED);
        return event.getValue();
    }

    private static TradingSignal buySignal(UUID signalId) {
        return signal(signalId, SignalType.BUY);
    }

    private static TradingSignal holdSignal(UUID signalId) {
        return signal(signalId, SignalType.HOLD);
    }

    private static TradingSignal signal(UUID signalId, SignalType type) {
        return TradingSignal.newBuilder()
                .setSignalId(signalId.toString())
                .setFigi(FIGI)
                .setSignal(type)
                .setSuggestedPrice(quotation(100.0))
                .setSuggestedSl(quotation(99.0))
                .setSuggestedTp(quotation(102.0))
                .build();
    }

    private static Quotation quotation(double value) {
        long units = (long) value;
        int nano = (int) Math.round((value - units) * 1_000_000_000.0);
        return Quotation.newBuilder().setUnits(units).setNano(nano).build();
    }
}
