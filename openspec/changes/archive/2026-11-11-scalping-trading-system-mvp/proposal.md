# Proposal

## Why

Требуется автоматизированная система скальпинга на Мосбирже через T-Invest API для депозита 200 000 руб. Ручной скальпинг не масштабируется по числу инструментов и скорости реакции; депозит 200к критически чувствителен к комиссиям, проскальзыванию и просадкам, поэтому исполнение должно быть автоматическим с жёстким риск-контролем. ТЗ дописано (контракты данных, БД, риск-лимиты), now — спроектировать и реализовать MVP на 3 инструментах (BR, SBER, TCSG).

## What Changes

- **New: Data Collector** (Java 21 + Spring Boot) — подписка на gRPC-стримы `MarketDataStreamService` (Candles + OrderBook + Trades + Info), публикация в Kafka `raw-market-data`, автопереподключение
- **New: Analyzer** (Python 3.11 + ta-lib/numpy/scipy) — consume `raw-market-data`, расчёт индикаторов (RSI, MACD, BB, ATR, VWAP, order imbalance), генерация сигналов BUY/SELL/HOLD, запись в PostgreSQL `analysis_results`, публикация в Kafka `trading-signals`
- **New: Trader** (Java 21 + Spring Boot) — consume `trading-signals`, дедупликация по `signal_id`, risk-check, исполнение через `PostOrder` (LIMIT/BESTPRICE, запрет MARKET), биржевые стоп-ордера (`StopOrdersService`), мониторинг позиций (`PositionsStream`), PnL Circuit Breaker, recovery при старте, публикация в Kafka `trade-results`
- **New: Risk Management** — динамический размер позиции (1%/сделку), комиссионный фильтр 3-4x, max_positions=3, max_holding_time=10мин, корреляционный лимит, PnL-CB 3% дневного, параметры в `risk_config`
- **New: Infrastructure** — PostgreSQL 15 (7 таблиц, Flyway-миграции), Kafka (3 топика, Protobuf), Prometheus + Grafana + alerting (Java + Python)
- **BREAKING**: нет — greenfield, существующего кода нет

## Capabilities

### New Capabilities

- `market-data-collection`: сбор рыночных данных через gRPC-стримы T-Invest API (Candles, OrderBook, Trades, Info) по списку инструментов, публикация в Kafka `raw-market-data`, переподключение при сбоях, кэш метаданных инструментов
- `signal-analysis`: потребление `raw-market-data`, расчёт технических индикаторов (RSI, MACD, BB, ATR, VWAP, order imbalance), генерация сигналов с confidence и предлагаемыми SL/TP, запись в `analysis_results`, публикация в `trading-signals`, multi-process масштабирование
- `trade-execution`: потребление `trading-signals`, дедупликация по `signal_id`, исполнение ордеров (LIMIT/BESTPRICE), выставление биржевых SL/TP через `StopOrdersService`, мониторинг позиций через `PositionsStream`, auto-close по таймауту, recovery при старте (синхронизация с `GetPositions`/`GetOrders`), публикация в `trade-results`
- `risk-management`: проверка рисков перед исполнением (убыток 1%/сделку, комиссионная рентабельность 3-4x, max_positions, max_holding_time, корреляционный лимит, self-rate-limit), PnL Circuit Breaker (3% дневного → close all + блок до след.сессии), параметры в `risk_config` (настраиваемые без передеплоя)
- `observability`: метрики Prometheus (Java + Python), бизнес-метрики (PnL, latency, win-rate, drawdown, consumer-lag), alerting (API disconnect, token error, PnL-CB, stale data, reject, token expiry)

### Modified Capabilities

<!-- Greenfield — нет существующих specs -->

## Impact

- **Новый код**: 3 модуля (Java Collector, Python Analyzer, Java Trader) — greenfield, каталоги `collector/`, `analyzer/`, `trader/`
- **Инфраструктура**: PostgreSQL 15 (Flyway-миграции, 7 таблиц), Apache Kafka (3 топика, Protobuf-схемы в репо), Prometheus + Grafana
- **Внешние зависимости**: T-Invest API (gRPC, Java SDK 1.8, Python SDK), ta-lib, numpy, scipy, prometheus_client
- **Конфигурация**: список инструментов (BR, SBER, TCSG), risk_config (лимиты), API-токен (env/Vault), грейд API
- **Документация**: `docs/Техническое задание.md` — уже дописано, используется как вход
