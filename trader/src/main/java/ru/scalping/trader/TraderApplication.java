package ru.scalping.trader;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Trader — исполнение торговых сигналов, риск-менеджмент,
 * управление позициями с биржевыми защитными ордерами.
 */
@SpringBootApplication
@EnableScheduling
public class TraderApplication {

    public static void main(String[] args) {
        SpringApplication.run(TraderApplication.class, args);
    }
}
