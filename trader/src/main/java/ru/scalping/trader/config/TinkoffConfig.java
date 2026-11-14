package ru.scalping.trader.config;

import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import ru.tinkoff.piapi.contract.v1.Account;
import ru.tinkoff.piapi.core.InvestApi;

import java.util.List;

/**
 * Привязка Trader к T-Invest SDK: единственная точка создания {@link InvestApi}
 * и определения торгового счёта.
 *
 * <p>Типы SDK не выходят за пределы этого класса и классов-адаптеров
 * ({@code TinkoffOrderExecutionService}, {@code TinkoffExchangeStateProvider}).
 */
@Configuration
@EnableConfigurationProperties(TinkoffProperties.class)
public class TinkoffConfig {

    /** Имя бина с идентификатором торгового счёта: инжектится через {@code @Qualifier}. */
    public static final String TRADING_ACCOUNT_ID_BEAN = "tradingAccountId";

    private static final Logger log = LoggerFactory.getLogger(TinkoffConfig.class);

    private InvestApi investApi;

    /**
     * Канал к T-Invest API. Токен обязателен: пустой токен останавливает старт контекста,
     * потому что Trader без брокера неработоспособен.
     */
    @Bean(destroyMethod = "")
    public InvestApi investApi(TinkoffProperties properties) {
        if (!properties.hasToken()) {
            throw new IllegalStateException(
                    "T-Invest token is not configured: set environment variable T_INVEST_TOKEN");
        }
        InvestApi api = properties.sandbox()
                ? InvestApi.createSandbox(properties.token())
                : InvestApi.create(properties.token());
        this.investApi = api;
        log.info("T-Invest SDK initialized: sandbox={}", properties.sandbox());
        return api;
    }

    /**
     * Идентификатор торгового счёта: из конфигурации либо первый счёт токена.
     */
    @Bean(name = TRADING_ACCOUNT_ID_BEAN)
    public String tradingAccountId(InvestApi investApi, TinkoffProperties properties) {
        if (properties.hasAccountId()) {
            return properties.accountId().trim();
        }
        List<Account> accounts = investApi.isSandboxMode()
                ? investApi.getSandboxService().getAccountsSync()
                : investApi.getUserService().getAccountsSync();
        if (accounts.isEmpty()) {
            throw new IllegalStateException(
                    "No T-Invest account found for the token: set environment variable T_INVEST_ACCOUNT_ID");
        }
        String accountId = accounts.get(0).getId();
        log.info("T-Invest account auto-detected: accountId={}", accountId);
        return accountId;
    }

    /**
     * У {@link InvestApi} нет {@code close()}: {@code destroy(int)} принимает таймаут,
     * поэтому Spring destroy-method не подходит и вызывается вручную.
     */
    @PreDestroy
    public void shutdown() {
        if (investApi == null) {
            return;
        }
        try {
            investApi.destroy(1);
            log.info("T-Invest SDK channel destroyed");
        } catch (RuntimeException e) {
            log.warn("Failed to destroy T-Invest SDK channel: {}", e.getMessage());
        }
    }
}
