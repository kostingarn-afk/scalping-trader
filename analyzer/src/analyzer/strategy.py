"""Стратегия mean reversion (BB + Z-score) — задача 3.4.

Сигнал BUY при RSI<30 и цене≤lower BB, SELL при RSI>70 и цене≥upper BB, HOLD иначе.
SL = entry - 1.5×ATR, TP = entry + 2×ATR.
"""
from __future__ import annotations

from dataclasses import dataclass
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
    SL/TP от ATR: SL = entry ∓ 1.5×ATR, TP = entry ± 2×ATR.
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


def _sl_tp(price: float, atr: Optional[float], mult: float, sign: int) -> Optional[float]:
    if atr is None or atr <= 0:
        return None
    return round(price + sign * mult * atr, 9)


def _confidence(excess: float, scale: float) -> float:
    """confidence 0.5..1.0 по превышению порога."""
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (excess / scale))
