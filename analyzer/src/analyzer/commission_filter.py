"""Комиссионная фильтрация сигналов — задача 3.5.

Отбрасывает сигналы, где ожидаемая прибыль < ratio × комиссии (вход+выход).
"""
from __future__ import annotations

from dataclasses import dataclass

from .strategy import Signal, SignalType


@dataclass(frozen=True)
class CommissionFilter:
    ratio: float = 3.0  # commission_profit_ratio из risk_config
    # Ставка за сторону: «Инвестор» 0.003, «Трейдер» 0.0005. Живёт в конфиге, а не в коде:
    # ставка меняет, сколько сигналов переживёт фильтр (на «Инвесторе» отсекается ~92 %).
    # Дефолт — «Трейдер»: порог 0.30 % вместо 1.8 % (шаг 1.9).
    rate_per_side: float = 0.0005

    def estimate_commission(self, price: float, tp: float, quantity: int) -> float:
        """Круговая комиссия: ставка за сторону × (вход + выход)."""
        return self.rate_per_side * (price + tp) * quantity

    def estimate_profit(self, signal: Signal, quantity: int) -> float:
        """Ожидаемая прибыль = |TP - entry| × quantity."""
        if signal.suggested_tp is None:
            return 0.0
        return abs(signal.suggested_tp - signal.suggested_price) * quantity

    def should_keep(self, signal: Signal, quantity: int = 1) -> bool:
        """True — сигнал проходит фильтр, False — отбрасывается."""
        if signal.type == SignalType.HOLD:
            return False
        # Без цели прибыли нет — стоимость круга считать нечего.
        if signal.suggested_tp is None:
            return False
        commission = self.estimate_commission(
            signal.suggested_price, signal.suggested_tp, quantity
        )
        profit = self.estimate_profit(signal, quantity)
        return profit >= self.ratio * commission
