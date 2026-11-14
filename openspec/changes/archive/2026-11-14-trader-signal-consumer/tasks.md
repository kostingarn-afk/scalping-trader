# Tasks

## 1. Инструменты и резолв

- [x] 1.1 `TinkoffInstrumentLoader`: классы `SPBFUT`/`FORTS` для фьючерсов, сохранение регистра тикера, повтор в верхнем регистре
- [x] 1.2 `JdbcInstrumentRepository`: `SPBFUT` → `instrument_type=FUTURE`
- [x] 1.3 `collector/application.yml`, `analyzer/config.py`: `SBER`, `GAZP`, `SiZ6@SPBFUT`

## 2. Trader: инфраструктура обработки сигналов

- [x] 2.1 `SignalDeduplicator.tryAcquire`: возвращать `false` при конфликте (`ON CONFLICT DO NOTHING` не бросает исключение)
- [x] 2.2 `TradeRepository`: чтение лотности инструмента из `instruments`
- [x] 2.3 `ExchangeStateProvider` + `TinkoffExchangeStateProvider`: `tradingStatus(figi)`
- [x] 2.4 `RiskConfigPoller`: `@Scheduled`-загрузка `risk_config` из БД в `RiskManager.updateConfig`

## 3. Trader: consumer и оркестратор

- [x] 3.1 `SignalConsumer`: поток на `ApplicationReadyEvent`, poll `trading-signals`, разбор `TradingSignal`, коммит после успешной обработки, корректный shutdown
- [x] 3.2 `TradeOrchestrator`: цепочка dedupe → expiry → HOLD → trading status → risk → вход → SL/TP → публикация
- [x] 3.3 Идемпотентный `order_id` из `signal_id`; `trade_id` — новый UUID
- [x] 3.4 Запись `trade_results` + `positions`, `RiskManager.onPositionOpened`
- [x] 3.5 Публикация `TradeResult` (`OPENED`/`REJECTED`) в `trade-results`

## 4. Тесты

- [x] 4.1 `TradeOrchestratorTest`: дубликат, истёкший сигнал, HOLD, чужой торговый статус, risk-reject, успешное исполнение, отказ биржи, недоступность БД, частичное исполнение
- [x] 4.2 `SignalDeduplicatorTest`: повторный `signal_id` не обрабатывается
- [x] 4.3 `TradeRepository` лотность: дефолт для отсутствующего инструмента

## 5. Проверка

- [x] 5.1 `mvn test` — зелёный (Collector 10, Trader 43, 1 skipped по env-гейту)
- [x] 5.2 `poetry run pytest` (26) + `ruff` — зелёные
- [x] 5.3 Живая проверка: сигнал в `trading-signals` → `processed_signals.result=rejected`, событие `REJECTED` в `trade-results`; повторный сигнал не обработан (дедупликация). Успешное исполнение проверено юнит-тестами — торги закрыты, sandbox не пополнен
- [x] 5.4 `openspec validate trader-signal-consumer` — valid
