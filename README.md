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
├── data/history/                  # выгрузки истории Collector'а для бэктеста (вне git)
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
| [`docs/Выбор-инструментов.md`](docs/Выбор-инструментов.md) | Почему торговый список — пять акций (`GAZP`, `VTBR`, `ROSN`, `NVTK`, `PLZL`) и почему убраны Сбер, «Т-Технологии» и фьючерс: расшифровка ATR и тикеров, критерий отбора, ограничения замера |
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

### Выгрузка истории (Collector)

Историю для бэктеста выгружает **только Collector** — Analyzer и Trader к брокерскому API
не обращаются. Режим включается профилем `history-dump`: стрим, Kafka и запись справочника
в БД не запускаются, приложение пишет CSV и завершается.

```bash
# WSL/Linux: нужен truststore с корнем Минцифры (см. раздел выше) и T_INVEST_TOKEN
mvn -pl collector spring-boot:run \
  -Dspring-boot.run.profiles=history-dump \
  -Dspring-boot.run.arguments="--collector.history.instruments=SBER,GAZP,SiZ6@SPBFUT \
    --collector.history.interval=1min \
    --collector.history.from=2026-10-01T00:00:00Z \
    --collector.history.to=2026-11-01T00:00:00Z"
```

| Параметр | Значение |
|---|---|
| `collector.history.instruments` | `TICKER` или `TICKER@CLASS` через запятую |
| `collector.history.interval` | `1min`, `5min`, `15min`, `1h` |
| `collector.history.from` / `to` | ISO-8601 UTC; период режется на страницы по лимиту `GetCandles` (1 мин — сутки, 5 мин — неделя) |
| `collector.history.out-dir` | каталог выгрузки, по умолчанию `../data/history` относительно каталога модуля `collector`, т.е. `<repo>/data/history` (env `HISTORY_OUT_DIR`) |

Коды возврата: `0` — все инструменты выгружены, `2` — часть вернула пустую историю, `3` — пусто
у всех, `1` — ошибка. Свечи на стыках страниц дедуплицируются по времени закрытия; незавершённые
свечи отбрасываются. Если внутри периода у инструмента нет баров за целый календарный день (UTC),
выгрузка пишет `WARN History gaps ...` со списком таких дней — пропуск в источнике не теряется молча.

### Бэктест (оценка стратегии на истории)

Сырые данные T-Invest получает только Collector: подписки стрима и исторические запросы
`MarketDataService.GetCandles`. Выгрузка истории складывается в `data/history/` —
`{ticker}_{interval}min.csv` (`ts,open,high,low,close,volume`, ISO-8601 UTC) плюс рядом
`.meta.json` (`ticker`, `class_code`, `lot_size`, `interval_minutes`). Analyzer к брокерскому
API не обращается и токен не использует — работает с CSV.

Прогон стратегии на уже выгруженной истории (сеть и токен не нужны):

```bash
cd analyzer
poetry install            # или: python -m venv .venv && .venv/Scripts/python.exe -m pip install -e .
export PYTHONPATH=src     # если пакет не установлен; в PowerShell: $env:PYTHONPATH = "src"
poetry run analyzer-backtest --data-dir ../data/history \
  --intervals 1,5,15 --tariffs investor \
  --out ../reports/backtest-$(date +%F).md
```

Если `poetry` в системе нет, рабочий вариант — локальное окружение `analyzer/.venv`
(в нём же лежат `pandas`, `statsmodels`, `scikit-learn`, `pytest`, `ruff`):

```bash
cd analyzer
.venv/Scripts/python.exe -m analyzer.backtest.cli --data-dir ../data/history-1h \
  --intervals 60 --tariffs trader --strategy momentum --out ../reports/run.md
.venv/Scripts/python.exe -m pytest
```

Параметры прогона: `--deposit` (200 000 ₽), `--risk-pct` (1 % на сделку),
`--max-notional-pct` (лимит номинала позиции, 100 % депозита), `--atr-sl` / `--atr-tp`
(1.5 / 2.0 × ATR), `--tp-sl` (перебор пар `TP:SL`, например `2:1.5,3:2,4:2.5` — каждый вариант
даёт отдельный прогон с меткой `tp3/sl2` в отчёте), `--max-bars` (таймаут 10 баров),
`--cooldown-bars` (баров после выхода без новых входов), `--trailing-atr` (трейлинг-стоп в ATR
от закрытия; 0 — выключен), `--no-tp` (цель не выставляется: выход по SL, трейлингу или таймауту;
комиссионный фильтр при этом продолжает считать цель сигнала), `--null-seeds N` (прогнать N серий
случайного входа с той же геометрией выхода и издержками и сравнить: отчёт получает раздел с
p-value), `--fill-on-close` (вход по закрытию сигнального бара — воспроизводит замер `to_be.md` §3).

Правило входа выбирается `--strategy`:

