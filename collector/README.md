# Collector — рыночные данные T-Invest в Kafka

Модуль стоит первым в pipeline и делает одну работу: превращает биржевой поток T-Invest API
в Protobuf-сообщения топика `raw-market-data`. Ни индикаторов, ни сигналов, ни ордеров здесь нет.

```
T-Invest gRPC (MarketDataStreamService)
        │  свечи, стакан, сделки, торговый статус
        ▼
   [Collector] ──► Kafka: raw-market-data ──► [Analyzer]
   Java 21, порт 8081
```

## 1. Назначение

Collector — **единственный потребитель сырых рыночных данных** T-Invest: свечи, стакан и сделки
попадают в систему только через него, а Analyzer получает их из Kafka (историю для бэктеста —
из CSV, которые выгружает тот же Collector, профиль `history-dump`, раздел 6). Trader рыночный
поток не читает вовсе — он подписан только на `trading-signals`. Но клиент SDK у него свой:
исполнение ордеров, сверка позиций и проверка торгового статуса идут прямыми unary-вызовами,
поэтому Trader требует собственный `T_INVEST_TOKEN`. Подписки и исторические запросы выполняет
один модуль, поэтому сбой или смена схемы SDK не расползаются по конвейеру.

Почему стримы, а не polling: `MarketDataStreamService` — bi-directional gRPC-стрим, задержка
доставки не привязана к периоду опроса, что укладывается в NFR «pipeline ≤ 2 с» (решение D1
в [`design.md`](../openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/design.md)).

Что модуль **не** делает: не считает индикаторы, не хранит историю в БД (пишет только справочник
инструментов), не торгует, не управляет позициями.

## 2. Поток данных

1. **Инициализация SDK.** `TinkoffConfig` создаёт `InvestApi` из `tinkoff.token` и `tinkoff.sandbox`; пустой токен — падение на старте с подсказкой про `T_INVEST_TOKEN`.
2. **Резолв инструментов.** `CollectorStartup` по `ApplicationReadyEvent` вызывает `InstrumentCache.init()`: для каждого `collector.instruments` идёт запрос в `InstrumentsService`, метаданные кладутся в память и upsert'ятся в таблицу `instruments`.
3. **Открытие стрима.** Тот же хук вызывает `StreamSubscriptionManager.start()`, который в цикле переподключения открывает стрим через `TinkoffMarketStreamService.subscribe(...)`.
4. **Подписки.** Ровно четыре на инструмент: `subscribeCandles` (интервал `collector.candle-interval`), `subscribeOrderbook` (глубина `collector.order-book-depth`), `subscribeTrades`, `subscribeInfo`. Других типов стримов в коде нет — ни `LastPrice`, ни свечей других интервалов.
5. **Приём и конвертация.** Колбэк SDK → `handleResponse` → `convert(...)` → `RawMarketData`. Неизвестный payload или пустой `figi` отбрасывается, ошибка конвертации логируется и не рвёт стрим.
6. **Фильтрация и публикация.** `handle` берёт торговый статус из кэша, `TradingStatusFilter` решает, публиковать ли сообщение, `MarketDataPublisher` отправляет байты в Kafka с ключом `figi`.

Ключевая деталь: `subscribe` **блокирует** вызывающий поток до закрытия или ошибки стрима. Это
и удерживает JVM живой (веб-сервер здесь только для actuator), и даёт точку для reconnect-цикла.

## 3. Компоненты по пакетам

### 3.1 `config/` — параметры и инфраструктурные бины

| Класс | Роль |
|---|---|
| `CollectorProperties` | record `collector.*`; в компактном конструкторе подставляет дефолты и требует непустой `instruments` |
| `KafkaConfig` | ручная сборка `KafkaProducer<String, byte[]>`; `@EnableScheduling`; producer отсутствует в профиле `history-dump` |
| `TinkoffConfig` | бин `InvestApi` (sandbox/prod по флагу), проверка токена, закрытие `destroy(1)` в `@PreDestroy` |
| `TinkoffProperties` | record `tinkoff.*`: `token`, `sandbox` (по умолчанию `true`) |

