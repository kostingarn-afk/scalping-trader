# Tasks

## 1. Ядро бэктеста (Analyzer)

- [x] 1.1 `backtest/models.py`: `Bar`, `Instrument`, `Direction`, `ExitReason`, `Trade`,
  `RunConfig`, `BacktestResult`
- [x] 1.2 `backtest/commission.py`: тарифы `investor`/`trader`/`premium`, бумаги и фьючерсы,
  комиссия за сторону и за круг, валидация имени тарифа
- [x] 1.3 `backtest/loader.py`: CSV-контракт Collector, sidecar `.meta.json`, агрегация баров
  1 мин → 5/15 мин
- [x] 1.4 `backtest/engine.py`: окно 200 баров → `compute_all` → `mean_reversion` → фильтр по
  тарифу → риск-сайзинг с лимитом номинала → выходы TP/SL/таймаут (SL-приоритет), equity и
  max drawdown
- [x] 1.5 `backtest/report.py`: сводка (win rate, expectancy ₽ и %, просадка, распределение
  выходов), markdown/CSV/JSON
- [x] 1.6 `backtest/cli.py` + console script `analyzer-backtest`, вывод отчёта в файл

## 2. Тесты

- [x] 2.1 `test_backtest_commission.py`: ставки по тарифам и классам, круговая комиссия,
  неизвестный тариф, паритет с `CommissionFilter` при `investor`
- [x] 2.2 `test_backtest_engine.py`: TP, SL, таймаут, SL-приоритет, шорт, лимит номинала и
  `skipped_by_notional`, блокировка фильтром, плоский рынок, детерминизм
- [x] 2.3 `test_backtest_loader.py`: разбор CSV (варианты заголовка, ошибки строк, порядок
  времени), чтение meta, отсутствие meta, агрегация 1 мин → 5 мин
- [x] 2.4 `test_backtest_report.py` + `test_backtest_cli.py`: метрики на известном наборе
  сделок, markdown/JSON/CSV, прогон CLI на каталоге выгрузок

## 3. Коллектор: выгрузка истории

- [x] 3.1 `collector/.../history/HistoryCandleSource` + `TinkoffHistoryCandleSource`:
  `MarketDataService.GetCandles`, время закрытия = `time + interval`, только завершённые свечи
- [x] 3.2 Пагинация `HistoryRange` по лимиту запроса (`HistoryInterval.maxPageSpan`) + профиль
  `history-dump` и `HistoryDumpRunner` с параметрами `collector.history.*`
- [x] 3.3 `HistoryCsvWriter`: CSV + `.meta.json` по контракту `loader.py`, дедупликация на стыках
  страниц (`HistoryDumpService`)
- [x] 3.4 Тесты: пагинация (без пропусков/пересечений), таймфреймы, writer (заголовок, meta,
  перезапись, формат цен), сервис (инструменты, дедупликация, пустая история)

## 4. Документация

- [x] 4.1 `README.md`: раздел «Бэктест», контракт выгрузки, порядок запуска
- [x] 4.2 `AGENTS.md`: правило «сырые данные T-Invest — только через Collector»

## 5. Проверка

- [ ] 5.1 `poetry run pytest` (по запросу пользователя), `ruff check .` — на хосте нет
  poetry/ruff (инструменты в WSL), прогон за пользователем
- [x] 5.2 `openspec validate backtest-runner` — valid (CLI 2.0.0: флаг `--change` заменён
  позиционным аргументом)
- [x] 5.3 `mvn -B -pl collector test` — BUILD SUCCESS, 33 теста (10 старых + 23 новых),
  0 падений; компиляция 57 основных и 8 тестовых классов
- [ ] 5.4 Живая выгрузка: `history-dump` по SBER/GAZP/SiZ6, затем прогон `analyzer-backtest`
  на 1/5/15 мин, тарифы `investor`/`trader`, отчёт в `reports/`
- [ ] 5.5 Сверка с `to_be.md` §3: при тарифе `investor` число сделок ≈ 0, порядок величин
  PnL после комиссии совпадает
