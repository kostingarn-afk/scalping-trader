package ru.scalping.trader.risk;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import ru.scalping.trader.repository.TradeRepository;

import java.util.Map;

/**
 * Загрузка риск-параметров из таблицы {@code risk_config} в {@link RiskManager}
 * (spec risk-management: параметры настраиваются без передеплоя).
 *
 * <p>Первичная загрузка выполняется на {@link ApplicationReadyEvent}: до первого срабатывания
 * cron действовали бы дефолты {@link RiskConfig#defaults()}, а не значения из БД.
 */
@Component
public class RiskConfigPoller {

    private static final Logger log = LoggerFactory.getLogger(RiskConfigPoller.class);

    private final RiskManager riskManager;
    private final TradeRepository tradeRepository;

    public RiskConfigPoller(RiskManager riskManager, TradeRepository tradeRepository) {
        this.riskManager = riskManager;
        this.tradeRepository = tradeRepository;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void loadOnStartup() {
        poll();
    }

    /**
     * Периодический poll. При ошибке БД текущая конфигурация сохраняется: работать на дефолтах
     * или на пустой конфигурации опаснее, чем на последней успешно прочитанной.
     */
    @Scheduled(cron = "${trader.poll-risk-config-cron:*/30 * * * * *}")
    public void poll() {
        Map<String, String> values = null;
        try {
            values = tradeRepository.loadRiskConfig();
            riskManager.updateConfig(RiskConfig.fromMap(values));
        } catch (RuntimeException e) {
            // Значение вне диапазона (например kelly_cap_pct вне 0.1–5.0) роняет разбор:
            // без самих значений в логе причину не найти по одному тексту исключения.
            log.warn("Failed to load risk_config, keeping current configuration: {} (loaded: {})",
                    e.getMessage(), values);
        }
    }
}
