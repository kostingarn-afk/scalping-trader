package ru.scalping.trader.signal;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import ru.scalping.proto.SignalType;
import ru.scalping.proto.TradingSignal;

import java.time.Instant;
import java.util.UUID;

/**
 * Обработка входящего сигнала (задачи 4.2, 4.3, 4.4, 4.5).
 *
 * <p>Цепочка: дедупликация → проверка устаревания → проверка статуса →
 * risk-check → исполнение → SL/TP → публикация.
 */
@Component
public class SignalProcessor {

    private static final Logger log = LoggerFactory.getLogger(SignalProcessor.class);

    private final SignalDeduplicator deduplicator;
    private final SignalValidator validator;

    public SignalProcessor(SignalDeduplicator deduplicator, SignalValidator validator) {
        this.deduplicator = deduplicator;
        this.validator = validator;
    }

    /**
     * @return результат валидации сигнала (до risk-check и исполнения).
     */
    public ValidationResult validate(TradingSignal signal) {
        UUID signalId = UUID.fromString(signal.getSignalId());

        // 1. Дедупликация
        if (!deduplicator.tryAcquire(signalId)) {
            return ValidationResult.duplicate(signalId);
        }

        // 2. Устаревание
        Instant expiresAt = Instant.ofEpochSecond(signal.getExpiresAt().getSeconds(), signal.getExpiresAt().getNanos());
        if (validator.isExpired(expiresAt)) {
            deduplicator.markRejected(signalId, "expired");
            return ValidationResult.rejected(signalId, "expired");
        }

        // 3. HOLD — не исполняется
        if (signal.getSignal() == SignalType.HOLD) {
            return ValidationResult.rejected(signalId, "hold signal");
        }

        return ValidationResult.ok(signalId);
    }

    public record ValidationResult(UUID signalId, Status status, String rejectReason) {
        public enum Status { OK, DUPLICATE, REJECTED }

        public static ValidationResult ok(UUID id) { return new ValidationResult(id, Status.OK, null); }
        public static ValidationResult duplicate(UUID id) { return new ValidationResult(id, Status.DUPLICATE, "duplicate"); }
        public static ValidationResult rejected(UUID id, String reason) { return new ValidationResult(id, Status.REJECTED, reason); }
    }
}
