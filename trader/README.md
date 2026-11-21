# Trader

Сервис исполнения торговых сигналов: получает `TradingSignal` из Kafka `trading-signals`, проверяет
сигнал по риск-лимитам, выставляет ордер и биржевые защитные ордера, ведёт позиции и публикует
`TradeResult` в Kafka `trade-results`. Порт — 8082, метрики — `/actuator/prometheus`.

Модуль Java 21 / Spring Boot 3.2, Maven-артефакт `trader`. Контракты — в [`../proto/`](../proto/)
(`trading_signal.proto`, `trade_result.proto`); общее описание системы — в [`../README.md`](../README.md);
локальный стенд PostgreSQL + Kafka — в [`../infra/local/README.md`](../infra/local/README.md).

## 1. Назначение

```
[Analyzer] ──▶ Kafka: trading-signals ──▶ [Trader] ──▶ Kafka: trade-results
                                              │
                                              ├──▶ T-Invest API (ордера, стопы, статусы, позиции)
                                              └──▶ PostgreSQL (trade_results, positions, processed_signals, risk_config)
```

Trader — единственная точка, где решение превращается в действие на бирже:

- **Риск-контроль на стороне исполнения.** Analyzer предлагает цену, SL, TP и уверенность, но не
  решает, сколько лотов купить: объём считает `RiskManager`. Ошибка или компрометация Analyzer не
  обходит лимиты — сигнал всё равно проходит `SignalProcessor` и `RiskManager`.
- **Исполнение не доверено Analyzer.** Analyzer — Python-модуль аналитики: он не подключается к
  брокерскому API и не держит токен T-Invest. Ордера, стопы и сверка позиций — только здесь, через
  интерфейсы `OrderExecutionService` и `ExchangeStateProvider` (привязка к SDK — в
  `config/TinkoffConfig` и адаптерах).
- **Trader владеет Flyway-миграциями** (design D6). Java-модуль с транзакционной записью применяет
  схему целиком; Analyzer пишет только в `analysis_results` через asyncpg. Два мигратора на одну БД
  недопустимы, поэтому миграции лежат в `trader/src/main/resources/db/migration/`.

## 2. Поток обработки сигнала

Точка входа — `SignalConsumer`: daemon-поток на `ApplicationReadyEvent` поверх бина `KafkaConsumer`
из `TraderKafkaConfig` (spring-kafka в проекте нет — design D1 изменения `trader-signal-consumer`).
Poll — 500 мс, автокоммит выключен, коммит — `commitSync` после успешной обработки записи.

| № | Шаг | Класс | Что решает | При отказе |
|---|---|---|---|---|
| 1 | Разбор записи | `SignalConsumer.process`, `TradeOrchestrator.process` | Валиден ли Protobuf и UUID `signal_id` | `InvalidProtocolBufferException` или невалидный UUID → лог, сигнал пропускается, offset коммитится |
| 2 | Дедупликация | `SignalDeduplicator.tryAcquire` | Новый ли сигнал | дубликат → `DUPLICATE`, без ордера и события; ошибка БД → «не новый» |
| 3 | Устаревание | `SignalValidator.isExpired` | `expires_at` в будущем | `expired` → `processed_signals.result=rejected`, публикуется `REJECTED` |
| 4 | HOLD | `SignalProcessor.validate`, `TradeOrchestrator` | Это попытка сделки | HOLD → `markRejected("hold signal")`, событие **не** публикуется (design D2) |
| 5 | Окно входа | `TradingWindow.isEntryAllowed` | Разрешены ли входы | `entry window` → `rejected` + `REJECTED` |
| 6 | Торговый статус | `ExchangeStateProvider.tradingStatus` | Статус `NORMAL_TRADING` (2) | статус ≠ 2 или ошибка API (`-1`) → `trading_status not normal` |
| 7 | Наличие SL/TP | `TradeOrchestrator.process` | Есть ли защитные уровни | `sl <= 0` или `tp <= 0` → `no sl/tp in signal` |
| 8 | Риск-проверки | `RiskManager.check` | Можно ли входить и каким объёмом | отказ с причиной из `RiskCheckResult` |
| 9 | Вход | `OrderExecutionService.placeOrder` | Выставить LIMIT | ордер отклонён → `trade_results` со `status=rejected` + `REJECTED` |
| 10 | Запись сделки | `TradeRepository.insertTrade` | Зафиксировать сделку | первая запись до `positions`: `positions.trade_id` — FK на `trade_results` |
| 11 | SL/TP | `placeStopLoss` / `placeTakeProfit` | Биржевая защита | best-effort: отказ логируется, `*_order_id` = `null`, позиция открыта |
| 12 | Позиция | `upsertPosition`, `PositionStore.put`, `onPositionOpened` | Учесть открытую позицию | — |
| 13 | Метрики и событие | `TraderMetrics`, `TradeResultPublisher` | `orders_placed_total`, `OPENED` | ошибка публикации только логируется |

