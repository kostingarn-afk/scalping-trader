package ru.scalping.trader.risk;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import ru.scalping.trader.repository.TradeRepository;

import java.util.List;

/**
 * Периодическое обновление статистики по закрытым сделкам: оценка Келли и ожидаемая
 * доходность (EV).
 *
 * <p>Обе величины считаются по одной выборке, поэтому читаются одним запросом и обновляются
 * вместе: два отдельных опроса удвоили бы нагрузку на БД без пользы. Ошибка чтения оставляет
 * прежние значения и не останавливает контур.
 */
@Component
public class RiskStatsPoller {

    /** Сколько последних сделок участвует в оценках. */
    static final int SAMPLE_SIZE = 200;

    private static final Logger log = LoggerFactory.getLogger(RiskStatsPoller.class);

    private final RiskManager riskManager;
    private final TradeRepository tradeRepository;

    public RiskStatsPoller(RiskManager riskManager, TradeRepository tradeRepository) {
        this.riskManager = riskManager;
        this.tradeRepository = tradeRepository;
    }

    @Scheduled(cron = "${trader.poll-stats-cron:0 */5 * * * *}")
    public void poll() {
        try {
            List<Double> pnls = tradeRepository.recentClosedPnls(SAMPLE_SIZE);
            riskManager.updateKellyFraction(KellyEstimator.fraction(pnls).orElse(null));
            riskManager.updateExpectedValue(
                    EvEstimator.expectedValue(pnls, riskManager.config().evMinTrades()).orElse(null));
        } catch (RuntimeException e) {
            log.warn("Failed to refresh trade statistics, keeping previous values: {}",
                    e.getMessage());
        }
    }
}
