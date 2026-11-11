package ru.scalping.trader.execution;

/**
 * Сервис исполнения ордеров через T-Invest API (задачи 4.5, 4.6).
 *
 * <p>Абстракция над {@code OrdersService.PostOrder} и {@code StopOrdersService.PostStopOrder}.
 * Реализация привязана к SDK и подставляется через профиль.
 */
public interface OrderExecutionService {

    /**
     * Выставление ордера на вход (LIMIT/BESTPRICE). MARKET запрещён — {@link UnsupportedOperationException}.
     */
    OrderResult placeOrder(OrderRequest request);

    /**
     * Выставление биржевого Stop-Loss через {@code StopOrdersService.PostStopOrder} (задача 4.6).
     */
    OrderResult placeStopLoss(String figi, double slPrice, int quantityLots, UUID orderId);

    /**
     * Выставление биржевого Take-Profit через {@code StopOrdersService.PostStopOrder} (задача 4.6).
     */
    OrderResult placeTakeProfit(String figi, double tpPrice, int quantityLots, UUID orderId);

    /**
     * Emergency-close market-ордером (PnL-CB). MARKET допустим только здесь.
     */
    OrderResult emergencyClose(String figi, int quantityLots, UUID orderId);
}