- **Идемпотентность.** `order_id` = `UUID.nameUUIDFromBytes("order:" + signalId)`: повторная обработка
  не создаст второй ордер (design D3). `trade_id` — новый UUID на сделку, в том числе при отказе до
  исполнения: ключ топика `trade-results` обязателен.
- Поток работает в режиме at-least-once (`auto.offset.reset=earliest`). Дедупликация превращает это
  в effectively-once: повторная доставка после рестарта не даёт двойного входа, потому что
  `signal_id` уже в `processed_signals`. Плата — упавший после шага 2 сигнал отбрасывается как
  дубликат: вход не повторится, но и не случится (javadoc: «необработанный сигнал лучше потерять,
  чем исполнить дважды»).

`RetryableSignalException` — сигнал, отложенный до повтора: offset **не** коммитится.
`TradeOrchestrator.handle` переводит в него `DataAccessException` (БД недоступна): consumer логирует
«Signal postponed», спит 1 с и перечитывает запись. Нарушение FK и прочие ошибки данных
(`DataIntegrityViolationException`) — «отравляющее» сообщение: логируется и коммитится, иначе оно
крутилось бы вечно; прочие `RuntimeException` логируются, цикл продолжается. Закрытие по SL/TP
биржей Trader не отслеживает: `SL_HIT`, `TP_HIT`, `CLOSED`, `PARTIAL_FILL`, `CANCELLED` объявлены в
`trade_result.proto`, но код их не публикует — публикуются `OPENED`, `REJECTED`, `TIMEOUT_CLOSED`.

## 3. `SignalDeduplicator`

Один уровень защиты — таблица `processed_signals`, без in-memory кэша. Ключ — `signal_id` (PK).

```sql
INSERT INTO processed_signals (signal_id, result) VALUES (?, 'executed')
ON CONFLICT DO NOTHING
```

`ON CONFLICT DO NOTHING` не бросает исключение, поэтому дубликат определяется по числу вставленных
строк: 1 — сигнал новый, 0 — уже обработан; отвергнутые помечаются `UPDATE ... SET result='rejected',
reject_reason=?`.

- **Сбой БД трактуется как «не новый»** (`catch` → `false`): если нельзя проверить, обрабатывался ли
  сигнал, безопаснее пропустить его, чем исполнить дважды.
- **Переживает рестарт.** Состояние в PostgreSQL: повторно доставленный сигнал (group `trader`,
  `earliest`) упирается в существующую строку и не исполняется второй раз.
- **TTL 1 час задекларирован, но не реализован.** `V2` создаёт индекс `idx_processed_signals_cleanup`
  и описывает очистку записей старше часа, однако scheduled-задачи в коде нет: дедупликация
  действует бессрочно, таблица растёт. Окно в час рассчитано на то, что сигналы живут секунды
  (`expires_at`), поэтому рост — вопрос эксплуатации, а не корректности.

## 4. `SignalValidator`

