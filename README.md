# Scalping Trading System

Автоматизированная система скальпинга на Московской бирже через [T-Invest API](https://developer.tbank.ru/).
Депозит 200 000 ₽, MVP — 3 инструмента (BR, SBER, TCSG). Pipeline:
Collector → Kafka → Analyzer → Kafka → Trader → Kafka с жёстким риск-контролем и биржевыми SL/TP.

## Текущий этап

**MVP реализован (41/41 задач OpenSpec), заархивирован 2026-11-11.** Код написан, юнит- и E2E-скелет-тесты
на месте, но проект не собран и не запущен — нужен `mvn compile`, `poetry install`, генерация
Protobuf-кода для Python и реализация привязок к T-Invest SDK (интерфейсы вынесены из бизнес-логики).

| Блок | Статус | Что есть | Чего нет |
|------|--------|----------|----------|
| Protobuf-схемы | ✅ | 4 `.proto` | Python-генерация в CI |
| Flyway (7 таблиц) | ✅ | V1, V2 + seed `risk_config` | — |
| Kafka-топики | ✅ | `create-topics.sh` | Запуск на стенде |
| Collector (Java) | ✅ скелет | gRPC-подписка, фильтр, reconnect, метрики | Привязка к SDK, сборка |
| Analyzer (Python) | ✅ скелет | индикаторы, стратегия, фильтр, БД, метрики | `protoc` build-hook |
| Trader (Java) | ✅ скелет | risk, PnL-CB, ордера, SL/TP, recovery, метрики | Привязка к SDK, сборка |
| Observability | ✅ | Prometheus, Grafana, alerts | Запуск на стенде |
| E2E | ⚠️ скелет | PnL-CB, 3 инструмента, NFR — unit; pipeline, recovery — заглушки | testcontainers/SDK |

## Архитектура

```
T-Invest API (gRPC)
    │
    ▼
[Collector] ──▶ Kafka: raw-market-data ──▶ [Analyzer] ──▶ Kafka: trading-signals ──▶ [Trader] ──▶ Kafka: trade-results
   Java 21          Protobuf                 Python 3.11        Protobuf               Java 21         Protobuf (compact)
```

| Компонент | Стек | Назначение |
|-----------|------|------------|
| Collector | Java 21 + Spring Boot 3 | gRPC-стримы `MarketDataStreamService` (Candles, OrderBook, Trades, Info) → Kafka |
| Analyzer | Python 3.11 + ta-lib/numpy/scipy | Индикаторы (RSI, MACD, BB, ATR, VWAP, imbalance), сигналы mean reversion → Kafka |
| Trader | Java 21 + Spring Boot 3 | Дедупликация, risk-check, `PostOrder` (LIMIT), биржевые SL/TP, PnL-CB, recovery |
| PostgreSQL 15 | Flyway (в Trader) | 7 таблиц: instruments, analysis_results, trade_results, positions, orders, risk_config, processed_signals |
| Kafka | 3 топика | raw-market-data (3мин), trading-signals (3дня), trade-results (compact) |
| Prometheus + Grafana | Micrometer + prometheus_client | Метрики + alerting (API disconnect, token error, PnL-CB, stale data, consumer lag) |

## Структура репозитория

```
.
├── docs/                          # Исходная документация (см. ниже)
├── proto/                         # Protobuf-схемы (common, raw_market_data, trading_signal, trade_result)
├── collector/                     # Java/Maven — Data Collector
├── analyzer/                      # Python/poetry — Analyzer
├── trader/                        # Java/Maven — Trader + Flyway-миграции
├── infra/                         # Kafka-топики, Prometheus, Grafana
│   ├── kafka/create-topics.sh
│   ├── prometheus/                # prometheus.yml + rules/scalping-alerts.yml
│   └── grafana/dashboards/        # scalping-mvp.json
├── openspec/                      # OpenSpec: proposal, specs, design, tasks
└── pom.xml                        # Maven multi-module (collector + trader)
```

## Документация

Исходная документация проекта — в [`docs/`](docs/). Начинать с ТЗ, далее по ссылкам из него:

| Файл | Содержание |
|------|------------|
| [`docs/Техническое задание.md`](docs/Техническое%20задание.md) | Архитектурная постановка: требования, БД (7 таблиц), Protobuf-контракты, NFR |
| [`docs/T-Invest API.md`](docs/T-Invest%20API.md) | Описание T-Invest API (gRPC-стримы, ордера, стопы) |
| [`docs/Математические библиотеки для анализа.md`](docs/Математические%20библиотеки%20для%20анализа.md) | ta-lib, numpy, scipy — индикаторы и стратегии |
| [`docs/recommendation.md`](docs/recommendation.md) | Рекомендации по работе с депозитом (лимиты, комиссии, стопы) |
| [`docs/Qwen__20261107_nt05xsewk.txt`](docs/Qwen__20261107_nt05xsewk.txt) | Архитектура упрощённая (блок-схема) |
| [`docs/Qwen__20261107_pdsk2v2hz.txt`](docs/Qwen__20261107_pdsk2v2hz.txt) | Архитектура подробная (блок-схема) |

Спецификации изменения (OpenSpec, архив 2026-11-11) — в
[`openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/`](openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/):
`proposal.md`, `design.md` (9 архитектурных решений), `tasks.md` (41 задача), `specs/` (5 capability-спеков:
`market-data-collection`, `signal-analysis`, `trade-execution`, `risk-management`, `observability`).

## Как работать

### Требования
- Java 21, Maven 3.9+
- Python 3.11, poetry, ta-lib (системная C-библиотека)
- PostgreSQL 15, Apache Kafka, Prometheus + Grafana
- Токен T-Invest API (env `T_INVEST_TOKEN`)

### Сборка (после реализации привязок SDK)
```bash
# Java-модули (генерация Protobuf + компиляция)
mvn compile

# Python
cd analyzer && poetry install && poetry run protoc ../proto/*.proto --python_out src/analyzer/
```

### Запуск инфраструктуры
```bash
# Kafka-топики
KAFKA_BOOTSTRAP=localhost:9092 KAFKA_BIN=/opt/kafka/bin infra/kafka/create-topics.sh

# PostgreSQL — Flyway-миграции применяются автоматически при старте Trader
# Prometheus + Grafana — конфиги в infra/prometheus/, infra/grafana/
```

### Запуск модулей
```bash
mvn -pl collector spring-boot:run   # порт 8081, /actuator/prometheus
cd analyzer && poetry run analyzer  # порт 8000, /metrics
mvn -pl trader spring-boot:run      # порт 8082, /actuator/prometheus
```

### Тесты
```bash
mvn test                           # Collector + Trader (unit)
cd analyzer && poetry run pytest   # Analyzer (unit)
mvn test -Dgroups=e2e              # E2E (требует sandbox-окружение)
```

### OpenSpec
```bash
# Изменение заархивировано 2026-11-11 — команды status/apply/archive неприменимы.
# Артефакты: openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/
```

## Риск-лимиты (risk_config)

Параметры настраиваются без передеплоя (таблица `risk_config`, poll в Trader):

| Параметр | Значение | Описание |
|----------|----------|----------|
| max_loss_per_trade_pct | 1.0 | Убыток на сделку, % депозита |
| pnl_circuit_breaker_pct | 3.0 | Дневной лимит убытка (PnL-CB), % |
| commission_profit_ratio | 3.0 | Мин. отношение прибыль/комиссия |
| max_open_positions | 3 | Максимум одновременных позиций |
| max_holding_time_minutes | 10 | Таймаут удержания → auto-close |
| correlation_threshold | 0.8 | Порог корреляции для запрета |
| max_trades_per_minute | 10 | Self-rate-limit |

## NFR

- Pipeline latency ≤ 2с (p99): gRPC ≤ 50мс, Kafka produce ≤ 20мс, Analyzer ≤ 500мс, Trader ≤ 200мс
- Исполнение ордера ≤ 500мс (p99)
- MVP: 3 инструмента; потолок — 100 (зависит от грейда API)
