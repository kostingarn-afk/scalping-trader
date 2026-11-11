package ru.scalping.collector;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Data Collector — сбор рыночных данных через gRPC-стримы T-Invest API
 * и публикация в Kafka {@code raw-market-data}.
 */
@SpringBootApplication
public class CollectorApplication {

    public static void main(String[] args) {
        SpringApplication.run(CollectorApplication.class, args);
    }
}
