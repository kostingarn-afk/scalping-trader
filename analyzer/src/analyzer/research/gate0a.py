"""Gate 0a: `φ*(τ) = Cost / σ_τ` — хватает ли движения окупить издержки.

Проверка арифметическая: она не зависит от модели, барьеров и θ, поэтому выполняется сразу
после заморозки политики (§7.1 `buy_frame.md`). `φ* ≥ 1` при всех τ означает, что типичное
движение не покрывает круг издержек и скальпинг невозможен никакой моделью.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .artifacts import build_artifact as build_envelope
from .costs import CostEstimate
from .units import RATIO, TICKS

DEFAULT_TAUS: Tuple[int, ...] = (1, 3, 5, 10, 15, 30)

PASS = "PASS"
IMPOSSIBLE = "IMPOSSIBLE"
NO_DATA = "NO_DATA"


class GateError(ValueError):
    """Gate 0a нельзя посчитать по заданным входам."""


@dataclass(frozen=True)
class PhiStarRow:
    tau: int
    sigma_tau_ticks: float
    cost_ticks: float
    phi_star: float
    status: str = "OK"

    def to_dict(self) -> Dict[str, object]:
        return {
            "tau": self.tau,
            "sigma_tau_ticks": round(self.sigma_tau_ticks, 6),
            "cost_ticks": round(self.cost_ticks, 6),
            "phi_star": round(self.phi_star, 6),
            "status": self.status,
        }


@dataclass(frozen=True)
class PhiStarTable:
    """Результат Gate 0a по одному инструменту."""

    instrument: str
    price: float
    tick: float
    rows: Tuple[PhiStarRow, ...]
    cost: CostEstimate
    stress_multiplier: float = 1.0

    @property
    def usable_rows(self) -> Tuple[PhiStarRow, ...]:
        return tuple(row for row in self.rows if row.status == "OK")

    @property
    def min_phi(self) -> Optional[float]:
        rows = self.usable_rows
        if not rows:
            return None
        return min(row.phi_star for row in rows)

    @property
    def best_tau(self) -> Optional[int]:
        rows = self.usable_rows
        if not rows:
            return None
        return min(rows, key=lambda row: row.phi_star).tau

    @property
    def verdict(self) -> str:
        smallest = self.min_phi
        if smallest is None:
            return NO_DATA
        return PASS if smallest < 1.0 else IMPOSSIBLE

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "price": self.price,
            "tick": self.tick,
            "stress_multiplier": self.stress_multiplier,
            "verdict": self.verdict,
            "best_tau": self.best_tau,
            "min_phi": None if self.min_phi is None else round(self.min_phi, 6),
            "cost": self.cost.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
        }

    def format_markdown(self) -> str:
        lines = [f"### {self.instrument}: {self.verdict}", ""]
        stress = "" if self.stress_multiplier == 1.0 else f" (стресс ×{self.stress_multiplier:g})"
        lines.append(
            f"Цена {self.price:g}, шаг {self.tick:g}, круг {self.cost.round_trip_ticks:.2f} тиков"
            f"{stress}; лучший τ: {self.best_tau}."
        )
        lines.append("")
        lines.append("| τ | σ_τ, тиков | φ* | Статус |")
        lines.append("|---|---|---|---|")
        for row in self.rows:
            sigma = f"{row.sigma_tau_ticks:.2f}" if row.status == "OK" else "—"
            phi = f"{row.phi_star:.3f}" if row.status == "OK" else "—"
            lines.append(f"| {row.tau} | {sigma} | {phi} | {row.status} |")
        return "\n".join(lines)


def sigma_tau_ticks(sigma_1_log: float, tau: int, vr: float, price: float, tick: float) -> float:
    """`σ_τ` в тиках: лог-волатильность минуты → тики через цену и шаг."""
    if sigma_1_log <= 0:
        raise GateError("sigma_1 must be positive")
    if tau < 1:
        raise GateError("tau must be at least 1")
    if vr <= 0:
        raise GateError(f"VR({tau}) must be positive, got {vr}")
    if price <= 0 or tick <= 0:
        raise GateError("price and tick must be positive")
    return sigma_1_log * sqrt(tau * vr) * price / tick


def build_phi_star_table(
    instrument: str,
    price: float,
    tick: float,
    sigma_1_log: float,
    vr_by_tau: Mapping[int, Optional[float]],
    cost: CostEstimate,
    taus: Sequence[int] = DEFAULT_TAUS,
    stress_multiplier: Optional[float] = None,
) -> PhiStarTable:
    """Посчитать `φ*` на сетке τ.

    Горизонты, для которых `VR` нет (мало данных), попадают в таблицу со статусом `NO_DATA`:
    пропустить их молча значило бы скрыть, что часть сетки не проверена.
    """
    if not taus:
        raise GateError("at least one tau is required")
    multiplier = cost.stress_multiplier if stress_multiplier is None else stress_multiplier
    cost_ticks = cost.round_trip_ticks * (multiplier / cost.stress_multiplier)

    rows = []
    for tau in taus:
        vr = vr_by_tau.get(tau)
        if vr is None or vr <= 0:
            rows.append(PhiStarRow(tau=tau, sigma_tau_ticks=0.0, cost_ticks=cost_ticks,
                                   phi_star=0.0, status=NO_DATA))
            continue
        sigma = sigma_tau_ticks(sigma_1_log, tau, vr, price, tick)
        rows.append(
            PhiStarRow(
                tau=tau,
                sigma_tau_ticks=sigma,
                cost_ticks=cost_ticks,
                phi_star=cost_ticks / sigma,
            )
        )
    return PhiStarTable(
        instrument=instrument,
        price=price,
        tick=tick,
        rows=tuple(rows),
        cost=cost,
        stress_multiplier=multiplier,
    )


def aggregate_verdict(tables: Iterable[PhiStarTable]) -> str:
    """Gate 0a пройден, если хотя бы один инструмент прошёл."""
    results = list(tables)
    if not results:
        raise GateError("no instruments to evaluate")
    return PASS if any(table.verdict == PASS for table in results) else IMPOSSIBLE


def build_artifact(
    tables: Sequence[PhiStarTable],
    *,
    trial_id: str,
    policy: Mapping[str, object],
    flags: Mapping[str, str],
) -> Dict[str, object]:
    """Артефакт `phi_star_table` обязательным конвертом."""
    verdict = aggregate_verdict(tables)
    return build_envelope(
        name="phi_star_table",
        units={"cost": TICKS, "sigma_tau": TICKS, "phi_star": RATIO},
        n={table.instrument: len(table.usable_rows) for table in tables},
        method=(
            "σ_1 — Parkinson по барам; VR(τ) по неперекрывающимся окнам; "
            "Cost — круг в тиках (2 × комиссия + спред); φ*(τ) = Cost / σ_τ"
        ),
        flags=flags,
        payload={
            "trial_id": trial_id,
            "policy": dict(policy),
            "verdict": verdict,
            "instruments": [table.to_dict() for table in tables],
        },
    )


def format_markdown(tables: Sequence[PhiStarTable]) -> str:
    verdict = aggregate_verdict(tables)
    lines = ["# Gate 0a — φ*(τ) = Cost / σ_τ", ""]
    lines.append(f"Вердикт: **{verdict}** (проход — `φ* < 1` хотя бы при одном τ).")
    lines.append("")
    lines.append("| Инструмент | Вердикт | Мин. φ* | Лучший τ | Круг, тиков |")
    lines.append("|---|---|---|---|---|")
    for table in tables:
        smallest = "—" if table.min_phi is None else f"{table.min_phi:.3f}"
        lines.append(
            f"| {table.instrument} | {table.verdict} | {smallest} | {table.best_tau} "
            f"| {table.cost.round_trip_ticks:.2f} |"
        )
    lines.append("")
    for table in tables:
        lines.append(table.format_markdown())
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_TAUS",
    "GateError",
    "IMPOSSIBLE",
    "NO_DATA",
    "PASS",
    "PhiStarRow",
    "PhiStarTable",
    "aggregate_verdict",
    "build_artifact",
    "build_phi_star_table",
    "format_markdown",
    "sigma_tau_ticks",
]
