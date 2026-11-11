# AGENTS.md

Инструкции для ИИ-агентов, работающих с этим репозиторием. Читай перед любым изменением кода.

---

## 1. Project Overview

Автоматизированная система скальпинга на Московской бирже через [T-Invest API](https://developer.tbank.ru/).
Депозит 200 000 ₽, MVP — 3 инструмента (BR, SBER, TCSG). Pipeline:
Collector → Kafka → Analyzer → Kafka → Trader → Kafka с жёстким риск-контролем и биржевыми SL/TP.

Полное ТЗ — [`docs/Техническое задание.md`](docs/Техническое%20задание.md). Архитектурные решения —
[`openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/design.md`](openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/design.md).

## 2. Tech Stack

- **Collector, Trader**: Java 21, Spring Boot 3.2, Maven (multi-module), Micrometer + Prometheus
- **Analyzer**: Python 3.11, poetry, ta-lib, numpy, scipy, asyncpg, prometheus_client
- **Protobuf**: 3.25, схемы в `proto/`, генерация при сборке (Java — `protobuf-maven-plugin`, Python — `protoc`)
- **T-Invest SDK**: `ru.tinkoff.piapi:java-sdk-core:1.8` (Java, Maven Central; координат `sdk-java` не существует,
  репозиторий `opensource.tbank.ru` требует авторизации); Python — по .proto
- **БД**: PostgreSQL 15, Flyway (владелец миграций — модуль `trader`)
- **Брокер**: Apache Kafka, 3 топика (`raw-market-data`, `trading-signals`, `trade-results`)
- **Мониторинг**: Prometheus + Grafana, alerting rules
- **Тесты**: JUnit 5 + Mockito + Testcontainers (Java); pytest + pytest-asyncio + testcontainers (Python)
- **Спеки**: OpenSpec (schema `spec-driven`), change `scalping-trading-system-mvp` заархивирован `2026-11-11`

## 3. Architecture

Три независимых модуля + общие proto-схемы + инфраструктура. Модули не зависят друг от друга на уровне
кода, общаются только через Kafka (Protobuf-сообщения).

```
.
├── proto/                         # Protobuf-схемы — единый контракт для всех модулей
├── collector/                     # Java/Maven — сбор рыночных данных (gRPC-стримы → Kafka)
├── analyzer/                      # Python/poetry — индикаторы + сигналы (Kafka → Kafka)
├── trader/                        # Java/Maven — исполнение + риск + позиции (Kafka → Kafka)
│   └── src/main/resources/db/migration/  # Flyway: V1 (7 таблиц), V2 (risk_config seed + TTL)
├── infra/                         # kafka/create-topics.sh, prometheus/, grafana/
│   └── local/                     # bootstrap/up/down, env.sh, README.md — PostgreSQL 15 + Kafka в WSL2 (Docker Engine, без Docker Desktop)
├── docs/                          # исходная документация (ТЗ, API, матбиблиотеки, блок-схемы)
└── openspec/                      # proposal, specs, design, tasks
```

### Структура модулей

**Collector** (`collector/src/main/java/ru/scalping/collector/`):
- `config/` — `CollectorProperties` (instruments, reconnect, depth), `KafkaConfig`
- `converter/` — `TinvestConverter` (T-Invest → Protobuf)
- `instrument/` — `InstrumentCache`, `InstrumentLoader` (интерфейс), `InstrumentMeta`, `InstrumentRepository`
- `publish/` — `MarketDataPublisher`, `TradingStatusFilter`
- `stream/` — `MarketStreamService` (интерфейс), `StreamSubscriptionManager` (reconnect), `CollectorStartup`
- `metrics/` — `CollectorMetrics`

**Analyzer** (`analyzer/src/analyzer/`):
- `config.py`, `indicators.py`, `strategy.py`, `commission_filter.py`
- `consumer.py` (multi-process), `signal_publisher.py`, `repository.py`, `metrics.py`, `main.py`

**Trader** (`trader/src/main/java/ru/scalping/trader/`):
- `risk/` — `RiskConfig`, `RiskManager`, `RiskCheckResult`, `PnlCircuitBreaker`
- `signal/` — `SignalDeduplicator`, `SignalValidator`, `SignalProcessor`
- `execution/` — `OrderExecutionService` (интерфейс), `OrderRequest`, `OrderResult`, `OrderType`
- `position/` — `Position`, `PositionStore`, `AutoCloseService`
- `recovery/` — `RecoveryService`, `ExchangeStateProvider` (интерфейс)
- `publish/` — `TradeResultPublisher`
- `repository/` — `TradeRepository`
- `metrics/` — `TraderMetrics`
- `config/` — `TraderKafkaConfig`

### Правила слоёв

- **Интерфейсы SDK** (`MarketStreamService`, `InstrumentLoader`, `OrderExecutionService`,
  `ExchangeStateProvider`) — абстракции; реализация привязки к T-Invest SDK подставляется через Spring-профиль.
  Бизнес-логика не импортирует типы SDK напрямую.
- **Trader — единственный владелец Flyway-миграций.** Analyzer пишет в `analysis_results` через asyncpg,
  не управляет схемой.
- **Protobuf-контракты** (`proto/`) — source of truth для форматов сообщений. Не дублируй поля в коде.
- **risk_config** — параметры риск-менеджмента в БД, подхватываются без передеплоя (poll в Trader).
  Не хардкодь лимиты в Java/Python-коде.

## 4. Coding Conventions

### Java (Collector, Trader)
- Java 21, `record` для DTO, `var` только для локальных переменных с очевидным типом.
- Пакеты: `ru.scalping.<module>.<layer>`. Классы — `PascalCase`, методы/поля — `camelCase`.
- Spring-компоненты — `@Component`/`@Service`/`@Configuration`, конструктор-инъекция (без `@Autowired` на полях).
- Конфигурация — `@ConfigurationProperties` (record), не `@Value` для составных параметров.
- Логирование — SLF4J (`LoggerFactory.getLogger`), не `System.out`.
- Метрики — Micrometer (`Counter`, `Gauge`, `Histogram`), имена с суффиксами `_total` / `_seconds`.
- Протобаф-сообщения собираются через `Builder`; конвертация — в `TinvestConverter` (Collector).
- Тесты — JUnit 5 + AssertJ + Mockito. Имя теста — `methodName_scenario_expectedResult`.

### Python (Analyzer)
- Python 3.11, type hints везде (`from __future__ import annotations`).
- `snake_case` для функций/переменных/модулей, `PascalCase` для классов.
- `dataclass(frozen=True)` для неизменяемых структур (Signal, Indicators, AnalyzerConfig).
- Async — `async`/`await` для I/O (asyncpg, Kafka). ta-lib — синхронный (CPU-bound, отпускает GIL).
- Логирование — `logging.getLogger(__name__)`, не `print`.
- Метрики — `prometheus_client` (`Counter`, `Histogram`, `Gauge`).
- Тесты — pytest + pytest-asyncio, `test_*.py`, `assert` без `assertEqual`.

### Общее
- Комментарии — только для сложной бизнес-логики («почему», не «что»). Javadoc/docstring на публичный API.
- Секреты (токены, пароли) — только через env-переменные (`${T_INVEST_TOKEN}`, `${DB_PASSWORD}`), никогда в коде.
- `T_INVEST_TOKEN` — сырой токен T-Invest (`t.…`) **без** префикса `Bearer`: SDK (`InvestApi.addAuthHeader`) добавляет
  `Bearer ` сам, иначе заголовок станет `Bearer Bearer …` и вернётся `40003 UNAUTHENTICATED`.
  Режим — `T_INVEST_SANDBOX` (`true` по умолчанию из `application.yml`); sandbox-токен против prod-эндпоинта не работает.
- `tinkoff.sandbox` биндить как `boolean` (не `String`): строковое поле читает `"false"` как непустое значение
  и режим контура определяется неверно. Эндпоинты берутся из SDK, не хардкодь их в конфиге.
- WSL не наследует переменные Windows — запуск модулей из WSL требует `WSLENV` либо `export`
  (README, «Переменные окружения T-Invest»).
- Сообщения коммитов — `type(scope): description` (feat/fix/refactor/test/docs).

## 5. Commands

### Java (Collector + Trader)
```bash
mvn compile                                    # компиляция + генерация Protobuf
mvn test                                       # Collector + Trader (unit; группы не разделены, см. §6)
mvn test -Dgroups=e2e                          # только e2e-тег (тег информационный; нужны sandbox и env T_INVEST_TOKEN)
mvn -pl collector spring-boot:run              # запуск Collector (порт 8081)
mvn -pl trader spring-boot:run                 # запуск Trader (порт 8082)
# Flyway: миграции применяются автоматически при старте Trader; flyway-maven-plugin не настроен
```

### Python (Analyzer)
```bash
cd analyzer
poetry install
poetry run protoc ../proto/*.proto --python_out src/analyzer/   # генерация Protobuf
poetry run pytest                                               # тесты
poetry run pytest tests/test_strategy.py -k buy_signal          # конкретный тест
poetry run ruff check .                                         # линтер
poetry run analyzer                                             # запуск (порт 8000)
```

### Инфраструктура
```bash
# Локальные PostgreSQL 15 + Kafka: Docker Engine внутри WSL2, Docker Desktop не нужен.
# Скрипты и runbook — infra/local/README.md (порты 5432/9092, креды scalping/scalping).
bash infra/local/bootstrap-wsl-docker.sh   # однократно, внутри WSL (docker-ce, fallback — docker.io)
bash infra/local/up.sh                     # образы + контейнеры + 3 топика
bash infra/local/down.sh                   # остановить (--purge — вместе с volume БД и сетью scalping-net)

# Kafka-топики вручную (контейнер scalping-kafka уже запущен; имя — при дефолтном KAFKA_CONTAINER)
docker exec -e KAFKA_BOOTSTRAP=scalping-kafka:29092 -e KAFKA_BIN=/opt/kafka/bin \
  scalping-kafka bash -c 'tr -d "\r" < /opt/scalping/create-topics.sh | bash'

# Проверка alert-rules
promtool check rules infra/prometheus/rules/scalping-alerts.yml
```

Flyway-миграции применяет Trader при старте; Kafka-топики создаются `create-topics.sh`
(в контейнере он смонтирован как `/opt/scalping/create-topics.sh`).

### OpenSpec
```bash
# change заархивирован 2026-11-11; команды ниже — для новых изменений
openspec status --change <change-id> --json
openspec instructions apply --change <change-id> --json
openspec validate --change <change-id>
openspec archive --change <change-id>          # после ревью
```

## 6. Testing

### Java
- Тесты в `src/test/java/` зеркалируют структуру `src/main/java/`.
- Unit-тесты — Mockito для моков SDK, AssertJ для assert'ов.
- Интеграционные (sandbox) — тег `@Tag("e2e")` обязателен; `@EnabledIfEnvironmentVariable(named = "T_INVEST_TOKEN")` — для тестов, реально ходящих в sandbox.
  Разделение групп в `pom.xml` не настроено: `mvn test` выполняет и e2e-классы, тег `e2e` — информационный.
- БД-тесты — Testcontainers PostgreSQL: зависимости объявлены в `trader/pom.xml`, реальный БД-тест пока не написан.
- Перед завершением задачи запускай `mvn test` (по явному запросу пользователя, см. §7) и убедись, что тесты проходят.

### Python
- Тесты в `analyzer/tests/`, имя `test_*.py`.
- pytest-asyncio для async-кода (`asyncio_mode = "auto"` в `pyproject.toml`).
- Synthetic данные для индикаторов — numpy-массивы с известными значениями.
- Перед завершением задачи запускай `poetry run pytest`.

### Покрытие
- Risk Manager: расчёт объёма, max_positions, correlation, rate-limit, PnL-CB — все сценарии.
- Стратегия: BUY/SELL/HOLD + SL/TP от ATR.
- Фильтры: торговый статус (NORMAL_TRADING), комиссионный фильтр, устаревание сигнала.

## 7. Strict Rules (DO NOT)

- **НЕ создавай** новые `.env`, `docker-compose.yml`, конфиги без явного запроса пользователя.
- **НЕ изменяй** Flyway-миграции (`V1__`, `V2__`) вручную — создавай новую `V<n>__*.sql` с инкрементным номером.
- **НЕ добавляй** новые зависимости в `pom.xml` / `pyproject.toml` без явного разрешения.
- **НЕ хардкодь** риск-лимиты в коде — они в таблице `risk_config`, подхватываются через poll.
- **НЕ используй** MARKET-ордера на вход — только LIMIT/BESTPRICE (D4). MARKET — только для emergency-close (PnL-CB).
- **НЕ пиши** комментарии к каждой строке — только сложная бизнес-логика («почему», не «что»).
- **НЕ выводи** токены, пароли, содержимое `.env` в логи, коммиты, тесты.
- **НЕ запускай** сборку/тесты без явного запроса пользователя (правило глобального AGENTS.md).
- **НЕ сужай, не откладывай и не упрощай** специфицированное поведение молча — если задача требует
  выхода за рамки спеки, остановись и спроси.

## 8. OpenSpec Workflow

Изменение `scalping-trading-system-mvp` (schema `spec-driven`) заархивировано `2026-11-11`; все 41 задача отмечены `[x]`.
Артефакты в `openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/`: `proposal.md`, `specs/` (5 capability-спеков), `design.md` (9 решений), `tasks.md`.

- Перед изменением кода читай `openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/design.md` — там обоснование решений.
- Новые изменения оформляй отдельным change в `openspec/changes/`; правки требований → `specs/`, правки задач → `tasks.md` — через `/opsx-continue` или вручную.
- После реализации — `openspec validate --change <change-id>`, затем `/opsx-archive`.
- `brief.md` (контекст изменения) — не создавался; заведение — отдельная задача.
