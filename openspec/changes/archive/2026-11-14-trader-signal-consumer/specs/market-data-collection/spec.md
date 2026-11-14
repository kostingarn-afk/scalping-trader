# Spec Delta

## MODIFIED Requirements

### Requirement: Подписка на gRPC-стримы рыночных данных

Collector SHALL подписываться на `MarketDataStreamService` (bi-directional stream) для каждого инструмента из конфигурации с четырьмя типами подписок: Candles (1 мин, waiting_close=false), OrderBook (depth=10), Trades, Info.

#### Scenario: Старт подписки на список инструментов
- **WHEN** Collector стартует с конфигурацией [SBER, GAZP, SiZ6] (фьючерс — `SiZ6@SPBFUT`)
- **THEN** открывается одно стрим-соединение и выставляются 12 подписок (3 инструмента × 4 типа)

#### Scenario: Превышение лимита подписок
- **WHEN** число запрошенных подписок превышает 300 (лимит соединения)
- **THEN** Collector открывает дополнительное стрим-соединение и распределяет подписки

#### Scenario: Резолв идентификаторов инструментов
- **WHEN** в конфигурации указан тикер без класса (`SBER`, `GAZP`) или с классом (`SiZ6@SPBFUT`)
- **THEN** Collector разрешает тикер в FIGI через `InstrumentsService`: акции — класс `TQBR`, фьючерсы — класс `SPBFUT`; регистр тикера сохраняется (фьючерсные тикеры регистрозависимы)
