"""Единицы измерения параметров исследования.

Контракт `docs/buy_frame.md` §2: параметр без единиц отклоняется, а не принимается по
умолчанию. Причина — ошибка единиц (проценты вместо тиков, рубли вместо тиков за круг) в
прошлых итерациях давала выводы, которые нельзя было пересчитать.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, Mapping, Optional, Tuple, Union

TICKS = "ticks"
MINUTE_INVERSE = "min^-1"
PCT = "pct"
RUB = "rub"
RATIO = "ratio"
MINUTES = "minutes"
COUNT = "count"
BPS = "bps"

#: Закрытый список: свободный текст не ловит опечатку (`tick` вместо `ticks`).
ALLOWED_UNITS: frozenset[str] = frozenset(
    {TICKS, MINUTE_INVERSE, PCT, RUB, RATIO, MINUTES, COUNT, BPS}
)

#: Единицы по умолчанию для типовых величин исследования.
EXPECTED_UNITS: Mapping[str, str] = {
    "price": TICKS,
    "barrier": TICKS,
    "barrier_tp": TICKS,
    "barrier_sl": TICKS,
    "cost": TICKS,
    "spread": TICKS,
    "theta": MINUTE_INVERSE,
    "lam": MINUTE_INVERSE,
    "beta": MINUTE_INVERSE,
    "horizon": MINUTES,
    "tick_size": RUB,
}

Number = Union[int, float]


class UnitsError(ValueError):
    """Нарушение контракта единиц измерения."""


@dataclass(frozen=True)
class Parameter:
    """Измеренная величина с обязательными единицами.

    `flag` — пометка качества (`UNVERIFIED`, `LOW_CONFIDENCE`, `CONSERVATIVE`), а не единица:
    она не заменяет `units` и не отменяет проверку единиц.
    """

    name: str
    value: Number
    units: str
    source: str = ""
    flag: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise UnitsError("parameter name is required")
        if not self.units:
            raise UnitsError(f"{self.name}: units are required")
        if self.units not in ALLOWED_UNITS:
            raise UnitsError(
                f"{self.name}: unknown units {self.units!r}; allowed: "
                f"{', '.join(sorted(ALLOWED_UNITS))}"
            )
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise UnitsError(f"{self.name}: value must be numeric, got {type(self.value).__name__}")

    def expect(self, units: str) -> "Parameter":
        """Проверить, что величина измерена в ожидаемых единицах."""
        if self.units != units:
            raise UnitsError(f"{self.name}: expected units {units!r}, got {self.units!r}")
        return self

    def as_ticks(self) -> int:
        """Целое число тиков: барьеры и цены в тиках целые по контракту."""
        self.expect(TICKS)
        if float(self.value) != float(int(self.value)):
            raise UnitsError(f"{self.name}: tick count must be integer, got {self.value}")
        return int(self.value)

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "units": self.units,
            "source": self.source,
            "flag": self.flag,
        }


class UnitsRegistry:
    """Свод параметров этапа: единственное место, где проверяются единицы."""

    def __init__(self, parameters: Iterable[Parameter] = ()) -> None:
        self._items: Dict[str, Parameter] = {}
        for parameter in parameters:
            self.add(parameter)

    def add(self, parameter: Parameter) -> Parameter:
        existing = self._items.get(parameter.name)
        if existing is not None and (
            existing.value != parameter.value or existing.units != parameter.units
        ):
            raise UnitsError(
                f"{parameter.name}: conflicting declarations "
                f"({existing.value} {existing.units} vs {parameter.value} {parameter.units})"
            )
        self._items[parameter.name] = parameter
        return parameter

    def require(self, name: str, units: str) -> Parameter:
        try:
            parameter = self._items[name]
        except KeyError:
            raise UnitsError(f"{name}: not declared in units registry") from None
        return parameter.expect(units)

    def as_ticks(self, name: str) -> int:
        return self.require(name, TICKS).as_ticks()

    def get(self, name: str) -> Optional[Parameter]:
        return self._items.get(name)

    def to_dict(self) -> Dict[str, Dict[str, object]]:
        return {name: parameter.to_dict() for name, parameter in sorted(self._items.items())}

    def __iter__(self) -> Iterator[Parameter]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Number],
        units_by_name: Optional[Mapping[str, str]] = None,
        source: str = "",
    ) -> "UnitsRegistry":
        """Собрать реестр из отображения; единицы берутся из `EXPECTED_UNITS`, если не заданы."""
        registry = cls()
        for name, value in values.items():
            units = (units_by_name or {}).get(name) or EXPECTED_UNITS.get(name)
            if not units:
                raise UnitsError(f"{name}: units are not declared for this parameter")
            registry.add(Parameter(name=name, value=value, units=units, source=source))
        return registry


def require_units(name: str, value: Number, units: str, source: str = "") -> Parameter:
    """Короткая форма создания параметра с проверкой."""
    return Parameter(name=name, value=value, units=units, source=source)


def split_flags(parameters: Iterable[Parameter]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Разделить параметры на «проверенные» и «помеченные» (для отчёта)."""
    verified: list[str] = []
    flagged: list[str] = []
    for parameter in parameters:
        if parameter.flag:
            flagged.append(f"{parameter.name} ({parameter.flag})")
        else:
            verified.append(parameter.name)
    return tuple(verified), tuple(flagged)


__all__ = [
    "ALLOWED_UNITS",
    "BPS",
    "COUNT",
    "EXPECTED_UNITS",
    "MINUTES",
    "MINUTE_INVERSE",
    "PCT",
    "Parameter",
    "RATIO",
    "RUB",
    "TICKS",
    "UnitsError",
    "UnitsRegistry",
    "require_units",
    "split_flags",
]
