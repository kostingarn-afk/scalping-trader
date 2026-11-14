package ru.scalping.trader.execution;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import ru.scalping.trader.config.TinkoffConfig;
import ru.tinkoff.piapi.contract.v1.MoneyValue;
import ru.tinkoff.piapi.contract.v1.OrderDirection;
import ru.tinkoff.piapi.contract.v1.OrderExecutionReportStatus;
import ru.tinkoff.piapi.contract.v1.PostOrderResponse;
import ru.tinkoff.piapi.contract.v1.Quotation;
import ru.tinkoff.piapi.contract.v1.StopOrderDirection;
import ru.tinkoff.piapi.contract.v1.StopOrderType;
import ru.tinkoff.piapi.core.InvestApi;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.UUID;

import static ru.tinkoff.piapi.contract.v1.OrderType.ORDER_TYPE_BESTPRICE;
import static ru.tinkoff.piapi.contract.v1.OrderType.ORDER_TYPE_LIMIT;
import static ru.tinkoff.piapi.contract.v1.OrderType.ORDER_TYPE_MARKET;

/**
 * Исполнение ордеров через T-Invest SDK (задачи 4.5, 4.6).
 *
 * <p>Вход — LIMIT/BESTPRICE, защита — биржевые стоп-ордера, MARKET — только emergency-close (D4).
 * Направление ордера не входит в {@link OrderRequest}, поэтому рабочий API адаптера —
 * {@link #placeBuyOrder(OrderRequest)} и {@link #placeSellOrder(OrderRequest)}; интерфейсный
 * {@link #placeOrder(OrderRequest)} без направления не используется (см. javadoc метода).
 */
@Component
public class TinkoffOrderExecutionService implements OrderExecutionService {

    private static final Logger log = LoggerFactory.getLogger(TinkoffOrderExecutionService.class);

    private static final String ORDER_COMMENT = "scalping-trader";
    private static final int NANO_SCALE = 9;

    private final InvestApi investApi;
    private final String accountId;

    public TinkoffOrderExecutionService(InvestApi investApi,
                                        @Qualifier(TinkoffConfig.TRADING_ACCOUNT_ID_BEAN) String accountId) {
        this.investApi = investApi;
        this.accountId = accountId;
    }

    /**
     * {@inheritDoc}
     *
     * <p>Направление берётся из {@link OrderRequest#side()}: в {@code OrderRequest} нет
     * типа сигнала, а выводить направление внутри адаптера — значит дублировать бизнес-логику.
     */
    @Override
    public OrderResult placeOrder(OrderRequest request) {
        OrderSide side = request.side();
        if (side == null) {
            throw new UnsupportedOperationException(
                    "OrderRequest.side is required: OrderSide.BUY or OrderSide.SELL");
        }
        return placeOrder(request,
                side == OrderSide.BUY
                        ? OrderDirection.ORDER_DIRECTION_BUY
                        : OrderDirection.ORDER_DIRECTION_SELL);
    }

    /**
     * Ордер на покупку (вход в long-позицию).
     */
    public OrderResult placeBuyOrder(OrderRequest request) {
        return placeOrder(request, OrderDirection.ORDER_DIRECTION_BUY);
    }

    /**
     * Ордер на продажу (закрытие long-позиции).
     */
    public OrderResult placeSellOrder(OrderRequest request) {
        return placeOrder(request, OrderDirection.ORDER_DIRECTION_SELL);
    }

    @Override
    public OrderResult placeStopLoss(String figi, double slPrice, int quantityLots, UUID orderId) {
        return placeStopOrder(figi, slPrice, quantityLots, StopOrderType.STOP_ORDER_TYPE_STOP_LOSS, orderId);
    }

    @Override
    public OrderResult placeTakeProfit(String figi, double tpPrice, int quantityLots, UUID orderId) {
        return placeStopOrder(figi, tpPrice, quantityLots, StopOrderType.STOP_ORDER_TYPE_TAKE_PROFIT, orderId);
    }

    @Override
    public OrderResult emergencyClose(String figi, int quantityLots, UUID orderId) {
        log.warn("Emergency close (MARKET): figi={}, lots={}, orderId={}", figi, quantityLots, orderId);
        try {
            PostOrderResponse response = postOrder(figi, quantityLots, zeroQuotation(),
                    OrderDirection.ORDER_DIRECTION_SELL, orderId, ORDER_TYPE_MARKET);
            return toResult(response, 0.0);
        } catch (RuntimeException e) {
            log.error("Emergency close failed: figi={}, lots={}, orderId={}: {}",
                    figi, quantityLots, orderId, describe(e));
            return OrderResult.rejected(describe(e));
        }
    }

