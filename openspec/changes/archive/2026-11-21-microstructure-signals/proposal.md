# Proposal

## Why

Collector уже подписан на стакан (`TinkoffMarketStreamService:88`, `subscribeOrderbook`) и
публикует в `raw-market-data` сообщения `ORDER_BOOK` и `TRADE`, но Analyzer их отбрасывает:
`parse_candle` (`handler.py:55`) принимает только свечи, а `compute_all` вызывается без стакана
(`main.py:55`), поэтому `order_imbalance` в production всегда `None`. Шаг 1.3 плана
(`to_do.md` §4.1) требует довести стакан до модели и добавить метрики потока — OBI и VPIN.

Без этого модели микроструктуры (включая будущую Avellaneda–Stoikov из 3.6) нечем питать: срез
данных уже публикуется, потери происходят целиком на стороне Analyzer.

## What Changes

- **Analyzer: разбор стакана и сделок** — `parse_message` разбирает `CANDLE`, `ORDER_BOOK`,
  `TRADE`; `parse_candle` остаётся совместимой обёрткой.
- **Analyzer: состояние микроструктуры** — последний стакан (глубина до 10) и поток сделок по
  каждому figi; направление сделки берётся из сообщения, а при `UNSPECIFIED` — по тик-правилу.
- **Analyzer: VPIN** — объёмные бакеты фиксированного размера, `VPIN = mean(|buy − sell|)/bucket`
  по последним N завершённым бакетам; недостаточно данных — `None`.
- **Analyzer: индикаторы** — `Indicators.order_imbalance` начинает заполняться в production,
  добавляется `Indicators.vpin`; оба попадают в `analysis_results.indicators`.
- **Analyzer: модель `microstructure`** — вход по знаку OBI при нетоксичном потоке (VPIN ниже
  потолка); HOLD без стакана, без сделок или при высокой токсичности. SL/TP — ATR-множители.
- **Конфигурация** — `vpin_bucket_volume`, `vpin_buckets`, `order_book_depth` в
  `AnalyzerConfig`; параметры модели — через `--model-param` (шаг 1.2).

Ограничение, фиксируемое явно: история в CSV содержит только свечи, поэтому модель
`microstructure` в бэктесте всегда HOLD; её проверка — unit-тесты на индикаторах и production-путь
после появления стакана в Kafka.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `signal-analysis`: добавляются требования о потреблении стакана и сделок, расчёте OBI в
  production-пути и VPIN.
- `signal-modeling`: добавляется требование о модели микроструктуры и её условиях входа.

## Impact

- Код: новый `analyzer/src/analyzer/microstructure.py` (стакан, бакеты, тик-правило),
  `signal_models/microstructure.py` (модель), правки `handler.py`, `main.py`, `indicators.py`,
  `config.py`, `signal_models/registry.py`, `__init__.py`.
- Тесты: `analyzer/tests/test_microstructure.py`, дополнение `test_indicators.py`.
- Зависимости: без новых; numpy уже используется.
- БД/Kafka/`proto/`: без миграций; `analysis_results.indicators` получает два новых ключа.
- Collector и Trader не меняются.
