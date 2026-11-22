"""Пошаговый прогон стратегии на исторических барах.

Логика повторяет живой пайплайн: окно завершённых свечей → `compute_all` →
`mean_reversion` → комиссионный фильтр → размер позиции → исполнение. Отличия от боевого
контура — только в источнике данных (локальная выгрузка вместо Kafka) и в эмуляции выходов
(биржевые SL/TP в бэктесте моделируются по high/low бара).

`strategy_fn` — точка расширения для тестов: по умолчанию используется production-правило.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from ..indicators import Indicators, compute_all, compute_atr_pct_series
from ..strategy import Signal, SignalType, mean_reversion, with_target
from .commission import CommissionModel
from .execution import ExecutionModel
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

# Москва — UTC+3 круглый год (перевод часов отменён в 2014), поэтому фиксированное смещение
# корректно и не требует базы часовых поясов.
MSK = timezone(timedelta(hours=3))


def minutes_of_day_msk(ts: datetime) -> int:
    """Минуты от полуночи по Москве: вход разрешается с `RunConfig.entry_after_msk_minutes`."""
    local = ts.astimezone(MSK)
    return local.hour * 60 + local.minute


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

    # Дневной лимит убытка (зеркало `PnlCircuitBreaker`): реализованный PnL текущей сессии
    # и даты, на которых торговля уже остановлена.
    stop_limit = cfg.daily_loss_stop_pct / 100.0 * cfg.deposit
    day_pnl = 0.0
    current_day: Optional[date] = None
    halted_days: set[date] = set()

    while index < total:
        bar = bar_list[index]
        day = bar.ts.date()
        if day != current_day:
            current_day = day
            day_pnl = 0.0

        if position is not None:
            # Дневной лимит: проверяется и плавающий убыток, иначе в позиции с одной сделкой
            # правило срабатывало бы только после закрытия — то есть никогда в момент,
            # когда убыток ещё можно остановить.
            if stop_limit > 0:
                floating = _floating_pnl(position, bar.close, instrument)
                if day_pnl + floating <= -stop_limit:
                    trades.append(
                        _close_trade(
                            position, bar, index, bar.close, ExitReason.DAILY_STOP,
                            instrument, model, cfg.execution,
                        )
                    )
                    day_pnl += trades[-1].net_pnl
                    halted_days.add(day)
                    position = None
                    last_exit_index = index
                    index += 1
                    continue

            fill = _exit_fill(position, bar, index, cfg)
            if fill is not None:
                exit_price, reason = fill
                trades.append(
                    _close_trade(
                        position, bar, index, exit_price, reason, instrument, model,
                        cfg.execution,
                    )
                )
                day_pnl += trades[-1].net_pnl
                if stop_limit > 0 and day_pnl <= -stop_limit:
                    halted_days.add(day)
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

        if day in halted_days:
            # Дневной лимит пробит: новых входов до следующей торговой сессии.
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

        # Окно входа: до `entry_after_msk_minutes` новые позиции не открываются. Проверяется
        # время исполнения (бар входа), а не время сигнала — ордер уходит именно тогда.
        if (
            cfg.entry_after_msk_minutes
            and minutes_of_day_msk(bar_list[fill_index].ts) < cfg.entry_after_msk_minutes
        ):
            index += 1
            continue

        start = max(0, index + 1 - cfg.window)
        # Окно модели шире окна индикаторов, когда у неё есть warmup; окно индикаторов
        # не меняется — иначе ATR/ADX (рекурсивные сглаживания) сдвинули бы правила.
        lookback = max(cfg.window, _warmup(strategy_fn))
        _fit_model(strategy_fn, bar_list[max(0, index + 1 - lookback) : index + 1])
        indicators = compute_all(
            closes[start : index + 1],
            highs[start : index + 1],
            lows[start : index + 1],
            volumes[start : index + 1],
        )
        signal = strategy_fn(indicators, bar.close, cfg.atr_mult_sl, cfg.atr_mult_tp)
        # Цель от порога комиссии ставится здесь, а не в модели: production применяет ту же
        # функцию (`main.py`), поэтому прогон воспроизводит боевую геометрию выхода.
        if cfg.min_tp_pct > 0 and not cfg.no_tp:
            signal = with_target(signal, bar.close, indicators.atr, cfg.min_tp_pct)
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
        lots, _ = sized
        # Сайзинг считается по базовой цене, затем цена корректируется на издержки исполнения:
        # итерация «размер ↔ цена» даёт доли процента, а цикл усложняет движок.
        units = float(lots * instrument.lot_size)
        fill_bar = bar_list[fill_index]
        side = 1.0 if signal.type is SignalType.BUY else -1.0
        entry_price = cfg.execution.fill_price(
            entry_price, side, units, fill_bar.volume, instrument.lot_size
        )
        position = _OpenPosition(
            direction=Direction.LONG if signal.type is SignalType.BUY else Direction.SHORT,
            entry_price=entry_price,
            sl=float(signal.suggested_sl),
            tp=None if cfg.no_tp else float(signal.suggested_tp),
            entry_ts=bar_list[fill_index].ts,
            entry_index=fill_index,
            lots=lots,
            notional=entry_price * units,
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
        entry_price * units,
        signal.suggested_tp * units,
        instrument.is_future,
        contracts=1,
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


def _warmup(strategy_fn: StrategyFn) -> int:
    """Сколько баров модель просит на оценку параметров (0 — правило без оценки)."""
    try:
        return max(0, int(getattr(strategy_fn, "warmup", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _fit_model(strategy_fn: StrategyFn, bars: Sequence[Bar]) -> None:
    """Передать модели окно истории до предсказания.

    Статистическая модель без этого остаётся необученной; правило реализует `fit` как no-op,
    поэтому вызов безопасен для всех моделей реестра.
    """
    fit = getattr(strategy_fn, "fit", None)
    if callable(fit):
        fit(bars)


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


def _floating_pnl(position: _OpenPosition, price: float, instrument: Instrument) -> float:
    """Плавающий результат открытой позиции по текущей цене, без комиссии.

    Комиссия входит в `net_pnl` уже закрытых сделок (`day_pnl`), поэтому здесь она не
    дублируется: для решения о стопе важнее не занизить убыток, а не завысить его
    приблизительной оценкой.
    """
    direction = 1.0 if position.direction is Direction.LONG else -1.0
    return (price - position.entry_price) * direction * position.lots * instrument.lot_size


def _close_trade(
    position: _OpenPosition,
    bar: Bar,
    index: int,
    exit_price: float,
    reason: ExitReason,
    instrument: Instrument,
    model: CommissionModel,
    execution: ExecutionModel = ExecutionModel(),
) -> Trade:
    units = position.lots * instrument.lot_size
    direction = 1.0 if position.direction is Direction.LONG else -1.0
    # Закрытие длинной — продажа, короткой — покупка: поправка исполнения в сторону рынка.
    filled_exit = execution.fill_price(
        exit_price, -direction, units, bar.volume, instrument.lot_size
    )
    gross = (filled_exit - position.entry_price) * units * direction
    cost = model.round_trip(
        position.entry_price * units,
        filled_exit * units,
        instrument.is_future,
        contracts=position.lots,
    )
    return Trade(
        direction=position.direction,
        entry_ts=position.entry_ts,
        exit_ts=bar.ts,
        entry_price=position.entry_price,
        exit_price=filled_exit,
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
    оценки порог нужно брать с обучающего окна walk-forward. Это ограничение отчёта,
    а не ошибка расчёта.
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