    private OrderResult placeOrder(OrderRequest request, OrderDirection direction) {
        if (request.type() == OrderType.MARKET) {
            throw new UnsupportedOperationException("MARKET orders are not allowed for entry (D4)");
        }
        if (request.type() != OrderType.LIMIT && request.type() != OrderType.BESTPRICE) {
            throw new UnsupportedOperationException(
                    "Entry supports LIMIT/BESTPRICE only, got " + request.type());
        }
        try {
            PostOrderResponse response = postOrder(request.figi(), request.quantityLots(),
                    toQuotation(request.price()), direction, request.orderId(),
                    toSdkOrderType(request.type()));
            OrderResult result = toResult(response, request.price());
            log.info("Entry order {}: figi={}, lots={}, price={}, orderId={}, success={}",
                    direction, request.figi(), request.quantityLots(), request.price(),
                    request.orderId(), result.success());
            return result;
        } catch (RuntimeException e) {
            log.error("Entry order failed: figi={}, lots={}, orderId={}: {}",
                    request.figi(), request.quantityLots(), request.orderId(), describe(e));
            return OrderResult.rejected(describe(e));
        }
    }

    private OrderResult placeStopOrder(String figi, double stopPrice, int quantityLots,
                                       StopOrderType type, UUID orderId) {
        log.info("Placing {}: figi={}, lots={}, stopPrice={}, orderId={}",
                type, figi, quantityLots, stopPrice, orderId);
        try {
            // Стоп-ордер закрывает long-позицию, поэтому направление всегда SELL.
            // Цена срабатывания и лимитная цена совпадают: защитный ордер должен исполниться
            // не хуже цены срабатывания.
            Quotation price = toQuotation(stopPrice);
            String stopOrderId = investApi.getStopOrdersService().postStopOrderGoodTillCancelSync(
                    figi, quantityLots, price, price,
                    StopOrderDirection.STOP_ORDER_DIRECTION_SELL, accountId, type);
            return OrderResult.filled(stopOrderId, stopPrice, quantityLots);
        } catch (RuntimeException e) {
            log.error("Stop order failed: type={}, figi={}, lots={}: {}", type, figi, quantityLots, describe(e));
            return OrderResult.rejected(describe(e));
        }
    }

    /**
     * Песочница не поддерживает {@code postLimitOrderSync} и стоп-ордера,
     * поэтому тип ордера передаётся явно через {@code postOrderSync}.
     */
    private PostOrderResponse postOrder(String figi, int quantityLots, Quotation price,
                                        OrderDirection direction, UUID orderId,
                                        ru.tinkoff.piapi.contract.v1.OrderType sdkOrderType) {
        String sdkOrderId = orderId == null ? UUID.randomUUID().toString() : orderId.toString();
        if (investApi.isSandboxMode()) {
            return investApi.getSandboxService().postOrderSync(accountId, quantityLots, price,
                    direction, sdkOrderId, sdkOrderType, ORDER_COMMENT);
        }
        return investApi.getOrdersService().postOrderSync(accountId, quantityLots, price,
                direction, sdkOrderId, sdkOrderType, ORDER_COMMENT);
    }

    private static OrderResult toResult(PostOrderResponse response, double requestedPrice) {
        if (response.getExecutionReportStatus() == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL) {
            double executedPrice = response.hasExecutedOrderPrice()
                    ? toDouble(response.getExecutedOrderPrice())
                    : requestedPrice;
            return OrderResult.filled(response.getOrderId(), executedPrice, (int) response.getLotsExecuted());
        }
        String reason = response.getMessage() == null || response.getMessage().isBlank()
                ? "order not filled: status=" + response.getExecutionReportStatus()
                : response.getMessage();
        return OrderResult.rejected(reason);
    }

    private static ru.tinkoff.piapi.contract.v1.OrderType toSdkOrderType(OrderType type) {
        return switch (type) {
            case LIMIT -> ORDER_TYPE_LIMIT;
            case BESTPRICE -> ORDER_TYPE_BESTPRICE;
            case MARKET -> ORDER_TYPE_MARKET;
            case STOP_LOSS, TAKE_PROFIT -> throw new UnsupportedOperationException(
                    "Stop orders are placed via placeStopLoss/placeTakeProfit");
        };
    }

    private static Quotation toQuotation(double value) {
        BigDecimal scaled = BigDecimal.valueOf(value).setScale(NANO_SCALE, RoundingMode.HALF_UP);
        long units = scaled.longValue();
        int nano = scaled.subtract(BigDecimal.valueOf(units)).movePointRight(NANO_SCALE).intValue();
        return Quotation.newBuilder().setUnits(units).setNano(nano).build();
    }

    private static Quotation zeroQuotation() {
        return Quotation.getDefaultInstance();
    }

    private static double toDouble(MoneyValue money) {
        return money.getUnits() + money.getNano() / 1_000_000_000.0;
    }

    private static String describe(RuntimeException e) {
        String message = e.getMessage();
        return message == null || message.isBlank() ? e.getClass().getSimpleName() : message;
    }
}
