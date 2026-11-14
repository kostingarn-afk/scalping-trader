package ru.scalping.trader.signal;

import com.google.protobuf.InvalidProtocolBufferException;
import jakarta.annotation.PreDestroy;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.InterruptException;
import org.apache.kafka.common.errors.WakeupException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import ru.scalping.proto.TradingSignal;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Consumer сигналов Analyzer из Kafka {@code trading-signals} (spec trade-execution).
 *
 * <p>Отдельный daemon-поток поверх готового {@link KafkaConsumer}-бина: в проекте нет
 * зависимости {@code spring-kafka}, а один consumer не оправдывает её добавление (D1).
 * Коммит — вручную после успешной обработки записи, автокоммит отключён в конфигурации бина.
 */
@Component
public class SignalConsumer {

    private static final Logger log = LoggerFactory.getLogger(SignalConsumer.class);

    private static final Duration POLL_TIMEOUT = Duration.ofMillis(500);
    private static final long SHUTDOWN_TIMEOUT_MS = 5_000;
    /** Пауза перед повтором записи, обработка которой отложена (БД недоступна). */
    private static final long RETRY_BACKOFF_MS = 1_000;

    private final KafkaConsumer<String, byte[]> consumer;
    private final TradeOrchestrator orchestrator;
    private final String topic;
    private final AtomicBoolean running = new AtomicBoolean(false);

    private volatile Thread worker;

    public SignalConsumer(@Qualifier("tradingSignalsConsumer") KafkaConsumer<String, byte[]> consumer,
                          TradeOrchestrator orchestrator,
                          @Value("${kafka.topic.trading-signals:trading-signals}") String topic) {
        this.consumer = consumer;
        this.orchestrator = orchestrator;
        this.topic = topic;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void start() {
        if (!running.compareAndSet(false, true)) {
            log.warn("Signal consumer is already running, second thread not started");
            return;
        }
        consumer.subscribe(List.of(topic));
        Thread thread = new Thread(this::pollLoop, "signal-consumer");
        thread.setDaemon(true);
        worker = thread;
        thread.start();
        log.info("Signal consumer started: topic={}", topic);
    }

    private void pollLoop() {
        try {
            while (running.get()) {
                ConsumerRecords<String, byte[]> records = consumer.poll(POLL_TIMEOUT);
                for (ConsumerRecord<String, byte[]> record : records) {
                    process(record);
                }
            }
        } catch (WakeupException e) {
            if (running.get()) {
                log.warn("Consumer woken up unexpectedly, stopping");
            }
        } catch (InterruptException e) {
            Thread.currentThread().interrupt();
        } finally {
            closeConsumer();
            log.info("Signal consumer stopped: topic={}", topic);
        }
    }

    private void process(ConsumerRecord<String, byte[]> record) {
        TradingSignal signal;
        try {
            signal = TradingSignal.parseFrom(record.value());
        } catch (InvalidProtocolBufferException e) {
            // Невалидное сообщение не станет валидным при повторе — пропускаем.
            log.error("Invalid TradingSignal: topic={}, partition={}, offset={}: {}",
                    record.topic(), record.partition(), record.offset(), e.getMessage());
            commit(record);
            return;
        }
        try {
            orchestrator.handle(signal);
        } catch (RetryableSignalException e) {
            // Инфраструктурный сбой: offset не коммитим — сообщение будет перечитано.
            // Пауза, чтобы не крутить цикл на одной и той же записи.
            log.warn("Signal postponed, offset not committed: topic={}, partition={}, offset={}: {}",
                    record.topic(), record.partition(), record.offset(), e.getMessage());
            backoff();
            return;
        } catch (RuntimeException e) {
            log.error("Signal handling failed, offset not committed: topic={}, partition={}, offset={}: {}",
                    record.topic(), record.partition(), record.offset(), e.getMessage(), e);
            return;
        }
        commit(record);
    }

    private void backoff() {
        try {
            Thread.sleep(RETRY_BACKOFF_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void commit(ConsumerRecord<String, byte[]> record) {
        TopicPartition partition = new TopicPartition(record.topic(), record.partition());
        consumer.commitSync(Map.of(partition, new OffsetAndMetadata(record.offset() + 1)));
    }

    private void closeConsumer() {
        try {
            consumer.close();
        } catch (RuntimeException e) {
            log.warn("Failed to close signal consumer: {}", e.getMessage());
        }
    }

    @PreDestroy
    public void shutdown() {
        running.set(false);
        consumer.wakeup();
        Thread thread = worker;
        if (thread == null) {
            return;
        }
        try {
            thread.join(SHUTDOWN_TIMEOUT_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        if (thread.isAlive()) {
            log.warn("Signal consumer thread did not stop within {} ms", SHUTDOWN_TIMEOUT_MS);
        }
    }
}
