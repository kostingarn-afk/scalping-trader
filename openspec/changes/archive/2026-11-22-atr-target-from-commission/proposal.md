# Proposal

## Why

Цель выхода в production задаётся константой `suggested_tp = entry + 2×ATR` (`strategy.py:41`),
а комиссионный фильтр требует, чтобы прибыль до TP покрывала `ratio × 2 × rate` от суммы сделки.
При тарифе «Инвестор» (`rate = 0.003`) это 1.8 % суммы, а `2×ATR(1min)` на ликвидных бумагах —
около 0.12 %: фильтр отсекает почти все минутные сигналы, то есть система не торгует вообще.

Цель, привязанная к ATR жёстко, не может одновременно покрывать комиссию и быть достижимой:
расстояние до TP должно зависеть от волатильности инструмента и от тарифа. Шаг 1.9 `to_do.md`
§4.1 задаёт цель `k × ATR(1min)`, где `k = ceil(0.30 / ATR(1min, %))`, и ставку «Трейдера»
0.0005 — порог 0.30 % вместо 1.8 %.

## What Changes

- **Политика цели** — `strategy.target_mult` и `with_target`: `k = ceil(min_tp_pct / ATR%)`,
  но не меньше 1; TP = `entry ± k×ATR`. `with_target` заменяет TP сигнала, геометрия
  BB/RSI остаётся в `mean_reversion` без изменений.
- **Production** — `main.py` применяет `with_target` с порогом `AnalyzerConfig.required_tp_pct`
  (= `ratio × 2 × rate_per_side × 100` = 0.30 %), ставка в конфиге — 0.0005.
- **Бэктест** — `RunConfig.min_tp_pct`; движок применяет ту же политику после вызова модели,
  поэтому прогон воспроизводит production. Порог считается по тарифу и классу инструмента.
  Явный `--atr-tp` или `--tp-sl` отключают политику: перебор TP остаётся статическим.
- **Trader** — миграция `V6` меняет сид `commission_rate_per_side` на `0.0005`; дефолт
  `RiskConfig.defaults()` синхронизирован. `V4` не правится.
- **Документация** — `analyzer/README.md` (политика цели, флаги), `trader/README.md` (ставка,
  порог фильтра), спецификация `signal-analysis`.

Не входит: EV-гейт входа (шаг 1.10), цель для моделей `momentum`/`v2`/`ou` (их TP остаётся
статическим), пересчёт `commission_profit_ratio`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `signal-analysis`: цель выхода строится от порога комиссии (`k × ATR`), а не от фиксированных
  2×ATR; порог зависит от тарифа.

## Impact

- Код Analyzer: `strategy.py`, `main.py`, `config.py`, `commission_filter.py`,
  `backtest/models.py`, `backtest/engine.py`, `backtest/cli.py`, `backtest/report.py`.
- БД: миграция `V6__commission_rate_trader.sql` (новая; `V1`–`V5` не правятся).
- Код Trader: `RiskConfig.defaults()`.
- Тесты: `test_target_tp.py` (новый), дополнение `test_backtest_engine.py`, `test_strategy.py`.
- Collector, `proto/`, Kafka — не затрагиваются.
