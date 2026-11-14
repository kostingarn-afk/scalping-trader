package ru.scalping.trader.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Параметры подключения Trader к T-Invest API (секция {@code tinkoff} в application.yml).
 *
 * @param token     сырой токен T-Invest без префикса {@code Bearer} (env {@code T_INVEST_TOKEN})
 * @param sandbox   режим песочницы; при отсутствии настройки — {@code true}
 * @param accountId идентификатор счёта; пусто — автоопределение через SDK (env {@code T_INVEST_ACCOUNT_ID})
 */
@ConfigurationProperties(prefix = "tinkoff")
public record TinkoffProperties(
        String token,
        @DefaultValue("true") boolean sandbox,
        String accountId
) {

    public boolean hasToken() {
        return token != null && !token.isBlank();
    }

    public boolean hasAccountId() {
        return accountId != null && !accountId.isBlank();
    }
}
