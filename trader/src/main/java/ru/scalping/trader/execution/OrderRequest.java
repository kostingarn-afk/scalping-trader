package ru.scalping.trader.execution;

import java.util.UUID;

/**
 * Запрос на выставление ордера.
 */
public record OrderRequest(
        String figi,
        int quantityLots,
        double price,
        OrderType type,
        UUID orderId,         // идемпотентный ключ
        UUID signalId         // сигнал-источник (null для SL/TP/timeout)
) {}
