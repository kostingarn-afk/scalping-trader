# Tasks

## 1. Метод отбора и состав списка

- [x] 1.1 Замер ATR(1h) и оборота по 20 бумагам, отбор по фильтру (ATR ≥ 0.4 %, оборот ≥ 100 млн ₽/день)
- [x] 1.2 Обоснование и расшифровка тикеров — `docs/Выбор-инструментов.md`
- [x] 1.3 `collector/src/main/resources/application.yml`: состав из 5 акций класса TQBR
- [x] 1.4 Правило ротации: 15 % за окно сессии + обязательный кандидат по структурному фильтру (`to_do.md` §3)

## 2. Список перестаёт быть константой

- [x] 2.1 Удалено мёртвое поле `AnalyzerConfig.instruments` (нигде не читалось)
- [x] 2.2 Примеры команд обезличены: `$INSTRUMENTS`, `$UNIVERSE` в `README.md`, `to_do.md`, `AGENTS.md`
- [x] 2.3 Правило зафиксировано в `AGENTS.md` §7 («не фиксируй состав в примерах»)
- [x] 2.4 `analyzer/config.py`: ставка фильтра вынесена в `commission_rate_per_side`

## 3. Спек-и и требования

- [x] 3.1 `openspec/specs/market-data-collection/spec.md`: «4 × N подписок», состав — параметр конфигурации
- [x] 3.2 `openspec/specs/signal-analysis/spec.md`: N инструментов, примеры обезличены, ставка из конфига
- [x] 3.3 `openspec/specs/observability/spec.md`: stale_data без привязки к конкретному тикеру
- [x] 3.4 `docs/Техническое задание.md`: MVP — 5 инструментов класса TQBR, фьючерсы вне MVP до исправления модели издержек

## 4. Параметризация вместо констант

- [x] 4.1 `risk_config`: `commission_rate_per_side`, `max_position_notional_pct` (миграция `V4`)
- [x] 4.2 `RiskManager`: урезание объёма по лимиту номинала, комиссионный фильтр по ставке из конфига
- [x] 4.3 `risk_config`: `entry_after_msk_minutes` (миграция `V3`), `TradingWindow` в `SignalProcessor`
- [x] 4.4 Бэктест: биржевой сбор за контракт `exchange_fee_per_contract_rub` + флаг `--exchange-fee-per-contract`

## 5. Проверки

- [x] 5.1 Python: 219 тестов, `ruff check` чистый
- [x] 5.2 Java: `mvn -pl trader -am test` — 55 тестов, BUILD SUCCESS
- [x] 5.3 `grep` по `openspec/` и ТЗ: перечней инструментов, привязанных к MVP, не осталось
- [ ] 5.4 `openspec validate --change instrument-universe` — требует CLI OpenSpec (в окружении не установлен)
