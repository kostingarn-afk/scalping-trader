package ru.scalping.collector.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Конфигурация доступа к T-Invest API ({@code tinkoff.*}).
 *
 * @param token   сырой токен T-Invest без префикса {@code Bearer} (SDK добавляет его сам);
 *                подставляется из env-переменной {@code T_INVEST_TOKEN}
 * @param sandbox {@code true} — песочница (контур по умолчанию), {@code false} — боевой контур
 */
@ConfigurationProperties(prefix = "tinkoff")
public record TinkoffProperties(
        String token,
        @DefaultValue("true") boolean sandbox
) {
    public TinkoffProperties {
        if (token == null) {
            token = "";
        }
    }
}
