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
│   ├── local/                     # bootstrap/up/down, env.sh, README.md — PG+Kafka в WSL2, без Docker Desktop
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
- PostgreSQL 15, Apache Kafka, Prometheus + Grafana (локально PostgreSQL и Kafka поднимаются
  `infra/local/up.sh` — Docker Engine в WSL2, без Docker Desktop)
- Токен T-Invest API — только в env-переменной `T_INVEST_TOKEN` (см. ниже)

### Переменные окружения T-Invest

| Переменная | Значение | Дефолт в `application.yml` |
|---|---|---|
| `T_INVEST_TOKEN` | сырой токен из личного кабинета T-Invest (`t.…`), **без** префикса `Bearer` | пусто — обязательна |
| `T_INVEST_SANDBOX` | `true` — sandbox, `false` — production | `true` |

`Bearer` добавляет сам SDK: `InvestApi.addAuthHeader(...)` формирует `Authorization: Bearer <token>`
из значения токена как есть. Если положить `Bearer` в переменную, на сервер уйдёт
`Authorization: Bearer Bearer …` и запрос вернёт `40003 UNAUTHENTICATED`.

```powershell
# Windows PowerShell
[Environment]::SetEnvironmentVariable("T_INVEST_TOKEN", "t.xxxxxxxx", "User")   # постоянно, новые процессы
$env:T_INVEST_TOKEN = "t.xxxxxxxx"                                              # текущая сессия
$env:T_INVEST_SANDBOX = "true"
```

```bash
# bash (WSL/Linux/CI)
export T_INVEST_TOKEN="t.xxxxxxxx"
export T_INVEST_SANDBOX=true
```

WSL не наследует переменные Windows: если токен задан через `SetEnvironmentVariable(..., "User")`,
внутри WSL2 его не видно и `mvn`/`spring-boot:run` из-под WSL получат пустой `tinkoff.token`.
Проброс — через `WSLENV` (флаги `/p`, `/l` не нужны: они только для путей и списков):

```powershell
# Windows PowerShell — один раз, User scope; действует при старте WSL из Windows
[Environment]::SetEnvironmentVariable("WSLENV", "T_INVEST_TOKEN:T_INVEST_SANDBOX", "User")
```

Альтернатива — `export` в `~/.bashrc` внутри WSL (дублирует секрет в домашнем каталоге WSL).

Sandbox-токен действует только против `sandbox-invest-public-api.tinkoff.ru:443` — этот адрес зашит
в `java-sdk-core:1.8` (`config.properties`: `ru.tinkoff.piapi.core.sandbox.target`; там же
`ru.tinkoff.piapi.core.api.target` — prod-контур), переопределять его не нужно. Документация T-Bank
приводит новый домен `sandbox-invest-public-api.tbank.ru:443`; оба хоста принимают один и тот же
sandbox-токен. Prod-токен с `T_INVEST_SANDBOX=true` получит ошибку авторизации.
`.env` в репозиторий не добавляется.

#### Сертификат sandbox-хоста (обязательно для Java-модулей)

TLS-цепочка `sandbox-invest-public-api.tinkoff.ru` замыкается на самоподписанный корень
**«Russian Trusted Root CA» (Минцифры)**. Его нет ни в `ca-certificates` Linux, ни в `cacerts` JDK,
поэтому Java падает при подключении с `PKIX path building failed` (curl при этом работает — ему
корень известен из системного/Windows-хранилища). JDK менять не нужно: создайте копию `cacerts`
с добавленным корнем и передайте её через `JAVA_TOOL_OPTIONS`.

