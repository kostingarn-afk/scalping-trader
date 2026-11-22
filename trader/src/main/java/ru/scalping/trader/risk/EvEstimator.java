package ru.scalping.trader.risk;

import java.util.List;
import java.util.Optional;

/**
 * Ожидаемая доходность сделки по закрытым сделкам: {@code EV = p·avgWin − (1−p)·avgLoss}.
 *
 * <p>{@code p} — доля прибыльных сделок, {@code avgWin}/{@code avgLoss} — средние модули PnL.
 * Комиссия уже вычтена в {@code pnl} ({@code trade_results}), издержки исполнения — тоже:
 * они входят в цены входа и выхода. Поэтому отдельно они не вычитаются, иначе те же деньги
 * снимались бы дважды.
 *
 * <p>Меньше {@code minTrades} сделок — оценки нет: на коротком ряду доля прибыльных
 * доминируется шумом. Односторонняя выборка (нет проигрышей или нет прибылей) тоже не оценка:
 * она говорит не о преимуществе, а об отсутствии противоположных исходов в выборке.
 */
public final class EvEstimator {

    private EvEstimator() {
    }

    public static Optional<Double> expectedValue(List<Double> pnls, int minTrades) {
        if (pnls == null || minTrades < 1) {
            return Optional.empty();
        }
        int wins = 0;
        int losses = 0;
        double winSum = 0.0;
        double lossSum = 0.0;
        for (Double pnl : pnls) {
            if (pnl == null || !Double.isFinite(pnl)) {
                continue;
            }
            if (pnl > 0) {
                wins++;
                winSum += pnl;
            } else {
                losses++;
                lossSum -= pnl;
            }
        }
        if (wins + losses < minTrades || wins == 0 || losses == 0) {
            return Optional.empty();
        }
        double p = (double) wins / (wins + losses);
        double avgWin = winSum / wins;
        double avgLoss = lossSum / losses;
        return Optional.of(p * avgWin - (1.0 - p) * avgLoss);
    }
}
