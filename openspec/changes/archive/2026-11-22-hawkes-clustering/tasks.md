# Tasks

## 1. Оценка потока

- [x] 1.1 `analyzer/hawkes.py`: `HawkesParams`, `simulate_hawkes` (thinning, `n=0` — Пуассон),
  `estimate_hawkes` (MLE экспоненциального ядра, `min_events`); проверка — синтетика: Пуассон
  даёт `n < 0.15`, Hawkes `n=0.6` восстанавливается в допуске 0.15
- [x] 1.2 `MicrostructureState`: буфер времён сделок, пересчёт не чаще `hawkes_refresh`,
  ключ `hawkes` в снимке; проверка — снимок после потока сделок содержит оценку в (0, 1)

## 2. Индикатор и production-путь

- [x] 2.1 `Indicators.hawkes`, параметр `hawkes` у `compute_all`; `config.py` —
  `hawkes_window`, `hawkes_refresh` с валидацией; проверка — тест передачи значения и дефолтов
- [x] 2.2 `handler.py` — `ts` сделки из сообщения, `main.py` — передача `hawkes` в `compute_all`;
  проверка — тест пайплайна с подставленным значением (при установленном `kafka`)

## 3. Модель

- [x] 3.1 `signal_models/hawkes.py`: `HawkesConfig`, `HawkesModel` (mean reversion при
  `n < порога`, HOLD при кластеризации/без данных), `hawkes_config` из `--model-param`;
  проверка — три сценария спеки
- [x] 3.2 Запись `hawkes` в реестре и экспорт; проверка — `model_names()` содержит имя, CLI
  принимает `--strategy hawkes`

## 4. Документация

- [x] 4.1 `analyzer/README.md`: Hawkes (формула, branching ratio, порог, ограничение бэктеста);
  проверка — раздел ссылается на реальные флаги и поля
- [x] 4.2 `brief.md` приведён в соответствие с изменёнными файлами и результатом проверки;
  проверка — сверка разделов brief с фактическим диффом

## Workflow follow-up

- Прогон тестов — по явному запросу владельца; ревью — только с разрешения.
- `openspec validate hawkes-clustering`; архив — после приёмки.
