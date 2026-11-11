# Design

## Context

Greenfield-проект: система автоматизированного скальпинга на Мосбирже через T-Invest API. ТЗ дописано (`docs/Техническое задание.md`, 440 строк) — контракты данных (Protobuf), БД (7 таблиц), риск-лимиты, NFR зафиксированы. Депозит 200 000 руб, MVP — 3 инструмента (BR, SBER, TCSG). Стек: Java 21 + Spring Boot (Collector, Trader), Python 3.11 (Analyzer), PostgreSQL 15, Kafka, Prometheus.

Архитектурные блок-схемы: `docs/Qwen__20261107_nt05xsewk.txt` (упрощённая), `docs/Qwen__20261107_pdsk2v2hz.txt` (подробная).

## Goals / Non-Goals

**Goals:**
- Рабочий MVP pipeline: Collector → Kafka → Analyzer → Kafka → Trader → Kafka, 3 инструмента
- Биржевая защита позиций (SL/TP через StopOrdersService)
- PnL Circuit Breaker и все риск-лимиты из ТЗ
- Recovery при рестарте Trader
- Метрики + alerting

**Non-Goals:**
- ML-классификация сигналов (lightgbm) — отложено, MVP на ta-lib индикаторах
- Бэктестинг (vectorbt) — отдельная задача
- Парный скальпинг / коинтеграция — MVP на одиночных сигналах
- Web UI / дашборд — Grafana только для метрик
- Автоматическая ротация токена — ручной перевыпуск с alerting
- HA-разворачивание (replica, failover) — single-instance для MVP
- 100 инструментов — потолок; MVP = 3

## Decisions

### D1. gRPC-стримы вместо polling
**Решение:** `MarketDataStreamService` (bi-directional stream) для Candles + OrderBook + Trades + Info.
**Альтернатива:** REST polling `GetCandles` — отвергнута: задержка ≥ периода опроса, несовместима со скальпингом и NFR pipeline ≤ 2с.
**Обоснование:** T-Invest API 3.3 явно рекомендует стримы для real-time; gRPC/HTTP/2 — низкая задержка.

### D2. Protobuf для Kafka-сообщений
**Решение:** Protobuf-схемы в репо (`proto/`), генерация кода при сборке. Без внешнего schema registry для MVP.
**Альтернатива:** Avro + Schema Registry — добавляет инфраструктуру (Confluent), избыточно для MVP.
**Обоснование:** T-Invest API уже на Protobuf — единый формат, переиспользование .proto-определений (MoneyValue, Quotation).

### D3. Биржевые стоп-ордера
**Решение:** SL/TP через `StopOrdersService.PostStopOrder` (биржевая сторона). Локальный мониторинг дублирует для alerting, не для исполнения.
**Альтернатива:** Локальный SL (Trader следит за ценой и закрывает) — отвергнут: при падении Trader = неограниченный убыток.
**Обоснование:** recomendation.md 3.4 — «категорически запрещено полагаться на Java-модуль».

### D4. LIMIT/BESTPRICE, запрет MARKET на вход
**Решение:** Основной тип — LIMIT (по suggested_price из сигнала). BESTPRICE — опция. MARKET запрещён на вход.
**Обоснование:** Проскальзывание на market-ордере критично для депозита 200к (recomendation.md 3.3). MARKET допустим только для emergency-close (PnL-CB).

### D5. Multi-process Analyzer
**Решение:** N процессов-консьюмеров в одной Kafka consumer group, partitioning по `instrument_id`. ta-lib отпускает GIL, но для масштабирования — процессы.
**Альтернатива:** asyncio + single-process — упирается в GIL для CPU-bound индикаторов.
**Параметр:** 2 процесса для MVP (3 инструмента), настраивается.

