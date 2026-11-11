package ru.scalping.trader.position;

import java.time.Instant;
import java.util.UUID;

/**
 * Открытая позиция (in-memory состояние, синхронизируется с БД и биржей).
 */
public record Position(
        String figi,
        int quantityLots,
        double avgPrice,
        double currentPrice,
        double unrealizedPnl,
        Instant openedAt,
        UUID tradeId,
        double slPrice,
        double tpPrice,
        String slOrderId,
        String tpOrderId
) {}
