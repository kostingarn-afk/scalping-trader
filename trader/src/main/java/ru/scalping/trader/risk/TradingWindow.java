package ru.scalping.trader.risk;

import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;

/**
 * Окно входа: запрет открытия позиций до заданного времени по Москве.
 *
 * <p>Основание — замер внутридневной сезонности (`to_do.md` §2.6): первый час основной сессии
 * даёт −3.8 б.п. дрейфа при t = −2.53, то есть вход в него систематически невыгоден. Порог
 * живёт в {@code risk_config} (`entry_after_msk_minutes`), а не в коде: при обновлении замера
 * окно правится без передеплоя, как и остальные риск-лимиты.
 *
 * <p>Правило касается только <b>входов</b>: сопровождение и закрытие уже открытых позиций
 * работает в любые часы.
 */
@Component
public class TradingWindow {

    /** Москва — UTC+3 круглый год (перевод часов отменён в 2014). */
    private static final ZoneId MARKET_ZONE = ZoneId.of("Europe/Moscow");

    private final Clock clock;
    private final ZoneId zone;

    public TradingWindow() {
        this(Clock.systemUTC(), MARKET_ZONE);
    }

    public TradingWindow(Clock clock, ZoneId zone) {
        this.clock = clock;
        this.zone = zone;
    }

    /** @return true если сейчас разрешено открывать новые позиции. */
    public boolean isEntryAllowed(RiskConfig config) {
        return isEntryAllowed(clock.instant(), config);
    }

    /** @return true если в момент {@code at} разрешено открывать новые позиции. */
    public boolean isEntryAllowed(Instant at, RiskConfig config) {
        int threshold = config.entryAfterMskMinutes();
        if (threshold <= 0) {
            return true;
        }
        LocalTime local = at.atZone(zone).toLocalTime();
        return local.toSecondOfDay() / 60 >= threshold;
    }
}
