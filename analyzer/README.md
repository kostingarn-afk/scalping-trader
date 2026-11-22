# Analyzer — индикаторы и торговые сигналы

Analyzer читает `raw-market-data`, считает индикаторы, применяет правило входа, фильтрует сигналы по издержкам, публикует `trading-signals` и пишет аудит в `analysis_results`. Офлайн-бэктест на выгрузке Collector'а — отдельная точка входа, сеть и токен ей не нужны.

```
Collector ──▶ Kafka: raw-market-data ──▶ [Analyzer] ──▶ Kafka: trading-signals ──▶ Trader
                                          └──▶ PostgreSQL: analysis_results
```

## 1. Назначение

Analyzer — единственное место, где живёт правило входа: индикаторы и пороги меняются здесь, Trader только исполняет. К брокерскому API Analyzer **не обращается и токен T-Invest не держит**:

- подписки на стримы и `MarketDataService.GetCandles` — ответственность Collector'а (`../AGENTS.md` §3); Analyzer получает Kafka-сообщения или CSV-выгрузку;
- во всём пакете нет ни `tinkoff`, ни `T_INVEST_TOKEN`, ни чтения env (проверено по коду) — поэтому бэктест воспроизводим на машине без доступа к API;
- историю для бэктеста выгружает Collector, Analyzer читает файлы (контракт — §11.1);
- из `raw-market-data` Analyzer потребляет свечи, **стакан** и **сделки**: по стакану считается `order_imbalance`, по потоку сделок — `vpin` и кластеризация `hawkes` (§5); без стакана и сделок эти поля пусты.

Полное ТЗ — [`../docs/Техническое задание.md`](../docs/Техническое%20задание.md), обзор проекта и инфраструктура — [`../README.md`](../README.md).

## 2. Поток обработки

Путь бара: `consumer.py` → `handler.py` → `main.py`.

1. **Чтение.** `KafkaConsumer` на `raw-market-data`, `group_id=analyzer`, `enable_auto_commit=False`, `auto_offset_reset="latest"`, ключ — строка (figi).
2. **Распаковка Protobuf.** `parse_candle` разбирает `RawMarketData` и возвращает `None` для: пустого payload и отсутствующих сгенерированных модулей; битых байт (плюс `WARNING Failed to parse RawMarketData`); сообщения без поля `candle` — стакан, сделка, торговый статус (`payload_type` для этого не годится: `CANDLE = 0` совпадает с «поле не задано»); незавершённой свечи (`is_complete = false`). Иначе — `(figi, {candle_ts: end, close, high, low, volume})`, `Quotation` → `units + nano / 1e9`.
3. **Буфер.** `CandleWindow` — отдельная `deque` на figi: `maxlen = 200` (`CANDLE_WINDOW`), минимум `25` баров (`MIN_CANDLES`, запас над MACD(21) и ATR(14)). Свеча с тем же `candle_ts`, что и последняя, **заменяет** её: стрим присылает одну свечу несколько раз. Пока история короче 25 баров, обработки нет — ни сигнала, ни записи в БД.
4. **Индикаторы.** `compute_all(closes, highs, lows, volumes)` по окну (до 200 баров).
5. **Правило входа.** `strategy.mean_reversion(indicators, price)`, `price` — закрытие последнего бара.
6. **Комиссионный фильтр.** `CommissionFilter.should_keep(signal)` (§6); не прошёл — сигнал подменяется на `HOLD` с причиной `commission filter`.
7. **Запись в БД.** `analysis_results` пишется **всегда**, включая `HOLD` (§7).
8. **Публикация.** В Kafka уходят только `BUY`/`SELL`; `HOLD` инкрементит `signals_rejected_total`.

Невалидное сообщение не порождает ни ошибки, ни записи, ни метрики — просто пропускается. Исключение внутри `pipeline.process` ловится в обработчике (`Pipeline failed for figi=...`): процесс не должен падать из-за одного сообщения.

## 3. Многопроцессность

`main()` поднимает сервер метрик и создаёт `num_processes` (по умолчанию 2) процессов `multiprocessing.Process`, каждый — свой `KafkaConsumer` в одной consumer group.

- **Партиции.** Collector кладёт сообщения с ключом `figi`, поэтому все бары инструмента попадают в одну партицию, а значит — в один процесс. Это делает корректным in-memory буфер на figi: делить и синхронизировать его не нужно.
- **Изоляция.** Обработчик создаётся внутри процесса (`_run_worker`): свой asyncio-loop, свой пул asyncpg (max 5), свой producer — наследовать их от родителя нельзя.
- **Коммит.** Offset коммитится после каждого сообщения, но `handler` перехватывает исключения сам (`try/except` вокруг `pipeline.process`), наружу они не выходят, поэтому коммит выполняется и при сбое обработки. Доставка фактически **at-most-once**, с разной ценой сбоя: упала запись в БД — бар не попал ни в `analysis_results`, ни в сигналы; упала публикация — запись в БД осталась, сигнал потерян (сохранение идёт до публикации). После рестарта бар не переигрывается в обоих случаях.
- **Почему процессы.** Индикаторы CPU-bound: ta-lib отпускает GIL, но single-process loop всё равно упирается в один поток. Альтернатива (asyncio в одном процессе) отвергнута в `../openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/design.md` (D5).
- **Метрики.** `start_metrics_server(config.metrics_port)` вызывается в родителе до старта воркеров: порт 8000, `/metrics` в формате Prometheus (про агрегацию — §8 и §13).