| Значение | Логика |
|---|---|
| `mean_reversion` (по умолчанию) | production-правило: RSI(7) 30/70 + цена за полосой BB(20, 2σ) |
| `v2` | порог `\|z\| ≥ --z-entry` σ, RSI `--rsi-buy/--rsi-sell`, запрет входа при `ADX ≥ --adx-max`, отсечение баров с `ATR%` ниже `--min-atr-pct` (или квантиля серии `--atr-pct-quantile`) |
| `momentum` | вход в сторону отклонения `\|z\| ≥ --z-entry` при подтверждении RSI (`--rsi-buy` 65 / `--rsi-sell` 35) и `ADX ≥ --adx-min` |

Боевой Analyzer продолжает работать на `mean_reversion`: `v2` и `momentum` подключены только
в бэктесте до подтверждения преимущества (см. `to_do.md` §3).

Walk-forward — подбор параметров на обучающем окне и оценка на следующем:

```bash
poetry run analyzer-backtest --data-dir ../data/history-1h --intervals 60 \
  --tariffs investor,trader --strategy momentum \
  --walkforward --wf-train-bars 2000 --wf-test-bars 580 --wf-min-trades 15 \
  --wf-grid "z_entry=1,1.5,2" --out ../reports/walkforward.md
```

`--wf-grid` задаёт декартово произведение осей (`name=v1,v2;name2=v3`), имена — поля конфига
стратегии. На `train` выбирается конфигурация с максимальным net при `--wf-min-trades` сделках,
на `test` она запускается без подгонки; бары хвоста `train` используются как прогрев
индикаторов, их сделки в результат не входят. Отчёт содержит по каждому окну выбранные
параметры, net и гейт `gross/комиссия`, а также строку `OOS` — сводку по всем тестовым окнам.

Отчёт: `.md` — сводка по каждому прогону (сигналы, сделки, win rate, expectancy, просадка,
распределение выходов TP/SL/TIMEOUT, признак «Открыта» для позиции на конец выгрузки);
при переборе TP/SL в конце добавляется таблица лучших прогонов с отношением
`Gross/комиссия` (порог кандидата — ≥ 1). `.json` — та же сводка для обработки, `.csv` — все сделки. Модель издержек — по тарифу, раздельно для
бумаг и фьючерсов (признак фьючерса — класс `SPBFUT`/`FORTS`). Обоснования решений —
`openspec/changes/backtest-runner/design.md`.

Нулевая модель (`--null-seeds N`) отвечает на вопрос, есть ли в правиле входа информация
вообще: тот же движок, тот же комиссионный фильтр, та же геометрия выхода, но входы случайны
(направление 50/50, частота — как у фактического прогона). Сравнение идёт по net **на сделку**,
потому что число сделок в случайных прогонах отличается из-за занятости позиции. `p ≤ 0.05` —
правило приносит то, чего случайный вход с теми же выходами не даёт; `p > 0.20` — сигнал
неотличим от шума, и улучшать нужно выход, издержки или горизонт, а не порог входа.
Практика: `--null-seeds 200` на три инструмента считается примерно за минуту.

Панельный режим (`--panel`) — кросс-секционный momentum вместо правила «входить или нет»:
на каждой дате ребаланса вселенная ранжируется по доходности за `--panel-lookback` торговых
дней, `--panel-legs` имён из верхушки покупаются и столько же из низа продаются, книга
долларово-нейтральна. Стопы не ставятся — для нейтральной книги они искажают сигнал, а не
защищают его. Позиции удерживаются `--panel-rebalance` дней, издержки платят только изменившиеся
ноги; ставка берётся из `--tariffs`.

```bash
analyzer-backtest --data-dir ../data/history-1h --tariffs trader --panel \
  --panel-universe "GAZP,LKOH,VTBR,ROSN,NVTK,PLZL,YDEX,GMKN,TATN,AFLT,SNGSP,ALRS,CHMF,NLMK,SNGS,IRAO" \
  --panel-grid "lookback_days=5,10,20;rebalance_days=5,10;legs=3,4" \
  --panel-null-seeds 100 --out ../reports/panel-grid.md
```

`--panel-grid` — та же сетка, что у walk-forward; `--panel-null-seeds` перемешивает ранжирование
внутри каждого ребаланса (состав периода и оборот те же, информация уничтожена); `--panel-walkforward`
с `--panel-train-days` / `--panel-test-days` выбирает конфигурацию на train по датам и оценивает на
следующем блоке. `--panel-universe` обязателен на практике: вселенная — исследовательское решение
(фьючерсы и бумаги с ATR ниже издержек отсеиваются заранее), и оно должно быть видно в команде.

Чего панель не учитывает: стоимость заимствования бумаг и требования по ГО для шорта, делистинг
(вселенная собрана из сегодняшних ликвидных имён), внутрипериодные движения (PnL от закрытия до
закрытия), проскальзывание сверх комиссии.

Что модель **не** учитывает (см. `design.md` D9): проскальзывание и очередь в стакане,
абонентскую плату тарифа, ступени ставок по обороту, минимальную комиссию брокера
(`min_commission_rub`, по умолчанию 0) и внутрисделочную просадку. Для фьючерсов лимит
номинала считается по полной стоимости контракта, а не по ГО — это консервативнее реальных
ограничений брокера. Цифры прогона — верхняя граница оценки, а не обещание доходности.

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
