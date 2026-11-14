package ru.scalping.trader.execution;

import java.util.UUID;

/**
 * Запрос на выставление ордера.
 *
 * @param side направление входа; {@code null} допустим только для SL/TP/timeout-запросов,
 *             для {@link OrderExecutionService#placeOrder(OrderRequest)} обязателен
 */
public record OrderRequest(
        String figi,
        int quantityLots,
        double price,
        OrderType type,
        OrderSide side,
        UUID orderId,         // идемпотентный ключ
        UUID signalId         // сигнал-источник (null для SL/TP/timeout)
) {}
