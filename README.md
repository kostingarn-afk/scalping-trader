# Scalping Trading System

Автоматизированная система скальпинга на Московской бирже через [T-Invest API](https://developer.tbank.ru/).
Депозит 200 000 ₽. Три независимых сервиса — Collector, Analyzer, Trader — обмениваются только
Protobuf-сообщениями через Kafka: общего кода и прямых вызовов между модулями нет.

```
T-Invest API (gRPC)
      │
      ▼
[Collector] ──▶ Kafka: raw-market-data ──▶ [Analyzer] ──▶ Kafka: trading-signals ──▶ [Trader] ──▶ Kafka: trade-results
   Java 21                                   Python 3.11                                Java 21
```

Collector забирает рыночные данные у брокера, Analyzer считает индикаторы и формирует сигналы,
Trader проверяет сигнал по риск-лимитам, выставляет ордер и биржевые стопы. Сырые рыночные данные
T-Invest получает **только Collector**: Analyzer работает исключительно с Kafka и токена не
использует, Trader обращается к брокеру только за исполнением ордеров, сверкой позиций и торговым
статусом (`T_INVEST_TOKEN` ему нужен, без него приложение не стартует).

## Модули

| Модуль | Стек | Ответственность | Документация |
|---|---|---|---|
| **Collector** | Java 21, Spring Boot 3 | gRPC-стримы `MarketDataStreamService` → Kafka; резолв и кэш инструментов; выгрузка истории для бэктеста | [`collector/README.md`](collector/README.md) |
| **Analyzer** | Python 3.11, ta-lib, numpy, scipy | Индикаторы, правило входа, комиссионный фильтр, публикация сигналов; офлайн-бэктест | [`analyzer/README.md`](analyzer/README.md) |
| **Trader** | Java 21, Spring Boot 3 | Дедупликация и валидация сигналов, риск-контроль, исполнение, SL/TP, PnL-CB, recovery | [`trader/README.md`](trader/README.md) |

Общая инфраструктура: `proto/` — контракты сообщений (source of truth для всех модулей);
PostgreSQL 15 с Flyway-миграциями (владелец — Trader); Kafka с тремя топиками — `raw-market-data`
(3 минуты), `trading-signals` (3 дня), `trade-results` (compact, без истечения); Prometheus +
Grafana с alert-rules.

Строка Trader перечисляет ответственность модуля по спеке: часть механик (PnL-circuit-breaker,
recovery) в коде есть, но в основной поток не включена — фактическое состояние в разделе
«Состояние проекта» и в «Ограничениях» [`trader/README.md`](trader/README.md).

## Где что искать

| Вопрос | Куда смотреть |
|---|---|
| Как устроен сбор данных, reconnect, метрики, выгрузка истории | [`collector/README.md`](collector/README.md) |
| Как считаются индикаторы, какие есть стратегии, как прогнать бэктест | [`analyzer/README.md`](analyzer/README.md) |
| Какие риск-проверки есть, как меняются лимиты, что происходит при обрыве | [`trader/README.md`](trader/README.md) |
| Локальные PostgreSQL и Kafka в WSL2 без Docker Desktop | [`infra/local/README.md`](infra/local/README.md) |
| Требования, БД, контракты, NFR — исходная постановка | [`docs/Техническое задание.md`](docs/Техническое%20задание.md) |
| Остальная исходная документация: API, математические библиотеки, рекомендации по депозиту, блок-схемы | [`docs/`](docs/) |
| Обоснования архитектурных решений (9 решений, 5 capability-спеков) | [`openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/`](openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/) |

## Структура репозитория

```
.
├── proto/            # Protobuf-схемы: common, raw_market_data, trading_signal, trade_result
├── collector/        # Java/Maven — сбор рыночных данных (+ профиль history-dump)
├── analyzer/         # Python/poetry — индикаторы, сигналы, бэктест
├── trader/           # Java/Maven — исполнение, риск, позиции, Flyway-миграции
├── infra/            # Kafka-топики, Prometheus, Grafana, локальный стенд (local/)
├── docs/             # Исходная документация проекта
├── openspec/         # Спецификации и архив изменения MVP
└── pom.xml           # Maven multi-module: collector + trader
```

Выгрузки истории и отчёты бэктеста в git не входят (`data/`, `reports/` в `.gitignore`).

## Окружение T-Invest

| Переменная | Значение | Дефолт в `application.yml` |
|---|---|---|
| `T_INVEST_TOKEN` | сырой токен из личного кабинета T-Invest (`t.…`), **без** префикса `Bearer` | пусто — обязательна |
| `T_INVEST_SANDBOX` | `true` — sandbox, `false` — production | `true` |

`Bearer` добавляет сам SDK (`InvestApi.addAuthHeader`). Если положить его в переменную, уйдёт
`Authorization: Bearer Bearer …` и сервер вернёт `40003 UNAUTHENTICATED`. Sandbox-токен работает
только против sandbox-контура; prod-токен при `T_INVEST_SANDBOX=true` получит ошибку авторизации.

```powershell
# Windows PowerShell
[Environment]::SetEnvironmentVariable("T_INVEST_TOKEN", "t.xxxxxxxx", "User")   # постоянно
$env:T_INVEST_SANDBOX = "true"                                                  # текущая сессия
[Environment]::SetEnvironmentVariable("WSLENV", "T_INVEST_TOKEN:T_INVEST_SANDBOX", "User")  # проброс в WSL
```

```bash
# WSL/Linux/CI
export T_INVEST_TOKEN="t.xxxxxxxx"
export T_INVEST_SANDBOX=true
```

WSL не наследует переменные Windows — без `WSLENV` (или `export` в `~/.bashrc` внутри WSL)
запуск из WSL получит пустой токен.

**TLS-корень Минцифры (обязательно для Java-модулей).** Цепочка sandbox-хоста замыкается на
самоподписанный «Russian Trusted Root CA», которого нет ни в `ca-certificates`, ни в `cacerts` JDK:
без него Collector и Trader падают с `PKIX path building failed`. JDK менять не нужно — достаточно
копии `cacerts` с добавленным корнем.

```bash
echo | openssl s_client -connect sandbox-invest-public-api.tinkoff.ru:443 \
  -servername sandbox-invest-public-api.tinkoff.ru -showcerts 2>/dev/null \
  | awk '/BEGIN CERTIFICATE/{n++} n==3' > /tmp/russian_root.pem

cp "$JAVA_HOME/lib/security/cacerts" /usr/local/share/scalping-truststore.p12
sudo keytool -importcert -noprompt -alias russian_trusted_root_ca -file /tmp/russian_root.pem \
  -keystore /usr/local/share/scalping-truststore.p12 -storepass changeit

export JAVA_TOOL_OPTIONS="-Djavax.net.ssl.trustStore=/usr/local/share/scalping-truststore.p12 \
  -Djavax.net.ssl.trustStorePassword=changeit"
```

Секреты — только через переменные окружения; `.env` в репозиторий не добавляется.

## Быстрый старт

Требования: Java 21, Maven 3.9+, Python 3.11, poetry, системная `ta-lib`, protoc 25.x
(protobuf-runtime отвергает модули от protoc 3.12).

```bash
bash infra/local/up.sh                     # PostgreSQL 15 + Kafka + 3 топика (WSL2, Docker Engine)
mvn compile                                # Java-модули + генерация Protobuf

cd analyzer
poetry install                 # создаёт poetry.lock — держим его в git для воспроизводимости
poetry run protoc -I ../proto --python_out src/analyzer ../proto/*.proto   # *_pb2.py в git не хранятся
```

```bash
mvn -pl collector spring-boot:run          # порт 8081, /actuator/prometheus
cd analyzer && poetry run analyzer         # порт 8000, /metrics
mvn -pl trader spring-boot:run             # порт 8082, /actuator/prometheus, Flyway при старте
```

Состав торгового списка — **операционный параметр, а не константа проекта**: он задаётся
`collector.instruments` (`TICKER` или `TICKER@CLASS`) под исследовательскую сессию и по её итогам
пересматривается. Акции резолвятся в классе `TQBR`, фьючерсы — в `SPBFUT`. Метод отбора —
[`docs/Выбор-инструментов.md`](docs/Выбор-инструментов.md).

## Требования к производительности (NFR)

- Pipeline latency ≤ 2 с (p99): gRPC ≤ 50 мс, Kafka produce ≤ 20 мс, Analyzer ≤ 500 мс, Trader ≤ 200 мс
- Исполнение ордера ≤ 500 мс (p99)
- Торговый список: MVP — 5 инструментов, потолок — 100 (зависит от грейда API)

## Состояние проекта

MVP реализован и заархивирован в OpenSpec 2026-11-11 (41 задача, 5 capability-спеков). Привязки
к T-Invest SDK вынесены за интерфейсы (`MarketStreamService`, `InstrumentLoader`,
`OrderExecutionService`, `ExchangeStateProvider`), бизнес-логика их не импортирует.

Что стоит знать до первого запуска:

- **Unit-тесты покрывают бизнес-логику; e2e-классы в `trader/` — скелеты** (часть проверяет
  константы, а не сквозной прогон), unit и e2e не разделены в `pom.xml`.
- **Часть механик реализована, но не включена в поток**: PnL-circuit-breaker и `RecoveryService`
  в коде есть, вызовов из основного цикла нет; позиции хранятся в памяти и после рестарта не
  восстанавливаются. Полный перечень — «Ограничения» в [`trader/README.md`](trader/README.md).
- **Sandbox не поддерживает биржевые стоп-ордера**, поэтому SL/TP проверяются логикой и тестами,
  а не живой торговлей.
- **В выгруженной истории есть выходные торги** (выходная сессия MOEX); фильтра сессий
  в бэктесте нет — см. [`collector/README.md`](collector/README.md), раздел о снимке датасета.

Актуальные ограничения по каждому модулю — в разделах «Ограничения» их README.
