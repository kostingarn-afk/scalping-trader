# Tasks

## 1. Оценка в бэктесте

- [x] 1.1 `backtest/ev_gate.py`: `estimate(trades, min_trades, window)` → `p`, `avgWin`,
  `avgLoss`, `EV` по `net_pnl`; границы выборки; проверка — юнит-тесты формулы, окна,
  односторонней и короткой выборки
- [x] 1.2 `EvGateConfig` и `RunConfig.ev_gate` (`enabled=True`, `min_trades=50`, `window=200`);
  проверка — тест дефолтов и валидации

## 2. Движок и CLI

- [x] 2.1 Гейт в `engine.py` перед комиссионным фильтром, счётчик `skipped_by_ev` в
  `BacktestResult`; проверка — тесты: малая выборка не блокирует, отрицательный EV отсекает,
  положительный пропускает
- [x] 2.2 `cli.py`: `--ev-gate/--no-ev-gate`, `--ev-min-trades`, `--ev-window`; проверка —
  тест разбора флагов и отключения гейта

## 3. Отчёт

- [x] 3.1 `report.py`: колонка «EV», параметры гейта в строке параметров, пояснение в сносках;
  проверка — тест отчёта с ненулевым счётчиком

## 4. Trader

- [x] 4.1 `EvEstimator`: `EV` по списку `pnl` и порогу выборки; проверка — юнит-тесты
- [x] 4.2 `RiskConfig.ev_min_trades` (дефолт 50) и `V7__ev_min_trades.sql`; проверка —
  юнит-тест разбора и `mvn test`
- [x] 4.3 `RiskStatsPoller` (бывший `KellyPoller`) обновляет Келли и EV; `RiskManager`
  отклоняет сигнал при `EV ≤ 0` с причиной `negative EV`; проверка — тесты поллера и отказа

## 5. Документация и артефакты

- [x] 5.1 `analyzer/README.md`: гейт, флаги, порядок проверок; `trader/README.md`: ключ
  `ev_min_trades`, причина отказа
- [x] 5.2 `brief.md` приведён в соответствие с диффом и результатом проверки

## Workflow follow-up

- `pytest tests`, `ruff check src tests`, `mvn -pl trader -am test` — по явному запросу владельца.
- `openspec validate ev-entry-gate`; архив — после приёмки.
