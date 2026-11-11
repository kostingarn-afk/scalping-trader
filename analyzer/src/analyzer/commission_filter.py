"""Комиссионная фильтрация сигналов — задача 3.5.

Отбрасывает сигналы, где ожидаемая прибыль < ratio × комиссии (вход+выход).
"""
from __future__ import annotations

from dataclasses import dataclass

from .strategy import Signal, SignalType


@dataclass(frozen=True)
class CommissionFilter:
    ratio: float = 3.0  # commission_profit_ratio из risk_config

    def estimate_commission(self, price: float, quantity: int) -> float:
        """Комиссия T-Invest: 0.3% от оборота (заглушка; точная формула зависит от инструмента)."""
        return 0.003 * price * quantity * 2  # вход + выход

    def estimate_profit(self, signal: Signal, quantity: int) -> float:
        """Ожидаемая прибыль = |TP - entry| × quantity."""
        if signal.suggested_tp is None:
            return 0.0
        return abs(signal.suggested_tp - signal.suggested_price) * quantity

    def should_keep(self, signal: Signal, quantity: int = 1) -> bool:
        """True — сигнал проходит фильтр, False — отбрасывается."""
        if signal.type == SignalType.HOLD:
            return False
        commission = self.estimate_commission(signal.suggested_price, quantity)
        profit = self.estimate_profit(signal, quantity)
        return profit >= self.ratio * commission