| Проверка | Метод | Правило |
|---|---|---|
| Устаревание | `isExpired(expiresAt)` | `expires_at < now` (Clock в UTC); `null` — «не устарел» |
| Торговый статус | `isTradable(status)` | `status == 2` (`SECURITY_TRADING_STATUS_NORMAL_TRADING`) |

Своего порога «максимальный возраст сигнала» нет: горизонт задаёт Analyzer через `expires_at`, Trader
лишь сравнивает с текущим временем. Поля проверяются по месту: `signal_id` — в оркестраторе (валидный
UUID), `suggested_sl`/`suggested_tp` — там же (больше нуля), `suggested_price` уходит в LIMIT как есть.
Допустимость инструмента явной проверки не имеет: лотность берётся из `instruments` с дефолтом 1, а
неизвестный FIGI отсекается внешним ключом `trade_results.instrument_id` при записи. Отказ по статусу
означает и сетевой сбой: при ошибке `GetTradingStatus` провайдер возвращает `-1`.

## 5. `RiskManager` и `RiskCheckResult`

`check(figi, price, sl, tp, lotSize, deposit, correlation)` возвращает `RiskCheckResult(allowed,
rejectReason, quantityLots)`: проверки идут в фиксированном порядке, объём возвращается только при
`allowed=true`.

| № | Проверка | Параметр `risk_config` | Логика | Причина отказа |
|---|---|---|---|---|
| 1 | PnL-CB | `pnl_circuit_breaker_pct` (флаг ставится извне) | флаг `pnlCircuitBreakerActive` → отказ до расчётов | `pnl_circuit_breaker active` |
| 2 | Корреляция | `correlation_threshold` = 0.8 | `correlation > threshold` (строго) | `correlation limit` |
| 3 | Число позиций | `max_open_positions` = 3 | `size >= max` и инструмента нет среди открытых | `max_open_positions reached` |
| 4 | Rate-limit | `max_trades_per_minute` = 10 | скользящее окно 1 минута, старые моменты вытесняются | `rate_limit exceeded` |
| 5 | Размер позиции | `max_loss_per_trade_pct` = 1.0 | `floor(deposit*pct/100 / \|entry-sl\| / lotSize)`; `< 1` → отказ | `position_size too small` |
| 6 | Лимит номинала | `max_position_notional_pct` = 100.0 | `floor(deposit*pct/100 / (price*lotSize))`, объём **урезается**; `< 1` → отказ | `notional limit` |
| 7 | Комиссионный фильтр | `commission_profit_ratio` = 3.0, `commission_rate_per_side` = 0.003 | `\|tp-entry\|*units >= ratio*rate*entry*units*2`; при `tp <= 0` пропускается | `commission filter` |

Детали при чтении кода: rate-limit расходуется до расчёта объёма (отказ на шагах 5–7 всё равно
занимает слот); депозит — константа `trader.deposit` (200 000 ₽), `correlation` всегда `0.0`; доливка
не блокируется лимитом `max_open_positions` (условие `&& !containsKey(figi)`); ставка комиссии в БД,
поэтому смена тарифа не требует пересборки; `RiskConfig.defaults()` действует до первой загрузки.

## 6. `PnlCircuitBreaker`

- **Расчёт:** `registerPnl(pnl, deposit, breakerPct)` прибавляет PnL закрытой сделки к `dailyPnl` и
  сравнивает с `threshold = -deposit*pct/100` (3 % от 200 000 ₽ — минус 6000 ₽). Срабатывание —
  `dailyPnl <= threshold`: возвращается `true`, поднимается флаг `active`.
- **Сброс:** `resetForNewSession()` обнуляет `dailyPnl`, снимает `active`, запоминает
  `lastSessionReset`. Автоматического определения новой сессии (по `TradingSchedules`, как в design
  D9) нет — метод должен вызвать кто-то извне.
- **При пробитии** сам CB ничего не закрывает: блокировка входов идёт через
  `RiskManager.setPnlCircuitBreakerActive(true)` — тогда `check` отклоняет всё с причиной
  `pnl_circuit_breaker active`. Закрытие позиций и событие в `trade-results` не реализованы.
