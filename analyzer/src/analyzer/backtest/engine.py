"""Пошаговый прогон стратегии на исторических барах.

Логика повторяет живой пайплайн: окно завершённых свечей → `compute_all` →
`mean_reversion` → комиссионный фильтр → размер позиции → исполнение. Отличия от боевого
контура — только в источнике данных (локальная выгрузка вместо Kafka) и в эмуляции выходов
(биржевые SL/TP в бэктесте моделируются по high/low бара).

`strategy_fn` — точка расширения для тестов: по умолчанию используется production-правило.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from ..indicators import Indicators, compute_all, compute_atr_pct_series
from ..strategy import Signal, SignalType, mean_reversion
from .commission import CommissionModel
from .models import (
    Bar,
    BacktestResult,
    Direction,
    ExitReason,
    Instrument,
    RunConfig,
    Trade,
)

StrategyFn = Callable[[Indicators, float, float, float], Signal]


@dataclass
class _OpenPosition:
    direction: Direction
    entry_price: float
    sl: float
    # None — цель не выставлена (`RunConfig.no_tp`): позиция закрывается по SL,
    # трейлингу или таймауту.
    tp: Optional[float]
    entry_ts: datetime
    entry_index: int
    lots: int
    notional: float
    atr: Optional[float] = None


def run_backtest(
    bars: Sequence[Bar],
    instrument: Instrument,
    config: Optional[RunConfig] = None,
    commission: Optional[CommissionModel] = None,
    interval_minutes: int = 1,
    strategy_fn: StrategyFn = mean_reversion,
    label: str = "",
    strategy_name: str = "",
) -> BacktestResult:
    """Прогнать стратегию по барам.

    В позиции одновременно находится не более одной сделки: новый сигнал рассматривается
    только на баре после выхода (соответствует `max_positions` риск-контура).

    `label` — метка варианта настроек для отчёта (например `tp3/sl2` в переборе TP/SL),
    `strategy_name` — правило входа (`mean_reversion`, `v2`) для шапки отчёта.
    """
    cfg = config or RunConfig()
    model = commission or CommissionModel()
    if interval_minutes < 1:
        raise ValueError("interval_minutes must be positive")

    bar_list = list(bars)
    if len(bar_list) < cfg.min_candles:
        return _result(
            instrument=instrument,
            interval_minutes=interval_minutes,
            model=model,
            cfg=cfg,
            bars=len(bar_list),
            signals=0,
            filtered=0,
            skipped=0,
            trades=[],
            label=label,
            strategy=strategy_name,
        )

    closes = np.fromiter((bar.close for bar in bar_list), dtype=float, count=len(bar_list))
    highs = np.fromiter((bar.high for bar in bar_list), dtype=float, count=len(bar_list))
    lows = np.fromiter((bar.low for bar in bar_list), dtype=float, count=len(bar_list))
    volumes = np.fromiter((bar.volume for bar in bar_list), dtype=np.int64, count=len(bar_list))

    trades: List[Trade] = []
    signals = filtered = skipped = 0
    position: Optional[_OpenPosition] = None
    last_exit_index: Optional[int] = None
    index = 0
    total = len(bar_list)

    while index < total:
        bar = bar_list[index]

        if position is not None:
            fill = _exit_fill(position, bar, index, cfg)
            if fill is not None:
                exit_price, reason = fill
                trades.append(
                    _close_trade(position, bar, index, exit_price, reason, instrument, model)
                )
                position = None
                last_exit_index = index
            else:
                # Подтяжка стопа по закрытию текущего бара: действует со следующего бара,
                # поэтому внутрибарного «знания будущего» нет.
                _update_trailing(position, bar, cfg)
            index += 1
            continue

        if last_exit_index is not None and index - last_exit_index <= cfg.cooldown_bars:
            index += 1
            continue

        if index + 1 < cfg.min_candles:
            index += 1
            continue

        # Без следующего бара вход исполнить негде: сигнал последнего бара не учитывается,
        # иначе «Сигналы» в отчёте содержали бы сделку, которой физически не могло быть.
        fill_index = index + 1 if cfg.fill_on_next_bar_open else index
        if fill_index >= total:
            break

        start = max(0, index + 1 - cfg.window)
        indicators = compute_all(
            closes[start : index + 1],
            highs[start : index + 1],
            lows[start : index + 1],
            volumes[start : index + 1],
        )
        signal = strategy_fn(indicators, bar.close, cfg.atr_mult_sl, cfg.atr_mult_tp)
        if not signal.is_actionable:
            index += 1
            continue
        signals += 1

        entry_price = bar_list[fill_index].open if cfg.fill_on_next_bar_open else bar.close

        if not passes_commission_filter(signal, entry_price, instrument, model, cfg):
            filtered += 1
            index += 1
            continue

        sized = size_position(entry_price, signal.suggested_sl, instrument, cfg)
        if sized is None:
            skipped += 1
            index += 1
            continue
        lots, notional = sized
        position = _OpenPosition(
            direction=Direction.LONG if signal.type is SignalType.BUY else Direction.SHORT,
            entry_price=entry_price,
            sl=float(signal.suggested_sl),
            tp=None if cfg.no_tp else float(signal.suggested_tp),
            entry_ts=bar_list[fill_index].ts,
            entry_index=fill_index,
            lots=lots,
            notional=notional,
            atr=indicators.atr,
        )
        # Бар входа обрабатывается следующей итерацией в режиме открытой позиции:
        # выход на этом же баре проверяется консервативно (SL-приоритет).
        index = fill_index

    return _result(
        instrument=instrument,
        interval_minutes=interval_minutes,
        model=model,
        cfg=cfg,
        bars=total,
        signals=signals,
        filtered=filtered,
        skipped=skipped,
        trades=trades,
        open_at_end=position is not None,
        label=label,
        strategy=strategy_name,
    )


def passes_commission_filter(
    signal: Signal,
    entry_price: float,
    instrument: Instrument,
    model: CommissionModel,
    config: RunConfig,
) -> bool:
    """Сделка проходит фильтр, если SL и TP лежат впереди фактической цены входа
    и ожидаемая прибыль до TP покрывает `commission_profit_ratio` комиссий круга.

    Семантика «прибыль / издержки» совпадает с production `CommissionFilter.should_keep`;
    отличия: комиссия считается по тарифу и по классу инструмента, а не по заглушке 0.3 %,
    и проверяется направление — при входе по `open` следующего бара цена может уйти за уровень
    сигнала (гэп). Раньше такой вход не отбрасывался: сделка открывалась «против» TP и
    закрывалась по нему же, попадая в отчёт как прибыльный выход с убытком.
    """
    if signal.suggested_tp is None or signal.suggested_sl is None:
        return False
    direction = 1.0 if signal.type is SignalType.BUY else -1.0
    if (signal.suggested_tp - entry_price) * direction <= 0:
        return False
    if (entry_price - signal.suggested_sl) * direction <= 0:
        return False
    units = instrument.lot_size
    profit = abs(signal.suggested_tp - entry_price) * units
    cost = model.round_trip(
        entry_price * units, signal.suggested_tp * units, instrument.is_future
    )
    if cost <= 0:
        return profit > 0
    return profit >= config.commission_profit_ratio * cost


def size_position(
    entry_price: float,
    stop_price: Optional[float],
    instrument: Instrument,
    config: RunConfig,
) -> Optional[Tuple[int, float]]:
    """Размер позиции: от риска на сделку, но не больше лимита номинала.

    @return (лоты, номинал); None — сделка не помещается в лимит или нет валидного стопа.
    """
    if stop_price is None:
        return None
    risk_per_lot = abs(entry_price - stop_price) * instrument.lot_size
    notional_per_lot = entry_price * instrument.lot_size
    if risk_per_lot <= 0 or notional_per_lot <= 0:
        return None

    risk_amount = config.deposit * config.risk_per_trade_pct / 100.0
    max_notional = config.deposit * config.max_position_notional_pct / 100.0

    lots_by_risk = int(risk_amount // risk_per_lot)
    lots_by_notional = int(max_notional // notional_per_lot)
    lots = min(lots_by_risk, lots_by_notional)
    if lots < 1:
        return None
    return lots, lots * notional_per_lot


def _update_trailing(position: _OpenPosition, bar: Bar, config: RunConfig) -> None:
    """Подтянуть стоп за ценой: только в сторону прибыли, по закрытию завершённого бара."""
    if config.trailing_atr <= 0 or position.atr is None or position.atr <= 0:
        return
    distance = config.trailing_atr * position.atr
    if position.direction is Direction.LONG:
        position.sl = max(position.sl, bar.close - distance)
    else:
        position.sl = min(position.sl, bar.close + distance)


def _exit_fill(
    position: _OpenPosition, bar: Bar, index: int, config: RunConfig
) -> Optional[Tuple[float, ExitReason]]:
    """Определить выход на баре. При касании SL и TP в одном баре выигрывает SL.

    Вход по закрытию бара (`fill_on_next_bar_open=False`) не проверяется на выход на том же
    баре: диапазон high/low этого бара сформирован до входа, проверка была бы lookahead.

    Гэп через стоп исполняется по цене открытия (худшая цена), а не по уровню стопа: иначе
    ночной гэп записывался бы как выход по SL выше факта.
    """
    bars_held = index - position.entry_index
    if not config.fill_on_next_bar_open and bars_held == 0:
        return None

    long_position = position.direction is Direction.LONG
    stop_price = min(position.sl, bar.open) if long_position else max(position.sl, bar.open)
    stop_hit = bar.low <= stop_price if long_position else bar.high >= stop_price

    if stop_hit:
        return stop_price, ExitReason.STOP_LOSS
    tp = position.tp
    if tp is not None and (bar.high >= tp if long_position else bar.low <= tp):
        return tp, ExitReason.TAKE_PROFIT
    if bars_held >= config.max_bars_in_trade:
        return bar.close, ExitReason.TIMEOUT
    return None


def _close_trade(
    position: _OpenPosition,
    bar: Bar,
    index: int,
    exit_price: float,
    reason: ExitReason,
    instrument: Instrument,
    model: CommissionModel,
) -> Trade:
    units = position.lots * instrument.lot_size
    direction = 1.0 if position.direction is Direction.LONG else -1.0
    gross = (exit_price - position.entry_price) * units * direction
    cost = model.round_trip(
        position.entry_price * units, exit_price * units, instrument.is_future
    )
    return Trade(
        direction=position.direction,
        entry_ts=position.entry_ts,
        exit_ts=bar.ts,
        entry_price=position.entry_price,
        exit_price=exit_price,
        sl=position.sl,
        tp=position.tp,
        lots=position.lots,
        lot_size=instrument.lot_size,
        exit_reason=reason,
        bars_held=index - position.entry_index,
        notional=position.notional,
        gross_pnl=gross,
        commission=cost,
        net_pnl=gross - cost,
    )


def _result(
    instrument: Instrument,
    interval_minutes: int,
    model: CommissionModel,
    cfg: RunConfig,
    bars: int,
    signals: int,
    filtered: int,
    skipped: int,
    trades: List[Trade],
    open_at_end: bool = False,
    label: str = "",
    strategy: str = "",
) -> BacktestResult:
    equity, drawdown = _equity_stats(trades, cfg.deposit)
    return BacktestResult(
        instrument=instrument,
        interval_minutes=interval_minutes,
        tariff=model.tariff,
        config=cfg,
        bars=bars,
        signals=signals,
        filtered_signals=filtered,
        skipped_by_notional=skipped,
        trades=tuple(trades),
        equity_final=equity,
        max_drawdown_pct=drawdown,
        open_at_end=open_at_end,
        label=label,
        strategy=strategy,
    )


def atr_pct_quantile(bars: Sequence[Bar], quantile: float) -> float:
    """Порог ATR% по квантилю серии — отсекает бары, где движение меньше издержек.

    Квантиль считается по той же истории, на которой идёт прогон (in-sample): для честной
    оценки порог нужно брать с обучающего окна walk-forward. Это ограничение отчёта, а не
    ошибка расчёта — в `to_do.md` §3 оно указано как условие приёмки.
    """
    if not 0 < quantile < 100:
        raise ValueError("quantile must be in (0, 100)")
    if not bars:
        raise ValueError("bars must not be empty")
    highs = np.fromiter((bar.high for bar in bars), dtype=float, count=len(bars))
    lows = np.fromiter((bar.low for bar in bars), dtype=float, count=len(bars))
    closes = np.fromiter((bar.close for bar in bars), dtype=float, count=len(bars))
    pct = compute_atr_pct_series(highs, lows, closes)
    if pct.size == 0:
        raise ValueError("not enough bars to compute ATR percentile")
    return float(np.quantile(pct, quantile / 100.0))


def _equity_stats(trades: Sequence[Trade], deposit: float) -> Tuple[float, float]:
    """Итоговый капитал и максимальная просадка от пика equity (в процентах)."""
    equity = deposit
    peak = deposit
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
    return equity, max_drawdown
