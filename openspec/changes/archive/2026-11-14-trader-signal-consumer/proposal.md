# Proposal

## Why

Сигналы Analyzer попадают в Kafka `trading-signals`, но Trader их не читает: consumer-цикла и
оркестратора (dedupe → expiry → risk → исполнение → SL/TP → публикация) в коде нет. Проверка
2026-11-14 подтвердила факт: 7 BUY-сигналов остались в топике необработанными.

Одновременно проверка T-Invest API показала, что инструменты MVP-конфигурации `BR` и `TCSG`
не резолвятся (`BR` — фьючерс с экспирацией, тикер содержит месяц/год; `TCSG` — редомициляция
TCS Group), поэтому Collector фактически собирает данные по одному инструменту вместо трёх.

## What Changes

- **Trader: consumer торговых сигналов.** Чтение `trading-signals` отдельным потоком, разбор
  Protobuf `TradingSignal`, коммит offset только после обработки.
- **Trader: оркестратор сделки.** Цепочка: дедупликация по `signal_id` → проверка `expires_at` →
  отбрасывание HOLD → проверка торгового статуса → risk-check (`RiskManager`) → вход LIMIT →
  запись `trade_results`/`positions` → биржевые SL/TP → публикация `TradeResult` в `trade-results`.
- **Trader: исправление дедупликации.** `SignalDeduplicator.tryAcquire` возвращал `true` для
  дубликата: `INSERT ... ON CONFLICT DO NOTHING` не бросает исключение.
- **Trader: загрузка risk-параметров из БД.** `RiskManager.updateConfig` нигде не вызывался —
  вступали только дефолты; добавляется периодический poll по `trader.poll-risk-config-cron`.
- **Trader: торговый статус инструмента.** `ExchangeStateProvider` расширяется методом
  `tradingStatus(figi)` (реализация — `MarketDataService.GetTradingStatus`).
- **Конфигурация инструментов:** `BR`, `TCSG` → `SBER`, `GAZP`, `SiZ6@SPBFUT`.
- **Collector: класс фьючерсов.** Резолв фьючерсов в классе `SPBFUT` (актуальный в T-Invest API)
  с сохранением регистра тикера; `FORTS` остаётся как fallback.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `market-data-collection`: сценарий старта подписки перечисляет [BR, SBER, TCSG]; фактический
  набор инструментов MVP меняется на [SBER, GAZP, SiZ6] (`SiZ6` — фьючерс класса `SPBFUT`).

## Impact

- Код: `trader/src/main/java/ru/scalping/trader/{signal,execution,repository,recovery}` (новые
  `SignalConsumer`, `TradeOrchestrator`, `RiskConfigPoller`), `collector/.../instrument/TinkoffInstrumentLoader.java`.
- Конфигурация: `collector/src/main/resources/application.yml`, `analyzer/src/analyzer/config.py`,
  `trader/src/main/resources/application.yml`.
- БД: используются существующие таблицы `processed_signals`, `trade_results`, `positions`,
  `instruments`, `risk_config` — миграции не меняются.
- Kafka: чтение `trading-signals`, запись `trade-results`.
- Ограничение внешнего API: биржевые SL/TP в sandbox недоступны (`SandboxModeViolationException`),
  поэтому в песочнице защитные ордера логируются как отклонённые и проверяются только в prod-контуре.
