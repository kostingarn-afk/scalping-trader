# Brief — backtest-runner

Контекст изменения: замер на реальной истории (`to_be.md` §3) показал, что система не торгует
(0 сделок из 534/430 сигналов), а экономика до издержек близка к нулю (−40 % и −34 % после
комиссии 0.04 %/сторона). Прежде чем вносить деньги и менять фильтр, нужен ответ на вопрос
«есть ли edge на месяцах истории» — штатного инструмента для этого в репозитории нет,
истории цен в системе нет (`raw-market-data` retention 3 минуты).

## Шаги

1. **Ядро бэктеста в Analyzer.** Пакет `analyzer/src/analyzer/backtest/`: модели (`models.py`),
   модель издержек по тарифу (`commission.py`), чтение CSV-выгрузки (`loader.py`), пошаговый
   прогон (`engine.py`), агрегированный отчёт (`report.py`), CLI (`cli.py`, console script
   `analyzer-backtest`). Прогон обязан считать индикаторы и стратегию **тем же кодом**, что
   живой пайплайн (`analyzer.indicators.compute_all`, `analyzer.strategy.mean_reversion`),
   иначе результаты несопоставимы.
2. **Модель издержек.** Тарифы `investor` / `trader` / `premium`, ставки раздельно для бумаг
   и фьючерсов, признак фьючерса — по классу инструмента (`SPBFUT`/`FORTS`). Production-фильтр
   `CommissionFilter` не трогаем: его правка — отдельный шаг (Вариант 5), включается только
   после подтверждённого edge.
3. **Риск-сайзинг и лимит номинала.** Объём от риска (`risk_per_trade_pct` депозита / расстояние
   до стопа) с ограничением `max_position_notional_pct`. Без лимита риск-сайзинг на 1-мин баре
   даёт ~19 000 лотов ≈ 4.8 млн ₽ на депозит 200 000 ₽.
4. **Выходы.** TP/SL от ATR, таймаут `max_bars_in_trade`, при касании SL и TP внутри одного бара
   приоритет у SL (консервативная оценка).
5. **Загрузка истории — в Collector.** Вся сырая работа с T-Invest API сосредоточена в `collector`
   (Java, `java-sdk-core` уже подключён). Профиль `history-dump` выгружает исторические свечи через
   `MarketDataService.GetCandles` в CSV + `.meta.json` по контракту `analyzer/.../backtest/loader.py`;
   в профиле не запускаются стрим, Kafka и запись справочника в БД. Analyzer к T-Invest не обращается.
6. **Проверка.** Синтетические тесты движка; прогон CLI на локальном CSV без сети; сверка
   порядка величин с `to_be.md` §3 (при тарифе `investor` и ATR 1-мин бара сделок быть не должно).

## Затронутые файлы

| Файл | Тип |
|---|---|
| `analyzer/src/analyzer/backtest/{__init__,models,commission,loader,engine,report,cli}.py` | новые |
| `analyzer/tests/test_backtest_{commission,loader,engine,report,cli}.py` | новые |
| `analyzer/pyproject.toml` | правка (console script `analyzer-backtest`) |
| `.gitignore` | правка (кэш истории `data/history/`) |
| `README.md`, `AGENTS.md` | правка (раздел «Бэктест», правило «сырые данные T-Invest — только Collector») |
| `openspec/changes/backtest-runner/*` | новые (proposal, design, spec, tasks, brief) |
| `collector/.../history/{HistoryInterval,HistoryCandle,HistoryCandleSource,TinkoffHistoryCandleSource,HistoryRange,HistoryDumpProperties,HistoryDumpConfig,HistoryCsvWriter,DumpReport,HistoryDumpService,HistoryDumpRunner}.java` | новые |
| `collector/src/main/resources/application-history-dump.yml` | новый (профиль выгрузки) |
| `collector/.../stream/CollectorStartup.java` | правка (`@Profile("!history-dump")`) |
| `collector/src/test/java/.../history/{HistoryInterval,HistoryRange,HistoryCsvWriter,HistoryDumpService}Test.java` | новые |
| `to_be.md` | внешний входной документ (в git не отслеживается — вне этого изменения) |