Продюсер собран вручную, а не через Spring Kafka: в модуле только `kafka-clients`, а нужный набор
настроек — три строки. `acks=1`, `linger.ms=5`, `compression.type=lz4` — компромисс между задержкой
и объёмом: данные живут в топике 3 минуты, подтверждение от всех реплик не окупается.

### 3.2 `stream/` — стрим, подписка, переподключение

`MarketStreamService` — абстракция из двух методов (`subscribe`, `close`), чтобы бизнес-логика
не зависела от типов SDK; `MarketDataListener` — функциональный интерфейс приёмника.

**`StreamSubscriptionManager`** — оркестратор и место, где живёт политика отказоустойчивости:

- хранит исходный список `collector.instruments` (тикеры, не FIGI — резолв делает стрим-сервис);
- `start()` крутит цикл, пока поток не прерван: успешный `subscribe` возвращается только при штатном закрытии, любой выброс — повод переподключиться;
- backoff: старт с `reconnect-initial-ms` (1000 мс), после каждой неудачи `delay * 2`, потолок `reconnect-max-ms` (30 000 мс); перед паузой инкрементируются `stream_reconnects_total` и `api_errors_total`;
- `handle(...)` — единственная точка фильтрации: статус берётся из `InstrumentCache`, а не из `Info`.

Важно: **задержка не сбрасывается после удачного переподключения** — ограничение и его последствия разобраны в разделе 9.

**`TinkoffMarketStreamService`** — адаптер SDK:

- проверяет, что список не пуст и сервис не закрыт (закрытие могло прийти раньше новой попытки);
- резолвит идентификаторы: строка `[A-Z0-9]{12}` считается FIGI, остальное — через `InstrumentLoader.loadByTicker`; нерезолвленный тикер уходит в стрим «как есть» (раздел 9);
- ошибку стрима сохраняет в `AtomicReference` и разблокирует latch, чтобы `subscribe` выбросил её наружу — так ошибка попадает в reconnect-цикл, а не теряется в колбэке;
- свеча: `begin` — время из SDK, `end` = `begin + длительность интервала`; стакан: bids/asks обрезаются до `order-book-depth`, отсутствующие `limit_up`/`limit_down` заменяются нулевой котировкой; сделка: `trade_id` пустой (идентификатора в стриме SDK 1.8 нет); `Info`: торговый статус и флаги доступности лимитных/рыночных заявок как `int32`.

Признака завершённости свечи в стриме нет, поэтому `is_complete` — эвристика `now >= begin + интервал`. Analyzer обрабатывает только `is_complete = true`: свеча, пришедшая с опозданием, останется неполной.

Значения `candle-interval` (алиасы): `1min`/`1m`/`min`, `2min`/`2m`, `3min`/`3m`, `5min`/`5m`, `10min`/`10m`, `15min`/`15m`, `30min`/`30m`, `1h`/`hour`/`1hour`, `2h`, `4h`, `1d`/`day`/`1day`, `week`/`1w`, `month`/`1month`; месяц считается как 30 дней.

Неизвестное значение — `IllegalArgumentException` из `resolveInterval()`, но бросается он **внутри** `subscribe()`, а тот вызывается из цикла переподключения `StreamSubscriptionManager.subscribeWithReconnect`, где перехватывается любое исключение. Валидации параметра при старте нет: приложение поднимается, порт 8081 и `/actuator/prometheus` отвечают, а поток бесконечно переподключается с backoff 1 → 30 с. Опечатку ищут по строке лога `Stream error, reconnecting in ... ms: Unsupported collector.candle-interval: ...`, а не по отсутствию старта.

**`CollectorStartup`** задаёт порядок: сначала справочник, потом стрим. Наоборот первые сообщения приходили бы по инструментам, которых ещё нет в кэше, и фильтр их отбрасывал бы.

