package ru.scalping.trader.risk;

/**
 * Результат риск-проверки сигнала.
 *
 * @param allowed      прошёл ли проверку
 * @param rejectReason причина отказа (null если allowed)
 * @param quantityLots рассчитанный объём в лотах (0 если not allowed)
 */
public record RiskCheckResult(boolean allowed, String rejectReason, int quantityLots) {

    public static RiskCheckResult allowed(int quantityLots) {
        return new RiskCheckResult(true, null, quantityLots);
    }

    public static RiskCheckResult rejected(String reason) {
        return new RiskCheckResult(false, reason, 0);
    }
}
