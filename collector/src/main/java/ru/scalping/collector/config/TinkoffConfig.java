package ru.scalping.collector.config;

import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import ru.tinkoff.piapi.core.InvestApi;

/**
 * Привязка T-Invest SDK к Spring-контексту Collector.
 *
 * <p>{@link InvestApi} — единственная точка входа в SDK; бизнес-логика получает его
 * через адаптеры {@code instrument.TinkoffInstrumentLoader} и
 * {@code stream.TinkoffMarketStreamService}, а не напрямую.
 */
@Configuration
@EnableConfigurationProperties(TinkoffProperties.class)
public class TinkoffConfig {

    private static final Logger log = LoggerFactory.getLogger(TinkoffConfig.class);

    /** Сколько секунд SDK ждёт корректного завершения gRPC-стримов при закрытии. */
    private static final int DESTROY_TIMEOUT_SECONDS = 1;

    private volatile InvestApi investApi;

    /**
     * Клиент T-Invest API.
     *
     * <p>{@code destroyMethod = ""} — у {@link InvestApi} нет no-arg {@code close()},
     * только {@code destroy(int)}, поэтому закрытие выполняет {@link #shutdown()}.
     */
    @Bean(destroyMethod = "")
    public InvestApi investApi(TinkoffProperties properties) {
        String token = properties.token();
        if (token == null || token.isBlank()) {
            throw new IllegalStateException(
                    "T-Invest token is not configured: set environment variable T_INVEST_TOKEN "
                            + "(raw token without 'Bearer ' prefix) or property tinkoff.token");
        }
        InvestApi api = properties.sandbox() ? InvestApi.createSandbox(token) : InvestApi.create(token);
        this.investApi = api;
        log.info("T-Invest SDK initialized, sandbox={}", properties.sandbox());
        return api;
    }

    /** Закрытие клиента SDK при остановке контекста. */
    @PreDestroy
    public void shutdown() {
        InvestApi api = this.investApi;
        if (api == null) {
            return;
        }
        try {
            api.destroy(DESTROY_TIMEOUT_SECONDS);
            log.info("T-Invest SDK destroyed");
        } catch (RuntimeException e) {
            log.warn("Failed to destroy T-Invest SDK: {}", e.getMessage());
        }
    }
}