## 4. Индикаторы

`indicators.py`, numpy-массивы + ta-lib. `Indicators` — frozen dataclass; `to_dict()` отбрасывает `None` и уходит в БД и в поле `indicators` сигнала.

| Индикатор | Параметры | Что даёт | Где используется |
|---|---|---|---|
| `rsi` | период 7 | перепроданность/перекупленность | `mean_reversion` 30/70, v2 25/75, momentum 65/35 |
| `macd`, `macd_signal`, `macd_hist` | 8 / 21 / 5 | импульс | только справочно, в правилах не участвует |
| `bb_upper`, `bb_middle`, `bb_lower` | 20, 2σ | канал волатильности | `mean_reversion`, `zscore` |
| `atr` | 14 | абсолютная волатильность | SL/TP всех правил, трейлинг, `atr_pct` |
| `adx` | 14 | сила тренда | v2 (потолок), momentum (пол) |
| `zscore` | из BB | `(close − middle) / (upper − middle)`: полуширина канала уже равна `nbdev` σ | v2, momentum |
| `atr_pct` | ATR / close × 100 | волатильность бара в шкале издержек | фильтр волатильности v2/momentum |
| `vwap` | по окну | «средняя цена» | в правилах не используется (§13) |
| `order_imbalance` | depth 10 | `(bid_vol − ask_vol) / (bid_vol + ask_vol)` | не используется: стакан в production не передаётся (§13) |

`compute_atr_pct_series` (ATR% по всей серии без прогревочных `NaN`) нужен только бэктесту — для `--atr-pct-quantile`.

## 5. Стратегии

### `mean_reversion` — production

`strategy.py`, вызывается из `main.py`; пороги не параметризованы конфигом.

- нет `rsi`/`bb_lower`/`bb_upper` → `HOLD "insufficient data"`; `bb_upper ≤ bb_lower` или `atr` пуст/`≤ 0` → `HOLD "flat market: no volatility"` (на неизменном ряду ta-lib отдаёт RSI = 0, без этой проверки получался ложный `BUY`);
- **BUY:** `rsi < 30` **и** `price ≤ bb_lower`; `confidence = 0.5 + 0.5·(30 − rsi)/30`, не выше 1.0;
- **SELL:** `rsi > 70` **и** `price ≥ bb_upper`; **HOLD** иначе;
- `SL = price ∓ 1.5·ATR`, `TP = price ± 2.0·ATR` (множители — параметры функции; бэктест передаёт свои из `RunConfig`).

### `v2` (`strategy_v2.py`) — только бэктест

Mean reversion с фильтрами режима. Дефолты `StrategyV2Config`: `z_entry=2.0`, `rsi_buy=25`, `rsi_sell=75`, `adx_max=25`, `min_atr_pct=0` (выключен).

- `HOLD`, если нет `zscore`/`rsi`/`atr` или `atr ≤ 0`; отдельно `adx is None` → `HOLD "insufficient data (adx)"`;
- `atr_pct < min_atr_pct` → `HOLD "volatility below threshold"`; `adx > adx_max` → `HOLD "trending market"`;
- **BUY:** `zscore ≤ −z_entry` **и** `rsi < rsi_buy`; **SELL:** `zscore ≥ z_entry` **и** `rsi > rsi_sell`.

### `momentum` (`strategy_momentum.py`) — только бэктест

Вход в сторону отклонения. Дефолты `MomentumConfig`: `z_entry=1.5`, `rsi_buy=65`, `rsi_sell=35`, `adx_min=0` (выключен), `min_atr_pct=0`. Но CLI всегда передаёт `--z-entry` (дефолт 2.0), поэтому в прогоне действует 2.0, а не 1.5 из dataclass.

- **BUY:** `zscore ≥ z_entry` **и** `rsi > rsi_buy`; **SELL:** `zscore ≤ −z_entry` **и** `rsi < rsi_sell`;
- `atr_pct < min_atr_pct` → `HOLD`; `adx < adx_min` → `HOLD "no trend"`, но только если ADX посчитан (`adx is None` фильтр пропускает);
- SL/TP — те же ATR-множители: сравнение стратегий идёт на одинаковом выходе.

### `ou` (`signal_models/ou.py`) — только бэктест

Mean reversion на процессе Орнштейна–Уленбека (`docs/mat_analyze.md` §2). Параметры θ, μ, σ,
σ_eq и half-life оцениваются AR(1) на скользящем окне в `fit`; прогон вызывает `fit` на окне
перед каждым предсказанием, поэтому оценка скользящая, а не однократная.

- `z = (price − μ)/σ_eq`; **BUY** при `z ≤ −z_entry`, **SELL** при `z ≥ z_entry`;
- `HOLD`, если: параметры не оценены (ряд не возвращается к среднему, окно короче `min_bars`),
  half-life больше `max_half_life_bars` (возврат не успеет за горизонт удержания), нет ATR;
