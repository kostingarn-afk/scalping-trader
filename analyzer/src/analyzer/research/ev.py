"""Выбор горизонта `τ*`, входной гейт и `τ_max`.

Объектив — `EV(τ)/√(V(τ)(τ+w))` (`holding_exiting.md` §5); выбирается один вариант, альтернатива
`EV/(τ+w)` допускается только как `REFERENCE_ONLY`. `τ*` ищется на train, вход разрешает
`EV_LCB`, а не `EV`: отбор по верхней оценке запрещён.

Граница сетки — не оптимум. Если максимум приходится на правый край сетки, это `BOUNDARY`
(сетка коротка, нужен более длинный горизонт) либо `SCOPE_VIOLATION`, когда край совпадает
с `τ_cap`: расширять сетку внутри R3 нельзя — только отдельным решением с записью в registry.

Горизонт входа — сам `τ*`, а не «первый τ с положительным `EV_LCB`»: политика заморожена,
и подмена горизонта внутри дня была бы выбором под данные.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Dict, List, Optional, Sequence, Tuple

from .first_passage import EVCurve, EVPoint

OK = "OK"
NO_TAU = "NO_TAU"
BOUNDARY = "BOUNDARY"
SCOPE_VIOLATION = "SCOPE_VIOLATION"

REFERENCE_ONLY = "REFERENCE_ONLY"


@dataclass(frozen=True)
class TauStarSelection:
    """Результат выбора горизонта на train."""

    tau: Optional[int]
    objective: Optional[float]
    ev: Optional[float]
    ev_lcb: Optional[float]
    var: Optional[float]
    waiting_bars: float
    tau_cap: int
    grid: Tuple[int, ...]
    verdict: str
    entry_allowed: bool
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "tau": self.tau,
            "objective": None if self.objective is None else round(self.objective, 8),
            "ev": None if self.ev is None else round(self.ev, 6),
            "ev_lcb": None if self.ev_lcb is None else round(self.ev_lcb, 6),
            "var": None if self.var is None else round(self.var, 6),
            "waiting_bars": None if not isfinite(self.waiting_bars) else round(self.waiting_bars, 3),
            "tau_cap": self.tau_cap,
            "grid": list(self.grid),
            "verdict": self.verdict,
            "entry_allowed": self.entry_allowed,
            "reasons": list(self.reasons),
            "objective_units": "ticks / sqrt(ticks^2 * bars)",
        }


@dataclass(frozen=True)
class EntryDecision:
    """Разрешение входа на исполнении: горизонт, точка кривой и причина."""

    allowed: bool
    tau: Optional[int]
    point: Optional[EVPoint]
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "allowed": self.allowed,
            "tau": self.tau,
            "reason": self.reason,
            "ev_lcb": None if self.point is None else self.point.to_dict()["ev_lcb"],
        }


def objective(point: EVPoint, waiting_bars: float) -> float:
    """`EV/√(V(τ)(τ+w))`; `w` не оценено (`inf`) — считается без ожидания с флагом выше."""
    horizon = waiting_bars if isfinite(waiting_bars) else 0.0
    return point.ev / sqrt(point.var * (point.tau + horizon))


def select_tau(curve: EVCurve, *, waiting_bars: float, tau_cap: int) -> TauStarSelection:
    """Выбрать `τ*` на множестве `τ ≤ τ_cap` и решить, разрешён ли вход."""
    if tau_cap < 1:
        raise ValueError("tau_cap must be at least 1")
    if waiting_bars < 0:
        raise ValueError("waiting_bars must be non-negative")

    grid = tuple(point.tau for point in curve.points)
    usable = curve.usable()
    if not usable:
        return TauStarSelection(
            tau=None,
            objective=None,
            ev=None,
            ev_lcb=None,
            var=None,
            waiting_bars=waiting_bars,
            tau_cap=tau_cap,
            grid=grid,
            verdict=NO_TAU,
            entry_allowed=False,
            reasons=("нет τ с построенной EV и положительной дисперсией",),
        )

    reasons: List[str] = []
    if not isfinite(waiting_bars):
        reasons.append("w не оценено: сигналов меньше двух, объектив считается без ожидания")

    within = [point for point in usable if point.tau <= tau_cap]
    if not within:
        cheapest = min(usable, key=lambda point: point.tau)
        return TauStarSelection(
            tau=None,
            objective=None,
            ev=None,
            ev_lcb=None,
            var=None,
            waiting_bars=waiting_bars,
            tau_cap=tau_cap,
            grid=grid,
            verdict=NO_TAU,
            entry_allowed=False,
            reasons=(
                f"все τ сетки выше τ_cap={tau_cap} (минимальный {cheapest.tau})",
            ),
        )

    best = max(within, key=lambda point: objective(point, waiting_bars))
    best_value = objective(best, waiting_bars)
    outside = [point for point in usable if point.tau > tau_cap]
    beyond_cap = False
    if outside:
        best_outside = max(outside, key=lambda point: objective(point, waiting_bars))
        beyond_cap = objective(best_outside, waiting_bars) > best_value

    verdict = OK
    if beyond_cap:
        verdict = SCOPE_VIOLATION
        reasons.append(
            f"максимум объектива достигается при τ > τ_cap={tau_cap}: "
            "требуется re-specification горизонта, а не расширение сетки"
        )
    elif best.tau == max(grid) and max(grid) >= tau_cap:
        verdict = SCOPE_VIOLATION
        reasons.append(
            f"максимум объектива на границе сетки τ={best.tau} при τ_cap={tau_cap}: "
            "требуется re-specification, а не продление сетки"
        )
    elif best.tau == max(grid):
        verdict = BOUNDARY
        reasons.append(f"максимум объектива на правом краю сетки τ={best.tau}")

    if best.ev_lcb <= 0:
        reasons.append(
            f"EV_LCB(τ*)={best.ev_lcb:.4f} ≤ 0: горизонт выбран, но вход запрещён"
        )
    entry_allowed = verdict != SCOPE_VIOLATION and best.ev_lcb > 0

    return TauStarSelection(
        tau=best.tau,
        objective=best_value,
        ev=best.ev,
        ev_lcb=best.ev_lcb,
        var=best.var,
        waiting_bars=waiting_bars,
        tau_cap=tau_cap,
        grid=grid,
        verdict=verdict,
        entry_allowed=entry_allowed,
        reasons=tuple(reasons),
    )


def entry_decision(
    curve: EVCurve, selection: TauStarSelection, *, tau_max: int
) -> EntryDecision:
    """Вход разрешён только по горизонту `τ*`, если он в пределах `τ_max` и `EV_LCB > 0`."""
    if selection.tau is None:
        return EntryDecision(False, None, None, NO_TAU)
    if selection.verdict == SCOPE_VIOLATION:
        return EntryDecision(False, selection.tau, None, SCOPE_VIOLATION)
    point = next((item for item in curve.points if item.tau == selection.tau), None)
    if point is None or not point.usable:
        return EntryDecision(False, selection.tau, point, NO_TAU)
    if selection.tau > tau_max:
        return EntryDecision(False, selection.tau, point, "TAU_EXCEEDS_MAX")
    if point.ev_lcb <= 0:
        return EntryDecision(False, selection.tau, point, "EV_LCB_NONPOSITIVE")
    return EntryDecision(True, selection.tau, point, OK)


def ladder(
    curve: EVCurve, *, waiting_bars: float, tau_caps: Sequence[int]
) -> List[Dict[str, object]]:
    """Лестница чувствительности по `τ_cap`: тот же объектив, разные пределы."""
    rows = []
    for cap in tau_caps:
        selection = select_tau(curve, waiting_bars=waiting_bars, tau_cap=cap)
        rows.append({"tau_cap": cap, **selection.to_dict()})
    return rows


# --- верхние границы горизонта ---------------------------------------------

COMPONENT_OK = "OK"
COMPONENT_REFERENCE = "REFERENCE_ONLY"
COMPONENT_MISSING = "MISSING"

#: Имена компонент `τ_max` в порядке объявления (`holding_exiting.md` §6).
TAU_COMPONENTS: Tuple[str, ...] = (
    "edge",
    "regime",
    "mtf",
    "model",
    "liq",
    "session",
    "cap",
)
#: Компоненты, которые округляются вверх; `τ_max` — вниз, кратным Δt.
ROUND_UP_COMPONENTS = frozenset({"edge", "regime", "mtf", "liq"})


@dataclass(frozen=True)
class TauComponent:
    """Один компонент `τ_max`: значение в минутах и статус."""

    name: str
    value: Optional[int]
    status: str
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "component": self.name,
            "value": self.value,
            "status": self.status,
            "note": self.note,
            "rounding": "up" if self.name in ROUND_UP_COMPONENTS else "down",
        }


@dataclass(frozen=True)
class TauMax:
    """Итог: минимум компонент, полнота набора и причины ограничений."""

    value: Optional[int]
    components: Tuple[TauComponent, ...]
    complete: bool
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "value": self.value,
            "complete": self.complete,
            "components": [item.to_dict() for item in self.components],
            "reasons": list(self.reasons),
            "units": {"value": "minutes"},
        }


def _round_up(value: float, step: int) -> int:
    return int(math.ceil(value / step)) * step


def _round_down(value: float, step: int) -> int:
    return int(math.floor(value / step)) * step


def tau_max_components(
    curve: EVCurve,
    *,
    tau_model: int,
    tau_liq: int,
    tau_session: int,
    tau_cap: int,
    tau_regime: Optional[int] = None,
    tau_mtf: Optional[int] = None,
    regime_status: str = COMPONENT_MISSING,
    step: int = 1,
) -> TauMax:
    """Собрать `τ_max = min(τ_edge, τ_regime, τ_mtf, τ_model, τ_liq, τ_session, τ_cap)`.

    Отсутствующий компонент не подменяется единицей и не выбрасывается молча: он помечается
    `REFERENCE_ONLY`/`MISSING`, попадает в `reasons`, а `complete` становится `False`.
    `τ_edge` — единственный компонент, отсутствие которого означает «входа нет»: если нет τ
    с `EV_LCB > 0`, минимум по остальным не имеет смысла.
    """
    if step < 1:
        raise ValueError("step must be at least 1")
    if tau_model < 1 or tau_liq < 1 or tau_cap < 1:
        raise ValueError("tau_model, tau_liq and tau_cap must be at least 1")
    if tau_session < 0:
        raise ValueError("tau_session must be non-negative")

    positive = [point for point in curve.positive()]
    components: List[TauComponent] = []
    reasons: List[str] = []

    if positive:
        edge_value = _round_up(max(point.tau for point in positive), step)
        components.append(TauComponent("edge", edge_value, COMPONENT_OK))
    else:
        components.append(
            TauComponent("edge", None, COMPONENT_MISSING, "нет τ с EV_LCB > 0")
        )
        reasons.append("τ_edge не определён: нет τ с EV_LCB > 0 — вход запрещён")

    if tau_regime is None:
        components.append(
            TauComponent("regime", None, regime_status, "режимная модель не дала горизонт")
        )
        reasons.append("τ_regime отсутствует: режимная модель не идентифицирована")
    else:
        components.append(TauComponent("regime", _round_up(tau_regime, step), COMPONENT_OK))

    if tau_mtf is None:
        components.append(
            TauComponent("mtf", None, regime_status, "согласованность 10m не оценена")
        )
    else:
        components.append(TauComponent("mtf", _round_up(tau_mtf, step), COMPONENT_OK))

    components.append(TauComponent("model", _round_up(tau_model, step), COMPONENT_OK))
    components.append(TauComponent("liq", _round_up(tau_liq, step), COMPONENT_OK))
    components.append(TauComponent("session", tau_session, COMPONENT_OK))
    components.append(TauComponent("cap", tau_cap, COMPONENT_OK))

    limited = [item for item in components if item.value is not None]
    if not any(item.name == "edge" and item.value is not None for item in components):
        value = None
    else:
        value = _round_down(min(int(item.value) for item in limited), step)  # type: ignore[arg-type]
        if value <= 0:
            value = 0
            reasons.append("τ_max = 0: хотя бы один компонент не оставляет горизонта")

    complete = all(item.status != COMPONENT_MISSING for item in components)
    return TauMax(
        value=value,
        components=tuple(components),
        complete=complete,
        reasons=tuple(reasons),
    )


__all__ = [
    "BOUNDARY",
    "COMPONENT_MISSING",
    "COMPONENT_OK",
    "COMPONENT_REFERENCE",
    "EntryDecision",
    "NO_TAU",
    "OK",
    "ROUND_UP_COMPONENTS",
    "SCOPE_VIOLATION",
    "TAU_COMPONENTS",
    "TauComponent",
    "TauMax",
    "TauStarSelection",
    "entry_decision",
    "ladder",
    "objective",
    "select_tau",
    "tau_max_components",
]
