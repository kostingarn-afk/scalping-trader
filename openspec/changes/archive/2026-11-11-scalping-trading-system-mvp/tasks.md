# Tasks

## 1. Infrastructure & Scaffolding

- [x] 1.1 Создать структуру каталогов: `collector/` (Java/Maven), `analyzer/` (Python/poetry), `trader/` (Java/Maven), `proto/` (Protobuf-схемы). Проверить: `ls collector analyzer trader proto` показывает ожидаемые файлы
- [x] 1.2 Описать Protobuf-схемы в `proto/`: `raw_market_data.proto`, `trading_signal.proto`, `trade_result.proto` (из ТЗ раздел 6). Сгенерировать Java- и Python-код из .proto. Проверить: `mvn generate-sources` и `poetry run python -c "import raw_market_data_pb2"` проходят
- [x] 1.3 Настроить Kafka: 3 топика (`raw-market-data` retention 3мин delete, `trading-signals` retention 3дня delete, `trade-results` compact). Проверить: `kafka-topics --list` показывает 3 топика с верными параметрами
- [x] 1.4 Создать Flyway-миграции (Java Trader) для 7 таблиц: `instruments`, `analysis_results`, `trade_results`, `positions`, `orders`, `risk_config`, `processed_signals` (из ТЗ раздел 5). Проверить: `mvn flyway:migrate` проходит, таблицы и индексы созданы
- [x] 1.5 Заполнить `risk_config` предопределёнными ключами: max_loss_per_trade_pct=1.0, pnl_circuit_breaker_pct=3.0, commission_profit_ratio=3.0, max_open_positions=3, max_holding_time_minutes=10, correlation_threshold=0.8, max_trades_per_minute=10. Проверить: `SELECT key,value FROM risk_config` возвращает 7 строк

## 2. Data Collector (Java)

- [x] 2.1 Scaffold Spring Boot 3 + Java 21 проект `collector/` с зависимостями: tinkoff-invest-sdk 1.8, kafka-clients, protobuf, micrometer-prometheus. Проверить: `mvn compile` проходит
- [x] 2.2 Реализовать gRPC-подписку на `MarketDataStreamService`: Candles (1мин, waiting_close=false), OrderBook (depth=10), Trades, Info по списку инструментов из конфигурации. Проверить: unit-тест с mock-стримом проверяет 4 типа подписок на 3 инструмента
- [x] 2.3 Реализовать кэш метаданных инструментов через `InstrumentsService.GetInstrumentBy` при старте + daily refresh, запись в `instruments` (upsert). Проверить: интеграционный тест на sandbox получает метаданные BR/SBER/TCSG
- [x] 2.4 Реализовать публикацию стрим-событий в Kafka `raw-market-data` (Protobuf `RawMarketData`). Проверить: unit-тест проверяет, что Candle/OrderBook/Trade/Info корректно сериализуются и публикуются
- [x] 2.5 Реализовать фильтрацию по торговому статусу: данные Candle/OrderBook/Trades публикуются только при `NORMAL_TRADING`, Info — всегда. Проверить: unit-тест с статусом `BREAK_IN_TRADING` — публикуется только Info
- [x] 2.6 Реализовать автопереподключение стрима с exponential backoff (1с→30с потолок) и повторной подпиской. Проверить: unit-тест с mock-разрывом соединения проверяет backoff и resubscribe
- [x] 2.7 Добавить метрики Micrometer: messages_published_total, stream_reconnects_total, api_errors_total. Проверить: `/metrics` endpoint возвращает метрики collector

## 3. Analyzer (Python)

- [x] 3.1 Scaffold Python 3.11 + poetry проект `analyzer/` с зависимостями: ta-lib, numpy, scipy, kafka-python, asyncpg, prometheus_client. Проверить: `poetry install` и `poetry run python -c "import talib, numpy, scipy"` проходят
- [x] 3.2 Реализовать Kafka consumer `raw-market-data` с partitioning по `instrument_id`, multi-process (N процессов из конфигурации, consumer group). Проверить: 2 процесса стартуют, распределяют 3 инструмента по partition
- [x] 3.3 Реализовать расчёт индикаторов: RSI(7), MACD(8,21,5), BB(20,2), ATR(14), VWAP, order imbalance (из стакана depth=10). Проверить: unit-тесты на synthetic данных проверяют значения индикаторов против эталона
- [x] 3.4 Реализовать стратегию mean reversion (BB + Z-score): сигнал BUY при RSI<30 и цене≤lower BB, SELL при RSI>70 и цене≥upper BB, HOLD иначе. SL = entry - 1.5×ATR, TP = entry + 2×ATR. Проверить: unit-тесты покрывают BUY/SELL/HOLD сценарии
- [x] 3.5 Реализовать комиссионную фильтрацию: отброс сигналов, где ожидаемая прибыль < 3× комиссии (вход+выход). Проверить: unit-тест с малой прибылью — сигнал отбрасывается
- [x] 3.6 Реализовать сохранение в `analysis_results` (asyncpg) с signal_id (UUID), strategy_id, analyzer_version, indicators (JSONB), expires_at. Проверить: интеграционный тест с testcontainers-postgres вставляет и читает запись
- [x] 3.7 Реализовать публикацию BUY/SELL в Kafka `trading-signals` (Protobuf `TradingSignal`), HOLD не публикуется. Проверить: unit-тест проверяет, что BUY публикуется, HOLD — нет
- [x] 3.8 Добавить метрики prometheus_client: signals_generated_total, signals_rejected_total, analysis_latency_seconds, consumer_lag. Проверить: `/metrics` endpoint возвращает метрики analyzer

