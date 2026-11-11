package ru.scalping.trader.execution;

/**
 * Результат выставления ордера.
 */
public record OrderResult(
        boolean success,
        String exchangeOrderId,
        String rejectReason,
        double executedPrice,
        int executedLots
) {
    public static OrderResult filled(String exchangeOrderId, double price, int lots) {
        return new OrderResult(true, exchangeOrderId, null, price, lots);
    }

    public static OrderResult rejected(String reason) {
        return new OrderResult(false, null, reason, 0, 0);
    }
}