- параметры запуска: `--z-entry` и `--model-param window=240`, `min_bars=30`,
  `max_half_life_bars=10`; неизвестный ключ — ошибка до прогона;
- `σ` берётся из остатка AR(1) с поправкой `√(2θ/(1−b²))`, `σ_eq = σ/√(2θ)`;
- SL/TP — те же множители ATR, что у остальных правил: сравнение идёт на одинаковом выходе.

### `microstructure` (`signal_models/microstructure.py`) — production-данные

Вход по дисбалансу стакана: **BUY** при `order_imbalance ≥ obi_entry`, **SELL** при
`order_imbalance ≤ −obi_entry`, при условии `vpin ≤ vpin_max`. Нет стакана, нет сделок,
`vpin` выше потолка или ATR ≤ 0 — HOLD с причиной.

Параметры: `--model-param obi_entry=0.2`, `vpin_max=0.5`.

**В бэктесте модель всегда HOLD:** CSV-выгрузка Collector содержит только свечи, стакана в
истории нет. Проверка — unit-тесты на индикаторах и production-путь после запуска Collector.

### `hmm` (`signal_models/regime.py`) — только бэктест

Режим рынка — Gaussian HMM с `states=2` на логе абсолютных доходностей окна
(лог-волатильность). «Спокойное» состояние — с наименьшим средним (`argmin(means_)`); вход
mean reversion (z-score + RSI) разрешён только в нём, в волатильном — HOLD.

- переобучение не чаще `refit_bars` (30) баров, текущее состояние пересчитывается каждый бар;
- параметры: `--model-param window=240`, `min_bars=60`, `states=2`, `refit_bars=30`,
  `n_iter=100`, `seed=7`; порог `--z-entry`, RSI — `--rsi-buy/--rsi-sell`;
- инициализация — по квантилям признака: kmeans-инициализация `hmmlearn` на скошенном
  распределении сходится в вырожденное решение (оба состояния описывают волатильный режим);
- `hmmlearn` импортируется лениво: без него реестр и остальные модели работают, а `--strategy
  hmm` падает с понятной ошибкой.

### `hawkes` (`signal_models/hawkes.py`) — production-данные

Поток сделок оценивается экспоненциальным Hawkes: `λ(t) = μ + Σ α·e^{−β(t−t_i)}`,
`branching ratio n = α/β` (`analyzer/hawkes.py`, MLE). При `n → 1` сделки идут лавиной и mean
reversion убыточен (`docs/mat_analyze.md` §2), поэтому модель **фильтрует** вход, а не меняет
направление.

- вход mean reversion разрешён при `hawkes < branching_threshold` (по умолчанию 0.7);
- HOLD при кластеризованном потоке, отсутствии оценки или недостатке индикаторов;
- параметры: `--model-param branching_threshold=0.7`, пороги — `--z-entry`, `--rsi-buy/sell`;
- пересчёт оценки — не чаще `hawkes_refresh` (25) сделок по окну `hawkes_window` (500);
  `AnalyzerConfig.hawkes_*`; меньше 50 сделок — оценки нет.

**В бэктесте модель всегда HOLD:** CSV-выгрузка Collector содержит только свечи, сделок в
истории нет. Проверка — синтетика (Пуассон против Hawkes) и production после запуска Collector.

**Боевой Analyzer работает на `mean_reversion`.** `v2`, `momentum`, `ou`, `microstructure`,
`hmm` и `hawkes` подключены только к `analyzer-backtest` или к production-индикаторам; включение
модели в живой контур — отдельный шаг после подтверждения преимущества (docstring модулей).

### Реестр моделей (`signal_models/`)

Подключение модели к бэктесту идёт через реестр, а не через правку CLI:

| Модуль | Роль |
|---|---|
| `signal_models/base.py` | протокол `SignalModel` (`name`, `version`, `params()`, `fit(history)`, `predict(ind, price, sl, tp)`) и структурный `BarLike` |
| `signal_models/builtin.py` | адаптеры `MeanReversionModel`, `MomentumModel`, `MeanReversionV2Model` и разбор их параметров из флагов запуска |
| `signal_models/registry.py` | `MODEL_SPECS`, `build_model(name, config)`, `model_names()`; неизвестное имя → `UnknownModelError` |

Как добавить модель (шаги 1.2–1.5 плана):

1. реализовать `SignalModel` (для правила без параметров `fit` — no-op, как в `builtin.py`);
2. добавить `ModelSpec` в `MODEL_SPECS`;
3. добавить golden-тест паритета, если модель заменяет существующее правило.

`--strategy` в `analyzer-backtest` строится из `model_names()`, поэтому новая запись сразу
доступна в CLI. Подстановки другой модели вместо неизвестной нет: опечатка в имени — ошибка до
прогона (в `to_do.md` §3 это отмечено как дефект прежнего `_builder_for`).

## 6. Комиссионный фильтр

`CommissionFilter` отбрасывает сигнал, если прибыль до TP не покрывает `ratio` круговых комиссий:

```
profit     = |TP − entry| × quantity             (quantity = 1, подсказка размера)
commission = rate_per_side × price × 1 × 2       (вход + выход)
keep       = profit ≥ ratio × commission
```

