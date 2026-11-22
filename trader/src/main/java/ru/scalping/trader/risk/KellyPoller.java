package ru.scalping.trader.risk;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import ru.scalping.trader.repository.TradeRepository;

/**
 * Периодическое обновление оценки Келли по закрытым сделкам.
 *
 * <p>Отдельно от {@link RiskConfigPoller}: другой источник (статистика сделок, а не конфиг) и
 * другая частота. Ошибка чтения оставляет прежнюю оценку и не останавливает контур.
 */
@Component
public class KellyPoller {

    /** Сколько последних сделок участвует в оценке. */
    static final int SAMPLE_SIZE = 200;

    private static final Logger log = LoggerFactory.getLogger(KellyPoller.class);

    private final RiskManager riskManager;
    private final TradeRepository tradeRepository;

    public KellyPoller(RiskManager riskManager, TradeRepository tradeRepository) {
        this.riskManager = riskManager;
        this.tradeRepository = tradeRepository;
    }

    @Scheduled(cron = "${trader.poll-kelly-cron:0 */5 * * * *}")
    public void poll() {
        try {
            riskManager.updateKellyFraction(
                    KellyEstimator.fraction(tradeRepository.recentClosedPnls(SAMPLE_SIZE))
                            .orElse(null));
        } catch (RuntimeException e) {
            log.warn("Failed to estimate Kelly fraction, keeping previous value: {}", e.getMessage());
        }
    }
}
