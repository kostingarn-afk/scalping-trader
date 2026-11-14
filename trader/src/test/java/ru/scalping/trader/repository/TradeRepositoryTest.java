package ru.scalping.trader.repository;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentMatchers;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class TradeRepositoryTest {

    private final JdbcTemplate jdbc = mock(JdbcTemplate.class);
    private final TradeRepository repository = new TradeRepository(jdbc);

    @Test
    void lotSize_existingInstrument_returnsLotFromDb() {
        when(jdbc.query(anyString(), ArgumentMatchers.<RowMapper<Integer>>any(), any(Object[].class)))
                .thenReturn(List.of(10));

        assertThat(repository.lotSize("BBG004730RP0")).isEqualTo(10);
    }

    @Test
    void lotSize_missingInstrument_returnsDefaultLot() {
        when(jdbc.query(anyString(), ArgumentMatchers.<RowMapper<Integer>>any(), any(Object[].class)))
                .thenReturn(List.of());

        assertThat(repository.lotSize("UNKNOWN")).isEqualTo(1);
    }

    @Test
    void lotSize_nullLot_returnsDefaultLot() {
        when(jdbc.query(anyString(), ArgumentMatchers.<RowMapper<Integer>>any(), any(Object[].class)))
                .thenReturn(java.util.Collections.singletonList(null));

        assertThat(repository.lotSize("BBG004730N88")).isEqualTo(1);
    }
}