### 3.3 `instrument/` — справочник и кэш

`TinkoffInstrumentLoader` разбирает ссылку на инструмент:

| Вход | Что пробуется |
|---|---|
| `TICKER` | акция `TQBR` → фьючерс `SPBFUT` → фьючерс `FORTS` (историческое имя класса) |
| `TICKER@CLASS` | фьючерс, если класс `SPBFUT`/`FORTS`, иначе акция в указанном классе |

Регистр значим: фьючерсные тикеры MOEX регистрозависимы (`SiZ6`), акционные — нет. Если автоопределение не удалось, выполняется повторная попытка в верхнем регистре; все неудачи собираются в текст исключения, чтобы было видно, какие классы проверялись.

В `InstrumentMeta` попадают `figi`, `ticker`, `classCode`, `name`, `lot`, `minPriceIncrement` (как `double`), `currency`, `apiTradeAvailable`, `tradingStatus`, `tier` (константа 0 — грейд API не запрашивается).

**`InstrumentCache`** — `ConcurrentHashMap` по FIGI, то есть O(1) на каждое входящее сообщение. Заполняется при старте и обновляется по cron `instrument-refresh-cron` (03:00). В БД пишет через `InstrumentRepository`; ошибка загрузки одного инструмента логируется и не мешает остальным. `tradingStatus(figi)` возвращает `-1`, если FIGI в кэше нет — такой инструмент фильтр отбросит.

**`JdbcInstrumentRepository`** — upsert в `instruments` обычным JDBC: соединение открывается на один вызов и закрывается сразу (запись раз в сутки, пул не нужен, JDBC-стартер не тянут). Схемой владеет Trader (Flyway), Collector только пишет. Тип выводится из кода класса (`SPBFUT`/`FORTS` → `FUTURE`, иначе `SHARE`), `uid` пишется как `NULL`, конфликт — по `figi`. Если PostgreSQL не поднят, Collector стартует: упадёт только upsert, стрим и Kafka от БД не зависят.

### 3.4 `converter/` — T-Invest SDK → Protobuf

`TinvestConverter` — статический маппинг без состояния. Главное про цены: **масштабирования нет**. `Quotation` в [`common.proto`](../proto/common.proto) совпадает с `Quotation` T-Invest (`units` + `nano`, 0..999 999 999), поэтому цена передаётся парой как есть — точность брокера сохраняется бит в бит, без промежуточного `double`. Перегрузка `toQuotation(double)` (нормализация отрицательных значений) в рабочем коде Collector'а не используется — только в тестах.

Остальное: `toTimestamp` → `google.protobuf.Timestamp`; `tradeDirection(1|2|…)` → `BUY`/`SELL`/`UNSPECIFIED`; сборка `Candle`, `OrderBook`, `Trade`, `TradingStatusInfo` и конверта `RawMarketData` с `payload_type` ([`raw_market_data.proto`](../proto/raw_market_data.proto)).

### 3.5 `publish/` — фильтр и Kafka

**`TradingStatusFilter`** пропускает:

| Payload | Условие публикации |
|---|---|
| `INFO` | всегда — статус нужен Analyzer'у и при закрытой сессии |
| `CANDLE`, `ORDER_BOOK`, `TRADE` | только при `trading_status == 2` (`NORMAL_TRADING`) |

Отбрасываются `OPENING_PERIOD` (4), `BREAK_IN_TRADING` (7) и любой другой статус. Статус берётся
из справочника (`InstrumentsService`), а не из `Info`-сообщения: `Info` в кэш не записывается,
поэтому смена статуса внутри дня влияет на фильтр только после daily refresh.

