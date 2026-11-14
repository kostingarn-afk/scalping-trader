"""Тесты стратегии mean reversion — задача 3.4."""
from __future__ import annotations

from analyzer.indicators import Indicators
from analyzer.strategy import SignalType, mean_reversion


def test_buy_signal_oversold():
    ind = Indicators(rsi=25.0, bb_lower=100.0, bb_upper=110.0, bb_middle=105.0, atr=1.0)
    sig = mean_reversion(ind, price=99.5)
    assert sig.type == SignalType.BUY
    assert sig.suggested_sl is not None
    assert sig.suggested_tp is not None
    assert sig.suggested_sl < 99.5  # SL ниже входа
    assert sig.suggested_tp > 99.5  # TP выше входа


def test_sell_signal_overbought():
    ind = Indicators(rsi=75.0, bb_lower=100.0, bb_upper=110.0, bb_middle=105.0, atr=1.0)
    sig = mean_reversion(ind, price=110.5)
    assert sig.type == SignalType.SELL
    assert sig.suggested_sl is not None
    assert sig.suggested_sl > 110.5  # SL выше входа для short
    assert sig.suggested_tp < 110.5  # TP ниже входа


def test_hold_signal_neutral():
    ind = Indicators(rsi=50.0, bb_lower=100.0, bb_upper=110.0, bb_middle=105.0, atr=1.0)
    sig = mean_reversion(ind, price=105.0)
    assert sig.type == SignalType.HOLD


def test_hold_on_insufficient_data():
    ind = Indicators()  # все None
    sig = mean_reversion(ind, price=100.0)
    assert sig.type == SignalType.HOLD


def test_hold_on_flat_market():
    # RSI=0 от ta-lib на неизменном ряду + нулевая ширина BB не должны давать BUY
    ind = Indicators(rsi=0.0, bb_lower=100.0, bb_upper=100.0, bb_middle=100.0, atr=0.0)
    sig = mean_reversion(ind, price=100.0)
    assert sig.type == SignalType.HOLD
    assert sig.reason == "flat market: no volatility"


def test_hold_when_atr_missing():
    # Без ATR нельзя построить SL/TP — сигнал не генерируется
    ind = Indicators(rsi=20.0, bb_lower=100.0, bb_upper=110.0, atr=None)
    sig = mean_reversion(ind, price=99.0)
    assert sig.type == SignalType.HOLD


def test_sl_tp_from_atr():
    ind = Indicators(rsi=20.0, bb_lower=100.0, bb_upper=110.0, atr=2.0)
    sig = mean_reversion(ind, price=99.0)
    # SL = entry - 1.5*ATR = 99 - 3 = 96
    assert abs(sig.suggested_sl - 96.0) < 1e-9
    # TP = entry + 2*ATR = 99 + 4 = 103
    assert abs(sig.suggested_tp - 103.0) < 1e-9