- **Метрика** `circuit_breaker_active` (Gauge 1/0) объявлена, но сеттер `circuitBreakerActive` в
  main-коде не вызывается. Связка «PnL-CB → RiskManager» отсутствует: `registerPnl`,
  `resetForNewSession` и `setPnlCircuitBreakerActive` не вызываются из потока исполнения. Компонент и
  тесты готовы, но в рантайме флаг всегда `false` — см. «Ограничения».

## 7. `TradingWindow`

- Порог — `risk_config.entry_after_msk_minutes` = 660 (11:00 МСК, минуты от полуночи). Время:
  `clock.instant().atZone(Europe/Moscow).toLocalTime()`, сравнение `toSecondOfDay()/60 >= threshold` —
  граница включена, в 11:00 вход уже разрешён; `threshold <= 0` выключает правило.
- Правило касается **только входов**: проверка стоит в `SignalProcessor.validate`, до риск-чека и
  исполнения; закрытие позиций (`AutoCloseService`) работает в любые часы.
- Порог читается из текущей конфигурации, поэтому правка строки в `risk_config` действует в пределах
  30 секунд (интервал poll), без передеплоя.

## 8. `PositionStore` и `AutoCloseService`

**Модель** — `record Position(figi, quantityLots, avgPrice, currentPrice, unrealizedPnl, openedAt,
tradeId, slPrice, tpPrice, slOrderId, tpOrderId)`. `PositionStore` держит их в `ConcurrentHashMap` по
`figi` (одна позиция на инструмент): `put/get/remove/all/count`, `findStale(maxHolding)` — позиции,
открытые раньше `now - maxHolding`.

**Авто-закрытие** — `AutoCloseService.checkStalePositions`, `@Scheduled(fixedDelay = 30_000)`, порог
`max_holding_time_minutes` = 10. Для каждой «залежавшейся» позиции вызывается
`emergencyClose(figi, |lots|, orderId)` (MARKET — единственное разрешённое место для market-ордера).
Успех: позиция удаляется из стора, публикуется `TIMEOUT_CLOSED` с `trade_id`, FIGI и объёмом.
Отказ: `error` в лог и `orders_rejected_total`; повтор — через 30 секунд.

Чего авто-закрытие **не** делает: не обновляет `trade_results` и `positions`, не снимает стопы, не
вызывает `onPositionClosed`, не пересчитывает `positions_open` — после трёх авто-закрытий
`max_open_positions` блокирует входы. **После рестарта** `PositionStore` пуст (`recover()` не
вызывается, раздел 10), поэтому `AutoCloseService` не видит унаследованные позиции.

## 9. Исполнение (`execution/`)

| Метод `OrderExecutionService` | Назначение | Тип ордера |
|---|---|---|
| `placeOrder(OrderRequest)` | вход в позицию | `LIMIT`/`BESTPRICE`; `MARKET` → `UnsupportedOperationException` |
| `placeStopLoss(figi, slPrice, lots, orderId)` | стоп-лосс | `STOP_ORDER_TYPE_STOP_LOSS` |
| `placeTakeProfit(figi, tpPrice, lots, orderId)` | тейк-профит | `STOP_ORDER_TYPE_TAKE_PROFIT` |
| `emergencyClose(figi, lots, orderId)` | аварийное закрытие | `MARKET` (SELL) |

`OrderRequest` — `figi`, `quantityLots`, `price`, `type`, `side`, `orderId` (идемпотентный ключ),
`signalId`. `OrderSide` — собственный enum `BUY/SELL`, чтобы бизнес-логика не зависела от
`OrderDirection` SDK и от порядка аргументов `postOrderSync`.

**Почему вход только LIMIT/BESTPRICE (design D4).** Проскальзывание market-ордера на депозите
200 000 ₽ съедает преимущество скальпинговой сделки; LIMIT по `suggested_price` фиксирует худшую цену
входа. MARKET оставлен для аварийного закрытия, когда важнее выйти, чем выйти по цене; запрет
продублирован в `OrderType` и в `TinkoffOrderExecutionService.placeOrder`.