**`MarketDataPublisher`** отправляет `toByteArray()` асинхронно, ключ — `figi`: все сообщения инструмента попадают в одну партицию и сохраняют порядок. Топик — `kafka.topic.raw-market-data`, в локальном стенде 3 партиции и retention 3 минуты ([`create-topics.sh`](../infra/kafka/create-topics.sh)); `acks=1`, `linger.ms=5`, `compression.type=lz4` — раздел 3.1. `messages_published_total` инкрементируется **в колбэке успешной отправки**; ошибка только логируется — ретраев и DLQ нет.

## 4. Метрики

Все три — Micrometer `Counter`, отдаются на `/actuator/prometheus` (порт 8081).

| Метрика | Тип | Что измеряет | Зачем |
|---|---|---|---|
| `messages_published_total` | Counter | успешно подтверждённые отправки в `raw-market-data` | главный признак жизни pipeline: нет роста — нет данных |
| `stream_reconnects_total` | Counter | попытки переподключения стрима | рост означает нестабильную сеть или проблему на стороне API |
| `api_errors_total` | Counter | ошибки T-Invest API (стрим и колбэк) | отличает «данных нет» от «API отказывает» |

Prometheus добавляет к scrape-таргету метку `service="collector"`
([`prometheus.yml`](../infra/prometheus/prometheus.yml)); по ней построены алерты `api_disconnect`
и `stale_data` в [`scalping-alerts.yml`](../infra/prometheus/rules/scalping-alerts.yml).
Метрики «сообщений отфильтровано» нет: падение потока видно по `messages_published_total`,
но причину приходится искать по логам DEBUG.

## 5. Конфигурация

`collector/src/main/resources/application.yml`:

| Ключ | Дефолт | Смысл |
|---|---|---|
| `collector.instruments` | список из конфигурации сессии | тикеры для подписки; пустой список — ошибка старта |
| `collector.candle-interval` | `1min` | интервал свечей подписки (алиасы — раздел 3.2) |
| `collector.order-book-depth` | `10` | сколько уровней bids/asks оставлять |
| `collector.reconnect-initial-ms` | `1000` | первая пауза перед переподключением |
| `collector.reconnect-max-ms` | `30000` | потолок экспоненциальной паузы |
| `collector.instrument-refresh-cron` | `0 0 3 * * *` | обновление справочника и upsert в БД |
| `kafka.bootstrap-servers` | `${KAFKA_BOOTSTRAP:localhost:9092}` | адрес брокера |
| `kafka.topic.raw-market-data` | `raw-market-data` | топик публикации |
| `tinkoff.token` | `${T_INVEST_TOKEN:}` | сырой токен **без** `Bearer`; пусто — ошибка старта |
| `tinkoff.sandbox` | `${T_INVEST_SANDBOX:true}` | `true` — песочница, `false` — боевой контур |
| `spring.datasource.url` | `${DB_URL:jdbc:postgresql://localhost:5432/scalping}` | БД для справочника |
| `spring.datasource.username` / `password` | `scalping` / `scalping` | креды БД (env `DB_USER`/`DB_PASSWORD`) |

Префикса `spring.kafka.*` в проекте нет: продюсер собирается вручную, авто-конфигурация Spring Kafka не подключена.

**`collector.instruments`** — операционный параметр, а не константа проекта: список выбирается под исследовательскую сессию и по её итогам пересматривается. Источник истины — конфигурация на момент запуска; метод отбора — [`docs/Выбор-инструментов.md`](../docs/Выбор-инструментов.md). Формат элемента — `TICKER` или `TICKER@CLASS`: акции резолвятся в `TQBR`, фьючерсы — в `SPBFUT` (класс `FORTS` в API не резолвится и остаётся запасным вариантом). Регистр значим для фьючерсов: `SiZ6` и `SIZ6` — разные строки. Токен, песочница и TLS-корень Минцифры (без него Java-модули падают с `PKIX path building failed`) описаны в корневом [`README.md`](../README.md).

## 6. Режим выгрузки истории (`history-dump`)

