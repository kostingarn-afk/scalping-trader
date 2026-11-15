"""Тесты движка бэктеста: входы, выходы, сайзинг, фильтр."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from analyzer.backtest import engine
from analyzer.backtest.commission import CommissionModel
from analyzer.backtest.models import Bar, Direction, ExitReason, Instrument, RunConfig
from analyzer.backtest.report import format_markdown
from analyzer.strategy import Signal, SignalType

START = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)
INSTRUMENT = Instrument("SBER", "TQBR", lot_size=1)


def _bar(
    minute: int,
    close: float = 100.0,
    high: Optional[float] = None,
    low: Optional[float] = None,
    open_price: Optional[float] = None,
) -> Bar:
    return Bar(
        ts=START + timedelta(minutes=minute),
        open=close if open_price is None else open_price,
        high=close + 0.2 if high is None else high,
        low=close - 0.2 if low is None else low,
        close=close,
        volume=100,
    )


def _flat_bars(count: int) -> List[Bar]:
    return [_bar(minute) for minute in range(count)]


def _always_buy(sl_factor: float = 0.99, tp_factor: float = 1.02):
    def strategy(indicators, price, atr_mult_sl, atr_mult_tp):  # noqa: ANN001
        return Signal(
            SignalType.BUY,
            confidence=0.9,
            suggested_price=price,
            suggested_sl=round(price * sl_factor, 6),
            suggested_tp=round(price * tp_factor, 6),
        )

    return strategy


def _config(**overrides) -> RunConfig:
    params = {
        "deposit": 200_000.0,
        "risk_per_trade_pct": 1.0,
        "max_position_notional_pct": 100.0,
        "max_bars_in_trade": 10,
        "fill_on_next_bar_open": False,
    }
    params.update(overrides)
    return RunConfig(**params)


def _run(bars: List[Bar], config: RunConfig, strategy=_always_buy()):  # noqa: ANN001
    # Тариф фиксирован явно: тесты проверяют механику выходов/сайзинга, а не ставку брокера.
    # С тарифом «Инвестор» по умолчанию мелкие движения в фикстурах отсекает комиссионный фильтр.
    return engine.run_backtest(
        bars=bars,
        instrument=INSTRUMENT,
        config=config,
        commission=CommissionModel("trader"),
        strategy_fn=strategy,
    )


def test_take_profit_exit():
    bars = _flat_bars(30)
    bars.append(_bar(30, close=102.5, high=103.0, low=101.5))

    result = _run(bars, _config())

    assert result.trades, "ожидалась хотя бы одна сделка"
    trade = result.trades[0]
    assert trade.direction is Direction.LONG
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(102.0)
    assert trade.bars_held == 6
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - trade.commission)
    assert trade.commission > 0


def test_stop_loss_exit():
    bars = _flat_bars(30)
    # Бар открывается по уровню входа и проваливается до SL: выход по уровню, не по open.
    bars.append(_bar(30, close=98.6, high=98.8, low=98.5, open_price=100.0))

    result = _run(bars, _config())

    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.exit_price == pytest.approx(99.0)
    assert trade.net_pnl < 0


def test_stop_loss_has_priority_when_bar_touches_both():
    bars = _flat_bars(30)
    bars.append(_bar(30, close=100.0, high=103.0, low=98.0))

    result = _run(bars, _config())

    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.exit_price == pytest.approx(99.0)


def test_close_fill_does_not_check_exit_on_entry_bar():
    """Вход по закрытию бара: high/low этого бара сформированы до входа — выход не проверяется."""
    bars = _flat_bars(30)
    bars[24] = _bar(24, close=100.0, high=105.0, low=95.0)
    bars.append(_bar(30, close=102.5, high=103.0, low=101.5))

    result = _run(bars, _config())

    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.bars_held == 6


def test_gap_through_stop_fills_at_bar_open():
    bars = _flat_bars(30)
    bars.append(_bar(30, close=95.0, high=96.0, low=94.0, open_price=95.0))

    result = _run(bars, _config())

    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.exit_price == pytest.approx(95.0)


def test_position_open_at_end_is_reported():
    result = _run(_flat_bars(25), _config())

    assert result.trades == ()
    assert result.signals == 1
    assert result.open_at_end is True


def test_timeout_exit_after_max_bars():
    bars = _flat_bars(40)

    result = _run(bars, _config(max_bars_in_trade=2))

    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.TIMEOUT
    assert trade.exit_price == pytest.approx(100.0)
    assert trade.bars_held == 2


def test_entry_fill_on_next_bar_open():
    bars = _flat_bars(25)
    bars.append(_bar(25, close=100.5, high=100.8, low=100.3, open_price=101.0))
    bars.append(_bar(26, close=102.5, high=103.0, low=101.5))

    result = _run(bars, _config(fill_on_next_bar_open=True))

    assert result.trades[0].entry_price == pytest.approx(101.0)


def test_notional_cap_limits_position_size():
    bars = _flat_bars(30)
    bars.append(_bar(30, close=102.5, high=103.0, low=101.5))

    capped = _run(bars, _config(max_position_notional_pct=10.0))

    trade = capped.trades[0]
    assert trade.notional == pytest.approx(20_000.0)
    assert trade.lots == 200


def test_signal_skipped_when_one_lot_exceeds_notional_cap():
    bars = _flat_bars(30)

    result = _run(bars, _config(max_position_notional_pct=0.04))

    assert result.trades == ()
    assert result.signals > 0
    assert result.skipped_by_notional == result.signals


def test_commission_filter_blocks_low_profit_signals():
    bars = _flat_bars(30)
    strategy = _always_buy(sl_factor=0.9995, tp_factor=1.0005)

    result = _run(bars, _config(), strategy=strategy)

    assert result.trades == ()
    assert result.signals > 0
    assert result.filtered_signals == result.signals


def test_flat_market_produces_no_signals_with_production_strategy():
    result = engine.run_backtest(
        bars=_flat_bars(60),
        instrument=INSTRUMENT,
        config=_config(),
    )

    assert result.signals == 0
    assert result.trades == ()


def test_short_history_produces_empty_result():
    result = _run(_flat_bars(10), _config())

    assert result.bars == 10
    assert result.signals == 0
    assert result.trades == ()
    assert result.equity_final == pytest.approx(200_000.0)


def test_short_signal_is_direction_short():
    def always_sell(indicators, price, atr_mult_sl, atr_mult_tp):  # noqa: ANN001
        return Signal(SignalType.SELL, 0.9, price, price * 1.01, price * 0.98)

    bars = _flat_bars(30)
    bars.append(_bar(30, close=97.5, high=98.5, low=97.0))

    result = _run(bars, _config(), strategy=always_sell)

    trade = result.trades[0]
    assert trade.direction is Direction.SHORT
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.net_pnl > 0


def test_entry_beyond_take_profit_is_rejected():
    """Гэп на входе: цена открытия уже выше TP — сделка не открывается.

    Иначе она закрывалась бы по TP, будучи убыточной, и попадала в отчёт как прибыльный выход.
    """
    bars = _flat_bars(25)
    bars.append(_bar(25, close=100.0, high=103.0, low=100.0, open_price=103.0))

    result = _run(bars, _config(fill_on_next_bar_open=True))

    assert result.trades == ()
    assert result.signals == 1
    assert result.filtered_signals == 1


def test_entry_beyond_stop_is_rejected():
    """Гэп на входе: цена открытия уже ниже SL — сделка не открывается."""
    bars = _flat_bars(25)
    bars.append(_bar(25, close=100.0, high=100.0, low=97.0, open_price=98.0))

    result = _run(bars, _config(fill_on_next_bar_open=True))

    assert result.trades == ()
    assert result.filtered_signals == 1


def test_entry_beyond_take_profit_is_rejected_for_short():
    """Симметрично для SELL: цена открытия ниже TP шорта — сделка не открывается."""

    def always_sell(indicators, price, atr_mult_sl, atr_mult_tp):  # noqa: ANN001
        return Signal(SignalType.SELL, 0.9, price, price * 1.01, price * 0.98)

    bars = _flat_bars(25)
    bars.append(_bar(25, close=100.0, high=100.0, low=97.0, open_price=97.5))

    result = _run(bars, _config(fill_on_next_bar_open=True), strategy=always_sell)

    assert result.trades == ()
    assert result.filtered_signals == 1


def test_label_is_kept_in_result():
    """Метка варианта нужна отчёту: при переборе TP/SL строки иначе неразличимы."""
    result = engine.run_backtest(
        bars=_flat_bars(30),
        instrument=INSTRUMENT,
        config=_config(),
        label="tp3/sl2",
    )

    assert result.label == "tp3/sl2"
    assert result.run_name == "SBER@TQBR 1min investor tp3/sl2"


def test_cooldown_skips_entries_after_exit():
    """Cooldown — ограничитель оборота: после выхода N баров без новых входов."""
    bars = _flat_bars(30)
    bars += [_bar(minute, close=100.0, high=103.0, low=99.5) for minute in range(30, 40)]

    without = _run(bars, _config())
    with_cooldown = _run(bars, _config(cooldown_bars=5))

    # Без паузы вход на каждом втором баре: 5 сделок и ещё одна открытая позиция.
    assert len(without.trades) == 5
    assert without.open_at_end is True
    # С паузой 5 баров — только повторный вход на баре 36.
    assert len(with_cooldown.trades) == 2
    assert with_cooldown.open_at_end is False


def test_cooldown_zero_keeps_behaviour():
    bars = _flat_bars(30)
    bars.append(_bar(30, close=102.5, high=103.0, low=101.5))

    default = _run(bars, _config())
    explicit = _run(bars, _config(cooldown_bars=0))

    assert len(default.trades) == len(explicit.trades) == 1


def test_trailing_stop_locks_profit():
    """Трейлинг 1×ATR: после роста до 101 стоп подтянут к 100.6 и срабатывает в плюс."""
    bars = _flat_bars(25)
    bars.append(_bar(25, close=101.0))
    bars.append(_bar(26, close=100.9, low=100.6))

    result = _run(bars, _config(trailing_atr=1.0))

    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.exit_price == pytest.approx(100.6)
    assert trade.net_pnl > 0


def test_trailing_stop_does_not_loosen():
    """Стоп только подтягивается: откат не отодвигает его обратно вниз."""
    bars = _flat_bars(25)
    bars.append(_bar(25, close=101.0))
    bars.append(_bar(26, close=100.5, low=100.6, open_price=100.9))

    result = _run(bars, _config(trailing_atr=1.0))

    assert result.trades[0].exit_price == pytest.approx(100.6)


def test_without_trailing_position_stays_open():
    bars = _flat_bars(25)
    bars.append(_bar(25, close=101.0))
    bars.append(_bar(26, close=100.9, low=100.6))

    result = _run(bars, _config())

    assert result.trades == ()
    assert result.open_at_end is True


def test_negative_trailing_is_rejected():
    with pytest.raises(ValueError):
        _config(trailing_atr=-1.0)


def test_tp_reported_when_enabled():
    bars = _flat_bars(30)
    bars.append(_bar(30, close=102.5, high=103.0, low=101.5))

    trade = _run(bars, _config()).trades[0]

    assert trade.tp == pytest.approx(102.0)


def test_no_tp_ignores_target_and_holds_to_timeout():
    """Без цели цена проходит уровень TP, но позиция закрывается только таймаутом."""
    bars = _flat_bars(25)
    bars.append(_bar(25, close=101.0))
    bars.extend(_bar(minute, close=101.0) for minute in range(26, 41))

    result = _run(bars, _config(no_tp=True))

    trade = result.trades[0]
    assert trade.tp is None, "цель не должна проставляться при no_tp"
    assert trade.exit_reason is ExitReason.TIMEOUT
    assert trade.bars_held == 10
    assert trade.exit_price == pytest.approx(101.0)


def test_no_tp_still_respects_stop_loss():
    bars = _flat_bars(25)
    bars.append(_bar(25, close=101.0))
    # Открытие выше стопа (100.5 > 99.0), но low его пробивает: выход по уровню стопа.
    bars.append(_bar(26, close=99.5, low=98.5, open_price=100.5))

    result = _run(bars, _config(no_tp=True))

    trade = result.trades[0]
    assert trade.tp is None
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(99.0)


def test_strategy_name_is_kept_in_result():
    result = engine.run_backtest(
        bars=_flat_bars(30),
        instrument=INSTRUMENT,
        config=_config(),
        strategy_name="v2",
    )

    assert result.strategy == "v2"


def test_atr_pct_quantile_returns_series_threshold():
    bars = _flat_bars(60)

    threshold = engine.atr_pct_quantile(bars, 80)

    # Фикстура: high/low на ±0.2 от close 100 → ATR = 0.4 → 0.4 % цены.
    assert threshold == pytest.approx(0.4, rel=1e-6)


def test_atr_pct_quantile_validates_arguments():
    with pytest.raises(ValueError):
        engine.atr_pct_quantile(_flat_bars(60), 0)
    with pytest.raises(ValueError):
        engine.atr_pct_quantile([], 80)


def test_run_is_deterministic():
    bars = _flat_bars(30)
    bars.append(_bar(30, close=102.5, high=103.0, low=101.5))

    first = format_markdown([_run(bars, _config())])
    second = format_markdown([_run(bars, _config())])

    assert first == second