### D6. Flyway, владелец миграций — Java Trader
**Решение:** Flyway в Java-модуле Trader применяет все миграции (7 таблиц). Python Analyzer пишет только в `analysis_results` через asyncpg/SQLAlchemy (read-only к остальным).
**Альтернатива:** Миграции в Python — конфликт с Java-стеком, два мигратора на одну БД.
**Обоснование:** Java — основной модуль с транзакционной записью; Python — аналитический writer одной таблицы.

### D7. Recovery через GetPositions/GetOrders/GetStopOrders
**Решение:** При старте Trader вызывает `GetPositions`, `GetOrders`, `GetStopOrders`, сверяет с БД `positions`/`orders`, восстанавливает in-memory состояние, подписывается на `TradesStream` + `PositionsStream`.
**Альтернатива:** Только из БД — БД может отставать от биржи (crash до коммита).
**Обоснование:** Биржа — source of truth для позиций и ордеров.

### D8. Дедупликация сигналов — processed_signals + TTL
**Решение:** Таблица `processed_signals` с TTL 1 час (записи старше 1ч удаляются). Проверка signal_id перед обработкой.
**Альтернатива:** Kafka-транзакции + exactly-once — сложнее, требует transactional consumer/producer.
**Обоснование:** Сигналы устаревают за секунды (expires_at), окно 1ч — с запасом. At-least-once + дедупликация = effectively-once.

### D9. PnL-CB — per-session, сброс по TradingSchedules
**Решение:** PnL-CB активен до конца торговой сессии. Сброс — при наступлении новой сессии (расписание из `InstrumentsService.TradingSchedules`).
**Альтернатива:** Сброс по времени (00:00) — не учитывает клиринг, вечерние сессии.

## Risks / Trade-offs

| Риск | Mitigation |
|------|------------|
| gRPC-стрим разрывается при сетевых проблемах | Autoreconnect с exponential backoff (1с→30с), alert при нереверении 30с |
| Токен истекает (3 месяца) | Alert за 7 дней до истечения, halt при ошибке 40003, ручной перевыпуск |
| Kafka consumer lag при пиковом потоке | Метрика consumer_lag, alert >1000, scale Analyzer processes |
| Частичные исполнения (PARTIALLY_FILL) | Обрабатывать как OPENED + PARTIAL_FILL событие, отслеживать через TradesStream |
| Биржа отклоняет стоп-ордер | Логировать, alert, позиция без SL — emergency-close market-ордером |
| Python GIL при росте числа инструментов | Multi-process, partitioning; при >10 инструментов — увеличить число процессов |
| Двойная запись (БД + Kafka) — рассинхрон | Kafka — event log, БД — source of truth для запросов; idempotent writes |
| Грейд API 1 (2 стрима) — мало для роста | MVP на 3 инструментах влезает в 1 соединение; для роста — повысить грейд (200+ заявок/мес) |

## Migration Plan

1. **Sandbox-фаза:** всё на SandboxService (эмуляция), без реальных денег
2. **Paper trading:** реальный API, readonly-токен, сигналы логируются без исполнения
3. **Prod малый объём:** full-access токен, 1 инструмент (BR), 1 лот, tight risk_config
4. **Prod MVP:** 3 инструмента (BR, SBER, TCSG), полные лимиты из ТЗ

Rollback: каждый модуль независим, останавливается без потери данных (Kafka retention, БД persistent). Trader stop = биржевые стопы продолжают работать.

## Open Questions

- **Стратегия сигнала**: mean reversion (BB+Z-score) или momentum breakout (volume+ROC) как основная для MVP? Обе описаны в math-док. Рекомендую начать с mean reversion (проще, стабильнее для отладки), добавить momentum как вторую стратегию. Решается в реализации strategy_id.
- **BESTPRICE доступность**: зависит от инструмента и торгового статуса (`market_order_available_flag`). Нужно проверить для BR/SBER/TCSG на sandbox. Если недоступен — только LIMIT.
- **Частота обновления метаданных инструментов**: daily или при старте? Рекомендую при старте + daily refresh.
