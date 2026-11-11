package ru.scalping.trader.publish;

import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import ru.scalping.proto.TradeResult;

import java.util.UUID;

/**
 * Публикация событий сделок в Kafka {@code trade-results} (задача 4.11).
 *
 * <p>Топик compact, key=trade_id, значение — Protobuf {@link TradeResult}.
 * События: OPENED, PARTIAL_FILL, SL_HIT, TP_HIT, TIMEOUT_CLOSED, REJECTED, CANCELLED, CLOSED.
 */
@Component
public class TradeResultPublisher {

    private static final Logger log = LoggerFactory.getLogger(TradeResultPublisher.class);

    private final KafkaProducer<String, byte[]> producer;
    private final String topic;

    public TradeResultPublisher(KafkaProducer<String, byte[]> producer,
                                @Value("${kafka.topic.trade-results:trade-results}") String topic) {
        this.producer = producer;
        this.topic = topic;
    }

    public void publish(TradeResult event) {
        String key = event.getTradeId();
        byte[] payload = event.toByteArray();
        producer.send(new ProducerRecord<>(topic, key, payload), (meta, ex) -> {
            if (ex != null) {
                log.error("Failed to publish trade-result {}: {}", key, ex.getMessage());
            }
        });
    }

    public void publish(UUID tradeId, TradeResult event) {
        publish(event);
    }
}
