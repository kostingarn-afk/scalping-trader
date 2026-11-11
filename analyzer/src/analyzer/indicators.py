"""Расчёт технических индикаторов (задача 3.3).

RSI(7), MACD(8,21,5), BB(20,2), ATR(14), VWAP, order imbalance (из стакана depth=10).
ta-lib отпускает GIL — CPU-bound расчёт эффективен.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import talib


@dataclass(frozen=True)
class Indicators:
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    atr: Optional[float] = None
    vwap: Optional[float] = None
    order_imbalance: Optional[float] = None

    def to_dict(self) -> Dict[str, float]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def compute_rsi(closes: np.ndarray, period: int = 7) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    rsi = talib.RSI(closes, timeperiod=period)
    val = rsi[-1]
    return float(val) if not np.isnan(val) else None


def compute_macd(closes: np.ndarray, fast: int = 8, slow: int = 21, signal: int = 5):
    if len(closes) < slow:
        return None, None, None
    macd, sig, hist = talib.MACD(closes, fastperiod=fast, slowperiod=slow, signalperiod=signal)
    v = macd[-1]
    if np.isnan(v):
        return None, None, None
    return float(v), float(sig[-1]), float(hist[-1])


def compute_bbands(closes: np.ndarray, period: int = 20, nbdev: float = 2.0):
    if len(closes) < period:
        return None, None, None
    upper, middle, lower = talib.BBANDS(closes, timeperiod=period, nbdevup=nbdev, nbdevdn=nbdev)
    if np.isnan(upper[-1]):
        return None, None, None
    return float(upper[-1]), float(middle[-1]), float(lower[-1])


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    atr = talib.ATR(highs, lows, closes, timeperiod=period)
    val = atr[-1]
    return float(val) if not np.isnan(val) else None


def compute_vwap(highs: np.ndarray, lows: np.ndarray, volumes: np.ndarray) -> Optional[float]:
    """VWAP = sum(typical_price * volume) / sum(volume)."""
    if volumes.sum() == 0:
        return None
    typical = (highs + lows + np.roll(highs, 0)) / 3.0  # (H+L+C)/3, C берём как highs roll=0 заглушкой
    return float((typical * volumes).sum() / volumes.sum())


def compute_order_imbalance(bids_qty: List[int], asks_qty: List[int], depth: int = 10) -> Optional[float]:
    """Order imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol), по depth уровням."""
    bid_vol = sum(bids_qty[:depth])
    ask_vol = sum(asks_qty[:depth])
    total = bid_vol + ask_vol
    if total == 0:
        return None
    return (bid_vol - ask_vol) / total


def compute_all(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    bids_qty: Optional[List[int]] = None,
    asks_qty: Optional[List[int]] = None,
) -> Indicators:
    """Расчёт всех индикаторов сразу."""
    rsi = compute_rsi(closes)
    macd, macd_sig, macd_hist = compute_macd(closes)
    bb_u, bb_m, bb_l = compute_bbands(closes)
    atr = compute_atr(highs, lows, closes)
    vwap = compute_vwap(highs, lows, volumes)
    imbalance = compute_order_imbalance(bids_qty or [], asks_qty or []) if bids_qty else None
    return Indicators(
        rsi=rsi, macd=macd, macd_signal=macd_sig, macd_hist=macd_hist,
        bb_upper=bb_u, bb_middle=bb_m, bb_lower=bb_l,
        atr=atr, vwap=vwap, order_imbalance=imbalance,
    )
