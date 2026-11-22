"""Тесты модели издержек исполнения (шаг 1.6 `to_do.md` §4.1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.backtest.commission import CommissionModel
from analyzer.backtest.engine import run_backtest
from analyzer.backtest.execution import ExecutionModel
from analyzer.backtest.models import Bar, Instrument, RunConfig
from analyzer.backtest.report import format_markdown
from analyzer.strategy import Signal, SignalType

START = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)


def _bar(
    minute: int,
    close: float = 100.0,
    open_price: float = 100.0,
    high: float = 100.2,
    low: float = 99.8,
    volume: int = 1000,
) -> Bar:
    return Bar(
        ts=START + timedelta(minutes=minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _always_buy():
    def strategy(ind, price, atr_mult_sl, atr_mult_tp):  # noqa: ANN001
        return Signal(
            SignalType.BUY,
            confidence=0.9,
            suggested_price=price,
            suggested_sl=round(price * 0.99, 6),
            suggested_tp=round(price * 1.02, 6),
        )

    return strategy


def _series() -> list[Bar]:
    bars = [_bar(minute) for minute in range(25)]
    bars.append(_bar(25, close=101.0, open_price=100.0, high=103.0, low=99.9))
    bars.extend(_bar(minute) for minute in range(26, 31))
    return bars


def _run(execution: ExecutionModel):
    return run_backtest(
        bars=_series(),
        instrument=Instrument(ticker="SBER"),
        config=RunConfig(
            window=25, min_candles=25, commission_profit_ratio=0.001, execution=execution
        ),
        commission=CommissionModel(tariff="trader"),
        strategy_fn=_always_buy(),
    )


# --- формула -------------------------------------------------------------------


def test_zero_model_is_identity() -> None:
    model = ExecutionModel()

    assert not model.enabled
    assert model.fill_price(100.0, +1, 1000, 1000, 1) == 100.0
    assert model.fill_price(100.0, -1, 1000, 1000, 1) == 100.0


def test_spread_moves_price_against_side() -> None:
    model = ExecutionModel(spread_bps=10.0)

    assert model.fill_price(100.0, +1, 10, 1000, 1) == pytest.approx(100.1)
    assert model.fill_price(100.0, -1, 10, 1000, 1) == pytest.approx(99.9)


def test_impact_depends_on_participation() -> None:
    model = ExecutionModel(impact_bps_at_full=20.0)

    assert model.cost_bps(units=250, bar_volume_lots=1000, lot_size=1) == pytest.approx(5.0)
    # Участие выше 100 % ограничено: импакт не растёт бесконечно.
    assert model.cost_bps(units=5000, bar_volume_lots=1000, lot_size=1) == pytest.approx(20.0)


def test_permanent_impact_enters_with_half_weight() -> None:
    model = ExecutionModel(permanent_bps_at_full=20.0)

    assert model.cost_bps(units=1000, bar_volume_lots=1000, lot_size=1) == pytest.approx(10.0)


def test_zero_volume_is_treated_as_full_participation() -> None:
    model = ExecutionModel(impact_bps_at_full=20.0)

    assert model.cost_bps(units=10, bar_volume_lots=0, lot_size=1) == pytest.approx(20.0)
    assert model.cost_bps(units=0, bar_volume_lots=0, lot_size=1) == pytest.approx(0.0)


def test_negative_parameters_rejected() -> None:
    with pytest.raises(ValueError, match="spread_bps"):
        ExecutionModel(spread_bps=-1)
    with pytest.raises(ValueError, match="impact_bps_at_full"):
        ExecutionModel(impact_bps_at_full=-1)


# --- прогон --------------------------------------------------------------------


def test_spread_worsens_entry_and_exit() -> None:
    baseline = _run(ExecutionModel())
    slipped = _run(ExecutionModel(spread_bps=10.0))

    assert baseline.trades and slipped.trades
    base, slip = baseline.trades[0], slipped.trades[0]

    assert base.entry_price == pytest.approx(100.0)
    assert base.exit_price == pytest.approx(102.0)
    assert slip.entry_price == pytest.approx(100.1)
    assert slip.exit_price == pytest.approx(102.0 * 0.999)
    assert slip.net_pnl < base.net_pnl


def test_zero_execution_keeps_summary_identical() -> None:
    first = _run(ExecutionModel())
    second = _run(ExecutionModel())

    assert [trade.net_pnl for trade in first.trades] == [trade.net_pnl for trade in second.trades]


def test_report_mentions_execution_costs_when_enabled() -> None:
    with_costs = format_markdown([_run(ExecutionModel(spread_bps=10.0, impact_bps_at_full=5.0))])
    without_costs = format_markdown([_run(ExecutionModel())])

    assert "исполнение: спред 10 б.п." in with_costs
    assert "исполнение:" not in without_costs
