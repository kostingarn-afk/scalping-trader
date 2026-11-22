package ru.scalping.trader.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Репозиторий для trade_results и positions (задача 4.12).
 */
@Component
public class TradeRepository {

    private final JdbcTemplate jdbc;

    public TradeRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void insertTrade(UUID tradeId, UUID signalId, String figi, String direction,
                            int quantityLots, String orderId, String exchangeOrderId,
                            double priceEntry, double slPrice, double tpPrice,
                            String slOrderId, String tpOrderId, String status) {
        jdbc.update("""
            INSERT INTO trade_results (
                trade_id, signal_id, instrument_id, direction, quantity_lots,
                order_id, exchange_order_id, price_entry, sl_price, tp_price,
                sl_order_id, tp_order_id, status, opened_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,now())
            ON CONFLICT (trade_id) DO UPDATE SET
                sl_order_id = EXCLUDED.sl_order_id,
                tp_order_id = EXCLUDED.tp_order_id,
                status = EXCLUDED.status
            """,
            tradeId, signalId, figi, direction, quantityLots,
            orderId, exchangeOrderId, priceEntry, slPrice, tpPrice,
            slOrderId, tpOrderId, status
        );
    }

    public void updateTradeOnClose(UUID tradeId, double priceExit, double pnl, double commission, String status) {
        jdbc.update("""
            UPDATE trade_results SET price_exit=?, pnl=?, commission=?, status=?, closed_at=now()
            WHERE trade_id=?
            """,
            priceExit, pnl, commission, status, tradeId
        );
    }

    public void upsertPosition(String figi, int quantityLots, double avgPrice, UUID tradeId) {
        jdbc.update("""
            INSERT INTO positions (instrument_id, quantity_lots, avg_price, trade_id, opened_at, updated_at)
            VALUES (?,?,?,?,now(),now())
            ON CONFLICT (instrument_id) DO UPDATE SET
                quantity_lots = EXCLUDED.quantity_lots,
                avg_price = EXCLUDED.avg_price,
                trade_id = EXCLUDED.trade_id,
                updated_at = now()
            """,
            figi, quantityLots, avgPrice, tradeId
        );
    }

    public void closePosition(String figi) {
        jdbc.update("DELETE FROM positions WHERE instrument_id=?", figi);
    }

    /**
     * Причина отказа биржи. {@link #insertTrade} не принимает {@code reject_reason},
     * а менять его сигнатуру нельзя — причина пишется отдельным UPDATE.
     */
    public void updateRejectReason(UUID tradeId, String reason) {
        jdbc.update("UPDATE trade_results SET reject_reason = ? WHERE trade_id = ?",
                truncate(reason, 256), tradeId);
    }

    /** Длина колонки {@code reject_reason} — VARCHAR(256). */
    private static String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }

    public Map<String, String> loadRiskConfig() {
        return jdbc.query("SELECT key, value FROM risk_config",
                rs -> {
                    java.util.Map<String, String> m = new java.util.HashMap<>();
                    while (rs.next()) m.put(rs.getString("key"), rs.getString("value"));
                    return m;
                });
    }

    /**
     * PnL последних закрытых сделок для оценки Келли (шаг 1.7).
     *
     * <p>Закрытые — исходы {@code TradeResultPublisher}: CLOSED, SL_HIT, TP_HIT, TIMEOUT_CLOSED.
     * Открытые и отклонённые не входят: результата у них нет.
     */
    public List<Double> recentClosedPnls(int limit) {
        return jdbc.query("""
                        SELECT pnl FROM trade_results
                        WHERE pnl IS NOT NULL
                          AND status IN ('CLOSED', 'SL_HIT', 'TP_HIT', 'TIMEOUT_CLOSED')
                        ORDER BY closed_at DESC NULLS LAST
                        LIMIT ?
                        """,
                (rs, rowNum) -> rs.getDouble("pnl"),
                limit);
    }

    /**
     * Лотность инструмента из справочника (владелец записи — Collector).
     *
     * @return {@code instruments.lot} либо {@code 1}, если записи нет или значение не задано
     */
    public int lotSize(String figi) {
        List<Integer> lots = jdbc.query("SELECT lot FROM instruments WHERE figi = ?",
                (rs, rowNum) -> rs.getObject("lot", Integer.class),
                figi);
        if (lots.isEmpty() || lots.get(0) == null || lots.get(0) < 1) {
            return 1;
        }
        return lots.get(0);
    }
}