**Стопы.** `StopOrdersService.postStopOrderGoodTillCancelSync`, направление `SELL` (стоп закрывает
long), цена срабатывания и лимитная цена совпадают с `slPrice`/`tpPrice`, срок — до отмены; объём —
фактически исполненный (`executedLots`) с фолбэком на расчётный.

**`OrderResult`** — `success`, `exchangeOrderId`, `rejectReason`, `executedPrice`, `executedLots`.
Успех — только `EXECUTION_REPORT_STATUS_FILL` (цена из `executedOrderPrice`, иначе запрошенная;
`executedLots` из `lotsExecuted`); любой другой статус превращается в `rejected(...)`, поэтому
частичное исполнение трактуется как отказ.

**Sandbox и prod.** В песочнице SDK не поддерживает `postLimitOrderSync` и стоп-ордера: ордера идут
через `SandboxService.postOrderSync`, в prod — через `OrdersService.postOrderSync`, тип передаётся
явно. Стопы в песочнице недоступны (`SandboxModeViolationException`) — отсюда best-effort-семантика
SL/TP. `orderId` детерминирован от `signal_id`, поэтому повтор после сбоя не создаёт второй ордер.

## 10. `RecoveryService`

По design D7 при старте Trader должен сверить состояние с биржей: позиции (`GetPositions`), заявки
(`GetOrders`), стопы (`GetStopOrders`), восстановить in-memory состояние и подписаться на стримы.
Биржа — source of truth для исполнения, БД — для запросов. В коде `recover()` только вызывает
`provider.loadOpenPositions()` и кладёт позиции в `PositionStore`; сверка с БД — комментарий, а не
код. Метод не вызывается ни из `ApplicationReadyEvent`, ни из `main`: на старте восстановления нет.

`ExchangeStateProvider` — абстракция над SDK. `loadOpenPositions()` различает модели ответа песочницы
(`PositionsSecurities`) и prod (`SecurityPosition`), пропускает нулевые объёмы; `GetPositions` не
отдаёт цену входа, текущую цену, PnL и идентификаторы стопов, поэтому в `Position` эти поля
заполняются нулями/`null`, а не догадкой. `tradingStatus(figi)` при ошибке API возвращает `-1`.

## 11. База данных и `risk_config`

Flyway включён в `application.yml` (`enabled=true`, `locations=classpath:db/migration`,
`baseline-on-migrate=true`) и применяет миграции при старте; `flyway-maven-plugin` не настроен —
схема обновляется только запуском приложения.

| Таблица | Миграция | Назначение | Кто пишет |
|---|---|---|---|
| `instruments` | V1 | FIGI, тикер, лот, шаг цены, статус | Collector |
| `analysis_results` | V1 | Результаты анализа и сигналы | Analyzer (asyncpg) |
| `trade_results` | V1 | История сделок: вход, выход, PnL, комиссия, стопы, статус, отказ | Trader |
| `positions` | V1 | Текущие открытые позиции (PK — `instrument_id`) | Trader |
| `orders` | V1 | История заявок | никто: `TradeRepository` сюда не пишет |
| `risk_config` | V1 (наполнение V2–V4) | Параметры риск-менеджмента без передеплоя | оператор/миграции |
| `processed_signals` | V1 (+ индекс в V2) | Дедупликация по `signal_id` | Trader |

**`risk_config`** — key/value (`key`, `value`, `description`, `updated_at`, `updated_by`).
`RiskConfigPoller` читает её на `ApplicationReadyEvent` и далее по cron `trader.poll-risk-config-cron`
= `*/30 * * * * *` (каждые 30 секунд). Ошибка чтения сохраняет прежнюю конфигурацию; отсутствующий
ключ заменяется дефолтом `RiskConfig.fromMap`.

