# Brief — research-gate0-baseline

Этап **R0** нового плана `to_do.md` (§5, шаги R0.1–R0.9). Контекст: приоритет изменён на
`docs/buy_frame.md` → `docs/holding_exiting.md`; операционные правила прежних шагов
(`k×ATR`, простой EV-гейт, HMM на 1m) отменены или понижены. Нумерация `R*` введена после
ревью: архивные шаги 1.1–1.10 остаются под старыми номерами.

## Шаги

1. **Единицы (R0.1).** `Parameter(name, value, units)` с проверкой; `units_registry`.
2. **Шаг цены (R0.2).** `min_price_increment` в `.meta.json`; резолвер meta → БД → НОД сетки
   с флагом `UNVERIFIED`.
3. **Сессии (R0.3).** Расписание в конфиге, фиксация TZ/DST, разовая сверка с
   `TradingSchedules`, `τ_session`, flat-at-close.
4. **Gate D (R0.4).** Пропуски внутри сессий (расписание из R0.3), нулевые объёмы, монотонность
   и дубли времени, бары вне сессий, overnight-доходности, кратность цены шагу, аномалии `|ΔP|`
   (включая корпоративные действия).
5. **Издержки (R0.5).** `cost_estimate` в тиках за круг: ставка, минкомиссия, спред как
   оценка (`UNVERIFIED`), стресс ×1.5.
6. **Метка (R0.6).** fixed-horizon или triple-barrier, из метки `h_max` для purging в CPCV.
7. **Дисперсии (R0.7).** Parkinson/GK/RS по барам, диагностика «шум против диапазона».
8. **VR и σ_τ (R0.8).** `VR(τ)`, `σ_τ = σ_1√(τ·VR(τ))`, десезонализация по минуте сессии.
9. **Trial registry (R0.9).** Структура записи и id в артефактах отбора — без него DSR
   невычислим.

`φ*(τ)` и Gate 0a в это изменение не входят: по §16 спеки они идут после фазы A (этапы R1–R2
нового плана), а `VR`/`σ_τ` считаются раньше — отклонение объявлено в `to_do.md` §4.

## Принятые решения

- **Шаг цены** — из справочника инструмента (поле уже есть в БД и в `InstrumentMeta`), в
  офлайн-выгрузке через `.meta.json`. Если поля нет — шаг выводится по сетке цен и помечается
  `UNVERIFIED`; молчаливой подстановки нет.
- **Сессии** — фиксированное расписание MOEX в конфиге (основная сессия; вечерняя не входит),
  а не `TradingSchedules`: офлайн-историй с календарём нет, а правило должно быть одинаковым
  в бэктесте и runtime.
- **Единицы** — обязательное поле, а не соглашение: ошибка единиц (проценты вместо тиков) —
  главный источник неверных выводов в прежних итерациях.
- **`φ*`** — вне этого изменения: по §16 спеки Gate 0a идёт после заморозки политики (этап R1),
  здесь только готовятся его входы — `Cost` в тиках и `σ_τ`.
- **10m** — полноправный фрейм: входит в набор Δ идентификации и в подтверждение режима
  (`docs/*.md` дополнены), в runtime агрегируется наравне с 15m.
- **Политика** (`q`, `α_total`, `λ`, `|Z_entry|`, `ρ_max`, `impact-бюджет`, `τ_cap`, `buffer`)
  вынесена в конфиг (файл + env), значения зафиксированы как стартовые и не подбираются;
  чувствительность печатается в Gate 4. Правило удержания — λ-форма, `λ = |Z_entry|/(2√2)`;
  вариант `r_alt` объявлен `REFERENCE_ONLY` и не выбран.

## Отклонённые альтернативы

- **Тик по данным всегда** — на «рваных» рядах даёт завышенный шаг и ломает пороги; допустим
  только как fallback с пометкой.
- **Календарь `TradingSchedules`** — требует сети/API в офлайн-исследовании и расходится между
  средами; расписание в конфиге проверяемо и одинаково.
- **Проценты вместо тиков** — сохраняет прежнюю ошибку: барьеры и издержки должны быть в одной
  единице, иначе `φ*` несопоставим.

## Риски

- Выведенный тик на неполной сетке цен может быть меньше реального → `φ*` завышен, вывод
  консервативнее. Помечается `UNVERIFIED`, попадает в отчёт.
- Расписание в конфиге не знает о праздниках и сокращённых днях → пропуски в такие дни
  попадут в Gate D как «пропуски». Это ожидаемо: Gate D и нужен, чтобы увидеть такие дни.
- Вечерняя сессия исключена: если торговая гипотеза потребует её, это отдельное решение.

## Способ проверки

- `pytest tests/test_research_units.py tests/test_research_ticks.py tests/test_research_sessions.py
  tests/test_research_integrity.py tests/test_research_variance.py tests/test_research_costs.py
  tests/test_research_labels.py tests/test_research_registry.py` — единицы, резолвинг тика,
  сессии, Gate D, дисперсии, издержки, метка, реестр на синтетике.
- `pytest tests` — без регрессий; `ruff check src tests`.
- Реальный прогон: `analyzer-research integrity --data-dir ../data/history-1min --out ../reports/research`
  — 20 инструментов (40 файлов: CSV + `.meta.json`); отчёт печатает доли пропусков, нулевых
  объёмов, источник шага цены и флаги.

## Файлы

Реализован блок 1: `units.py`, `ticks.py`, `tests/test_research_units.py`,
`tests/test_research_ticks.py`, поле `min_price_increment` в `HistoryCsvWriter` и чтение его в
`backtest/loader.py`. Остальные строки таблицы — планируемые.

| Файл | Что будет сделано |
|---|---|---|
| `analyzer/src/analyzer/research/units.py` | параметры с обязательными единицами |
| `analyzer/src/analyzer/research/ticks.py` | шаг цены из метаданных и вывод по сетке цен |
| `analyzer/src/analyzer/research/sessions.py` | расписание сессий, `τ_session`, flat-at-close |
| `analyzer/src/analyzer/research/integrity.py` | Gate D |
| `analyzer/src/analyzer/research/variance.py` | Parkinson/GK/RS, шум, `VR`, `σ_τ` |
| `analyzer/src/analyzer/research/costs.py` | издержки круга в тиках, стресс ×1.5 |
| `analyzer/src/analyzer/research/labels.py` | вариант метки и `h_max` |
| `analyzer/src/analyzer/research/registry.py` | `trial_registry`: структура и id опытов |
| `analyzer/src/analyzer/research/cli.py` | подкоманды `integrity`, `report` |
| `analyzer/pyproject.toml` | entry-point `analyzer-research` |
| `collector/.../history/HistoryCsvWriter.java` | `min_price_increment` в meta |
| `analyzer/src/analyzer/backtest/loader.py` | чтение `min_price_increment` из meta |
| `analyzer/tests/test_research_*.py` | проверки (units, ticks, sessions, integrity, variance, costs, labels, registry) |

## Статус актуальности

Сверено с артефактами change на 2026-11-22 (ревизия плана 3, после трёх проходов ревью).
Реализован блок 1 задач; правки кода сопровождаются правкой этого файла.
Открытая блокировка: перевыгрузка с `min_price_increment` (задача 1.6) требует `T_INVEST_TOKEN`
и сборки Collector — не выполнена.
