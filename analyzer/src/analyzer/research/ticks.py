"""Шаг цены: резолвинг из метаданных, справочника или сетки цен.

Барьеры, издержки `C` и требуемое движение `φ*` считаются в тиках, поэтому шаг цены —
первый вход всего исследования. Выведенный по сетке цен шаг помечается `UNVERIFIED`:
на «рваных» данных он может оказаться меньше реального, и пороги тогда завышены.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import gcd
from typing import Iterable, List, Optional, Sequence, Tuple

from .units import RUB, Parameter

SOURCE_METADATA = "metadata"
SOURCE_REFERENCE = "reference"
SOURCE_PRICE_GRID = "price-grid"

UNVERIFIED = "UNVERIFIED"

#: Точность сетки цен, выше которой значения считаются шумом представления float.
MAX_PRICE_DECIMALS = 6


class TickSizeError(ValueError):
    """Шаг цены не может быть определён."""


@dataclass(frozen=True)
class TickSize:
    """Минимальный шаг цены инструмента.

    `verified=False` означает, что значение выведено по сетке цен, а не взято из справочника:
    оно пригодно для расчёта, но обязано попасть в отчёт с пометкой.
    """

    value: float
    source: str
    verified: bool = True
    flag: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.value > 0:
            raise TickSizeError(f"tick size must be positive, got {self.value}")
        if not self.source:
            raise TickSizeError("tick size source is required")
        if not self.verified and self.flag is None:
            object.__setattr__(self, "flag", UNVERIFIED)

    def to_ticks(self, delta: float) -> int:
        """Перевести ценовое расстояние в тики с округлением половины вверх по модулю."""
        return int(
            (Decimal(str(delta)) / Decimal(str(self.value))).to_integral_value(ROUND_HALF_UP)
        )

    def from_ticks(self, ticks: int) -> float:
        return float(ticks) * self.value

    def is_on_grid(self, price: float, tolerance: Optional[float] = None) -> bool:
        """Лежит ли цена на сетке шага (допуск — на шум представления float)."""
        tol = max(self.value * 1e-6, 1e-9) if tolerance is None else tolerance
        ratio = Decimal(str(price)) / Decimal(str(self.value))
        offset = abs(ratio - ratio.to_integral_value(ROUND_HALF_UP)) * Decimal(str(self.value))
        return float(offset) <= tol

    def as_parameter(self) -> Parameter:
        return Parameter(
            name="tick_size",
            value=self.value,
            units=RUB,
            source=self.source,
            flag=self.flag,
        )


def resolve_tick_size(
    meta_value: Optional[float] = None,
    reference_value: Optional[float] = None,
    prices: Iterable[float] = (),
) -> TickSize:
    """Определить шаг цены: метаданные → справочник → сетка цен.

    Метаданные и справочник дают проверенное значение; сетка цен — с пометкой `UNVERIFIED`.
    Молчаливая подстановка «разумного» шага запрещена: если источников нет, это ошибка.
    """
    for value, source in (
        (meta_value, SOURCE_METADATA),
        (reference_value, SOURCE_REFERENCE),
    ):
        if value is None:
            continue
        if not value > 0:
            raise TickSizeError(f"{source}: tick size must be positive, got {value}")
        return TickSize(value=float(value), source=source, verified=True)

    inferred = infer_tick_from_prices(prices)
    return TickSize(value=inferred, source=SOURCE_PRICE_GRID, verified=False)


def infer_tick_from_prices(prices: Iterable[float]) -> float:
    """НОД разностей сетки цен.

    Цены округляются до `MAX_PRICE_DECIMALS` знаков, а затем переводятся в целые: в `float`
    `0.01` неотличимо от `0.010000000000000002`, и НОД по бинарным разностям «съезжает» к 1e-14.
    Точность тоньше 1e-6 для сетки цен MOEX недостижима, поэтому шум за этой границей —
    ошибка представления, а не шаг; инструменты с более тонким шагом требуют метаданных.
    """
    decimals = [Decimal(str(price)) for price in prices]
    if len({value for value in decimals}) < 2:
        raise TickSizeError(
            "cannot infer tick size: fewer than two distinct prices in the sample"
        )
    scaled, scale = _to_scaled_integers(decimals)
    ordered = sorted(set(scaled))
    step = 0
    for left, right in zip(ordered, ordered[1:]):
        step = gcd(step, right - left)
    if step == 0:
        raise TickSizeError("cannot infer tick size: price grid collapses to one point")
    return step / scale


def _to_scaled_integers(values: Sequence[Decimal]) -> Tuple[List[int], int]:
    exponent = min(
        max(-value.as_tuple().exponent for value in values),
        MAX_PRICE_DECIMALS,
    )
    scale = 10**exponent
    quantized = [
        value.quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP) for value in values
    ]
    scaled = [int(value.scaleb(exponent).to_integral_value(ROUND_HALF_UP)) for value in quantized]
    return scaled, scale


def describe(tick: TickSize) -> str:
    """Строка для отчёта: значение, источник и пометка."""
    suffix = f" ({tick.flag})" if tick.flag else ""
    return f"{tick.value:g} [{tick.source}]{suffix}"


__all__ = [
    "MAX_PRICE_DECIMALS",
    "SOURCE_METADATA",
    "SOURCE_PRICE_GRID",
    "SOURCE_REFERENCE",
    "TickSize",
    "TickSizeError",
    "describe",
    "infer_tick_from_prices",
    "resolve_tick_size",
]