- `HOLD` отбрасывается всегда, до расчёта.
- `ratio` — `AnalyzerConfig.commission_profit_ratio = 3.0`, ставка — `commission_rate_per_side = 0.003` («Инвестор»). Это поля dataclass в `config.py`, не env и не `risk_config`.
- С дефолтами фильтр требует `2·ATR ≥ 3·0.006·P`, то есть **ATR ≥ 0.9 % цены**; на минутных барах ATR% — сотые доли процента, поэтому отсекается почти всё (отсюда комментарий в коде про ~92 % на «Инвесторе»). На «Трейдере» (0.0005) порог — `ATR ≥ 0.15 %`.
- Отношение не зависит от размера позиции (прибыль и комиссия линейны по количеству), поэтому `quantity = 1`.

В бэктесте работает другая реализация (§11.2): комиссия по тарифу и классу инструмента плюс проверка, что TP и SL лежат впереди фактической цены входа.

## 7. Публикация сигналов и БД

**Kafka `trading-signals`** (`signal_publisher.py`), `TradingSignal` из [`../proto/trading_signal.proto`](../proto/trading_signal.proto):

| Поле | Значение |
|---|---|
| `signal_id` | UUID, новый на каждый обработанный бар |
| `figi`, `signal` | инструмент; `BUY`/`SELL` (HOLD не публикуется) |
| `ts` | **не заполняется** — остаётся значением по умолчанию |
| `expires_at` | `now + signal_ttl_seconds` (30 с) |
| `confidence`, `strategy_id`, `analyzer_version` | из сигнала и конфига (`mean_reversion_bb`, `0.1.0`) |
| `suggested_price`, `suggested_sl`, `suggested_tp` | `Quotation` (units + nano) |
| `position_size_hint` | константа 1; размер считает риск-контур Trader |
| `indicators` | `map<string,float>`, весь `Indicators.to_dict()` — для аудита |

