"""Тесты комиссионного фильтра — задача 3.5."""
from __future__ import annotations

from analyzer.commission_filter import CommissionFilter
from analyzer.strategy import Signal, SignalType


def test_signal_rejected_low_profit():
    # Малая прибыль относительно комиссии
    sig = Signal(SignalType.BUY, 0.8, 100.0, 99.0, 100.05)
    f = CommissionFilter(ratio=3.0)
    assert not f.should_keep(sig, quantity=1)


def test_signal_kept_high_profit():
    # Большая прибыль — проходит
    sig = Signal(SignalType.BUY, 0.8, 100.0, 99.0, 110.0)
    f = CommissionFilter(ratio=3.0)
    assert f.should_keep(sig, quantity=1)


def test_hold_always_rejected():
    sig = Signal(SignalType.HOLD, 0.0, 100.0, None, None)
    f = CommissionFilter(ratio=3.0)
    assert not f.should_keep(sig)


def test_commission_estimate():
    f = CommissionFilter(ratio=3.0)
    # 0.3% * 100 * 1 * 2 (вход+выход) = 0.6
    assert abs(f.estimate_commission(100.0, 1) - 0.6) < 1e-9