## 4. Trader + Risk Management (Java)

- [x] 4.1 Scaffold Spring Boot 3 + Java 21 проект `trader/` с зависимостями: tinkoff-invest-sdk 1.8, kafka-clients, protobuf, flyway, micrometer-prometheus. Проверить: `mvn compile` проходит
- [x] 4.2 Реализовать Kafka consumer `trading-signals` с дедупликацией по `signal_id` через `processed_signals` (TTL 1ч). Проверить: unit-тест с дубликатом signal_id — второй игнорируется
- [x] 4.3 Реализовать проверку устаревания сигнала (expires_at < now → reject) и торгового статуса (только NORMAL_TRADING). Проверить: unit-тесты с истёкшим сигналом и non-normal статусом — reject
- [x] 4.4 Реализовать Risk Manager: динамический размер позиции (1%/сделку), max_open_positions (3), correlation limit, max_trades_per_minute (10). Параметры из `risk_config` (poll без рестарта). Проверить: unit-тесты покрывают расчёт объёма, лимит позиций, корреляцию, rate-limit
- [x] 4.5 Реализовать исполнение ордера через `PostOrder` (LIMIT, запрет MARKET на вход) с уникальным order_id (идемпотентность). Проверить: интеграционный тест на sandbox — LIMIT-ордер выставляется, MARKET — rejected с ошибкой
- [x] 4.6 Реализовать выставление биржевых SL/TP через `StopOrdersService.PostStopOrder` после исполнения ордера на вход. Проверить: интеграционный тест на sandbox — после fill вызываются PostStopOrder для SL и TP, IDs сохраняются в trade_results
- [x] 4.7 Реализовать подписку на `TradesStream` и `PositionsStream` для real-time обновления. Обработка SL_HIT/TP_HIT/PARTIAL_FILL. Проверить: интеграционный тест на sandbox — исполнение стоп-ордера генерирует событие SL_HIT
- [x] 4.8 Реализовать auto-close по таймауту (max_holding_time=10мин из risk_config): ордер на закрытие, событие TIMEOUT_CLOSED. Проверить: unit-тест с mock-часами — позиция старше 10мин закрывается
- [x] 4.9 Реализовать PnL Circuit Breaker: отслеживание дневного PnL, при убытке 3% — close all + блок до след.сессии (сброс по TradingSchedules). Проверить: unit-тесты — срабатывание при -6000, блокировка новых, сброс на новую сессию
- [x] 4.10 Реализовать recovery при старте: синхронизация с `GetPositions`, `GetOrders`, `GetStopOrders`, сверка с БД, восстановление in-memory. Проверить: интеграционный тест на sandbox — после рестарта открытая позиция восстановлена
- [x] 4.11 Реализовать публикацию событий в Kafka `trade-results` (Protobuf `TradeResult`, compact, key=trade_id): OPENED, PARTIAL_FILL, SL_HIT, TP_HIT, TIMEOUT_CLOSED, REJECTED, CANCELLED, CLOSED. Проверить: unit-тест проверяет все типы событий корректно сериализуются
- [x] 4.12 Реализовать запись в `trade_results` и `positions` (open/close/update). Проверить: интеграционный тест с testcontainers-postgres — сделка вставляется, позиция обновляется
- [x] 4.13 Добавить метрики Micrometer: orders_placed_total, orders_rejected_total, pnl_realized_total, pnl_unrealized, positions_open, circuit_breaker_active. Проверить: `/metrics` endpoint возвращает метрики trader

## 5. Observability & Alerting

- [x] 5.1 Настроить Prometheus scrape config для collector, analyzer, trader `/metrics`. Проверить: Prometheus targets UP
- [x] 5.2 Настроить Grafana дашборд: PnL, latency per stage, consumer lag, signals/min, trades/min, positions, circuit_breaker. Проверить: дашборд загружается, панели показывают данные
- [x] 5.3 Настроить alerting rules: api_disconnect (стрим down >30с), token_error (40003), pnl_circuit_breaker, stale_data (>30с без данных), consumer_lag (>1000), order_rejected, token_expiring_soon (7 дней). Проверить: alert rules валидны (`promtool check rules`)

## 6. Integration & E2E

- [x] 6.1 E2E на sandbox: полный pipeline Collector→Kafka→Analyzer→Kafka→Trader→Kafka по 1 инструменту (BR), сигнал BUY → ордер → SL/TP выставлены → позиция в БД. Проверить: в `trade_results` есть запись со status=opened, sl_order_id и tp_order_id заполнены
- [x] 6.2 E2E PnL-CB: симуляция 3 убыточных сделок суммарно -6000 руб → PnL-CB срабатывает, позиции закрываются, новые заявки блокируются. Проверить: alert pnl_circuit_breaker сгенерирован, новые сигналы rejected
- [x] 6.3 E2E recovery: Trader рестарт при открытой позиции → позиция восстановлена из GetPositions, TradesStream подписан. Проверить: после рестарта `positions` в БД и in-memory совпадают
- [x] 6.4 E2E 3 инструмента: BR+SBER+TCSG параллельно, сигналы по каждому, дедупликация, max_positions=3. Проверить: все 3 инструмента обрабатываются, 4-й сигнал rejected при 3 открытых
- [x] 6.5 Проверить NFR: pipeline latency ≤ 2с (из метрик `scalping_pipeline_latency_seconds` p99), исполнение ордера ≤ 500мс (из метрик `order_placement_latency_seconds` p99). Проверить: `promtool query` p99 < целевых значений на нагрузочном тесте

## Workflow follow-up

- Archive the change after the project's review requirements are satisfied.
- Verify the archived result.
