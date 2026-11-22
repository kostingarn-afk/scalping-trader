"""Стратегия mean reversion (BB + Z-score) — задача 3.4.

Сигнал BUY при RSI<30 и цене≤lower BB, SELL при RSI>70 и цене≥upper BB, HOLD иначе.
SL = entry - 1.5×ATR; цель выхода строится от порога комиссии (`with_target`, шаг 1.9):
TP = entry ± k×ATR, где `k = ceil(min_tp_pct / ATR%)`, но не меньше 1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from .indicators import Indicators


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    type: SignalType
    confidence: float
    suggested_price: float
    suggested_sl: Optional[float]
    suggested_tp: Optional[float]
    reason: str = ""

    @property
    def is_actionable(self) -> bool:
        return self.type in (SignalType.BUY, SignalType.SELL)


def mean_reversion(ind: Indicators, price: float, atr_mult_sl: float = 1.5, atr_mult_tp: float = 2.0) -> Signal:
    """Mean reversion: возврат к средней BB.

    BUY: RSI < 30 и цена ≤ lower BB (перепроданность).
    SELL: RSI > 70 и цена ≥ upper BB (перекупленность).
    HOLD: иначе.
    SL от ATR: SL = entry ∓ 1.5×ATR. Целевое значение TP здесь — статическое кратное;
    боевая и бэктестовая цель ставятся `with_target` от порога комиссии.
    """
    if ind.rsi is None or ind.bb_lower is None or ind.bb_upper is None:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data")

    # Плоский рынок: при нулевой ширине BB условия «цена ≤ lower» и «цена ≥ upper» истинны
    # одновременно, а ta-lib на неизменном ряду отдаёт RSI=0 — получается ложный BUY
    # с максимальной уверенностью. Без волатильности SL/TP построить нельзя, поэтому HOLD.
    if ind.bb_upper <= ind.bb_lower or ind.atr is None or ind.atr <= 0:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "flat market: no volatility")

    # BUY: перепроданность
    if ind.rsi < 30 and price <= ind.bb_lower:
        sl = _sl_tp(price, ind.atr, atr_mult_sl, sign=-1)
        tp = _sl_tp(price, ind.atr, atr_mult_tp, sign=+1)
        confidence = _confidence(30 - ind.rsi, 30)
        return Signal(SignalType.BUY, confidence, price, sl, tp, "rsi oversold + lower BB")

    # SELL: перекупленность
    if ind.rsi > 70 and price >= ind.bb_upper:
        sl = _sl_tp(price, ind.atr, atr_mult_sl, sign=+1)
        tp = _sl_tp(price, ind.atr, atr_mult_tp, sign=-1)
        confidence = _confidence(ind.rsi - 70, 30)
        return Signal(SignalType.SELL, confidence, price, sl, tp, "rsi overbought + upper BB")

    return Signal(SignalType.HOLD, 0.0, price, None, None, "no signal")


def target_mult(price: float, atr: Optional[float], min_tp_pct: float) -> int:
    """Кратное ATR для цели: покрыть `min_tp_pct` процентов цены, но не меньше 1.

    `min_tp_pct` — порог комиссии в процентах от суммы сделки
    (`commission_profit_ratio × 2 × rate_per_side × 100`).
    """
    if atr is None or atr <= 0 or price <= 0 or min_tp_pct <= 0:
        return 1
    atr_pct = 100.0 * atr / price
    return max(1, math.ceil(min_tp_pct / atr_pct))


def with_target(
    signal: Signal, price: float, atr: Optional[float], min_tp_pct: float
) -> Signal:
    """Поставить цель `k×ATR` вместо статического кратного.

    Применяется после правила входа и в production, и в бэктесте — иначе прогон перестаёт
    воспроизводить боевое поведение. `min_tp_pct ≤ 0` и сигнал HOLD оставляют сигнал как есть.
    """
    if not signal.is_actionable or min_tp_pct <= 0 or atr is None or atr <= 0:
        return signal
    mult = target_mult(price, atr, min_tp_pct)
    side = 1.0 if signal.type is SignalType.BUY else -1.0
    return replace(signal, suggested_tp=round(price + side * mult * atr, 9))


def _sl_tp(price: float, atr: Optional[float], mult: float, sign: int) -> Optional[float]:
    if atr is None or atr <= 0:
        return None
    return round(price + sign * mult * atr, 9)


def _confidence(excess: float, scale: float) -> float:
    """confidence 0.5..1.0 по превышению порога."""
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (excess / scale))
