# Spec Delta

## Purpose

Исполнение торговых сигналов через T-Invest API, управление позициями с биржевыми защитными ордерами, recovery состояния при рестарте.

## ADDED Requirements

### Requirement: Дедупликация сигналов по signal_id

Trader SHALL дедуплицировать входящие сигналы по `signal_id` через таблицу `processed_signals`. Повторный сигнал с известным `signal_id` игнорируется.

#### Scenario: Повторный сигнал
- **WHEN** Trader получает сигнал с signal_id, уже присутствующим в `processed_signals`
- **THEN** сигнал игнорируется, в лог записывается "duplicate signal"

#### Scenario: Новый сигнал
- **WHEN** Trader получает сигнал с неизвестным signal_id
- **THEN** signal_id записывается в `processed_signals`, сигнал обрабатывается

### Requirement: Исполнение ордера LIMIT или BESTPRICE

Trader SHALL исполнять ордера через `PostOrder` с типом LIMIT (основной) или BESTPRICE (опционально). Использование MARKET на вход ЗАПРЕЩЕНО.

#### Scenario: Исполнение LIMIT-ордера
- **WHEN** Trader получает BUY-сигнал с suggested_price
- **THEN** вызывается `PostOrder` с order_type=LIMIT, price=suggested_price, уникальным order_id (идемпотентность)

#### Scenario: Сигнал устарел
- **WHEN** Trader получает сигнал с expires_at < now
- **THEN** сигнал отбрасывается, в `processed_signals` записывается result=rejected, reject_reason="expired"

### Requirement: Биржевые защитные ордера SL/TP

Trader SHALL выставлять Stop-Loss и Take-Profit через `StopOrdersService.PostStopOrder` на стороне биржи сразу после исполнения ордера на вход.

#### Scenario: Позиция открыта — выставление стопов
- **WHEN** ордер на вход исполнен (EXECUTION_REPORT_STATUS_FILL)
- **THEN** Trader выставляет SL через PostStopOrder (цена из сигнала suggested_sl) и TP (цена из suggested_tp), IDs сохраняются в trade_results.sl_order_id/tp_order_id

#### Scenario: Срабатывание SL на бирже
- **WHEN** биржа исполняет стоп-ордер SL
- **THEN** Trader получает уведомление через TradesStream, позиция закрывается, в Kafka `trade-results` публикуется событие SL_HIT

### Requirement: Мониторинг позиций через PositionsStream

Trader SHALL подписываться на `OperationsStreamService.PortfolioStream` и `PositionsStream` для real-time обновления позиций и PnL.

#### Scenario: Обновление позиции
- **WHEN** цена позиции изменилась
- **THEN** unrealized_pnl в таблице `positions` обновляется, метрика Prometheus обновляется

### Requirement: Auto-close по таймауту удержания

Trader SHALL закрывать позиции, удерживаемые дольше max_holding_time (10 минут, настраивается в risk_config), отправкой ордера на закрытие.

#### Scenario: Позиция удерживается 10 минут
- **WHEN** позиция открыта 10 минут назад и не закрыта SL/TP
- **THEN** Trader отправляет ордер на закрытие, в Kafka публикуется событие TIMEOUT_CLOSED

### Requirement: Recovery при старте

Trader SHALL при старте синхронизировать состояние: открытые позиции через `GetPositions`, in-flight ордера через `GetOrders`, биржевые стоп-ордера через `GetStopOrders`.

#### Scenario: Рестарт с открытой позицией
- **WHEN** Trader стартует и в БД есть открытая позиция, но in-memory состояние пусто
- **THEN** Trader вызывает GetPositions, сверяет с БД, восстанавливает in-memory состояние, подписывается на TradesStream для отслеживания

### Requirement: Публикация событий сделок в Kafka

Trader SHALL публиковать все события сделок в Kafka-топик `trade-results` (compact, key=trade_id) в формате Protobuf (сообщение `TradeResult`).

#### Scenario: Ордер отклонён биржей
- **WHEN** PostOrder возвращает EXECUTION_REPORT_STATUS_REJECTED
- **THEN** в Kafka публикуется событие REJECTED с reject_reason, в trade_results status=rejected

### Requirement: Проверка торгового статуса перед ордером

Trader SHALL проверять торговый статус инструмента перед выставлением ордера. Ордер выставляется только при `SECURITY_TRADING_STATUS_NORMAL_TRADING`.

#### Scenario: Инструмент недоступен
- **WHEN** Trader получает сигнал, но инструмент имеет статус BREAK_IN_TRADING
- **THEN** ордер не выставляется, сигнал отклоняется с причиной "trading_status not normal"
