# Tasks

## 1. Оценка параметров

- [x] 1.1 `signal_models/ou.py`: `OuParams`, `estimate_ou(closes, dt)` через AR(1)/OLS,
  отказ при `b ∉ (0,1)`, коротком окне и нечисловых значениях; проверка — синтетика OU с
  известными θ/μ/σ восстанавливается в допуске
- [x] 1.2 `OuConfig` (window, min_bars, z_entry, max_half_life_bars) с валидацией и меткой
  варианта; `ou_config` разбирает `--model-param`, неизвестный ключ — ошибка; проверка — тест на
  неверные и неизвестные параметры

## 2. Модель и реестр

- [x] 2.1 `OuMeanReversionModel`: `fit` оценивает параметры по окну, `predict` — BUY/SELL по
  z-score, HOLD при отсутствии параметров, медленном возврате, отсутствии ATR; `warmup` =
  window; проверка — тесты сигналов и half-life
- [x] 2.2 Запись `ou` в `MODEL_SPECS`, экспорт из пакета; проверка — `model_names()` содержит
  `ou`, `--strategy ou` доступен

## 3. Прогон и CLI

- [x] 3.1 `backtest/engine.py`: вызов `fit` на окне `max(cfg.window, warmup)` перед
  `predict`; окно индикаторов не меняется; проверка — тест порядка вызовов и паритет прежних
  сигналов
- [x] 3.2 `backtest/cli.py`: `--model-param key=value` (int → float → str), разбор до
  `_strategy_config`; `replace(..., min_atr_pct=...)` только при наличии поля; проверка — CLI
  `--strategy ou --model-param window=120` строит отчёт с меткой окна

## 4. Документация

- [x] 4.1 `analyzer/README.md`: OU (формулы, half-life, HOLD-условия), `--model-param`, правило
  «fit перед predict»; проверка — раздел ссылается на реальные флаги и пути
- [x] 4.2 `brief.md` приведён в соответствие с изменёнными файлами и результатом проверки;
  проверка — сверка разделов brief с фактическим диффом

## Workflow follow-up

- Прогон тестов — по явному запросу владельца; ревью — только с разрешения.
- `openspec validate ou-mean-reversion`; архив — после приёмки.
