"""Тесты индикаторов на synthetic данных — задача 3.3."""
from __future__ import annotations

import numpy as np

from analyzer.indicators import (
    compute_all,
    compute_order_imbalance,
    compute_rsi,
    compute_vwap,
)


def test_rsi_on_flat_series():
    closes = np.full(20, 100.0)
    rsi = compute_rsi(closes, period=7)
    # ta-lib считает RSI = 100 - 100/(1+RS), где RS = avg_gain/avg_loss = 0/0,
    # и на неизменном ряду отдаёт 0.0 (не 100.0). Сигналы на таком ряду отсекает
    # guard плоского рынка в strategy.mean_reversion.
    assert rsi is None or rsi == 0.0


def test_rsi_insufficient_data():
    closes = np.array([100.0, 101.0, 102.0])
    assert compute_rsi(closes, period=7) is None


def test_order_imbalance_balanced():
    bids = [10, 10, 10, 0, 0, 0, 0, 0, 0, 0]
    asks = [10, 10, 10, 0, 0, 0, 0, 0, 0, 0]
    assert compute_order_imbalance(bids, asks) == 0.0


def test_order_imbalance_bid_heavy():
    bids = [100, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    asks = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert compute_order_imbalance(bids, asks) == 1.0


def test_order_imbalance_empty():
    assert compute_order_imbalance([], []) is None


def test_vwap_basic():
    highs = np.array([10.0, 20.0])
    lows = np.array([8.0, 18.0])
    vols = np.array([100, 100])
    vwap = compute_vwap(highs, lows, vols)
    assert vwap is not None
    assert 8.0 <= vwap <= 20.0


def test_compute_all_returns_indicators():
    n = 30
    closes = np.linspace(100, 110, n)
    highs = closes + 1
    lows = closes - 1
    vols = np.full(n, 1000)
    ind = compute_all(closes, highs, lows, vols)
    assert ind.rsi is not None
    assert ind.bb_upper is not None
    assert ind.atr is not None
