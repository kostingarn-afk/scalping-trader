# Tasks

## 1. Политика цели

- [x] 1.1 `strategy.target_mult` и `strategy.with_target`: `k = max(1, ceil(min_tp_pct / ATR%))`,
  TP = `entry ± k×ATR`; нет ATR или `min_tp_pct ≤ 0` — сигнал без изменений; проверка —
  юнит-тесты формулы, покрытия порога, направления и выключенной политики
- [x] 1.2 `AnalyzerConfig.required_tp_pct` = `ratio × 2 × rate × 100`; `main.py` применяет
  `with_target` к сигналу до комиссионного фильтра; проверка — юнит-тест property и
  интеграционный тест pipeline (TP покрывает порог)

## 2. Ставка

- [x] 2.1 `config.py` и `commission_filter.py`: `commission_rate_per_side = 0.0005`;
  проверка — тест дефолта и порога 0.30 %
- [x] 2.2 `trader/.../V6__commission_rate_trader.sql`: `UPDATE risk_config` ставки на 0.0005;
  `RiskConfig.defaults()` синхронизирован; проверка — `mvn -pl trader -am test`

## 3. Бэктест

- [x] 3.1 `RunConfig.min_tp_pct` и применение политики в `engine.py` после вызова модели;
  проверка — тест: TP сделки равен `k×ATR` при `min_tp_pct > 0`, и прежний TP при нуле
- [x] 3.2 `cli.py`: `--atr-tp` по умолчанию `None`; при отсутствии явного TP порог считается
  по тарифу и классу инструмента; `--tp-sl`, walk-forward и панель работают со статической
  целью; проверка — тесты `_required_tp_pct` и разбора вариантов
- [x] 3.3 `report.py`: шапка называет действующую цель и пороги по тарифам; проверка — тест
  строки параметров в обоих режимах

## 4. Документация и артефакты

- [x] 4.1 `analyzer/README.md`: политика цели, формула `k`, флаги, смена семантики `--atr-tp`;
  `trader/README.md`: ставка 0.0005 и порог фильтра
- [x] 4.2 `brief.md` приведён в соответствие с диффом и результатом проверки

## Workflow follow-up

- `pytest tests`, `ruff check src tests`, `mvn -pl trader -am test` — по явному запросу владельца.
- `openspec validate atr-target-from-commission`; архив — после приёмки.
