package ru.scalping.trader.risk;

import java.util.List;
import java.util.Optional;

/**
 * Оценка критерия Келли по закрытым сделкам: {@code f = p − (1−p)/b}, где
 * {@code p} — доля прибыльных сделок, {@code b} — отношение среднего выигрыша к среднему
 * проигрышу.
 *
 * <p>Доля возвращается как доля капитала (может быть отрицательной — преимущества нет).
 * Меньше {@link #MIN_TRADES} сделок — оценки нет: на коротком ряду {@code p} доминируется шумом.
 */
public final class KellyEstimator {

    /** Ниже этого числа сделок оценка не возвращается. */
    public static final int MIN_TRADES = 20;

    private KellyEstimator() {
    }

    public static Optional<Double> fraction(List<Double> pnls) {
        if (pnls == null) {
            return Optional.empty();
        }
        int wins = 0;
        int losses = 0;
        double winSum = 0.0;
        double lossSum = 0.0;
        for (Double pnl : pnls) {
            if (pnl == null || !Double.isFinite(pnl) || pnl == 0.0) {
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
        if (wins + losses < MIN_TRADES || wins == 0 || losses == 0) {
            // Односторонний исход не даёт отношения b: считать «все выигрыши» преимуществом
            // значило бы вернуть полный Келли на выборке без проигрышей.
            return Optional.empty();
        }
        double p = (double) wins / (wins + losses);
        double avgWin = winSum / wins;
        double avgLoss = lossSum / losses;
        if (avgLoss <= 0 || avgWin <= 0) {
            return Optional.empty();
        }
        double b = avgWin / avgLoss;
        return Optional.of(p - (1.0 - p) / b);
    }
}