```bash
# 1) root из TLS-цепочки (сервер отдаёт полную цепочку, третий сертификат — корень)
echo | openssl s_client -connect sandbox-invest-public-api.tinkoff.ru:443 \
  -servername sandbox-invest-public-api.tinkoff.ru -showcerts 2>/dev/null \
  | awk '/BEGIN CERTIFICATE/{n++} n==3' > /tmp/russian_root.pem

# 2) копия cacerts + корень
cp "$JAVA_HOME/lib/security/cacerts" /usr/local/share/scalping-truststore.p12
sudo keytool -importcert -noprompt -alias russian_trusted_root_ca -file /tmp/russian_root.pem \
  -keystore /usr/local/share/scalping-truststore.p12 -storepass changeit

# 3) запуск модулей с этим truststore
export JAVA_TOOL_OPTIONS="-Djavax.net.ssl.trustStore=/usr/local/share/scalping-truststore.p12 \
  -Djavax.net.ssl.trustStorePassword=changeit"
```

Без этого шага Collector падает на открытии стрима, а Trader — при определении `accountId`.

### Сборка (после реализации привязок SDK)
```bash
# Java-модули (генерация Protobuf + компиляция)
mvn compile

# Python
cd analyzer && poetry install && poetry run protoc ../proto/*.proto --python_out src/analyzer/
```

Для Python-генерации нужен **protoc 25.x** (`--version` → `libprotoc 25.3`): protobuf-runtime проекта
(`protobuf 4.25`) отвергает модули, сгенерированные protoc 3.12 из репозитория Ubuntu
(`Descriptors cannot be created directly`). Готовый бинарь: `protoc-25.3-linux-x86_64.zip` с GitHub
releases, распаковать в `/usr/local`. Модули `*_pb2.py` в git не хранятся (`.gitignore`) и создаются
этим шагом; без них Analyzer поднимает воркеров, но падает на первом сообщении.

`analyzer/poetry.lock` создаётся при `poetry install` — его следует держать в git для воспроизводимости
окружения.

### Запуск инфраструктуры

PostgreSQL 15 и Kafka в Docker **без Docker Desktop** (WSL2 + Docker Engine) — полный runbook
в [`infra/local/README.md`](infra/local/README.md).

```bash
bash infra/local/bootstrap-wsl-docker.sh   # однократно, внутри WSL2 Ubuntu
bash infra/local/up.sh                     # скачать образы, запустить, создать топики
bash infra/local/down.sh                   # остановить (--purge — вместе с volume БД и сетью)
```

```bash
# Kafka-топики вручную (если контейнер scalping-kafka уже запущен; имя — при дефолтном KAFKA_CONTAINER)
docker exec -e KAFKA_BOOTSTRAP=scalping-kafka:29092 -e KAFKA_BIN=/opt/kafka/bin \
  scalping-kafka bash -c 'tr -d "\r" < /opt/scalping/create-topics.sh | bash'
```

Flyway-миграции применяются автоматически при старте Trader.
Prometheus + Grafana — конфиги в `infra/prometheus/`, `infra/grafana/`.

### Запуск модулей
```bash
mvn -pl collector spring-boot:run   # порт 8081, /actuator/prometheus
cd analyzer && poetry run analyzer  # порт 8000, /metrics
mvn -pl trader spring-boot:run      # порт 8082, /actuator/prometheus
```

`collector.instruments` принимает `TICKER` или `TICKER@CLASS`: акции резолвятся в классе `TQBR`,
фьючерсы — в `SPBFUT` (не `FORTS`), регистр тикера значим (`SiZ6`, а не `SIZ6`). Текущий набор MVP:
`SBER`, `GAZP`, `SiZ6@SPBFUT`; у фьючерса есть экспирация — перед ней тикер нужно роллировать вручную.
`TCSG` и короткий `BR` через T-Invest API не резолвятся.

### Тесты
```bash
mvn test                           # Collector + Trader (unit; группы не разделены — см. ниже)
cd analyzer && poetry run pytest   # Analyzer (unit)
mvn test -Dgroups=e2e              # только e2e-тег (нужны sandbox-окружение и env T_INVEST_TOKEN)
```

Разделение unit/e2e в `pom.xml` не настроено: `mvn test` выполняет и классы с `@Tag("e2e")`,
тег `e2e` — информационный. Реальные sandbox-тесты гейтятся `@EnabledIfEnvironmentVariable(named = "T_INVEST_TOKEN")`.

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
