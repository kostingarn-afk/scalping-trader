"""Кластеризация сделок: симуляция и оценка Hawkes — шаг 1.5 `to_do.md` §4.1.

Модель потока: `λ(t) = μ + Σ α·exp(−β(t − t_i))`, branching ratio `n = α/β`. При `n → 1`
события идут лавиной, и mean reversion становится убыточным (`docs/mat_analyze.md` §2).

Оценка — MLE экспоненциального ядра: параметризация `log μ`, `logit n`, `log β` держит `n`
в (0, 1) без ограничений оптимизатора. Симуляция — thinning Огаты; `n = 0` даёт Пуассон.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy.optimize import minimize

# Меньше этого числа событий MLE нестабильна: оценка не возвращается.
MIN_EVENTS = 50


@dataclass(frozen=True)
class HawkesParams:
    mu: float
    beta: float
    branching: float


def simulate_hawkes(
    mu: float,
    branching: float,
    beta: float,
    duration: float,
    seed: int = 0,
) -> np.ndarray:
    """Смоделировать поток событий на [0, duration) методом thinning.

    `branching=0` даёт пуассоновский поток с интенсивностью `mu`.
    """
    if mu <= 0 or beta <= 0 or duration <= 0:
        raise ValueError("mu, beta and duration must be positive")
    if not 0.0 <= branching < 1.0:
        raise ValueError("branching must be in [0, 1)")

    rng = np.random.default_rng(seed)
    alpha = branching * beta
    events: list[float] = []
    time = 0.0
    excitation = 0.0

    while True:
        intensity = mu + alpha * excitation
        step = rng.exponential(1.0 / intensity)
        candidate = time + step
        if candidate >= duration:
            break
        decayed = excitation * math.exp(-beta * step)
        intensity_after = mu + alpha * decayed
        # Между событиями интенсивность только убывает, поэтому вероятность приёма ≤ 1.
        if rng.random() <= intensity_after / intensity:
            events.append(candidate)
            excitation = decayed + 1.0
        else:
            excitation = decayed
        time = candidate

    return np.asarray(events, dtype=float)


def _neg_log_likelihood(theta: np.ndarray, times: np.ndarray, span: float) -> float:
    mu = math.exp(float(theta[0]))
    branching = 1.0 / (1.0 + math.exp(-float(theta[1])))
    beta = math.exp(float(theta[2]))
    alpha = branching * beta

    excitation = 0.0
    previous = 0.0
    log_likelihood = math.log(mu)
    for current in times[1:]:
        excitation = math.exp(-beta * (current - previous)) * (1.0 + excitation)
        intensity = mu + alpha * excitation
        if not math.isfinite(intensity) or intensity <= 0:
            return 1e12
        log_likelihood += math.log(float(intensity))
        previous = float(current)

    compensator = mu * span + branching * float(
        np.sum(1.0 - np.exp(-beta * (span - times)))
    )
    value = log_likelihood - compensator
    return -value if math.isfinite(value) else 1e12


def estimate_hawkes(
    times: Sequence[float], min_events: int = MIN_EVENTS
) -> Optional[HawkesParams]:
    """Оценить branching ratio по временам событий. None — данных мало или оценка не сошлась."""
    values = np.sort(np.asarray(times, dtype=float))
    if values.size < min_events or not np.all(np.isfinite(values)):
        return None
    values = values - values[0]
    span = float(values[-1])
    if span <= 0:
        return None

    mean_gap = span / max(values.size - 1, 1)
    rate = values.size / span
    start = np.array(
        [math.log(max(rate * 0.5, 1e-12)), 0.0, math.log(max(1.0 / mean_gap, 1e-12))]
    )
    result = minimize(
        _neg_log_likelihood,
        start,
        args=(values, span),
        method="L-BFGS-B",
        options={"maxiter": 300},
    )
    if not result.success:
        return None

    mu = math.exp(float(result.x[0]))
    branching = 1.0 / (1.0 + math.exp(-float(result.x[1])))
    beta = math.exp(float(result.x[2]))
    if mu <= 0 or beta <= 0 or not 0.0 <= branching < 1.0:
        return None
    return HawkesParams(mu=mu, beta=beta, branching=branching)
