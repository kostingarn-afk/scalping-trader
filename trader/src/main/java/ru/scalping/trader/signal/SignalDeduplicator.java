package ru.scalping.trader.signal;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.UUID;

/**
 * Дедупликация сигналов по signal_id (задача 4.2, spec trade-execution).
 *
 * <p>Через таблицу {@code processed_signals} (TTL 1 час — очистка scheduled task).
 * At-least-once + дедупликация = effectively-once.
 */
@Component
public class SignalDeduplicator {

    private static final Logger log = LoggerFactory.getLogger(SignalDeduplicator.class);

    private final JdbcTemplate jdbc;

    public SignalDeduplicator(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * @return true если сигнал новый (обрабатываем), false если дубликат или БД недоступна
     *         (необработанный сигнал лучше потерять, чем исполнить дважды).
     */
    public boolean tryAcquire(UUID signalId) {
        try {
            // ON CONFLICT DO NOTHING не бросает исключение, поэтому конфликт определяется
            // по числу вставленных строк: 1 — сигнал новый, 0 — уже обработан.
            int rows = jdbc.update(
                    "INSERT INTO processed_signals (signal_id, result) VALUES (?, 'executed') ON CONFLICT DO NOTHING",
                    signalId
            );
            if (rows == 1) {
                return true;
            }
            log.warn("Duplicate signal_id={}", signalId);
            return false;
        } catch (Exception e) {
            log.warn("Failed to acquire signal_id={}: {}", signalId, e.getMessage());
            return false;
        }
    }

    public void markRejected(UUID signalId, String reason) {
        jdbc.update(
                "UPDATE processed_signals SET result='rejected', reject_reason=? WHERE signal_id=?",
                reason, signalId
        );
    }
}
