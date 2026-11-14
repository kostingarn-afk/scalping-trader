# Brief — trader-signal-consumer

Контекст изменения: сигналы Analyzer копились в Kafka `trading-signals`, потому что в Trader нет
consumer'а и оркестратора; параллельно выяснилось, что инструменты `BR`/`TCSG` из MVP-конфигурации
не резолвятся через T-Invest API.

## Шаги

1. **Инструменты.** Резолв фьючерсов в классе `SPBFUT` (проверено: `SiZ6@SPBFUT` → `FUTSI1226000`),
   сохранение регистра тикера в `TinkoffInstrumentLoader`, `SPBFUT` → `instrument_type=FUTURE`,
   замена набора инструментов на `SBER`, `GAZP`, `SiZ6@SPBFUT` в конфигурациях Collector и Analyzer.
2. **Дефекты Trader, найденные при проектировании.**
   `SignalDeduplicator.tryAcquire` всегда возвращал `true` (конфликт `ON CONFLICT DO NOTHING`
   не бросает исключение) — дедупликация не работала; `RiskManager.updateConfig` нигде не вызывался
   — параметры `risk_config` из БД не применялись.
3. **Consumer.** `SignalConsumer` — daemon-поток на `ApplicationReadyEvent` поверх существующего
   `KafkaConsumer`-бина, ручной коммит после успешной обработки.
4. **Оркестратор.** `TradeOrchestrator`: dedupe → expiry → HOLD → торговый статус → risk-check →
   вход LIMIT → запись БД → SL/TP → публикация `TradeResult`.
5. **Проверка.** Юнит-тесты оркестратора и дедупликатора, живой прогон синтетического сигнала,
   `openspec validate`.

## Затронутые файлы

| Файл | Тип |
|---|---|
| `collector/.../instrument/TinkoffInstrumentLoader.java` | правка |
| `collector/.../instrument/JdbcInstrumentRepository.java` | правка |
| `collector/src/main/resources/application.yml` | правка |
| `analyzer/src/analyzer/config.py` | правка |
| `trader/.../signal/SignalConsumer.java` | новый |
| `trader/.../signal/TradeOrchestrator.java` | новый |
| `trader/.../signal/SignalDeduplicator.java` | правка |
| `trader/.../risk/RiskConfigPoller.java` | новый |
| `trader/.../repository/TradeRepository.java` | правка (лотность) |
| `trader/.../recovery/ExchangeStateProvider.java`, `TinkoffExchangeStateProvider.java` | правка |
| `trader/src/test/.../TradeOrchestratorTest.java`, `SignalDeduplicatorTest.java` | новые |

Миграции и `pom.xml` не меняются.

## Принятые решения

- Собственный поток consumer'а вместо spring-kafka (нет новой зависимости).
- Идемпотентный `order_id` — детерминированный UUID от `signal_id`.
- SL/TP — best-effort: в sandbox SDK запрещает стоп-ордера (`SandboxModeViolationException`).
- Корреляция передаётся как `0.0`: в Trader нет источника цен для расчёта (зафиксировано как риск).
- Лотность — из `instruments.lot` (владелец записи Collector), дефолт `1`.

## Риски

| Риск | Влияние |
|---|---|
| SL/TP в песочнице не выставляются | Позиция без биржевой защиты до `AutoCloseService`; проверка только в prod |
| Корреляционный лимит не работает (вход `0.0`) | Возможны коррелированные позиции |
| `SiZ6` экспирируется 2026-12-18 | Перед экспирацией нужен ручной роллинг тикера |
| `trade_results.signal_id` FK на `analysis_results` | Сигнал без записи в `analysis_results` не будет сохранён (сообщение не коммитится) |

## Проверка

- `mvn test`, `poetry run pytest`, `ruff check`
- живой прогон: сигнал в `trading-signals` → событие в `trade-results` + строка в `trade_results`
- `openspec validate --change trader-signal-consumer`

## Отклонения от плана

| Отклонение | Причина |
|---|---|
| Имя Kafka-бина `signalConsumer` → `tradingSignalsConsumer` | `@Component`-класс `SignalConsumer` получал одноимённое имя бина, и Spring падал на старте с конфликтом определений. Поймано живым запуском, юнит-тестами не видно |
| HOLD не публикует `REJECTED` | HOLD — не попытка сделки; `processed_signals` помечается `rejected`/`hold signal`, событие в Kafka не идёт (соответствует design D2) |
| `RetryableSignalException` вместо «молча проглотить» | Инфраструктурный сбой БД (`DataAccessException`) пробрасывается — consumer не коммитит offset и повторяет запись с паузой 1 с (at-least-once). Нарушение FK и прочие ошибки данных логируются и коммитятся, иначе «отравляющее» сообщение крутилось бы вечно |
| `TradeRepository.updateRejectReason` | Причина отказа биржи не писалась в `trade_results.reject_reason`: `insertTrade` не принимает этот параметр, менять сигнатуру запрещено |
| SL/TP выставляются на `executedLots` (фолбэк — расчётный объём) | Фактически исполненный объём может отличаться от расчётного |
| `E2ERecoveryTest` переведён с лямбды на анонимный класс | Интерфейс `ExchangeStateProvider` получил новый метод `tradingStatus` |
| Добавлен `TradeRepositoryTest` | Сверх плана: покрывает `lotSize` (задача 4.3) |

## Результат проверки

- `mvn test`: Collector 10, Trader 43, 0 падений (1 skipped — env-гейт `T_INVEST_TOKEN`).
- `poetry run pytest`: 26 passed; `ruff check`: чисто.
- Живой прогон: сигнал `11111111-…` → `processed_signals.result=rejected`, причина `trading_status not normal` (суббота, `BREAK_IN_TRADING`), событие `REJECTED` в `trade-results`; повторная публикация того же `signal_id` не создала второго события (дедупликация).
- `openspec validate trader-signal-consumer` — valid.
- Trader стартует: `risk_config` из БД загружен (`RiskConfig[...]`), consumer подписан на `trading-signals`, health `UP`.
- Успешное исполнение ордера живьём не проверено: торги закрыты, sandbox-счёт не пополнялся (решение владельца).

## Документация

- `README.md` дополнен: truststore с корнем «Russian Trusted Root CA» (без него Java-модули не
  соединяются с sandbox), требования к `protoc` 25.x, формат `collector.instruments` (`TICKER@CLASS`).
- `docs/Техническое задание.md`: перечень MVP-инструментов приведён к фактическому набору
  (`SBER`, `GAZP`, `Si` — фьючерс SPBFUT) вместо нерезолвящихся `BR`/`TCSG`.
