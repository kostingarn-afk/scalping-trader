"""Издержки круга в тиках.

`Cost` — единственная величина издержек в исследовании (`φ* = Cost / σ_τ`, Gate 0a).
Считается как круг: комиссия входа + комиссия выхода + спред. Спред без стакана — оценка,
поэтому помечается `UNVERIFIED`; стресс `×1.5` — отдельный расчёт, а не правка результата.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from .ticks import TickSize
from .units import TICKS, Parameter

#: Ставка брокера за сторону сделки. Дублирует `AnalyzerConfig`, но здесь живёт как тариф
#: исследования: смена тарифа — параметр прогона, а не правка кода Analyzer.
TARIFF_RATES: Mapping[str, float] = {
    "investor": 0.003,
    "trader": 0.0005,
    "premium": 0.0004,
}

DEFAULT_TARIFF = "trader"
UNVERIFIED = "UNVERIFIED"


class CostError(ValueError):
    """Издержки нельзя посчитать по заданным входам."""


@dataclass(frozen=True)
class CostEstimate:
    """Круг в тиках и его слагаемые."""

    tariff: str
    rate_per_side: float
    commission_ticks_per_side: float
    spread_ticks: float
    round_trip_ticks: float
    stress_multiplier: float
    flags: Dict[str, str]

    def to_parameter(self) -> Parameter:
        flag = UNVERIFIED if self.flags.get("spread") == UNVERIFIED else None
        return Parameter(
            name="cost",
            value=round(self.round_trip_ticks, 6),
            units=TICKS,
            source=f"tariff:{self.tariff}",
            flag=flag,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "tariff": self.tariff,
            "rate_per_side": self.rate_per_side,
            "commission_ticks_per_side": self.commission_ticks_per_side,
            "spread_ticks": self.spread_ticks,
            "round_trip_ticks": self.round_trip_ticks,
            "stress_multiplier": self.stress_multiplier,
            "flags": dict(self.flags),
        }


def estimate_cost(
    price: float,
    tick: TickSize,
    *,
    tariff: str = DEFAULT_TARIFF,
    rate_per_side: Optional[float] = None,
    min_commission_rub: float = 0.0,
    spread_bps: float = 0.0,
    spread_verified: bool = False,
    stress: float = 1.0,
) -> CostEstimate:
    """Посчитать круг издержек в тиках.

    `spread_bps` — спред в базисных пунктах от цены за одну сторону (половина полного спреда,
    если полный измерен); без измерения ставится `spread_verified=False`.
    """
    if price <= 0:
        raise CostError(f"price must be positive, got {price}")
    if spread_bps < 0:
        raise CostError("spread_bps must be non-negative")
    if min_commission_rub < 0:
        raise CostError("min_commission_rub must be non-negative")
    if stress <= 0:
        raise CostError("stress multiplier must be positive")

    rate = _resolve_rate(tariff, rate_per_side)
    commission_rub = max(rate * price, min_commission_rub)
    commission_ticks = commission_rub / tick.value
    spread_ticks = spread_bps / 10_000.0 * price / tick.value
    round_trip = (2 * commission_ticks + spread_ticks) * stress

    flags: Dict[str, str] = {}
    if not spread_verified:
        flags["spread"] = UNVERIFIED
    if rate_per_side is not None and tariff not in TARIFF_RATES:
        flags["tariff"] = "CUSTOM"
    if min_commission_rub > 0:
        flags["min_commission"] = "APPLIED"

    return CostEstimate(
        tariff=tariff,
        rate_per_side=rate,
        commission_ticks_per_side=commission_ticks,
        spread_ticks=spread_ticks,
        round_trip_ticks=round_trip,
        stress_multiplier=stress,
        flags=flags,
    )


def stress_cost(
    price: float,
    tick: TickSize,
    *,
    tariff: str = DEFAULT_TARIFF,
    rate_per_side: Optional[float] = None,
    min_commission_rub: float = 0.0,
    spread_bps: float = 0.0,
    spread_verified: bool = False,
    multiplier: float = 1.5,
) -> CostEstimate:
    """Стресс-вариант Gate 4: те же слагаемые, умноженные на `multiplier`."""
    return estimate_cost(
        price,
        tick,
        tariff=tariff,
        rate_per_side=rate_per_side,
        min_commission_rub=min_commission_rub,
        spread_bps=spread_bps,
        spread_verified=spread_verified,
        stress=multiplier,
    )


def _resolve_rate(tariff: str, rate_per_side: Optional[float]) -> float:
    if rate_per_side is not None:
        if not 0 < rate_per_side <= 0.01:
            raise CostError(f"rate_per_side must be in (0, 0.01], got {rate_per_side}")
        return rate_per_side
    try:
        return TARIFF_RATES[tariff]
    except KeyError:
        raise CostError(
            f"unknown tariff {tariff!r}; known: {', '.join(sorted(TARIFF_RATES))}"
        ) from None


__all__ = [
    "CostError",
    "CostEstimate",
    "DEFAULT_TARIFF",
    "TARIFF_RATES",
    "estimate_cost",
    "stress_cost",
]
