"""Дисперсии: range-based оценки, отношение шума, `VR(τ)` и `σ_τ`.

`σ_τ = σ_1·√(τ·VR(τ))` — единственная допустимая горизонтная волатильность (§6.6
`buy_frame.md`): у mean reversion дисперсия растёт медленнее линейной, и подстановка `σ√τ`
завышает требуемое движение.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log, sqrt
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from ..backtest.models import Bar
from .sessions import SessionSchedule

LN2 = log(2.0)

BOUNCE = "BOUNCE"
SMOOTHED = "SMOOTHED"
LOW_CONFIDENCE = "LOW_CONFIDENCE"


class VarianceError(ValueError):
    """Дисперсию нельзя оценить по заданным данным."""


@dataclass(frozen=True)
class VarianceEstimate:
    """Оценки дисперсии одной минуты (в логарифмических единицах)."""

    parkinson: float
    garman_klass: float
    rogers_satchell: float
    close_to_close: float
    noise_ratio: float
    bars: int
    flag: Optional[str] = None
    flags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "parkinson": self.parkinson,
            "garman_klass": self.garman_klass,
            "rogers_satchell": self.rogers_satchell,
            "close_to_close": self.close_to_close,
            "noise_ratio": self.noise_ratio,
            "bars": self.bars,
            "flag": self.flag,
            "flags": dict(self.flags),
        }


def estimate_variances(bars: Sequence[Bar]) -> VarianceEstimate:
    """Средние по барам range-based оценки и дисперсия доходностей закрытие-закрытие.

    Отрицательные per-bar значения GK/RS обрезаются нулём (они возможны на шумных барах);
    доля обрезки больше 5 % помечается `LOW_CONFIDENCE`.
    """
    if len(bars) < 2:
        raise VarianceError("at least two bars are required")
    parkinson: List[float] = []
    garman_klass: List[float] = []
    rogers_satchell: List[float] = []
    clipped = 0
    for bar in bars:
        high, low, open_, close = bar.high, bar.low, bar.open, bar.close
        if min(high, low, open_, close) <= 0 or high < low:
            raise VarianceError(f"invalid OHLC in bar {bar.ts.isoformat()}")
        hl = log(high / low)
        co = log(close / open_)
        parkinson.append(hl * hl / (4 * LN2))
        gk = 0.5 * hl * hl - (2 * LN2 - 1) * co * co
        rs = log(high / close) * log(high / open_) + log(low / close) * log(low / open_)
        if gk < 0:
            gk = 0.0
            clipped += 1
        if rs < 0:
            rs = 0.0
            clipped += 1
        garman_klass.append(gk)
        rogers_satchell.append(rs)

    returns = [log(current.close / previous.close) for previous, current in zip(bars, bars[1:])]
    mean_return = sum(returns) / len(returns)
    close_var = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    if close_var <= 0:
        raise VarianceError("close-to-close variance is zero: series is flat")

    parkinson_mean = sum(parkinson) / len(parkinson)
    ratio = parkinson_mean / close_var
    flags: Dict[str, str] = {}
    flag: Optional[str] = None
    if ratio > 2.0:
        flag = BOUNCE
        flags["noise"] = BOUNCE
    elif ratio < 0.5:
        flag = SMOOTHED
        flags["noise"] = SMOOTHED
    if clipped / (2 * len(bars)) > 0.05:
        flags["clipping"] = LOW_CONFIDENCE

    return VarianceEstimate(
        parkinson=parkinson_mean,
        garman_klass=sum(garman_klass) / len(garman_klass),
        rogers_satchell=sum(rogers_satchell) / len(rogers_satchell),
        close_to_close=close_var,
        noise_ratio=ratio,
        bars=len(bars),
        flag=flag,
        flags=flags,
    )


def variance_ratio(returns: Sequence[float], tau: int) -> float:
    """`VR(τ) = Var(r_τ) / (τ·Var(r_1))` по неперекрывающимся окнам."""
    if tau < 1:
        raise VarianceError("tau must be at least 1")
    if len(returns) < 2:
        raise VarianceError("at least two returns are required")
    base = _variance(returns)
    if base <= 0:
        raise VarianceError("variance of one-minute returns is zero")
    if tau == 1:
        return 1.0
    windows = len(returns) // tau
    if windows < 2:
        raise VarianceError(
            f"not enough returns for tau={tau}: need at least {2 * tau}, got {len(returns)}"
        )
    aggregated = [
        sum(returns[index * tau : (index + 1) * tau]) for index in range(windows)
    ]
    return _variance(aggregated) / (tau * base)


def sigma_tau(sigma_1: float, tau: int, vr: float) -> float:
    """`σ_τ = σ_1·√(τ·VR(τ))`; `VR ≤ 0` — ошибка, а не повод подставить `σ√τ`."""
    if sigma_1 <= 0:
        raise VarianceError("sigma_1 must be positive")
    if tau < 1:
        raise VarianceError("tau must be at least 1")
    if vr <= 0:
        raise VarianceError(f"VR({tau}) must be positive, got {vr}")
    return sigma_1 * sqrt(tau * vr)


def variance_ratio_curve(returns: Sequence[float], taus: Sequence[int]) -> Dict[int, float]:
    """Таблица `VR(τ)` для сетки горизонтов (артефакт `vr_table`)."""
    return {tau: variance_ratio(returns, tau) for tau in taus}


@dataclass(frozen=True)
class DeseasonResult:
    returns: Tuple[float, ...]
    profile: Dict[int, float]
    flags: Dict[str, str] = field(default_factory=dict)


def deseasonalize_returns(
    bars: Sequence[Bar],
    schedule: SessionSchedule,
    *,
    min_observations: int = 30,
) -> DeseasonResult:
    """Разделить доходность на типичную для её минуты сессии.

    Минуты с малым числом наблюдений не получают коэффициента (1.0) и помечаются
    `LOW_CONFIDENCE`: подстановка медианы по трём точкам искажает ряд сильнее, чем отсутствие
    поправки.
    """
    if min_observations < 2:
        raise VarianceError("min_observations must be at least 2")
    pairs: List[Tuple[float, int]] = []
    for previous, current in zip(bars, bars[1:]):
        if not (schedule.is_open(previous.ts) and schedule.is_open(current.ts)):
            continue
        if schedule.local(previous.ts).date() != schedule.local(current.ts).date():
            continue
        minute = schedule.session_minute(current.ts)
        if minute is None:
            continue
        pairs.append((log(current.close / previous.close), minute))
    if len(pairs) < min_observations:
        raise VarianceError(
            f"not enough in-session returns: got {len(pairs)}, need {min_observations}"
        )

    by_minute: Dict[int, List[float]] = {}
    for value, minute in pairs:
        by_minute.setdefault(minute, []).append(abs(value))
    overall = median([abs(value) for value, _ in pairs])
    if overall <= 0:
        raise VarianceError("median absolute return is zero: cannot deseasonalize")

    profile: Dict[int, float] = {}
    flags: Dict[str, str] = {}
    fallback: List[int] = []
    for minute, values in by_minute.items():
        if len(values) < min_observations:
            profile[minute] = 1.0
            fallback.append(minute)
            continue
        profile[minute] = median(values) / overall
    if fallback:
        flags["deseasonalization"] = (
            f"{LOW_CONFIDENCE}: {len(fallback)} минут без коэффициента "
            f"(меньше {min_observations} наблюдений)"
        )

    adjusted = tuple(
        value / profile[minute] if profile.get(minute) else value for value, minute in pairs
    )
    return DeseasonResult(returns=adjusted, profile=profile, flags=flags)


def _variance(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


__all__ = [
    "BOUNCE",
    "DeseasonResult",
    "LOW_CONFIDENCE",
    "SMOOTHED",
    "VarianceError",
    "VarianceEstimate",
    "deseasonalize_returns",
    "estimate_variances",
    "sigma_tau",
    "variance_ratio",
    "variance_ratio_curve",
]
