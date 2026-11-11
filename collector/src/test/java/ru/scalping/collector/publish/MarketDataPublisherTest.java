package ru.scalping.collector.publish;

import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import ru.scalping.collector.metrics.CollectorMetrics;
import ru.scalping.proto.PayloadType;
import ru.scalping.proto.RawMarketData;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class MarketDataPublisherTest {

    @Mock KafkaProducer<String, byte[]> producer;
    @Mock CollectorMetrics metrics;

    private MarketDataPublisher publisher;

    @BeforeEach
    void setUp() {
        publisher = new MarketDataPublisher(producer, metrics, "raw-market-data");
    }

    @Test
    void publishesCandleWithFigiKey() {
        RawMarketData data = RawMarketData.newBuilder()
                .setFigi("SBER")
                .setPayloadType(PayloadType.CANDLE)
                .build();

        publisher.publish(data);

        ArgumentCaptor<ProducerRecord<String, byte[]>> captor = ArgumentCaptor.forClass(ProducerRecord.class);
        verify(producer).send(captor.capture(), any());
        ProducerRecord<String, byte[]> rec = captor.getValue();
        assertThat(rec.topic()).isEqualTo("raw-market-data");
        assertThat(rec.key()).isEqualTo("SBER");
        assertThat(rec.value()).isNotEmpty();
    }
}