| Ключ | По умолчанию | Что регулирует |
|---|---|---|
| `max_loss_per_trade_pct` | 1.0 | Убыток на сделку, % депозита — база расчёта объёма |
| `pnl_circuit_breaker_pct` | 3.0 | Дневной лимит убытка для PnL-CB, % депозита (6000 ₽ от 200 000 ₽) |
| `commission_profit_ratio` | 3.0 | Во сколько раз прибыль до TP должна покрыть комиссию круга |
| `max_open_positions` | 3 | Максимум одновременно открытых позиций |
| `max_holding_time_minutes` | 10 | Время удержания до принудительного закрытия |
| `correlation_threshold` | 0.8 | Порог корреляции с открытыми позициями |
| `max_trades_per_minute` | 10 | Лимит сделок в минуту (self-rate-limit) |
| `entry_after_msk_minutes` | 660 | Запрет входов до 11:00 МСК; 0 — выключено |
| `commission_rate_per_side` | 0.003 | Ставка за сторону: «Инвестор» 0.003, «Трейдер» 0.0005, «Премиум» 0.0004 |
| `max_position_notional_pct` | 100.0 | Лимит номинала, % депозита; объём урезается до лимита |

Правка лимита — обычный `UPDATE`, применение в пределах polling-интервала:

```sql
UPDATE risk_config SET value = '0.0005', updated_at = now(), updated_by = 'manual'
 WHERE key = 'commission_rate_per_side';
```

## 12. Метрики

`TraderMetrics` регистрирует шесть метров Micrometer, отдаются на `/actuator/prometheus` (тег
`application=trader`).

| Имя | Тип | Смысл (из кода) |
|---|---|---|
| `orders_placed_total` | Counter | «Total orders placed» — вход исполнен |
| `orders_rejected_total` | Counter | «Total orders rejected» — отказ биржи, неудачное авто-закрытие |
| `pnl_realized_total` | Gauge | «Realized PnL»; значение в копейках (`pnl * 100`) |
| `pnl_unrealized` | Gauge | «Unrealized PnL»; также в копейках |
| `positions_open` | Gauge | «Open positions count» — выставляется при открытии |
| `circuit_breaker_active` | Gauge | «PnL Circuit Breaker active (1/0)» |

`pnlRealized`, `pnlUnrealized`, `circuitBreakerActive` и `positionsOpen` после закрытия позиции из
main-кода не вызываются: PnL-гейджи и флаг CB нулевые, `positions_open` отражает только открытия.

## 13. Конфигурация

| Ключ `application.yml` | Значение | Смысл |
|---|---|---|
| `server.port` / `management.endpoints.web.exposure.include` | 8082 / `health,info,prometheus` | HTTP-порт; открытые actuator-эндпоинты (health с деталями, тег `application=trader`) |
| `spring.datasource.url` | `${DB_URL:jdbc:postgresql://localhost:5432/scalping}` | Подключение к PostgreSQL |
| `spring.datasource.username` / `password` | `${DB_USER:scalping}` / `${DB_PASSWORD:scalping}` | Учётные данные БД; переопределяются env, но **дефолт-пароль в конфиге есть** — для любого контура, кроме локального, его нужно задавать явно |
| `spring.flyway.enabled` / `locations` / `baseline-on-migrate` | `true` / `classpath:db/migration` / `true` | Миграции при старте, baseline для непустой БД |
| `kafka.bootstrap-servers` | `${KAFKA_BOOTSTRAP:localhost:9092}` | Брокеры Kafka |
| `kafka.consumer-group` | `trader` | Consumer group сигналов |
| `kafka.topic.trading-signals` / `trade-results` | `trading-signals` / `trade-results` | Входящий и исходящий топики |
| `tinkoff.token` | `${T_INVEST_TOKEN:}` | Сырой токен без `Bearer`; пустой дефолт, и `TinkoffConfig.investApi` бросает `IllegalStateException` — без env-переменной приложение не стартует |
| `tinkoff.sandbox` / `account-id` | `${T_INVEST_SANDBOX:true}` / `${T_INVEST_ACCOUNT_ID:}` | Режим песочницы; счёт (пусто — автоопределение через SDK) |
| `trader.deposit` | 200000 | Депозит для расчёта объёма |
| `trader.poll-risk-config-cron` | `*/30 * * * * *` | Период перечитывания `risk_config` |