Профиль включается `--spring.profiles.active=history-dump` и превращает Collector в одноразовую утилиту: скачать исторические свечи `MarketDataService.GetCandles` в CSV и выйти. Отдельный профиль нужен потому, что здесь не требуются ни стрим, ни Kafka, ни запись справочника:

- `spring.main.web-application-type: none` — веб-сервер не поднимается;
- `CollectorStartup`, `StreamSubscriptionManager`, `MarketDataPublisher` исключены профилем;
- `KafkaProducer` не создаётся (`@Profile("!history-dump")`);
- пул Hikari не проверяет соединение при старте (`initialization-fail-timeout: -1`) — выгрузка не должна требовать запущенного PostgreSQL;
- работу выполняет `HistoryDumpRunner` (`ApplicationRunner`) и завершает процесс через `System.exit`.

> **Требование: глубина выгрузки — не более 1 года.** Период старше одного года не запрашивается
> из T-Invest API ни рабочим прогоном, ни пробным запросом «узнать доступную глубину»: ограничение
> действует для всех модулей и профилей (`AGENTS.md` §7). Потребность в длинном ряду решается
> накоплением собственной истории, а не глубиной запроса — следствия для плана в `to_do.md`,
> раздел «Открытые задачи». Кодом глубина не проверяется: по периоду `HistoryDumpProperties` валидирует
> только `from < to`, так что ограничение держится на дисциплине запуска.

```bash
INSTRUMENTS=$TICKER1,$TICKER2      # тикеры задачи; состав списка в документации не фиксируется
mvn -pl collector spring-boot:run \
  -Dspring-boot.run.profiles=history-dump \
  -Dspring-boot.run.arguments="--collector.history.instruments=$INSTRUMENTS \
    --collector.history.interval=1h \
    --collector.history.from=2026-10-01T00:00:00Z \
    --collector.history.to=2026-11-01T00:00:00Z"
```

| Параметр | Обязателен | Дефолт | Смысл |
|---|---|---|---|
| `collector.history.instruments` | да | — | `TICKER` или `TICKER@CLASS` через запятую |
| `collector.history.interval` | нет | `1min` | `1min`, `5min`, `15min`, `1h` |
| `collector.history.from` / `to` | да | — | ISO-8601 UTC, `from < to`; конец исключителен |
| `collector.history.out-dir` | нет | `../data/history` (в коде — `data/history`) | каталог CSV; env `HISTORY_OUT_DIR` |
| `collector.history.requests-per-minute` | нет | `120` | предел запросов к API в минуту |

**Почему 120.** Лимит песочницы — 200 unary-запросов в минуту, сервиса котировок — 600. Месяц минутных свечей — ~300 страниц; замер 2026-11-21 дал 304 страницы за 45 с ≈ 400 req/min, и API ответил `RESOURCE_EXHAUSTED` уже на второй бумаге. 120 — запас под динамический лимит.

**Лимиты одного запроса `GetCandles`** заданы в `HistoryInterval.maxPageSpan`:

| Интервал | Лимит API | Окно в коде |
|---|---|---|
| `1min` | сутки | 1 день |
| `5min` | неделя | 7 дней |
| `15min` | 3 недели | 21 день (30 дней API отклоняет) |
| `1h` | 3 месяца | 30 дней (запас, раздел 9) |

Период режется `HistoryRange.split` на страницы `[from, to)`: подряд, без дырок и пересечений, последняя может быть короче. Перед каждым запросом вызывается `RequestPacer.await()` — интервал `60 с / requests-per-minute`. Первый вызов не ждёт; если ответ сервера дольше интервала, паузы нет и «переработка» **не** превращается в кредит: иначе после долгого ответа пачка запросов ушла бы подряд и снова упёрлась в лимит.

Дальше результат приводится в порядок:

- **дедупликация** по времени закрытия: границы страниц включительны, стык может дать дубликат; остаётся последняя версия, порядок — по возрастанию;
- **незавершённые свечи отбрасываются** (`HistoricCandle.getIsComplete`) — в CSV только закрытые бары; `ts` — **время закрытия** (`time + интервал`);
- **пустые календарные дни** (UTC) внутри периода дают `WARN History gaps ...` со списком дней: пропуск в источнике не теряется молча. Крайние дни не проверяются — там граница запроса.

