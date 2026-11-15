"""Тесты индикаторов на synthetic данных — задача 3.3."""
from __future__ import annotations

import numpy as np
import pytest

from analyzer.indicators import (
    compute_all,
    compute_atr_pct,
    compute_atr_pct_series,
    compute_order_imbalance,
    compute_rsi,
    compute_vwap,
    compute_zscore,
)


def test_rsi_on_flat_series():
    """RSI на неизменном ряду неинформативен: значение зависит от версии ta-lib (0.0 или 50.0).

    Проверяем, что расчёт не падает и остаётся в диапазоне; отсутствие ложного сигнала
    на плоском рынке покрыто `test_hold_on_flat_market` в `test_strategy.py`.
    """
    closes = np.full(20, 100.0)

    rsi = compute_rsi(closes, period=7)

    assert rsi is None or 0.0 <= rsi <= 100.0


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


def test_compute_all_fills_v2_fields():
    """Поля стратегии v2: z-score, ADX, ATR% — заполняются на серии достаточной длины."""
    n = 120
    closes = np.linspace(100, 110, n) + np.sin(np.arange(n) / 3.0)
    highs = closes + 1
    lows = closes - 1
    vols = np.full(n, 1000)

    ind = compute_all(closes, highs, lows, vols)

    assert ind.adx is not None and ind.adx >= 0
    assert ind.atr_pct is not None and ind.atr_pct > 0
    assert ind.zscore is not None
    # z-score в единицах σ: положительный при цене выше середины канала BB.
    assert ind.zscore == pytest.approx(
        (closes[-1] - ind.bb_middle) / (ind.bb_upper - ind.bb_middle)
    )


def test_compute_zscore_none_on_flat_channel():
    assert compute_zscore(100.0, bb_upper=100.0, bb_middle=100.0) is None


def test_compute_atr_pct_rejects_zero_close():
    assert compute_atr_pct(0.5, 0.0) is None
    assert compute_atr_pct(None, 100.0) is None


def test_atr_pct_series_skips_warmup_nan():
    n = 60
    closes = np.linspace(100, 110, n)
    series = compute_atr_pct_series(closes + 1, closes - 1, closes)

    assert series.size == n - 14
    assert np.all(np.isfinite(series))