Параметры Kafka-клиентов заданы кодом: consumer — `enable.auto.commit=false`,
`auto.offset.reset=earliest`; producer — `acks=1`, сериализаторы `String`/`ByteArray`.

## 14. Запуск и тесты

```bash
bash infra/local/up.sh              # PostgreSQL + Kafka + 3 топика (WSL2, Docker Engine)
mvn -pl trader spring-boot:run      # Flyway применит V1–V4 автоматически
mvn test                            # тесты Java-модулей (только по явному запросу — правило проекта)
mvn -pl trader test                 # только тесты Trader
```

Требуется `T_INVEST_TOKEN`: без него бин `InvestApi` не создаётся и контекст падает
(`IllegalStateException` в `TinkoffConfig`). `trade-results` — compact-топик с ключом `trade_id`
(`infra/kafka/create-topics.sh`), поэтому последнее событие по сделке вытесняет предыдущее.

Unit-тесты (`trader/src/test/java/ru/scalping/trader/`):

| Класс | Тесты | Что проверяют |
|---|---|---|
| `risk.RiskManagerTest` | `calculatesPositionSizeFromDistance`, `positionSizeTooSmallRejected`, `maxOpenPositionsReached`, `correlationLimitRejects`, `pnlCircuitBreakerBlocks`, `validSignalAllowed`, `notionalLimitCutsQuantityInsteadOfRejecting`, `notionalLimitRejectsWhenEvenOneLotDoesNotFit`, `commissionFilterRejectsThinTargetOnInvestorTariff`, `commissionFilterPassesSameTargetOnTraderTariff`, `commissionFilterSkippedWithoutTarget` | Формулу объёма, лимит позиций, корреляцию, флаг CB, урезание по номиналу, комиссионный фильтр и его отключение без TP |
| `risk.PnlCircuitBreakerTest` | `triggersAt3PercentLoss`, `doesNotTriggerBelowThreshold`, `blocksAfterTrigger`, `resetsOnNewSession`, `accumulatesPnl` | Порог 3 %, накопление PnL, сброс на новую сессию |
| `risk.TradingWindowTest` | `firstSessionHourIsBlocked`, `entryAllowedExactlyAtThreshold`, `eveningSessionIsAllowed`, `zeroThresholdDisablesTheRule`, `thresholdIsReadFromConfigNotFromCode`, `configMappingUsesDatabaseKey`, `configMappingDefaultsWhenKeyMissing` | Окно входа, включённую границу 11:00, выключение нулём, ключ из `risk_config` |
| `signal.SignalDeduplicatorTest` | `tryAcquire_insertedRow_returnsTrue`, `tryAcquire_conflictNoRow_returnsFalse`, `tryAcquire_databaseError_returnsFalse` | Дедупликацию по числу строк и поведение при сбое БД |
| `signal.SignalValidatorTest` | `expiredSignalRejected`, `freshSignalAccepted`, `normalTradingTradable`, `breakInTradingNotTradable` | Устаревание по `expires_at`, статус `NORMAL_TRADING` |
| `signal.TradeOrchestratorTest` | `handle_duplicateSignal_noOrderPlaced`, `handle_expiredSignal_publishesRejected`, `handle_holdSignal_noOrderPlaced`, `handle_databaseUnavailable_throwsRetryableException`, `handle_exchangeReject_persistsRejectReason`, `handle_partiallyFilledExecution_stopsUseExecutedLots`, `handle_tradingStatusNotNormal_rejectsSignal`, `handle_riskRejected_rejectsSignal`, `handle_successfulExecution_opensPositionAndPublishesOpened`, `handle_exchangeReject_publishesRejected`, `handle_invalidSignalId_skipsSignal`, `handle_stopLossRejected_stillOpensPosition` | Порядок шагов, идемпотентный `order_id`, запись `trade_results`/`positions`, объём стопов по `executedLots`, публикацию `OPENED`/`REJECTED`, маппинг `DataAccessException` в `RetryableSignalException` |
| `position.PositionStoreTest` | `findStaleReturnsOldPositions`, `removeDeletesPosition` | Выборку по времени удержания, удаление |
| `repository.TradeRepositoryTest` | `lotSize_existingInstrument_returnsLotFromDb`, `lotSize_missingInstrument_returnsDefaultLot`, `lotSize_nullLot_returnsDefaultLot` | Лотность из `instruments`, дефолт 1 |

