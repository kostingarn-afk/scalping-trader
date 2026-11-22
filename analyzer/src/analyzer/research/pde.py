"""Численное решение задачи о первом касании для OU — `REFERENCE_ONLY`.

Уравнение (`holding_exiting.md` §4, второй протокол):
`∂u/∂t = θ(μ − y)·∂u/∂y + ½σ²·∂²u/∂y²` с `u(0, ·) = 0` внутри барьеров, `u = 1` на целевом
барьере, `u = 0` на противоположном. `u(τ, y₀)` — вероятность первого касания нужного барьера.

Зачем: эмпирическая оценка по минутным барам зависит от разрешения данных (бар видит только
свой high/low) и от конвенции «SL первым». Численное решение даёт независимую точку сверки на
синтетике. В операционные артефакты оно не идёт: применимость OU на горизонте `τ ≲ t½` не
проверена, и подменять ею данные нельзя.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
from scipy.linalg import solve_banded

REFERENCE_ONLY = "REFERENCE_ONLY"

#: Узлов сетки на один тик: `1/m` — разрешение по цене.
DEFAULT_SUBDIVISION = 20
#: Шагов по времени на минуту. При шаге 1 мин схема с центральной разностью по дрейфу даёт
#: число Куранта порядка единицы и осциллирует, поэтому шаг дробится.
STEPS_PER_MINUTE = 10


class ReferenceError(ValueError):
    """Краевую задачу нельзя решить на заданных параметрах."""


@dataclass(frozen=True)
class FirstPassageReference:
    """Вероятности касания из краевой задачи и условия, при которых они получены."""

    p_tp: float
    p_sl: float
    p_n: float
    theta: float
    sigma: float
    mu: float
    tp_ticks: float
    sl_ticks: float
    tau: int
    nodes: int
    method: str = REFERENCE_ONLY

    @property
    def barrier_ev(self) -> float:
        """`a·p_TP − b·p_SL`: вклад барьеров, без слагаемого `ΔP·1_N` и без издержек."""
        return self.tp_ticks * self.p_tp - self.sl_ticks * self.p_sl

    def to_dict(self) -> Dict[str, object]:
        return {
            "p_tp": round(self.p_tp, 6),
            "p_sl": round(self.p_sl, 6),
            "p_n": round(self.p_n, 6),
            "theta": self.theta,
            "sigma": self.sigma,
            "mu": self.mu,
            "tp_ticks": self.tp_ticks,
            "sl_ticks": self.sl_ticks,
            "tau": self.tau,
            "nodes": self.nodes,
            "method": self.method,
            "units": {"theta": "min^-1", "sigma": "ticks/sqrt(min)", "mu": "ticks"},
        }


def first_passage_probabilities(
    *,
    theta: float,
    sigma: float,
    mu: float = 0.0,
    tp_ticks: float,
    sl_ticks: float,
    tau: int,
    subdivision: int = DEFAULT_SUBDIVISION,
    steps: int = 0,
) -> FirstPassageReference:
    """Решить краевую задачу неявной схемой на равномерной сетке.

    `mu` — уровень возврата в тиках относительно цены входа; `sigma` — в тиках/√мин.
    `steps` — число шагов по времени (по умолчанию `tau·10`).
    """
    if theta <= 0:
        raise ReferenceError("theta must be positive")
    if not np.isfinite(sigma) or sigma <= 0:
        raise ReferenceError("sigma must be positive and finite")
    if tp_ticks <= 0 or sl_ticks <= 0:
        raise ReferenceError("barriers must be positive")
    if tau < 1:
        raise ReferenceError("tau must be at least 1 minute")
    if subdivision < 1:
        raise ReferenceError("subdivision must be at least 1")
    if steps < 0:
        raise ReferenceError("steps must be non-negative")
    if not np.isfinite(mu):
        raise ReferenceError("mu must be finite")

    sub = float(subdivision)
    h = 1.0 / sub
    lower_nodes = int(round(sl_ticks * sub))
    upper_nodes = int(round(tp_ticks * sub))
    if lower_nodes < 1 or upper_nodes < 1:
        raise ReferenceError("barriers must be at least one grid step")
    grid = -sl_ticks + h * np.arange(lower_nodes + upper_nodes + 1)
    origin = lower_nodes  # 0 — всегда узел сетки

    interior = len(grid) - 2
    drift = theta * (mu - grid[1:-1])
    diffusion = 0.5 * sigma**2
    total_steps = steps or tau * STEPS_PER_MINUTE
    dt = tau / float(total_steps)

    # Неявная схема: (I − dt·L)u^{n+1} = u^n, L — генератор OU с центральными разностями.
    upper = -dt * (diffusion / h**2 + drift / (2.0 * h))
    diagonal = 1.0 + dt * (2.0 * diffusion / h**2)
    lower = -dt * (diffusion / h**2 - drift / (2.0 * h))

    banded = np.zeros((3, interior))
    banded[0, 1:] = upper[:-1]
    banded[1, :] = diagonal
    banded[2, :-1] = lower[1:]

    p_tp, p_sl = _solve_two_rhs(
        banded, upper[-1], lower[0], total_steps, interior, origin
    )
    p_tp = min(1.0, max(0.0, p_tp))
    p_sl = min(1.0, max(0.0, p_sl))
    return FirstPassageReference(
        p_tp=p_tp,
        p_sl=p_sl,
        p_n=max(0.0, 1.0 - p_tp - p_sl),
        theta=float(theta),
        sigma=float(sigma),
        mu=float(mu),
        tp_ticks=float(tp_ticks),
        sl_ticks=float(sl_ticks),
        tau=int(tau),
        nodes=len(grid),
    )


def _solve_two_rhs(
    banded: np.ndarray,
    upper_edge: float,
    lower_edge: float,
    steps: int,
    interior: int,
    origin: int,
) -> Tuple[float, float]:
    """Прогон по времени сразу для двух правых частей: `u_TP` и `u_SL`."""
    tp = np.zeros(interior, dtype=float)
    sl = np.zeros(interior, dtype=float)
    for _ in range(steps):
        rhs_tp = tp.copy()
        rhs_sl = sl.copy()
        rhs_tp[-1] -= upper_edge  # TP на верхнем узле: u = 1
        rhs_sl[0] -= lower_edge  # SL на нижнем узле: u = 1
        tp = solve_banded((1, 1), banded, rhs_tp)
        sl = solve_banded((1, 1), banded, rhs_sl)
    index = origin - 1  # внутренний массив соответствует узлам 1..n-2
    return float(tp[index]), float(sl[index])


__all__ = [
    "DEFAULT_SUBDIVISION",
    "FirstPassageReference",
    "REFERENCE_ONLY",
    "STEPS_PER_MINUTE",
    "ReferenceError",
    "first_passage_probabilities",
]
