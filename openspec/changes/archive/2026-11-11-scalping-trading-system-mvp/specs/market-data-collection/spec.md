# Spec Delta

## Purpose

Сбор рыночных данных с Московской биржи через gRPC-стримы T-Invest API и публикация в Kafka для последующего анализа.

## ADDED Requirements

### Requirement: Подписка на gRPC-стримы рыночных данных

Collector SHALL подписываться на `MarketDataStreamService` (bi-directional stream) для каждого инструмента из конфигурации с четырьмя типами подписок: Candles (1 мин, waiting_close=false), OrderBook (depth=10), Trades, Info.

#### Scenario: Старт подписки на список инструментов
- **WHEN** Collector стартует с конфигурацией [BR, SBER, TCSG]
- **THEN** открывается одно стрим-соединение и выставляются 12 подписок (3 инструмента × 4 типа)

#### Scenario: Превышение лимита подписок
- **WHEN** число запрошенных подписок превышает 300 (лимит соединения)
- **THEN** Collector открывает дополнительное стрим-соединение и распределяет подписки

### Requirement: Публикация сырых данных в Kafka

Collector SHALL публиковать каждое полученное стрим-событие в Kafka-топик `raw-market-data` в формате Protobuf (сообщение `RawMarketData`), различая типы по `payload_type`.

#### Scenario: Получена свеча
- **WHEN** из стрима получена свеча по SBER
- **THEN** в Kafka публикуется сообщение с `payload_type=CANDLE`, заполненными полями `Candle` и `figi`

#### Scenario: Получен стакан
- **WHEN** из стрима получен стакан по BR
- **THEN** в Kafka публикуется сообщение с `payload_type=ORDER_BOOK`, до 10 уровней bids и asks

### Requirement: Автопереподключение при сбое стрима

Collector SHALL автоматически переподключаться при разрыве стрим-соединения с exponential backoff и повторной подпиской на все инструменты.

#### Scenario: Потеря соединения
- **WHEN** стрим-соединение разрывается
- **THEN** Collector логирует ошибку, ждёт с exponential backoff (начало 1с, потолок 30с), переподключается и повторно подписывается на все инструменты

### Requirement: Кэш метаданных инструментов

Collector SHALL кэшировать метаданные инструментов (lot, min_price_increment, trading_status, api_trade_available_flag) через `InstrumentsService` и периодически обновлять.

#### Scenario: Запрос метаданных при старте
- **WHEN** Collector стартует
- **THEN** вызывается `InstrumentsService.GetInstrumentBy` для каждого инструмента, метаданные кэшируются в памяти и в таблице `instruments`

### Requirement: Фильтрация по торговому статусу

Collector SHALL публиковать события Info (торговые статусы), но НЕ публиковать данные Candle/OrderBook/Trades по инструментам со статусом, отличным от `NORMAL_TRADING`.

#### Scenario: Инструмент в периоде открытия
- **WHEN** инструмент SBER имеет статус `OPENING_PERIOD`
- **THEN** Collector публикует только событие Info, данные Candle/OrderBook/Trades не публикуются
