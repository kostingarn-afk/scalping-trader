# Proposal

## Why

Шаг 1.5 плана (`to_do.md` §4.1) вводит оценку кластеризации сделок. В `docs/mat_analyze.md` §2
зафиксировано: при branching ratio `n = α/β → 1` рынок входит в «лавину», и mean reversion
становится математически убыточным — нужен переключатель режима потока. Сейчас Analyzer
считает VPIN по объёмам, но не знает, кластеризованы ли сделки во времени: тот же объём может
прийти равномерно или всплесками, и это разные режимы.

## What Changes

- **Analyzer: оценка Hawkes** — `analyzer/hawkes.py`: симуляция экспоненциального Hawkes
  (thinning Огаты) и MLE branching ratio по временам сделок; стационарность `n < 1`.
- **Analyzer: индикатор** — `Indicators.hawkes`: branching ratio по последним сделкам,
  пересчёт с прореживанием; недостаточно сделок — пусто.
- **Analyzer: состояние** — `MicrostructureState` хранит времена сделок, обновляет оценку не
  чаще `hawkes_refresh` сделок и отдаёт её в снимке.
- **Модель `hawkes`** — mean reversion разрешён только при `n` ниже порога; кластеризованный
  поток — HOLD с причиной.
- **Параметры** — `hawkes_window`, `hawkes_refresh` в `AnalyzerConfig`; порог — `--model-param`.

Не входит: Jump-diffusion как отдельная модель (оценка скачков), переключение на momentum,
production-включение, Avellaneda–Stoikov (3.6).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `signal-analysis`: требование об оценке кластеризации сделок и её сохранении в индикаторах.
- `signal-modeling`: требование о модели с фильтром кластеризованного потока.

## Impact

- Код: новый `analyzer/hawkes.py`, `signal_models/hawkes.py`; правки `microstructure.py`,
  `indicators.py`, `handler.py`, `main.py`, `config.py`, `registry.py`, `__init__.py`.
- Тесты: `analyzer/tests/test_hawkes.py`; дополнение `test_microstructure.py`.
- Зависимости: `scipy` уже используется (MLE); новых пакетов нет.
- БД/Kafka/`proto/`: без миграций; `analysis_results.indicators` получает ключ `hawkes`.
- Ограничение: в CSV-истории нет сделок, поэтому модель в бэктесте всегда HOLD; проверка —
  синтетика и production после запуска Collector.
