"""Тесты численной сверки первого касания (REFERENCE_ONLY)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from analyzer.research.first_passage import build_curve
from analyzer.research.pde import (
    REFERENCE_ONLY,
    ReferenceError,
    first_passage_probabilities,
)
from analyzer.research.signals import Signal


def test_symmetric_barriers_give_equal_probabilities():
    reference = first_passage_probabilities(
        theta=0.5, sigma=0.4, mu=0.0, tp_ticks=2, sl_ticks=2, tau=20
    )

    assert reference.p_tp == pytest.approx(reference.p_sl, abs=1e-9)
    assert reference.p_tp + reference.p_sl + reference.p_n == pytest.approx(1.0)


def test_positive_anchor_favours_upper_barrier():
    """Возврат к уровню выше входа должен повышать вероятность TP, а не SL.

    Это же ловит ошибку знака в коэффициенте дрейфа: при инвертированном знаке
    вероятности меняются местами.
    """
    reference = first_passage_probabilities(
        theta=0.2, sigma=0.5, mu=2.0, tp_ticks=1, sl_ticks=1, tau=10
    )

    assert reference.p_tp > 0.7
    assert reference.p_sl < 0.3
    assert reference.method == REFERENCE_ONLY


def test_long_horizon_pushes_probabilities_to_one():
    short = first_passage_probabilities(
        theta=0.2, sigma=0.5, mu=0.0, tp_ticks=1, sl_ticks=1, tau=2
    )
    long = first_passage_probabilities(
        theta=0.2, sigma=0.5, mu=0.0, tp_ticks=1, sl_ticks=1, tau=120
    )

    assert short.p_n > 0.3
    assert long.p_n < 0.05


def test_result_stable_under_grid_refinement():
    coarse = first_passage_probabilities(
        theta=0.3, sigma=0.6, mu=0.5, tp_ticks=2, sl_ticks=2, tau=15, subdivision=10
    )
    fine = first_passage_probabilities(
        theta=0.3, sigma=0.6, mu=0.5, tp_ticks=2, sl_ticks=2, tau=15, subdivision=20
    )

    assert coarse.p_tp == pytest.approx(fine.p_tp, abs=5e-3)


def test_reference_rejects_bad_parameters():
    with pytest.raises(ReferenceError):
        first_passage_probabilities(theta=0.0, sigma=0.5, tp_ticks=1, sl_ticks=1, tau=5)
    with pytest.raises(ReferenceError):
        first_passage_probabilities(theta=0.2, sigma=0.0, tp_ticks=1, sl_ticks=1, tau=5)
    with pytest.raises(ReferenceError):
        first_passage_probabilities(theta=0.2, sigma=0.5, tp_ticks=0, sl_ticks=1, tau=5)
    with pytest.raises(ReferenceError):
        first_passage_probabilities(theta=0.2, sigma=0.5, tp_ticks=1, sl_ticks=1, tau=0)
    with pytest.raises(ReferenceError):
        first_passage_probabilities(theta=0.2, sigma=0.5, tp_ticks=1, sl_ticks=1, tau=5,
                                    subdivision=0)


def _ou_minute_bars(*, theta: float, sigma: float, minutes: int, paths: int,
                    seed: int = 7, substeps: int = 60):
    """Минутные бары OU, стартующие из 0: индекс 0 — бар входа, далее 1..minutes.

    `substeps=60` (шаг 1 с): бар видит только свои high/low, и при грубом шаге эмпирика
    систематически недосчитывает касания. Сверка с непрерывным решением требует, чтобы
    разрешение данных не было узким местом.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0 / substeps
    decay = math.exp(-theta * dt)
    shock = sigma * math.sqrt((1.0 - math.exp(-2.0 * theta * dt)) / (2.0 * theta))

    close = np.zeros((minutes, paths))
    high = np.zeros((minutes, paths))
    low = np.zeros((minutes, paths))
    level = np.zeros(paths)
    for minute in range(minutes):
        top = level.copy()
        bottom = level.copy()
        for _ in range(substeps):
            level = level * decay + shock * rng.standard_normal(paths)
            top = np.maximum(top, level)
            bottom = np.minimum(bottom, level)
        close[minute] = level
        high[minute] = top
        low[minute] = bottom
    return close, high, low


def _empirical_probabilities(*, theta: float, sigma: float, tp_ticks: int, sl_ticks: int,
                             tau: int, paths: int = 2000):
    close, high, low = _ou_minute_bars(
        theta=theta, sigma=sigma, minutes=tau, paths=paths
    )
    sessions = []
    for column in range(paths):
        sessions.append(
            {
                "open": np.concatenate(([0.0], close[:, column])),
                "high": np.concatenate(([0.0], high[:, column])),
                "low": np.concatenate(([0.0], low[:, column])),
                "close": np.concatenate(([0.0], close[:, column])),
                "volume": np.ones(tau + 1),
            }
        )
    signals = [Signal(session=index, index=0, direction=1, z=-3.0, price=0.0)
               for index in range(paths)]
    curve = build_curve(
        sessions,
        signals,
        tp_ticks=tp_ticks,
        sl_ticks=sl_ticks,
        cost_ticks=0.0,
        tick_value=1.0,
        taus=(tau,),
        bootstrap=50,
        min_paths=100,
    )
    return curve.points[0]


def test_empirical_first_passage_matches_reference_on_synthetic_ou():
    """Эмпирическая оценка по минутным барам против краевой задачи на той же модели."""
    theta, sigma, tp, sl, tau = 0.2, 0.5, 1, 1, 10
    empirical = _empirical_probabilities(
        theta=theta, sigma=sigma, tp_ticks=tp, sl_ticks=sl, tau=tau
    )
    reference = first_passage_probabilities(
        theta=theta, sigma=sigma, mu=0.0, tp_ticks=tp, sl_ticks=sl, tau=tau
    )

    assert empirical.p_tp == pytest.approx(reference.p_tp, abs=0.04)
    assert empirical.p_sl == pytest.approx(reference.p_sl, abs=0.04)
    assert empirical.p_n == pytest.approx(reference.p_n, abs=0.04)
    assert empirical.p_tp + empirical.p_sl + empirical.p_n == pytest.approx(1.0)
