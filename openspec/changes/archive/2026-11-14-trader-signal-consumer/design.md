# Design

## Контекст

Требования к обработке сигналов уже описаны в `openspec/specs/trade-execution/spec.md` и
`risk-management/spec.md`; архитектурные основания — в
`openspec/changes/archive/2026-11-11-scalping-trading-system-mvp/design.md`. Изменение носит
реализационный характер: требования не переписываются, кроме списка инструментов.

## D1. Consumer — собственный поток, а не Spring Kafka

**Решение:** `SignalConsumer` — `@Component`, стартующий daemon-поток на `ApplicationReadyEvent`,
поверх существующего бина `KafkaConsumer<String, byte[]>` (`TraderKafkaConfig`).

**Почему:** в проекте нет зависимости `spring-kafka`, есть готовый `KafkaConsumer`; добавлять
зависимости ради одного consumer'а не нужно (правило проекта — зависимости только по согласованию).

**Следствия:** ручное управление коммитами (`enable.auto.commit=false` уже настроен); обработка
ошибки сообщения не должна останавливать цикл; `wakeup()` при shutdown.

## D2. Порядок обработки сигнала

1. Разбор Protobuf → `signal_id` (невалидный UUID → сообщение пропускается с логом).
2. Дедупликация через `processed_signals` (at-least-once + дедуп = effectively-once).
3. Устаревание `expires_at` → `result=rejected`, `reject_reason=expired`.
4. `HOLD` → отбрасывается.
5. Торговый статус инструмента ≠ `NORMAL_TRADING` → `rejected`, `trading_status not normal`.
6. Risk-check (`RiskManager.check`) → отказ фиксируется в `processed_signals`.
7. Вход: `LIMIT` по `suggested_price` с идемпотентным `order_id` (не `MARKET` — D4 из архива).
8. Исполнено (`success=true`): запись `trade_results`, `positions`, `RiskManager.onPositionOpened`,
   выставление SL/TP, публикация `OPENED`.
9. Не исполнено: `REJECTED` + `trade_results` со `status=rejected`, `reject_reason`.

**Почему такой порядок:** он же в спеке `trade-execution`; самые дешёвые проверки — первыми,
запись в БД и биржевые вызовы — после риск-контроля.

## D3. Идемпотентность и связь с БД

`order_id` = детерминированный UUID от `signal_id` (например, `UUID.nameUUIDFromBytes(("order:" + signalId))`),
чтобы повторная обработка после сбоя не создавала дубль ордера. `trade_id` — новый UUID на сделку.

⚠️ `trade_results.signal_id` имеет FK на `analysis_results(signal_id)`; запись делается только если
сигнал пришёл от Analyzer (в проде так и есть). Если записи нет — БД вернёт нарушение FK; это
логируется как ошибка обработки, сообщение не коммитится (сигнал останется в топике для повтора).

## D4. SL/TP в sandbox

`StopOrdersService` в песочнице выбрасывает `SandboxModeViolationException`. Решение: вызов SL/TP
выполняется best-effort, ошибка логируется, событие `OPENED` публикуется без `sl_order_id`/`tp_order_id`.
**Риск:** в песочнице позиция остаётся без биржевых защит; компенсируется `AutoCloseService` по таймауту.
Проверка SL/TP возможна только в prod-контуре (или после появления песочницы с поддержкой стопов).

## D5. Размер позиции и параметры риска

- Лотность берётся из таблицы `instruments.lot` (владелец записи — Collector), дефолт `1`.
- Депозит — `trader.deposit` из конфигурации (200 000 ₽).
- Корреляция с открытыми позициями передаётся как `0.0`: в Trader нет истории цен, необходимой для
  расчёта коэффициента. **Риск:** корреляционный лимит фактически не работает; выносится отдельной
  задачей (нужен источник цен или расчёт на стороне Analyzer).
- `risk_config` из БД подхватывается `RiskConfigPoller` по cron `trader.poll-risk-config-cron`
  (было: `updateConfig` не вызывался вовсе).

## D6. Инструменты: `BR`/`TCSG` → `SBER`/`GAZP`/`SiZ6`

Проверено через SDK 1.8 (sandbox):

| Тикер | Класс | FIGI | Лот | Статус |
|---|---|---|---|---|
| SBER | TQBR | BBG004730N88 | 1 | NORMAL_TRADING |
| GAZP | TQBR | BBG004730RP0 | 10 | NORMAL_TRADING |
| SiZ6 | SPBFUT | FUTSI1226000 | 1 | NORMAL_TRADING |

`BR`, `TCSG`, а также `Si`/`Si-12.26` через `FutureByTickerSync` не резолвятся; фьючерсы T-Invest
лежат в классе `SPBFUT`, а не `FORTS`. **Риск:** `SiZ6` экспирируется 2026-12-18 — перед экспирацией
тикер нужно роллировать вручную (автоматического роллирования нет).

## D7. Отклонённые альтернативы

| Альтернатива | Почему отклонена |
|---|---|
| `@KafkaListener` / spring-kafka | Новая зависимость; существующий `KafkaConsumer`-бин уже настроен |
| Обработка сигналов в главном потоке (`ApplicationRunner`) | Блокирует старт приложения и actuator |
| Расчёт корреляции в Trader по свечам из Kafka | Требует второго consumer'а и истории; вне объёма изменения |
| Замена `BR` на «любой фьючерс BR-<месяц>» | На sandbox не резолвится; ликвидность и экспирация не проверены |