Коды возврата: `0` — выгружены все инструменты, `2` — часть вернула пустую историю, `3` — пусто у всех, `1` — ошибка выгрузки (любое исключение, включая ответ API).

**CSV-контракт** (его читает `analyzer/backtest/loader.py`):

```text
ts,open,high,low,close,volume
2026-11-13T07:01:00Z,270,270.5,269.5,270.2,120
```

Файл `{ticker}_{minutes}min.csv`: `ts` — ISO-8601 UTC (время закрытия), цены без экспоненты и лишних нулей (`BigDecimal.stripTrailingZeros().toPlainString()`), объём — в лотах. Рядом sidecar `{ticker}_{minutes}min.meta.json`: обязательные `ticker`, `class_code`, `lot_size`, `interval_minutes` плюс информационные `bars`, `fetched_at`, `source` (`tinvest:GetCandles`). Файлы перезаписываются целиком — выгрузка идемпотентна. Пустая история **не создаёт файл**: Analyzer не должен увидеть выгрузку без данных.

## 7. Выгруженный датасет — снимок 2026-11-21

Инвентаризация `data/history-1min/` (в git не входит, `data/` в `.gitignore`). Период
**2026-01-21 … 2026-11-21** (правая граница не включается: последний бар закрывается
2026-11-20 в 23:59 UTC), минутные свечи, 20 бумаг, **5 670 963 бара**, ~273 МБ.

| Тикер | Баров / дней | Тикер | Баров / дней |
|---|---:|---|---:|
| AFLT | 280 958 / 304 | PLZL | 288 384 / 304 |
| ALRS | 274 104 / 304 | ROSN | 314 507 / 304 |
| CHMF | 264 317 / 304 | SBER | 322 857 / 304 |
| GAZP | 301 569 / 304 | SNGS | 227 543 / 304 |
| GMKN | 274 511 / 304 | SNGSP | 276 379 / 304 |
| IRAO | 282 321 / 304 | T | 308 540 / 303 |
| LKOH | 292 450 / 304 | TATN | 283 739 / 304 |
| MOEX | 266 044 / 304 | VTBR | 312 691 / 304 |
| MTSS | 265 324 / 304 | YDEX | 289 299 / 304 |
| NLMK | 255 861 / 304 | NVTK | 289 565 / 304 |

Оговорки, без которых таблица читается неправильно:

- **Это инвентаризация файлов, а не торговый список.** Наличие тикера в каталоге не значит, что
  он торгуется: состав списка задаётся `collector.instruments` на момент запуска.
- **Выходные торги.** MOEX работает и в субботу/воскресенье: на выходные приходится **23,6 %**
  баров датасета, поэтому 304 дня — календарные дни с данными, а не 304 привычные сессии.
- **Пропуск у `T`:** 303 дня вместо 304 — 2026-04-16 не содержит ни одного бара (единственный
  полностью пустой день в файле).
- **Фьючерсы не выгружались** — только акции.

Сверка (PowerShell, из корня репозитория):

```powershell
$sum = 0
Get-ChildItem 'data/history-1min' -Filter *.meta.json |
  ForEach-Object { $sum += (Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).bars }
$sum   # 5670963
```

## 8. Запуск и тесты

```bash
mvn -pl collector spring-boot:run          # порт 8081, /actuator/health, /actuator/prometheus
mvn -pl collector -am test                 # unit-тесты модуля (приложение не запускается)
```

Перед запуском нужны `T_INVEST_TOKEN` (сырой `t.xxxxxxxx`), Kafka и PostgreSQL
([`infra/local/README.md`](../infra/local/README.md)); отсутствие БД не блокирует старт, но upsert
справочника будет падать в лог.