Миграции Flyway, `pom.xml`, `proto/` не меняются. Новых зависимостей нет.

## Принятые решения

- **Переиспользование production-расчёта** вместо копии: любое расхождение бэктеста с живым
  пайплайном должно быть дефектом бэктеста, а не второй реализацией индикаторов.
- **Модель издержек в бэктесте, а не в `CommissionFilter`.** Правка фильтра меняет поведение
  боевого контура; для исследования достаточно модели внутри пакета. Согласованность с
  production: решения совпадают везде, кроме полосы ≤1 % у порога (production считает комиссию
  выхода по цене входа, бэктест — по фактической цене TP). Расхождение осознанное и
  зафиксировано тестом `test_filter_deviation_from_production_is_confined_to_threshold_band`.
- **CSV как контракт между Collector и Analyzer.** Модули не зависят друг от друга на уровне
  кода (AGENTS.md §3), CSV + sidecar `.meta.json` это сохраняет; формат описан в `loader.py`.
  `ts` — время закрытия свечи; агрегация группирует бары по времени открытия.
- **Вход по открытию следующего бара** (`fill_on_next_bar_open=True`): сигнал считается по
  закрытой свече, вход по цене, доступной после неё. Замер в `to_be.md` §3 считал вход по
  закрытию сигнального бара — расхождение по входу фиксируется в отчёте. В режиме `--fill-on-close`
  бар входа на выход не проверяется (иначе lookahead).
- **Лимит номинала в бэктесте, а не в `RiskManager`.** Правка `RiskManager` + миграция `V3` —
  шаг 4 плана (`to_be.md` §5), в этом изменении только модель для сравнения конфигураций.
  Для фьючерсов номинал считается по полной стоимости контракта (консервативнее ГО).

## Риски

| Риск | Влияние |
|---|---|
| Модель не учитывает проскальзывание и очередь | Результат оптимистичен; нужен запас по expectancy |
| talib RSI сглаживает по Уайлдеру, замер §3 — упрощённый RSI | Число сигналов и win rate будут отличаться от §3 в пределах нескольких процентов |
| Внутрибарная неоднозначность решена в пользу SL | Оценка консервативна, TP-сделок может быть меньше, чем в реальности |
| Overfitting при переборе параметров | Прогон по сетке не заменяет out-of-sample проверку |
| Collector-дампер ограничен лимитами `GetCandles` | 1-мин история грузится пагинацией (≈1 день на запрос) |

## Проверка

- `poetry run pytest` (по запросу пользователя), `ruff check .`
- прогон `analyzer-backtest` на локальном CSV: отчёт содержит сигналы, сделки, win rate,
  expectancy, просадку и распределение выходов
- `openspec validate --change backtest-runner`

## Отклонения от плана

| Отклонение | Причина |
|---|---|
| `engine.run_backtest` принимает `strategy_fn` | Точка расширения для тестов: синтетические бары не дают гарантированного сигнала production-стратегии, а проверять выходы TP/SL/таймаут нужно детерминированно. По умолчанию используется `mean_reversion` |
| `--fill-on-close` вместо отдельной конфигурации замера | Воспроизведение §3 (`to_be.md`) достигается одним флагом `RunConfig.fill_on_next_bar_open=False` |
| `openspec validate --change` → `openspec validate <name>` | В CLI 2.0.0 опции `--change` нет, имя передаётся позиционным аргументом |
| 4-й тестовый файл `test_backtest_cli.py` | Сверх плана: проверяет сквозной путь CLI (каталог выгрузок → отчёт), без него `cli.py` остаётся непокрытым |
| `@Profile("!history-dump")` на `CollectorStartup` | Без этого профиль выгрузки поднимал бы gRPC-стрим и писал справочник инструментов в БД |
| `datasource.hikari.initialization-fail-timeout: -1` в профиле | Выгрузке БД не нужна, а пул Hikari по умолчанию падает при старте без PostgreSQL |
| `System.exit` в `HistoryDumpRunner` вместо `SpringApplication.exit` | `SpringApplication.exit` + `System.exit` закрывали бы контекст дважды; shutdown hook Spring закрывает контекст и `InvestApi` сам |

