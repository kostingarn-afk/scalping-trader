"""Метка: вариант и горизонт purging.

Вариант метки фиксируется до отбора моделей (§5 `buy_frame.md`): иначе `h_max` подбирается под
результат, и purging в CPCV перестаёт защищать от утечки.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

FIXED_HORIZON = "fixed_horizon"
TRIPLE_BARRIER = "triple_barrier"
KINDS: Tuple[str, ...] = (FIXED_HORIZON, TRIPLE_BARRIER)


class LabelError(ValueError):
    """Некорректное описание метки."""


@dataclass(frozen=True)
class LabelSpec:
    """Вариант метки и выведенный из него горизонт `h_max` (в барах).

    Для `triple_barrier` `h_max` — максимальный горизонт барьеров: purging в CPCV обязан
    вырезать столько баров, сколько живёт самая длинная метка.
    """

    kind: str
    horizon_bars: int
    barrier_horizons: Tuple[int, ...] = ()
    registry_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise LabelError(f"unknown label kind {self.kind!r}; known: {', '.join(KINDS)}")
        if self.horizon_bars < 1:
            raise LabelError(f"horizon_bars must be at least 1, got {self.horizon_bars}")
        if self.kind == TRIPLE_BARRIER:
            if not self.barrier_horizons:
                raise LabelError("triple_barrier requires at least one barrier horizon")
            if any(horizon < 1 for horizon in self.barrier_horizons):
                raise LabelError("barrier horizons must be at least 1")
        elif self.barrier_horizons:
            raise LabelError(f"{self.kind} does not use barrier horizons")

    @property
    def h_max(self) -> int:
        if self.kind == TRIPLE_BARRIER:
            return max(self.barrier_horizons)
        return self.horizon_bars

    def with_registry_id(self, registry_id: str) -> "LabelSpec":
        if not registry_id:
            raise LabelError("registry id is required")
        return LabelSpec(
            kind=self.kind,
            horizon_bars=self.horizon_bars,
            barrier_horizons=self.barrier_horizons,
            registry_id=registry_id,
        )

    def require_registered(self) -> "LabelSpec":
        """Метка, не записанная в registry, не может участвовать в отборе."""
        if not self.registry_id:
            raise LabelError("label is not registered: freeze it before model selection")
        return self

    def to_mapping(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "horizon_bars": self.horizon_bars,
            "barrier_horizons": list(self.barrier_horizons),
            "h_max": self.h_max,
            "registry_id": self.registry_id,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "LabelSpec":
        horizons = payload.get("barrier_horizons") or ()
        if isinstance(horizons, (str, bytes)) or not isinstance(horizons, Sequence):
            raise LabelError("barrier_horizons must be a sequence of integers")
        return cls(
            kind=str(payload.get("kind", "")),
            horizon_bars=int(payload.get("horizon_bars", 0)),
            barrier_horizons=tuple(int(value) for value in horizons),
            registry_id=payload.get("registry_id"),  # type: ignore[arg-type]
        )


__all__ = [
    "FIXED_HORIZON",
    "KINDS",
    "LabelError",
    "LabelSpec",
    "TRIPLE_BARRIER",
]
