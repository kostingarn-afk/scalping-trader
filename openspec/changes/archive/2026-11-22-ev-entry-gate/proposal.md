# Proposal

## Why

Правило проекта (§7 `AGENTS.md`, §1 `to_do.md`) требует входить только при положительной ожидаемой
доходности после издержек: `EV = p·avgWin − (1−p)·avgLoss − C − S`. Сейчас входа не ограничены
ничем, кроме комиссионного фильтра: он проверяет только, что цель покрывает комиссию, но ничего
не говорит о вероятности её достижения. Прогон шага 1.9 это показал прямо: цель от комиссии
(`k = 5` при медианном ATR% 0.066) достигается в 27.7 % случаев, и каждая сделка в среднем теряет
деньги — фильтр такие входы пропускает.

Нужен гейт, который по **фактическим** исходам закрытых сделок (TP, SL, таймаут, дневной стоп)
считает EV и не даёт входить, пока оценка неположительна.

## What Changes

- **Оценка** — `backtest/ev_gate.py`: по последним `window` закрытым сделкам прогона `p`
  (доля прибыльных), `avgWin`, `avgLoss` (средние модули), `EV = p·avgWin − (1−p)·avgLoss`
  в рублях на сделку. Комиссия и издержки исполнения уже внутри `net_pnl`, поэтому отдельно
  не вычитаются (иначе двойной счёт).
- **Гейт в движке** — `RunConfig.ev_gate` (`enabled`, `min_trades = 50`, `window = 200`).
  До накопления `min_trades` сделок гейт не действует (иначе нечем оценивать), после —
  сигнал с `EV ≤ 0` отбрасывается; в отчёте появляется счётчик «EV».
- **CLI** — `--ev-gate/--no-ev-gate` (по умолчанию включён), `--ev-min-trades`, `--ev-window`.
- **Trader** — `EvEstimator` по тем же закрытым сделкам (`recentClosedPnls`), поле
  `RiskManager.expectedValue`, отказ `negative EV`; пополнение — из общего опроса статистики
  (`RiskStatsPoller`, бывший `KellyPoller`), минимальная выборка — `risk_config.ev_min_trades`
  (миграция `V7`).
- **Отчёт** — колонка «EV» (сколько сигналов отклонено гейтом) и строка параметров гейта.

Не входит: оценка `p` по модели (только фактические исходы), реоптимизация `k` (шаг 1.9),
портфельный EV, Bayesian-сглаживание доли.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `signal-analysis`: вход разрешён только при неотрицательной ожидаемой доходности.
- `risk-management`: Trader отклоняет сигнал при неположительном EV и хранит порог выборки
  в `risk_config`.

## Impact

- Код Analyzer: новый `backtest/ev_gate.py`; правки `backtest/models.py`, `backtest/engine.py`,
  `backtest/cli.py`, `backtest/report.py`.
- Код Trader: новые `EvEstimator`, `RiskStatsPoller`; правки `RiskManager`, `RiskConfig`,
  `TradeRepository` (переиспользуется `recentClosedPnls`).
- БД: миграция `V7__ev_min_trades.sql`.
- Тесты: `analyzer/tests/test_ev_gate.py`, дополнения движка и отчёта; `EvEstimatorTest`,
  дополнение `RiskManagerTest`.
- Kafka, `proto/`, Collector — не затрагиваются.