## Волна 2: выгрузка истории в Collector

Порядок работ:

1. `HistoryInterval` — таймфреймы (`1min`, `5min`, `15min`, `1h`) и лимит одного запроса
   `GetCandles` (1 мин — сутки, 5 мин — неделя, 15 мин и час — месяц).
2. `HistoryRange` — нарезка периода на страницы без пропусков и пересечений.
3. `TinkoffHistoryCandleSource` — `MarketDataService.getCandlesSync`; время закрытия =
   `time + interval` (SDK отдаёт время **открытия**), незавершённые свечи отбрасываются.
4. `HistoryDumpService` — обход страниц, дедупликация по времени закрытия, `DumpReport`
   на инструмент; пустая история файл не создаёт.
5. `HistoryCsvWriter` — CSV + `.meta.json` по контракту Analyzer'а; цены без экспоненты,
   повторная выгрузка перезаписывает файл.
6. `HistoryDumpProperties` + `HistoryDumpConfig` + `HistoryDumpRunner` — профиль `history-dump`,
   параметры `collector.history.*`, коды возврата `0/2/3/1`, завершение процесса после выгрузки.
7. `CollectorStartup` — `@Profile("!history-dump")`: стрим и запись справочника в БД
   в режиме выгрузки не стартуют.
8. `application-history-dump.yml` — `web-application-type: none` и
   `datasource.hikari.initialization-fail-timeout: -1` (выгрузке БД не нужна; пул не должен
   падать при отсутствии PostgreSQL).

Принятые решения:

- Время закрытия считается так же, как в стрим-адаптере (`TinkoffMarketStreamService`):
  `getTime()` — начало свечи, поэтому `end = time + interval`. Это и есть `ts` в CSV.
- Дедупликация по времени закрытия, а не отказ от граничных баров: на стыках страниц
  возможны повторы, терять данные нельзя.
- Выход через `System.exit` после выгрузки: в профиле нет веб-сервера и стрима, а shutdown hook
  Spring закрывает контекст и `InvestApi`.

Риски волны 2:

| Риск | Влияние |
|---|---|
| Лимиты `GetCandles` могут отличаться в sandbox и prod | Пагинация опирается на `HistoryInterval.maxPageSpan`; при ошибке лимита страницу нужно уменьшить |
| Профиль не покрыт интеграционным тестом (нет Spring-контекста в тестах) | Проверены чистые компоненты; запуск профиля проверяется живой выгрузкой |
| `bars`/`fetched_at` в meta сверх контракта | Analyzer читает только обязательные поля, расширение безопасно |

## Исправления по ревью (проход 1, 2026-11-14)

