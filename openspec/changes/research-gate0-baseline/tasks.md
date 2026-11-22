# Tasks — research-gate0-baseline (этап R0 плана)

## 1. Единицы и шаг цены

- [x] 1.1 `research/units.py`: `Parameter(name, value, units)`, словарь допустимых единиц,
      отказ при отсутствии `units` и при несовпадении ожидаемой единицы
- [x] 1.2 тесты `tests/test_research_units.py`: параметр без единиц, неверные единицы, сборка
      `units_registry`
- [x] 1.3 `research/ticks.py`: резолвер шага цены (метаданные → справочник → НОД сетки),
      флаг `UNVERIFIED`, перевод цены/дельты в тики
- [x] 1.4 тесты `tests/test_research_ticks.py`: шаг из метаданных, fallback, цена не кратна шагу,
      округление вверх/вниз
- [x] 1.5 Collector: `min_price_increment` в `.meta.json` (`HistoryCsvWriter`), обновить
      `HistoryMeta` в `backtest/loader.py`, тест контракта meta
- [x] 1.6 проверка meta-пути на синтетическом `.meta.json` с `min_price_increment`; после этого —
      перевыгрузка одного инструмента профилем `history-dump` и проверка источника «метаданные»
      (перевыгрузка AFLT за 2026-11-17 выполнена: 998 баров, meta содержит
      `"min_price_increment": 0.01`, отчёт Gate D показывает источник `metadata`)

## 2. Сессии и flat-at-close

- [x] 2.1 `research/sessions.py`: расписание из конфигурации, фиксация TZ и DST, `τ_session`,
      признак «вход запрещён» (предпосылка для 3.1)
- [x] 2.2 тесты `tests/test_research_sessions.py`: граница `T_close`, `buffer`, отсутствие
      переноса через сессию, DST-переход
- [x] 2.3 сверка часов с `TradingSchedules` (однократно) и запись результата в registry
      (2026-11-22, sandbox-API MOEX: будни 09:10–18:54, аукционы 09:00–18:59, выходные — «нет
      торгов». Это **расходится** с данными выгрузки: будни 07:00–23:50, выходные торгуются.
      Расхождение зафиксировано; приоритет у фактических данных, часы остаются
      `PROJECT-DECISION:sessions` до сверки на production-токене)

## 3. Gate D

- [x] 3.1 `research/integrity.py`: проверки — пропуски внутри сессий (расписание из 2.1), нулевые
      объёмы, дубли и монотонность `ts`, аукционные принты, бары вне сессий, overnight-доходности,
      кратность цены шагу, аномалии `|ΔP|`, распределение `|ΔP|` в тиках
- [x] 3.2 уровни замечаний: `critical` останавливает исследование, `warning` попадает в отчёт
- [x] 3.3 тесты `tests/test_research_integrity.py` на синтетике: дубли времени, цена вне сетки,
      пропуск внутри сессии, ночной разрыв, корпоративный разрыв
- [x] 3.4 прогон по `data/history-1min` (20 инструментов, 40 файлов): отчёт сохраняется,
      критичных нарушений нет либо они перечислены

## 4. Издержки, метка, дисперсии, VR

- [x] 4.1 `research/costs.py`: круг в тиках (ставка, минкомиссия, спред), флаг `UNVERIFIED`,
      стресс `×1.5`; тесты `tests/test_research_costs.py`
- [x] 4.2 `research/labels.py`: вариант метки, `h_max`, запись в registry; тесты
      `tests/test_research_labels.py`
- [x] 4.3 `research/variance.py`: Parkinson/GK/RS, диагностика шума, `VR(τ)`, `σ_τ`,
      десезонализация; тесты `tests/test_research_variance.py` (включая запрет `σ√τ` без `VR`)

## 5. Реестр опытов

- [x] 5.1 `research/registry.py`: структура записи, id, чтение `N_trials`; тесты
      `tests/test_research_registry.py`
- [x] 5.2 проверка, что артефакты этапа несут id записи; критерий «DSR без registry» даёт отказ
      (конверт артефакта `build_artifact`, отказ на пустом реестре покрыт тестами)

## 6. CLI и обвязка

- [x] 6.1 `research/cli.py`: подкоманды `integrity` и `report`, `--data-dir`, `--out` (обязателен,
      иначе отчёт не сохраняется)
- [x] 6.2 `pyproject.toml`: entry-point `analyzer-research`
- [x] 6.3 проверка обязательных полей артефактов (единицы, `n`, метод, флаги) — общий валидатор
      `research/artifacts.py`
- [x] 6.4 тесты `tests/test_research_cli.py`: разбор аргументов, обязательность `--out`,
      ненулевой код возврата при критичном Gate D
- [x] 6.5 `pytest tests -q` без регрессий, `ruff check src tests`
- [x] 6.6 прогон `analyzer-research integrity --data-dir ../data/history-1min --out ../reports/research`
      и сохранение отчёта

## 7. Документация изменения

- [x] 7.1 `brief.md` актуализирован (шаги R0.1–R0.9, планируемые файлы, отклонения)
- [x] 7.2 `openspec validate --strict research-gate0-baseline` проходит