Ключ партиционирования — `figi` (как у Collector'а): сигналы по инструменту идут последовательно. Producer: `acks=1`, `compression_type=gzip`.

**PostgreSQL `analysis_results`** (`repository.py`, asyncpg, пул 1–5): каждая обработанная свеча — `signal_id, ts, instrument_id (figi), candle_ts, candle_interval, strategy_id, analyzer_version, indicators (JSON), signal, confidence, suggested_price, suggested_sl, suggested_tp, position_size_hint, expires_at`; `ON CONFLICT (signal_id) DO NOTHING`.

Analyzer **не владеет схемой**: миграции применяет Trader (Flyway), Analyzer пишет в одну таблицу. Иначе на БД было бы два мигратора, а владельцем транзакционных таблиц остаётся Java-модуль (`design.md` D6).

## 8. Метрики

`metrics.py`, HTTP на `metrics_port` (8000), путь `/metrics`; сервер поднимается в родительском процессе.

| Имя | Тип | Смысл |
|---|---|---|
| `signals_generated_total` | Counter | сигналов опубликовано (только BUY/SELL) |
| `signals_rejected_total` | Counter | публикации не было: HOLD, в том числе после комиссионного фильтра |
| `analysis_latency_seconds` | Histogram | время `process`: индикаторы + фильтр + БД + публикация |
| `consumer_lag` | Gauge, label `partition` | объявлена, но нигде не выставляется — фактического лага нет |

Счётчики инкрементируются в воркерах, а registry и HTTP-сервер живут в родителе; multiprocess-режим `prometheus_client` (`PROMETHEUS_MULTIPROC_DIR`) не включён — по коду значения воркеров до `/metrics` не доходят (§13).

## 9. Конфигурация

`AnalyzerConfig` — frozen dataclass с дефолтами; env-переменные **не читаются**. Необязательный позиционный аргумент `poetry run analyzer <file.yaml>` подгружает YAML: берутся только ключи, совпадающие с полями dataclass, остальные молча игнорируются.

| Поле | Дефолт | Назначение |
|---|---|---|
| `kafka_bootstrap` | `localhost:9092` | брокер (consumer и producer) |
| `consumer_group` | `analyzer` | consumer group |
| `topic_raw` / `topic_signals` | `raw-market-data` / `trading-signals` | топики входа и выхода |
| `num_processes` | `2` | число процессов-консьюмеров |
| `strategy_id` / `analyzer_version` | `mean_reversion_bb` / `0.1.0` | в БД и в сигнале |
| `signal_ttl_seconds` | `30` | срок жизни сигнала |
| `commission_profit_ratio` | `3.0` | порог «прибыль / комиссия» |
| `commission_rate_per_side` | `0.003` | ставка за сторону («Инвестор»; «Трейдер» 0.0005) |
| `db_url` | `postgresql://scalping:scalping@localhost:5432/scalping` | DSN asyncpg |
| `metrics_port` | `8000` | порт `/metrics` |
| `compression_type` | `gzip` | сжатие producer'а (`lz4` требует пакета) |

## 10. Генерация Protobuf

`*_pb2.py` в git не хранятся (`.gitignore`), их создаёт build-шаг. `proto_loader` импортирует модули «мягко» и добавляет каталог пакета в `sys.path`: protoc генерирует абсолютные импорты (`import common_pb2`).

```bash
cd analyzer
poetry install
poetry run protoc -I ../proto --python_out src/analyzer ../proto/*.proto
```

`-I ../proto` обязателен: `raw_market_data.proto` и `trading_signal.proto` импортируют `common.proto` (та же форма команды — в тексте ошибки `handler.make_handler`).

Нужен **protoc 25.x** (`libprotoc 25.3`): runtime — `protobuf 4.25` (`pyproject.toml`), а модули от protoc 3.12 из репозитория Ubuntu он отвергает с `Descriptors cannot be created directly`. Готовый бинарь — `protoc-25.3-*-x86_64.zip` с GitHub releases.

Без модулей `PROTO_AVAILABLE = false`, `make_handler` бросает `RuntimeError` при создании обработчика — воркеры падают на старте, а не на первом сообщении. Публикация в этом состоянии возвращает `False` с предупреждением.

## 11. Бэктест

`analyzer-backtest` (entry-point → `backtest/cli.py`) переиспользует production-расчёт (`compute_all` + `mean_reversion`); отличаются источник данных, модель издержек и эмуляция выходов. Обоснования — [`../openspec/changes/backtest-runner/design.md`](../openspec/changes/backtest-runner/design.md).

Торговый горизонт — **`1min`** (`../AGENTS.md` §7). Прогоны на `1h`, включая панель, — исследование
отбора и режима: их результат не переносится в торговый контур без отдельного гейта на минутных
данных. То же относится к `5min`/`15min` (дефолт `--intervals 1,5,15`): преимущество старшего
фрейма для торговли требует отдельного доказательства, а не следует из прогона.

### 11.1 Контракт входных данных

Выгрузку делает Collector (профиль `history-dump`, см. [`../collector/README.md`](../collector/README.md) и `../AGENTS.md` §5), Analyzer только читает:

- `{ticker}_{interval}min.csv` — `ts,open,high,low,close,volume`, `ts` — **время закрытия** свечи, ISO-8601 UTC, по возрастанию;
- `{ticker}_{interval}min.meta.json` — `ticker`, `class_code`, `lot_size`, `interval_minutes` (обязательные), `fetched_at`, `source`.

`scan_dir` читает все `*.csv` каталога, инструмент берёт из sidecar. Без meta нужны `--csv` + `--ticker` (+ `--lot-size`/`--class-code`): угадывать контракт запрещено. Загрузчик отвергает нарушение OHLC, неположительные цены, отрицательный объём, невозрастающее время и шаг, не кратный таймфрейму.

Старшие таймфреймы **считаются из минутного** (`aggregate`), а не качаются. Группировка — по времени открытия бара (`ts − interval`), иначе 5-мин свеча склеивалась бы из закрытий 07:00…07:04 и смещалась на бар. Пропуски групп не создают; крайние группы сессии остаются неполными. Пример: `--intervals 1,5,15` на минутной выгрузке.

### 11.2 Движок

`engine.run_backtest`: окно 200 баров, минимум 25; одна позиция на инструмент, следующий сигнал — после выхода.

- **Вход** — по `open` следующего бара: сигнал считается по закрытой свече, заявка попадает в стакан уже следующего бара. `--fill-on-close` — вход по закрытию сигнального бара (замер `to_be.md` §3); тогда бар входа не проверяется на выход (high/low сформировались до входа — lookahead).
- **Выходы:** SL/TP от ATR; при касании обоих в баре выигрывает **SL**; гэп через стоп — по цене открытия, гэп к TP — по TP; таймаут — закрытие бара, где `bars_held ≥ --max-bars`.
- **Трейлинг** (`--trailing-atr`): подтягивается по закрытию завершённого бара только в сторону прибыли, поэтому расшириться не может.
- **`--no-tp`:** цель не выставляется (`Trade.tp = None`), выход по SL/трейлингу/таймауту; фильтр по-прежнему считает прибыль до цели сигнала.
- **`--cooldown-bars`:** после выхода N баров без входов — ограничитель оборота (комиссия линейна по числу сделок, преимущество — нет).
- **`--daily-stop`:** зеркало PnL-CB Trader. Реализованный PnL за UTC-дату (сессия MOEX целиком внутри одной UTC-даты), проверяется и плавающий убыток; при пробитии позиция закрывается по закрытию, входы запрещены до следующего дня.
- **`--entry-after-msk HH:MM`:** запрет входов до времени по Москве; проверяется время бара **исполнения**.
- **Размер:** `min(риск / |entry − SL|, лимит номинала)` в лотах; меньше лота — сделка пропускается (`Пропуск` в отчёте).
- **Фильтр бэктеста:** комиссия по тарифу и классу инструмента + проверка направления — при входе по `open` цена может уйти за TP/SL сигнала, такая сделка отбрасывается, а не открывается «против» цели.

### 11.3 Модель издержек

`backtest/commission.py`: ставки **за сторону** в долях суммы сделки, круг = две стороны. Биржевая комиссия включена в брокерскую; признак фьючерса — класс `SPBFUT`/`FORTS`.

| Тариф | Бумаги, за сторону | Фьючерсы, за сторону |
|---|---|---|
| `investor` (по умолчанию) | 0.003 | 0.001 |
| `trader` | 0.0005 | 0.0004 |
| `premium` | 0.0004 | 0.00025 |

`--exchange-fee-per-contract` добавляет фиксированный сбор биржи за контракт (₽ за сторону) — только фьючерсам; без него фьючерсный прогон считает лишь процент брокера. `min_commission_rub` в модели есть, но CLI его не задаёт (0).

**Не моделируется:** проскальзывание и очередь в стакане, абонентская плата, ступени ставок по обороту, минимальная комиссия брокера, внутрисделочная просадка. Для фьючерсов лимит номинала — по полной стоимости контракта, а не по ГО (консервативнее реальных ограничений). Цифры прогона — верхняя граница оценки, а не обещание доходности.

### 11.4 Отчёты

Формат определяется расширением `--out`:

- **`.md`** — параметры (депозит, риск, лимит номинала, TP/SL или перебор, таймаут, вход, `filter ratio`, cooldown, трейлинг, стратегия и варианты); таблица прогонов: Бары, Сигналы, Фильтр, Пропуск, Сделок, Win %, TP/SL/TO, Expectancy ₽/%, Итог ₽/%, Max DD %, Комиссия ₽, «Открыта»; блок «Лучшие прогоны» — топ-10 по итогу с `Gross/комиссия`, печатается при ≥2 разных метках; блок «Выходы» — вклад TP/SL/TIMEOUT/DAILY_STOP в net (при >12 прогонах — только лучший).
- **`.json`** — список `Summary`; **`.csv`** — сделки: `run, direction, entry_ts, exit_ts, entry_price, exit_price, sl, tp, lots, lot_size, exit_reason, bars_held, notional, gross_pnl, commission, net_pnl`.

Раздел нулевой модели дописывается только в `.md`; для `.json`/`.csv` печатается в консоль. «Сигналы» считаются только при отсутствии позиции; «Открыта» — позиция на конец выгрузки, в сделках и PnL не учтена.

### 11.5 Нулевая модель (`--null-seeds N`)

Тот же движок, фильтр, геометрия выхода и издержки, но входы случайны (направление 50/50, вероятность = фактические сделки / (`бары − min_candles`)).

- Сравнение — по **net на сделку**: число сделок в случайных прогонах отличается из-за занятости позиции, сумма сравнивала бы разные выборки.
- p-value — доля случайных прогонов не хуже фактического с поправкой `+1`: `(better + 1) / (seeds + 1)`; без неё при малом N получалось бы «p = 0» вместо «не встретилось».
- `p ≤ 0.05` — правило приносит то, чего случайный вход с теми же выходами не даёт; `p > 0.20` — сигнал неотличим от шума, улучшать нужно выход, издержки или горизонт.
- В переборах считается максимум для 6 лучших прогонов (`NULL_MAX_RUNS`).

### 11.6 Walk-forward (`--walkforward`)

Train фиксированной длины, test — следующий блок, шаг = длина test; неполный последний блок отбрасывается.

- На train выбирается конфигурация с **максимальным net** при сделках ≥ `--wf-min-trades` (30). Нет кандидата — окно помечается, тест не запускается.
- На test — без подгонки; хвост train (до `window` = 200 баров) служит прогревом индикаторов, его сделки отбрасываются по `entry_ts`.
- Отчёт: параметры, train net, test сделки/Win %/gross/комиссия/net, гейт `gross/комиссия` (кандидат при ≥ 1 и положительном net), `t` — t-статистика среднего net-PnL (значимость при `|t| ≥ 2`), строка **OOS** по всем тестовым окнам.
- `--wf-grid "z_entry=1,1.5,2;adx_min=0,25"` — декартово произведение осей, имена — поля конфига стратегии. У `mean_reversion` параметров нет: непустая сетка — ошибка.
- `--null-seeds` и `--tp-sl` с walk-forward несовместимы (проверка до прогонов).

### 11.7 Панель (`--panel`)

Кросс-секционный momentum: на дате ребаланса вселенная ранжируется по доходности за `--panel-lookback` торговых дней, `--panel-legs` имён сверху покупаются и столько же снизу продаются; веса равные, книга долларово-нейтральна.

- Данные — последнее закрытие UTC-даты; дни без торгов остаются `NaN` (заполнять нельзя). Периоды не перекрываются, шаг — `--panel-rebalance` дней; период пропускается, если меньше `2 × legs` имён с данными на прошлую, входную и выходную даты.
- Издержки платят только изменившиеся ноги: `changed × 2 × cost / (2 × legs)`, ставка — тариф бумаг из `--tariffs`.
- **Стопов нет** — для нейтральной книги они искажают сигнал. Исключение — `--panel-daily-stop`: лимит убытка книги, при пробитии период закрывается по этому дню (внутридневных данных нет, выход только на закрытии).
- `--panel-signal`: `raw` — сырая доходность; `residual` — минус бета × доходность рынка; `volscaled` — доходность / собственный риск за окно.
- `--panel-grid "lookback_days=5,10;rebalance_days=5;legs=3,4"` (значения → `int`); `--panel-null-seeds` перемешивает оценки внутри ребаланса (состав и оборот те же, информация уничтожена); `--panel-walkforward` с `--panel-train-days`/`--panel-test-days`/`--panel-min-periods` выбирает конфигурацию на train и оценивает на следующем блоке.
- В отчёт входит `Net без 3 лучших %` — проверка на концентрацию: результат на двух-трёх периодах подтверждённым не считается.
- `--panel-universe` — явный список тикеров (неизвестный — ошибка): вселенная должна быть видна в команде.
- Не учитывается: заимствование бумаг и ГО для шорта, делистинг, внутрипериодные движения (PnL от закрытия до закрытия), проскальзывание; биржевой сбор за контракт к панели не применяется.

### 11.8 Флаги CLI

| Флаг | Дефолт | Смысл |
|---|---|---|
| `--data-dir` | — | каталог выгрузок (`*.csv` + `*.meta.json`) |
| `--csv` | `[]` | отдельный CSV (можно повторять) |
| `--ticker` | — | тикер для CSV без meta (только один файл) |
| `--class-code` / `--lot-size` | `TQBR` / `1` | класс инструмента; акций в лоте / размер контракта |
| `--native-interval` | из meta | таймфрейм выгрузки, мин (без meta обязателен) |
| `--intervals` | `1,5,15` | целевые таймфреймы |
| `--tariffs` | `investor` | тарифы через запятую |
| `--deposit` | `200000` | депозит, ₽ |
| `--risk-pct` | `1.0` | риск на сделку, % депозита |
| `--max-notional-pct` | `100.0` | лимит номинала позиции, % депозита |
| `--atr-sl` / `--atr-tp` | `1.5` / `2.0` | множители ATR для SL и TP |
| `--tp-sl` | `` | перебор пар `TP:SL` (`2:1.5,3:2`), метка `tp3/sl2` |
| `--strategy` | `mean_reversion` | `mean_reversion` / `v2` / `momentum` / `ou` / `microstructure` / `hmm` / `hawkes` (из реестра `signal_models`) |
| `--model-param` | — | параметры модели `key=value`, повторяемый (например `window=240` для `ou`) |
| `--z-entry` | `2.0` | порог `\|z\|` в σ BB (v2, momentum) |
| `--rsi-buy` / `--rsi-sell` | — | v2: 25/75, momentum: 65/35 (перекрывают дефолты стратегии) |
| `--adx-max` / `--adx-min` | `25.0` / `0.0` | v2: потолок ADX; momentum: минимум (0 — выкл.) |
| `--min-atr-pct` | `0.0` | минимальный ATR в % цены (0 — выкл.) |
| `--atr-pct-quantile` | — | порог ATR% как квантиль серии (только v2) |
| `--max-bars` | `10` | таймаут сделки, баров |
| `--cooldown-bars` | `0` | баров после выхода без входов |
| `--trailing-atr` | `0.0` | трейлинг-стоп в ATR от закрытия (0 — выкл.) |
| `--no-tp` / `--fill-on-close` | выкл. | не выставлять TP; вход по закрытию сигнального бара |
| `--daily-stop` | `0.0` | дневной лимит убытка, % депозита (0 — выкл.) |
| `--entry-after-msk` | `` | запрет входов до `HH:MM` МСК |
| `--exchange-fee-per-contract` | `0.0` | сбор биржи за контракт, ₽ за сторону |
| `--null-seeds` | `0` | прогонов нулевой модели (0 — выкл.) |
| `--walkforward` | выкл. | walk-forward по окнам |
| `--wf-train-bars` / `--wf-test-bars` | `0` / `0` | длины окон (обязательны при `--walkforward`) |
| `--wf-grid` / `--wf-min-trades` | `` / `30` | сетка параметров; минимум сделок на train |
| `--panel` | выкл. | панельный режим |
| `--panel-lookback` / `--panel-rebalance` | `5` / `5` | окно и шаг ребаланса, дней |
| `--panel-legs` / `--panel-signal` | `4` / `raw` | имён в стороне книги; `raw`/`residual`/`volscaled` |
| `--panel-daily-stop` | `0.0` | дневной лимит убытка книги, % |
| `--panel-grid` / `--panel-universe` | `` | сетка панели; тикеры через запятую (пусто — все выгрузки) |
| `--panel-null-seeds` | `0` | сидов нулевой модели панели |
| `--panel-walkforward` | выкл. | walk-forward панели по датам |
| `--panel-train-days` / `--panel-test-days` | `0` / `0` | окна панели, дней |
| `--panel-min-periods` | `10` | минимум периодов на train |
| `--out` / `--title` | — | файл отчёта и его заголовок |

Коды возврата: `0` — успех, `2` — ошибка данных или флагов (сообщение в stderr).

## 12. Запуск и тесты

```bash
cd analyzer
poetry install            # или: python -m venv .venv && .venv/Scripts/python.exe -m pip install -e .
poetry run analyzer       # порт 8000, /metrics; конфиг: poetry run analyzer config.yaml

# $UNIVERSE — тикеры текущего замера; список в документации не фиксируется
poetry run analyzer-backtest --data-dir ../data/history \
  --intervals 1,5,15 --tariffs investor --out ../reports/backtest.md
```

Без poetry работает локальное окружение `.venv`; нужен `pip install -e .` либо `PYTHONPATH=src`, иначе `python -m analyzer.backtest.cli` не найдёт пакет. Зависимости бэктеста (`pandas`, `statsmodels`, `scikit-learn`) объявлены в `pyproject.toml`, отдельная установка не нужна:

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
```

Тесты (`analyzer/tests/`) сети и API не требуют:

| Файл | Что проверяет |
|---|---|
| `test_indicators.py` | RSI на плоском ряду, imbalance, VWAP, `compute_all`, защита `zscore`/`atr_pct` |
| `test_strategy.py` | BUY/SELL/HOLD, недостаток данных, плоский рынок, SL/TP от ATR |
| `test_strategy_v2.py` | фильтры ADX/ATR%, симметрия, confidence, метка, валидация конфига |
| `test_strategy_momentum.py` | пробой + подтверждение RSI, ADX-фильтр, волатильность, метка |
| `test_commission_filter.py` | отсев/пропуск, HOLD, расчёт комиссии, смена тарифа |
| `test_handler.py` | разбор `RawMarketData`, окно свечей (нужны `*_pb2.py`) |
| `test_signal_publisher.py` | HOLD не публикуется |
| `test_backtest_loader.py` | meta, OHLC-инварианты, шаг, агрегация 1→5 мин |
| `test_backtest_engine.py` | TP/SL/таймаут, приоритет SL, гэп, лимит номинала, cooldown, трейлинг, дневной стоп, окно МСК |
| `test_backtest_commission.py` | тарифы, паритет с production-фильтром вне пороговой полосы, сбор за контракт |
| `test_backtest_report.py` | сводка, CSV/JSON, блоки «Выходы» и «Лучшие прогоны» |
| `test_backtest_nullmodel.py` | детерминизм сидов, p-value с поправкой `+1` |
| `test_backtest_walkforward.py` | выбор на train, окна, прогревочные сделки, строка OOS |
| `test_backtest_cli.py` | флаги, коды возврата, перебор TP/SL, walk-forward, null |
| `test_panel.py` | ранжирование, издержки изменившихся ног, нулевая модель, walk-forward, концентрация |

`pyproject.toml`: `testpaths = ["tests"]`, `pythonpath = ["src"]`, `asyncio_mode = "auto"`; ruff — `line-length = 100`, `target-version = "py311"`.

**Про `*_pb2.py`:** в git их нет (§10). `test_handler.py` помечен `skipif(not PROTO_AVAILABLE)` — без модулей он не выполняется, а пропускается. `test_signal_publisher.py` проходит и без них: проверяется ветка `HOLD`, возвращающая `False` до обращения к Protobuf; публикация `BUY`/`SELL` без сгенерированных модулей не тестируется (в бою вернула бы `False`).

## 13. Ограничения

Подтверждено кодом:

1. **Нет сессионного фильтра.** Ни `handler`, ни `engine` не отсекают ночь и выходные: в ATR и в поиске входов участвуют все бары выгрузки. Ограничение по времени суток есть только в бэктесте (`--entry-after-msk`) и сессионным календарём не является.
2. **VWAP считается неточно:** в `compute_vwap` вместо закрытия подставлен `np.roll(highs, 0)`, то есть типичная цена = `(H + L + H) / 3`. В правилах входа VWAP не участвует, но в БД и сигнале значение — не классический VWAP.
3. **`order_imbalance` в production всегда `None`:** `compute_all` вызывается без `bids/asks`, а сообщения со стаканом `parse_candle` отбрасывает.
4. **`candle_interval` в БД — константа `"1min"`** (`main.py`), независимо от интервала источника.
5. **`TradingSignal.ts` не заполняется** — времени генерации в сообщении нет, только `expires_at`.
6. **Метрики:** `consumer_lag` не выставляется; счётчики растут в воркерах, а registry — в родителе, multiprocess-режим не включён. По коду `/metrics` не покажет значения воркеров; запуском не проверялось.
7. **В БД пишется каждый бар после прогрева**, включая `HOLD` — таблица растёт линейно (на минутных данных тысячи строк в день на инструмент).
8. **`signal_id` — новый UUID на каждую попытку.** `ON CONFLICT DO NOTHING` защищает от повтора того же id, но повторной обработки в текущем коде нет: `handler` глотает исключение, offset коммитится, бар с ошибкой теряется (раздел 3). Единственное, что спасает от дубля — сигнал вообще не публикуется при сбое.
9. **`v2` и `momentum` не в production** — только бэктест; живой контур работает на `mean_reversion` (RSI 30/70 + BB(20, 2σ)).
10. **Порог фильтра — из `config.py`, а не из `risk_config`** (в коде назван дубликатом для автономии): правка `commission_profit_ratio` в БД на Analyzer не влияет.
11. **ADX требует 29 баров** (`2 × period + 1`), а окно готово с 25: первые бары дают `adx = None` — `v2` вход блокирует, `momentum` пропускает (фильтр обходится).
12. **Панель не моделирует** заимствование бумаг, ГО для шорта, делистинг и внутрипериодные движения; сбор за контракт к ней не применяется.
13. **Справка расходится с поведением:** `--daily-stop` в `--help` назван лимитом «на сделку», а движок считает PnL дня; `--native-interval` обещает «иначе 1», а без meta и без флага загрузчик возвращает ошибку.
14. **Выгрузка без meta не читается** из `--data-dir` (только явные `--csv` + `--ticker`) — требование контракта, но на «сырой» выгрузке прогон не запустится.
