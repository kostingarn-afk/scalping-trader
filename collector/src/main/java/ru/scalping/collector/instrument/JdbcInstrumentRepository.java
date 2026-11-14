package ru.scalping.collector.instrument;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.Objects;

/**
 * Upsert метаданных инструмента в таблицу {@code instruments} через JDBC ({@code java.sql}).
 *
 * <p>Коллектор не содержит JDBC-стартера, поэтому соединение открывается напрямую через
 * {@link DriverManager}. Для одного вызова раз в сутки пул не нужен: соединение открывается
 * внутри {@link #upsert(InstrumentMeta)} и закрывается в try-with-resources, что исключает
 * удержание простаивающего соединения и не требует дополнительных зависимостей
 * (HikariCP/Spring JDBC).
 *
 * <p>Схемой владеет модуль Trader (Flyway), здесь выполняется только запись.
 */
@Component
public class JdbcInstrumentRepository implements InstrumentRepository {

    private static final Logger log = LoggerFactory.getLogger(JdbcInstrumentRepository.class);

    private static final String POSTGRES_DRIVER = "org.postgresql.Driver";
    private static final String DEFAULT_URL = "jdbc:postgresql://localhost:5432/scalping";
    private static final String DEFAULT_USERNAME = "scalping";
    private static final String DEFAULT_PASSWORD = "scalping";
    /** Классы фьючерсов MOEX в T-Invest: актуальный SPBFUT и историческое имя FORTS. */
    private static final java.util.Set<String> FUTURE_CLASS_CODES = java.util.Set.of("SPBFUT", "FORTS");

    private static final String UPSERT_SQL = """
            INSERT INTO instruments (figi, uid, ticker, class_code, name, instrument_type, lot,
                                     min_price_increment, currency, api_trade_available, trading_status, tier, updated_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
            ON CONFLICT (figi) DO UPDATE SET
                ticker = EXCLUDED.ticker,
                class_code = EXCLUDED.class_code,
                name = EXCLUDED.name,
                instrument_type = EXCLUDED.instrument_type,
                lot = EXCLUDED.lot,
                min_price_increment = EXCLUDED.min_price_increment,
                currency = EXCLUDED.currency,
                api_trade_available = EXCLUDED.api_trade_available,
                trading_status = EXCLUDED.trading_status,
                tier = EXCLUDED.tier,
                updated_at = now()
            """;

    private final String url;
    private final String username;
    private final String password;

    public JdbcInstrumentRepository(Environment environment) {
        this.url = environment.getProperty("spring.datasource.url", DEFAULT_URL);
        this.username = environment.getProperty("spring.datasource.username", DEFAULT_USERNAME);
        this.password = environment.getProperty("spring.datasource.password", DEFAULT_PASSWORD);
        registerDriver();
    }

    @Override
    public void upsert(InstrumentMeta meta) {
        Objects.requireNonNull(meta, "meta");
        try (Connection connection = DriverManager.getConnection(url, username, password);
             PreparedStatement statement = connection.prepareStatement(UPSERT_SQL)) {
            statement.setString(1, meta.figi());
            statement.setString(2, meta.ticker());
            statement.setString(3, meta.classCode());
            statement.setString(4, meta.name());
            statement.setString(5, instrumentType(meta.classCode()));
            statement.setInt(6, meta.lot());
            statement.setBigDecimal(7, BigDecimal.valueOf(meta.minPriceIncrement()));
            statement.setString(8, meta.currency());
            statement.setBoolean(9, meta.apiTradeAvailable());
            statement.setInt(10, meta.tradingStatus());
            statement.setInt(11, meta.tier());
            statement.executeUpdate();
            log.debug("Upserted instrument {} ({})", meta.ticker(), meta.figi());
        } catch (SQLException e) {
            throw new IllegalStateException(
                    "Failed to upsert instrument " + meta.figi() + " into 'instruments': " + e.getMessage(), e);
        }
    }

    /**
     * Тип инструмента выводится из кода класса: {@code SPBFUT}/{@code FORTS} → {@code FUTURE},
     * иначе {@code SHARE}.
     *
     * <p>Ограничение: {@link InstrumentMeta} не содержит признака типа инструмента, а контракт
     * записи менять нельзя — эвристика по {@code classCode} покрывает весь MVP-набор
     * (акции TQBR/SPEQX, фьючерсы SPBFUT).
     */
    private static String instrumentType(String classCode) {
        return classCode != null && FUTURE_CLASS_CODES.contains(classCode.toUpperCase(java.util.Locale.ROOT))
                ? "FUTURE"
                : "SHARE";
    }

    /**
     * Регистрация PostgreSQL-драйвера. Драйвер подгружается опционально: если его нет на
     * classpath, падает только {@link #upsert(InstrumentMeta)} (ошибка логируется в
     * {@code InstrumentCache}), старт Collector не блокируется — стрим и Kafka от БД не зависят.
     */
    private static void registerDriver() {
        try {
            Class.forName(POSTGRES_DRIVER);
        } catch (ClassNotFoundException e) {
            log.warn("PostgreSQL JDBC driver ({}) is not on classpath — instruments upsert will fail",
                    POSTGRES_DRIVER);
        }
    }
}
