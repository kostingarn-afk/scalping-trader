# Spec Delta

## Purpose

Мониторинг системы: метрики Prometheus (Java + Python), бизнес-метрики (PnL, latency, win-rate), alerting при критических событиях.

## ADDED Requirements

### Requirement: Метрики Prometheus для всех модулей

Collector, Trader (Java) и Analyzer (Python) SHALL экспортировать метрики Prometheus. Java — Micrometer, Python — prometheus_client.

#### Scenario: Сбор метрик
- **WHEN** Prometheus скрейпит /metrics endpoint каждого модуля
- **THEN** возвращаются метрики: JVM/Python runtime, Kafka consumer lag, messages processed, errors count

### Requirement: Бизнес-метрики

Система SHALL экспортировать бизнес-метрики: realized/unrealized PnL, drawdown, win rate, SL/TP hit count, latency per stage (collector→analyzer→trader), signals/min, trades/min, rejects/min.

#### Scenario: Метрика PnL
- **WHEN** сделка закрыта с PnL +150 руб
- **THEN** метрика `scalping_realized_pnl_total` увеличивается на 150, `scalping_win_rate` пересчитывается

#### Scenario: Метрика latency
- **WHEN** сигнал проходит pipeline Collector→Analyzer→Trader за 800 мс
- **THEN** метрика `scalping_pipeline_latency_seconds` обновляется (histogram)

### Requirement: Alerting при критических событиях

Система SHALL генерировать алерты при: API disconnect, token error (40003), PnL-CB сработал, stale data (>30с без данных), consumer lag >1000, ордер rejected, токен истекает через 7 дней.

#### Scenario: Потеря API-соединения
- **WHEN** gRPC-стрим разрывается и не восстанавливается 30 секунд
- **THEN** генерируется alert "api_disconnect" (Prometheus alerting rule + notification)

#### Scenario: Токен истекает
- **WHEN** до истечения токена осталось 7 дней
- **THEN** генерируется alert "token_expiring_soon" с датой истечения

#### Scenario: Stale data
- **WHEN** по инструменту SBER нет данных в Kafka `raw-market-data` более 30 секунд
- **THEN** генерируется alert "stale_data" с указанием инструмента