| Находка | Что исправлено |
|---|---|
| C1: в режиме `--fill-on-close` выход проверялся на баре входа по high/low, сформированным до входа (lookahead) | `_exit_fill` не проверяет бар входа при `fill_on_next_bar_open=False`; тест `test_close_fill_does_not_check_exit_on_entry_bar` |
| M1: заявленный «точный паритет» с production-фильтром неверен (комиссия выхода по фактической цене TP, разный порог) | Требование спеки, D2 и тесты переписаны: совпадение везде, кроме полосы ≤1 % у порога; полоса зафиксирована отдельным тестом; `sl=None` задокументирован как отбрасываемый неисполнимый сигнал |
| M2: `--native-interval` имел `default=1` и всегда перекрывал `interval_minutes` из meta | `default=None`, значение передаётся только при явном указании; тест `test_cli_native_interval_does_not_override_meta` |
| M3: спека добавляла требование к Collector, не реализованное в изменении | Требование переписано как контракт потребителя (Analyzer), производящая сторона вынесена в «вне объёма» с задачами 3.1–3.4 |
| M4: агрегация группировала бары по времени закрытия → сетка 5/15 мин сдвинута на бар | Ключ группы считается по времени открытия (`ts − native`); тест проверяет `ts` агрегированной свечи |
| N1–N3: нет валидации OHLC/цен/объёма и сверки шага с meta; ложный docstring про разрывы | Добавлены `_validate_ohlc`, `_validate_step`; docstring агрегации описывает неполные группы; 4 новых теста |
| N4: сигнал на последнем баре и открытая позиция не отражались | `BacktestResult.open_at_end` + колонка «Открыта» в отчёте; тест `test_position_open_at_end_is_reported` |
| N5: гэп через стоп бронировался по уровню SL | Выход по `open` при гэпе за уровень; тест `test_gap_through_stop_fills_at_bar_open` |
| N6: определение «Сигналы» было неочевидным | Сноска в отчёте: сигналы считаются только при отсутствии позиции |
| N7: сделки с нулевым net записывались в убыток | `losses` считается по `net_pnl < 0`; тест `test_zero_net_trade_is_not_counted_as_loss` |
| N8: CSV собирался конкатенацией строк | `csv.writer` с корректным экранированием |
| N9, N17: не моделируются абонплата/ступени тарифа; для фьючерса номинал = полная стоимость | Зафиксировано в D9 и D6, README |
| N10: тест «скипа интервала» не проверял ветку | Тест переписан на 5-мин выгрузку с `--intervals 1` |
| N11, N12: `--ticker` применялся ко всем CSV и игнорировался при `--data-dir`; ошибка записи отчёта не обрабатывалась | Валидация `--ticker` и обработка `OSError` с кодом 2 |
| N13: `commission_profit_ratio=0` и `min_candles=0` проходили валидацию | Добавлены проверки в `RunConfig.__post_init__` |
| N14: фикстура отчёта содержала несогласованный PnL | Фикстура считает `gross` из цен и лота; ассерты пересчитаны |
| N15: спека обещала предупреждение в отчёте | Предупреждение — в stderr, спека приведена к факту |
| N16: в таблице файлов brief не было `test_backtest_cli.py` и `to_be.md` | Таблица дополнена |

Осталось Info: max DD не учитывает внутрисделочную просадку (D9), `to_be.md` не в git (вне
изменения), прогон тестов — за пользователем.

## Результат проверки

- `python -m py_compile` по всем новым файлам — без ошибок (синтаксис), строк >100 символов нет.
- `openspec validate backtest-runner` — valid (после правок спеки — перепроверить).
- Java: `mvn -B -pl collector test` — **BUILD SUCCESS**, 33 теста, 0 падений (23 новых в
  `history/`), компиляция 57 основных и 8 тестовых классов с `release 21`.
- Сигнатуры SDK сверены с артефактом `java-sdk-core-1.8`:
  `getCandlesSync(String, Instant, Instant, CandleInterval)`, `HistoricCandle.getTime()/getOpen()/
  getHigh()/getLow()/getClose()/getVolume()/getIsComplete()`.
- `poetry run pytest`, `ruff check` — **не запускались**: на хосте Windows нет poetry/ruff
  (инструменты в WSL), а правило репозитория запрещает самостоятельный запуск сборки и тестов.
  Прогон — за пользователем. До прогона тесты считаются неподтверждёнными: тестовые данные
  зависимы от семантики talib (RSI/ATR) и от арифметики порогов комиссии.
- Ревью волны 2 не проводилось по решению пользователя.


