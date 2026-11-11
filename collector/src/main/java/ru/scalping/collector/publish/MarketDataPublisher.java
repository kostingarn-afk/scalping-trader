package ru.scalping.collector.publish;

import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import ru.scalping.collector.metrics.CollectorMetrics;
import ru.scalping.proto.RawMarketData;

/**
 * Публикация {@link RawMarketData} в Kafka-топик {@code raw-market-data}.
 * Ключ — figi (для partitioning по инструменту).
 */
@Component
public class MarketDataPublisher {

    private static final Logger log = LoggerFactory.getLogger(MarketDataPublisher.class);

    private final KafkaProducer<String, byte[]> producer;
    private final CollectorMetrics metrics;
    private final String topic;

    public MarketDataPublisher(KafkaProducer<String, byte[]> producer,
                               CollectorMetrics metrics,
                               @Value("${kafka.topic.raw-market-data:raw-market-data}") String topic) {
        this.producer = producer;
        this.metrics = metrics;
        this.topic = topic;
    }

    public void publish(RawMarketData data) {
        byte[] payload = data.toByteArray();
        ProducerRecord<String, byte[]> record = new ProducerRecord<>(topic, data.getFigi(), payload);
        producer.send(record, (metadata, exception) -> {
            if (exception != null) {
                log.error("Failed to publish market data for figi={}: {}", data.getFigi(), exception.getMessage());
            } else {
                metrics.messagePublished();
            }
        });
    }
}
