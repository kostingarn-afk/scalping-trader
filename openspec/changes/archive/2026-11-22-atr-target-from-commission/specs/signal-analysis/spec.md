# Spec Delta

## MODIFIED Requirements

### Requirement: Генерация сигнала BUY/SELL/HOLD

Analyzer SHALL формировать сигнал на основе индикаторов с confidence (0.0–1.0), предлагаемой
ценой входа, SL (от ATR) и TP, и временем устаревания (expires_at).

Цель выхода TP MUST строиться от порога комиссии: `k = ceil(min_tp_pct / ATR%)`, но не меньше 1,
где `ATR%` — ATR в процентах от цены, `min_tp_pct = commission_profit_ratio × 2 × rate_per_side × 100`.
TP MUST равняться `entry ± k×ATR` в сторону сделки. Фиксированное кратное `2×ATR` как цель
по умолчанию MUST NOT применяться.

#### Scenario: Сигнал на покупку

- **WHEN** RSI < 30 и цена касается нижней BB (mean reversion)
- **THEN** генерируется сигнал BUY с confidence, suggested_price (лимитная), suggested_sl
  (entry − 1.5×ATR), suggested_tp (entry + k×ATR), expires_at = now + 30с

#### Scenario: Цель покрывает порог комиссии

- **WHEN** ATR равен 0.06 % цены, а порог комиссии — 0.30 %
- **THEN** k = 5, TP отстоит от входа на 0.30 % и сигнал проходит комиссионный фильтр

#### Scenario: Волатильность выше порога

- **WHEN** ATR в процентах больше порога комиссии
- **THEN** k = 1, цель равна одному ATR

#### Scenario: Нет сигнала

- **WHEN** индикаторы не дают уверенного сигнала
- **THEN** генерируется сигнал HOLD, в Kafka не публикуется (только сохраняется в БД)

## ADDED Requirements

### Requirement: Ставка брокера в конфигурации Analyzer

Analyzer SHALL брать ставку за сторону из конфигурации (`commission_rate_per_side`) и из неё
выводить порог цели выхода. Для тарифа «Трейдер» значение MUST равняться 0.0005, что даёт
порог 0.30 % при `commission_profit_ratio = 3`.

#### Scenario: Порог выводится из ставки

- **WHEN** ставка равна 0.0005 и ratio равен 3
- **THEN** порог цели выхода равен 0.30 % суммы сделки

#### Scenario: Смена тарифа

- **WHEN** ставка в конфигурации изменена
- **THEN** порог цели и доля отсекаемых сигналов меняются без правки кода
