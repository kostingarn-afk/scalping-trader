# Signal Analysis Specification

## Purpose

Анализ рыночных данных из Kafka, расчёт технических индикаторов, генерация торговых сигналов с предлагаемыми SL/TP и публикация в Kafka для исполнения.

## Requirements

### Requirement: Расчёт технических индикаторов

Analyzer SHALL рассчитывать на основе полученных свечей и стаканов индикаторы: RSI (period 7), MACD (8,21,5), Bollinger Bands (20,2), ATR (14), VWAP, order imbalance (из стакана depth=10).

#### Scenario: Получена серия свечей по инструменту
- **WHEN** Analyzer получает свечи по SBER из Kafka `raw-market-data`
- **THEN** рассчитываются RSI, MACD, BB, ATR, VWAP; значения сохраняются в поле `indicators` (JSONB) записи `analysis_results`

#### Scenario: Получен стакан
- **WHEN** Analyzer получает стакан по BR
- **THEN** рассчитывается order imbalance (bid_vol - ask_vol)/(bid_vol + ask_vol) и сохраняется в `indicators.order_imbalance`

### Requirement: Генерация сигнала BUY/SELL/HOLD

Analyzer SHALL формировать сигнал на основе индикаторов с confidence (0.0–1.0), предлагаемой ценой входа, SL (от ATR) и TP, и временем устаревания (expires_at).

#### Scenario: Сигнал на покупку
- **WHEN** RSI < 30 и цена касается нижней BB (mean reversion)
- **THEN** генерируется сигнал BUY с confidence, suggested_price (лимитная), suggested_sl (entry - 1.5×ATR), suggested_tp (entry + 2×ATR), expires_at = now + 30с

#### Scenario: Нет сигнала
- **WHEN** индикаторы не дают уверенного сигнала
- **THEN** генерируется сигнал HOLD, в Kafka не публикуется (только сохраняется в БД)

### Requirement: Сохранение результатов анализа в БД

Analyzer SHALL сохранять каждый результат анализа в таблицу `analysis_results` с уникальным `signal_id` (UUID), `strategy_id`, `analyzer_version`.

#### Scenario: Сохранение результата
- **WHEN** Analyzer генерирует сигнал
- **THEN** в `analysis_results` вставляется запись с signal_id, ts, instrument_id, indicators, signal, confidence, suggested_price/sl/tp, expires_at

### Requirement: Публикация сигнала в Kafka

Analyzer SHALL публиковать сигналы BUY/SELL в Kafka-топик `trading-signals` в формате Protobuf (сообщение `TradingSignal`). HOLD не публикуется.

#### Scenario: Публикация BUY-сигнала
- **WHEN** сгенерирован сигнал BUY по TCSG
- **THEN** в Kafka `trading-signals` публикуется сообщение TradingSignal с signal_id, figi, signal=BUY, confidence, suggested_price/sl/tp, expires_at

### Requirement: Multi-process масштабирование

Analyzer SHALL работать как N процессов-консьюмеров в одной consumer group, с partitioning по `instrument_id` для распределения нагрузки.

#### Scenario: Несколько инструментов
- **WHEN** configured 3 инструмента и 2 процесса-консьюмера
- **THEN** инструменты распределяются между процессами по partition key instrument_id, каждый процесс обрабатывает свои инструменты

### Requirement: Комиссионная фильтрация сигналов

Analyzer SHALL оценивать потенциальную валовую прибыль сигнала и отбрасывать сигналы, где прибыль менее 3× суммарной комиссии (вход + выход).

#### Scenario: Сигнал не проходит комиссионный фильтр
- **WHEN** ожидаемая прибыль от сигнала < 3× комиссии
- **THEN** сигнал отбрасывается (HOLD), в Kafka не публикуется, в БД сохраняется с пометкой причины отклонения