`e2e/` — пять классов с `@Tag("e2e")`, все скелетные или unit-уровня: `E2EPipelineSandboxTest`
единственный гейтится окружением (`@EnabledIfEnvironmentVariable` `T_INVEST_TOKEN`), тело —
`assertThat(true)`; `E2ENfrLatencyTest` проверяет константы бюджета (2 с pipeline, 500 мс ордер), не
latency; `E2EPnlCircuitBreakerTest` — три `registerPnl(-2000)` на локальном объекте; `E2ERecoveryTest`
вручную кладёт позицию в `PositionStore`; `E2EThreeInstrumentsTest` — три `onPositionOpened` и отказ
четвёртому сигналу в `RiskManager`.

## 15. Ограничения

Подтверждено кодом, не предположения:

1. **SL/TP в песочнице не проверены.** SDK запрещает стоп-ордера в sandbox; SL/TP ставятся
   best-effort, позиция без биржевой защиты до авто-закрытия, проверка возможна только в prod.
2. **PnL-CB не подключён к исполнению.** `registerPnl`, `resetForNewSession` и
   `setPnlCircuitBreakerActive` не вызываются из main-кода; закрытия позиций по CB и события в
   `trade-results` нет; `circuit_breaker_active` и PnL-гейджи не обновляются.
3. **Recovery не запускается.** `recover()` не имеет вызова на старте, сверка с БД не реализована;
   после рестарта `PositionStore` пуст, авто-закрытие не видит унаследованные позиции.
4. **Авто-закрытие не синхронизирует состояние.** Не обновляются `trade_results` и `positions`, не
   вызывается `onPositionClosed`, не снимаются стопы, не обновляется `positions_open`: после
   нескольких авто-закрытий `max_open_positions` блокирует входы.
5. **Корреляционный лимит фактически отключён:** оркестратор всегда передаёт `correlation=0.0`,
   источника цен для расчёта в Trader нет.
6. **TTL `processed_signals` не реализован, таблица `orders` не используется:** индекс очистки есть,
   scheduled-задачи нет (таблица растёт, дедупликация действует бессрочно); `TradeRepository` пишет
   только в `trade_results`, `positions` и `processed_signals`.
7. **События закрытия не публикуются.** `SL_HIT`, `TP_HIT`, `CLOSED`, `PARTIAL_FILL`, `CANCELLED`
   объявлены в контракте, но код их не формирует.
8. **Частичное исполнение = отказ.** `toResult` признаёт успехом только полный `FILL`; частично
   исполненный ордер вернётся как `rejected`, хотя часть лотов могла исполниться на бирже.
9. **Депозит фиксирован** (`trader.deposit` = 200 000 ₽), а не текущий equity: после просадки
   расчёт объёма не адаптируется.
10. **Unit и e2e не разделены в `pom.xml`.** `@Tag("e2e")` информационный: `mvn test` запускает и
    e2e-классы; реально гейтится окружением только `E2EPipelineSandboxTest`.
11. **Ошибка публикации в Kafka теряет событие.** `TradeResultPublisher` только логирует ошибку в
    callback; повторной отправки и outbox нет — БД и поток событий могут разойтись.
12. **Неизвестный FIGI — «отравляющее» сообщение.** FK `trade_results.instrument_id` отсекает
    инструмент вне `instruments`, но уже после записи в `processed_signals`: сигнал пропускается.
13. **Цена входа не проверяется** на адекватность: `suggested_price` уходит в LIMIT без сверки с
    рынком, защита от устаревшей цены — только `expires_at`.
