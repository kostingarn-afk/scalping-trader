"""Тесты гейта ожидаемой доходности (`backtest/ev_gate.py`) — шаг 1.10."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.backtest.ev_gate import EvEstimate, estimate
from analyzer.backtest.models import Direction, ExitReason, Trade

START = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
LOTS = 10
LOT_SIZE = 1


def _trade(net: float, index: int = 0) -> Trade:
    entry = 100.0
    gross = net + 50.0
    when = START + timedelta(minutes=index)
    return Trade(
        direction=Direction.LONG,
        entry_ts=when,
        exit_ts=when + timedelta(minutes=3),
        entry_price=entry,
        exit_price=entry + gross / (LOTS * LOT_SIZE),
        sl=99.0,
        tp=None,
        lots=LOTS,
        lot_size=LOT_SIZE,
        exit_reason=ExitReason.TIMEOUT,
        bars_held=3,
        notional=entry * LOTS * LOT_SIZE,
        gross_pnl=gross,
        commission=50.0,
        net_pnl=net,
    )


def _trades(nets: list[float]) -> list[Trade]:
    return [_trade(net, index) for index, net in enumerate(nets)]


def test_expected_value_from_wins_and_losses():
    # 60 сделок: 40 выигрышей по 100 и 20 проигрышей по 50 → p = 2/3, EV = 66.67 − 16.67 = 50
    trades = _trades([100.0] * 40 + [-50.0] * 20)

    estimate_value = estimate(trades, min_trades=50)

    assert estimate_value is not None
    assert estimate_value.trades == 60
    assert estimate_value.win_rate == pytest.approx(40 / 60)
    assert estimate_value.avg_win_rub == pytest.approx(100.0)
    assert estimate_value.avg_loss_rub == pytest.approx(50.0)
    assert estimate_value.ev_rub == pytest.approx(50.0)
    assert estimate_value.is_positive


def test_negative_expected_value():
    # 60 сделок: 20 выигрышей по 50 и 40 проигрышей по 100 → EV < 0
    trades = _trades([50.0] * 20 + [-100.0] * 40)

    estimate_value = estimate(trades, min_trades=50)

    assert estimate_value is not None
    assert estimate_value.ev_rub < 0
    assert not estimate_value.is_positive


def test_zero_pnl_counts_as_loss():
    """Нулевой net — не выигрыш: он не окупает издержки и попадает в знаменатель проигрышей."""
    trades = _trades([100.0] * 30 + [0.0] * 30)

    estimate_value = estimate(trades, min_trades=50)

    assert estimate_value is not None
    assert estimate_value.win_rate == pytest.approx(0.5)
    # avgLoss включает нули, поэтому средний проигрыш остаётся нулевым,
    # а EV = 0.5·100 − 0.5·0 = 50
    assert estimate_value.avg_loss_rub == pytest.approx(0.0)
    assert estimate_value.ev_rub == pytest.approx(50.0)


def test_no_estimate_below_minimum_sample():
    trades = _trades([100.0] * 30 + [-50.0] * 19)

    assert estimate(trades, min_trades=50) is None


def test_no_estimate_for_one_sided_sample():
    assert estimate(_trades([100.0] * 60), min_trades=50) is None
    assert estimate(_trades([-100.0] * 60), min_trades=50) is None


def test_window_keeps_only_recent_trades():
    # 100 старых убытков и 60 свежих прибылей: окно 60 видит только прибыли → односторонняя
    # выборка, поэтому окно 80 даёт оценку, а окно 60 — нет.
    trades = _trades([-100.0] * 100 + [100.0] * 60)

    assert estimate(trades, min_trades=50, window=60) is None
    wide = estimate(trades, min_trades=50, window=80)
    assert wide is not None
    assert wide.trades == 80


def test_window_is_limited_by_sequence_length():
    trades = _trades([100.0] * 30 + [-50.0] * 30)

    estimate_value = estimate(trades, min_trades=50, window=200)

    assert estimate_value is not None
    assert estimate_value.trades == 60


def test_invalid_limits_return_no_estimate():
    trades = _trades([100.0] * 30 + [-50.0] * 30)

    assert estimate(trades, min_trades=0) is None
    assert estimate(trades, min_trades=50, window=0) is None


def test_empty_selection_returns_no_estimate():
    assert estimate([], min_trades=1) is None


def test_estimate_is_immutable_snapshot():
    trades = _trades([100.0] * 40 + [-50.0] * 20)
    estimate_value = estimate(trades, min_trades=50)

    trades.append(_trade(-1000.0, 999))

    assert isinstance(estimate_value, EvEstimate)
    assert estimate_value.ev_rub == pytest.approx(50.0)