| Тест | Что проверяет |
|---|---|
| `TinvestConverterTest` | `double` → `units`/`nano` (включая отрицательные цены), корректный `payload_type` |
| `TradingStatusFilterTest` | `INFO` проходит всегда; свеча при `2` проходит, при `4` и `7` — нет |
| `MarketDataPublisherTest` | топик, ключ = `figi`, непустое тело |
| `StreamSubscriptionManagerTest` | три попытки подписки, ≥2 reconnect-метрик, публикация после успеха |
| `HistoryIntervalTest` | коды интервалов, регистр, неизвестный код, окна не превышают лимиты API |
| `HistoryRangeTest` | нарезка без дыр и пересечений, короткая последняя страница, валидация |
| `HistoryDumpServiceTest` | файл на инструмент, пагинация, пустая история без файла, пейсер перед каждым запросом, дедупликация, поиск пропусков |
| `HistoryCsvWriterTest` | заголовок, формат строки, `.meta.json`, имя `_60min`, перезапись, отсутствие экспоненты |
| `RequestPacerTest` | первый вызов без паузы, 500 мс при 120/мин, отсутствие «кредита», `unlimited`, валидация |
| `HistoryDumpPropertiesTest` | дефолт 120 < лимита песочницы, явное значение, неположительное — ошибка |

## 9. Ограничения и что не реализовано

- **Нет повторов на `RESOURCE_EXHAUSTED`.** Пейсер снижает вероятность упереться в лимит, но ответ
  ошибкой валит выгрузку целиком (`exit 1`) — без backoff и без «продолжить с той же страницы».
- **`HOUR.maxPageSpan` = 30 дней** при документированных 90: до трёх раз больше запросов, чем нужно.
- **Backoff не сбрасывается** после успешного переподключения: серия аварий поднимает паузу
  до 30 с и оставляет её там — связь восстанавливается, но каждая следующая авария ждёт дольше,
  чем нужно.
- **Торговый статус из `Info`-стрима не обновляет кэш.** Фильтр опирается на значение из
  `InstrumentsService` (старт + cron); если FIGI в кэше нет, статус `-1` и данные отбрасываются.
- **Нерезолвленный тикер уходит в стрим как есть**, SDK отвечает ошибкой, менеджер уходит
  в бесконечный reconnect — опечатка в `collector.instruments` диагностируется только по логам.
- **Опечатка в `collector.candle-interval` не останавливает приложение**: исключение возникает
  уже внутри цикла переподключения, выглядит как сетевой сбой — процесс жив, метрики растут,
  данных нет (механика — раздел 3.2).
- **`is_complete` — эвристика** (`now >= begin + интервал`), а не признак биржи; Analyzer
  обрабатывает только `is_complete = true`.
- **Отправка в Kafka без ретраев**: `send` асинхронный, ошибка только логируется, `acks=1`;
  при падении лидера партиции сообщение теряется.
- **`StreamSubscriptionManager.stop()` нигде не вызывается**: при остановке стрим закрывает только
  `TinkoffConfig.shutdown()` (`InvestApi.destroy(1)`).
- **`collector.instruments` обязателен и в `history-dump`**, хотя стрим там не запускается: бин
  `CollectorProperties` создаётся `KafkaConfig` в любом профиле.
- **Разделение unit/e2e в `pom.xml` не настроено**, e2e-классов у Collector'а нет; `mvn test`
  выполняет весь набор.
- **Без покрытия остались** `TinkoffMarketStreamService`, `TinkoffInstrumentLoader`,
  `InstrumentCache`, `JdbcInstrumentRepository`, `HistoryDumpRunner` — их логика проверяется
  только прогоном на живом контуре.
- **Обработка сообщения идёт в потоке колбэка SDK**: конвертация и вызов `handle` → `publish` выполняются синхронно (сама отправка в Kafka — асинхронная); при росте списка до десятков инструментов это первое место, которое потребует нагрузки (очередь + отдельный publish-поток).
