package ru.scalping.trader.signal;

import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class SignalDeduplicatorTest {

    private final JdbcTemplate jdbc = mock(JdbcTemplate.class);
    private final SignalDeduplicator deduplicator = new SignalDeduplicator(jdbc);
    private final UUID signalId = UUID.randomUUID();

    @Test
    void tryAcquire_insertedRow_returnsTrue() {
        when(jdbc.update(anyString(), any(Object[].class))).thenReturn(1);

        assertThat(deduplicator.tryAcquire(signalId)).isTrue();
    }

    @Test
    void tryAcquire_conflictNoRow_returnsFalse() {
        when(jdbc.update(anyString(), any(Object[].class))).thenReturn(0);

        assertThat(deduplicator.tryAcquire(signalId)).isFalse();
    }

    @Test
    void tryAcquire_databaseError_returnsFalse() {
        when(jdbc.update(anyString(), any(Object[].class)))
                .thenThrow(new DataAccessResourceFailureException("db is down"));

        assertThat(deduplicator.tryAcquire(signalId)).isFalse();
    }
}
