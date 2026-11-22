"""Тесты правила удержания, полосы валидности и таблицы триггеров (волна 3 R3)."""
from __future__ import annotations

import numpy as np
import pytest

from analyzer.research.holding import (
    CLOSING_TRIGGERS,
    CUSUM_THRESHOLD,
    EXIT_TRIGGERS,
    KIND_COST,
    KIND_HARD,
    KIND_REDUCTION,
    REDUCING_TRIGGERS,
    HoldingError,
    RemainingMoments,
    a0_bounds,
    a_coordinate,
    close_cost_drift,
    cusum_breaker,
    hold_decision,
    online_band,
    remaining_moments,
)


def _arrays(closes, *, highs=None, lows=None):
    close = np.asarray(closes, dtype=float)
    return {
        "ts": np.asarray([1_700_000_000 + 60 * i for i in range(len(close))], dtype=np.int64),
        "open": close.copy(),
        "high": close.copy() if highs is None else np.asarray(highs, dtype=float),
        "low": close.copy() if lows is None else np.asarray(lows, dtype=float),
        "close": close,
        "volume": np.ones(len(close)),
    }


def _moments(m: float, s: float) -> RemainingMoments:
    return RemainingMoments(
        m_rem=m, s_rem=s, p_tp=0.5, p_sl=0.3, p_n=0.2, paths=100,
        to_tp=4, to_sl=2, tau_rem=10, stride=5,
    )


# --- координата A ---------------------------------------------------------


def test_a_coordinate_sign_follows_edge():
    """Ниже якоря для лонга — edge есть; выше — edge против позиции."""
    assert a_coordinate(price=99.0, anchor=100.0, sigma_p=2.0, direction=1) == pytest.approx(0.5)
    assert a_coordinate(price=101.0, anchor=100.0, sigma_p=2.0, direction=1) == pytest.approx(-0.5)
    assert a_coordinate(price=101.0, anchor=100.0, sigma_p=2.0, direction=-1) == pytest.approx(0.5)


def test_a_coordinate_rejects_bad_sigma():
    with pytest.raises(HoldingError):
        a_coordinate(price=100.0, anchor=100.0, sigma_p=0.0, direction=1)
    with pytest.raises(HoldingError):
        a_coordinate(price=100.0, anchor=100.0, sigma_p=1.0, direction=0)


def test_a0_lower_bound_is_conservative():
    point, lower = a0_bounds(
        price=98.0, anchor=100.0, sigma_p=2.0, sigma_p_lcb=1.8, sigma_p_ucb=2.2, direction=1
    )

    assert point == pytest.approx(1.0)
    assert lower < point


# --- онлайн-полоса --------------------------------------------------------


def test_band_falls_with_time():
    early = online_band(a0_lcb=2.0, theta_lcb=0.01, theta_ucb=0.02, minutes=1,
                        tau_max=60, alpha_total=0.05)
    late = online_band(a0_lcb=2.0, theta_lcb=0.01, theta_ucb=0.02, minutes=30,
                       tau_max=60, alpha_total=0.05)

    assert late.lower < early.lower
    assert late.mean < early.mean


def test_more_checks_lower_the_band():
    """Бонферрони: больше проверок — жёстче z — ниже полоса."""
    few = online_band(a0_lcb=2.0, theta_lcb=0.01, theta_ucb=0.02, minutes=10,
                      tau_max=10, alpha_total=0.05)
    many = online_band(a0_lcb=2.0, theta_lcb=0.01, theta_ucb=0.02, minutes=10,
                       tau_max=60, alpha_total=0.05)

    assert many.checks == 60
    assert few.checks == 10
    assert many.z_value > few.z_value
    assert many.lower < few.lower


def test_single_check_matches_declared_alpha():
    band = online_band(a0_lcb=1.0, theta_lcb=0.02, theta_ucb=0.02, minutes=0,
                       tau_max=1, alpha_total=0.05)

    assert band.checks == 1
    assert band.alpha_check == pytest.approx(0.05)
    assert band.z_value == pytest.approx(1.6449, abs=1e-3)
    assert band.lower == pytest.approx(band.mean)


def test_band_rejects_bad_parameters():
    with pytest.raises(HoldingError):
        online_band(a0_lcb=1.0, theta_lcb=0.0, theta_ucb=0.02, minutes=1, tau_max=10,
                    alpha_total=0.05)
    with pytest.raises(HoldingError):
        online_band(a0_lcb=1.0, theta_lcb=0.03, theta_ucb=0.02, minutes=1, tau_max=10,
                    alpha_total=0.05)
    with pytest.raises(HoldingError):
        online_band(a0_lcb=1.0, theta_lcb=0.01, theta_ucb=0.02, minutes=1, tau_max=10,
                    alpha_total=1.5)


# --- остаточные моменты ---------------------------------------------------


def test_remaining_moments_recover_symmetric_outcomes():
    """Все бары упираются в TP: m_rem = to_tp, s_rem = 0, p_tp = 1."""
    session = _arrays([100.0] * 40, highs=[104.0] * 40, lows=[99.0] * 40)

    moments = remaining_moments([session], to_tp=4, to_sl=2, tau_rem=5, tick_value=1.0,
                                stride=1, min_paths=10)

    assert moments is not None
    assert moments.p_tp == pytest.approx(1.0)
    assert moments.m_rem == pytest.approx(4.0)
    assert moments.s_rem == pytest.approx(0.0)


def test_remaining_moments_cover_both_outcomes():
    session = _arrays(
        [100.0, 104.0, 98.0, 104.0, 98.0, 100.0],
        highs=[100.0, 104.0, 100.0, 104.0, 100.0, 100.0],
        lows=[100.0, 100.0, 98.0, 100.0, 98.0, 100.0],
    )

    moments = remaining_moments([session], to_tp=4, to_sl=2, tau_rem=1, tick_value=1.0,
                                stride=1, min_paths=2)

    assert moments is not None
    assert moments.p_tp > 0
    assert moments.p_sl > 0
    assert moments.s_rem > 0


def test_remaining_moments_return_none_below_minimum():
    session = _arrays([100.0] * 10)

    assert remaining_moments([session], to_tp=4, to_sl=2, tau_rem=5, tick_value=1.0,
                             stride=1, min_paths=50) is None


def test_remaining_moments_validate_inputs():
    session = _arrays([100.0] * 10)

    with pytest.raises(HoldingError):
        remaining_moments([session], to_tp=0, to_sl=2, tau_rem=5, tick_value=1.0)
    with pytest.raises(HoldingError):
        remaining_moments([session], to_tp=1, to_sl=2, tau_rem=0, tick_value=1.0)
    with pytest.raises(HoldingError):
        remaining_moments([session], to_tp=1, to_sl=2, tau_rem=3, tick_value=1.0, stride=0)


# --- правило удержания ----------------------------------------------------


def test_hold_when_remaining_edge_beats_risk_and_cost():
    decision = hold_decision(a_t=1.5, to_tp=4, to_sl=2, tau_rem=10, lambda_risk=0.7,
                             cost_drift=0.0, moments=_moments(2.0, 1.0))

    assert decision.hold is True
    assert decision.reason == "HOLD"
    assert decision.risk_term == pytest.approx(0.7)


def test_close_when_risk_term_swallows_edge():
    decision = hold_decision(a_t=0.4, to_tp=4, to_sl=2, tau_rem=10, lambda_risk=2.0,
                             cost_drift=0.0, moments=_moments(0.5, 1.0))

    assert decision.hold is False
    assert decision.reason == "ANCHOR_EDGE"


def test_close_when_close_cost_drift_exceeds_edge():
    decision = hold_decision(a_t=1.0, to_tp=4, to_sl=2, tau_rem=10, lambda_risk=0.5,
                             cost_drift=3.0, moments=_moments(2.0, 1.0))

    assert decision.hold is False
    assert decision.reason == "CLOSE_COST_DRIFT"


def test_overshoot_closes_before_any_estimate():
    decision = hold_decision(a_t=0.0, to_tp=4, to_sl=2, tau_rem=10, lambda_risk=0.5,
                             cost_drift=0.0, moments=None)

    assert decision.hold is False
    assert decision.reason == "OVERSHOOT"


def test_missing_estimate_closes_position():
    decision = hold_decision(a_t=1.0, to_tp=4, to_sl=2, tau_rem=10, lambda_risk=0.5,
                             cost_drift=0.0, moments=None)

    assert decision.hold is False
    assert decision.reason == "ESTIMATE_MISSING"


def test_hold_decision_serialises():
    decision = hold_decision(a_t=1.5, to_tp=4, to_sl=2, tau_rem=10, lambda_risk=0.7,
                             cost_drift=0.0, moments=_moments(2.0, 1.0))

    payload = decision.to_dict()

    assert payload["hold"] is True
    assert payload["moments"]["paths"] == 100


# --- издержки закрытия и CUSUM -------------------------------------------


def test_close_cost_drift_follows_spread_ratio():
    assert close_cost_drift(c_now=1.0, sigma_now=1.0, sigma_later=1.0,
                            spread_now=1.0, spread_later=2.0) == pytest.approx(1.0)
    assert close_cost_drift(c_now=1.0, sigma_now=1.0, sigma_later=1.0,
                            spread_now=1.0, spread_later=1.0) == pytest.approx(0.0)


def test_close_cost_drift_uses_volatility_without_spread():
    drift = close_cost_drift(c_now=1.0, sigma_now=1.0, sigma_later=3.0,
                             spread_now=0.0, spread_later=0.0)
    assert drift == pytest.approx(2.0)


def test_close_cost_drift_rejects_bad_inputs():
    with pytest.raises(HoldingError):
        close_cost_drift(c_now=-1.0, sigma_now=1.0, sigma_later=1.0,
                         spread_now=1.0, spread_later=1.0)
    with pytest.raises(HoldingError):
        close_cost_drift(c_now=1.0, sigma_now=0.0, sigma_later=1.0,
                         spread_now=1.0, spread_later=1.0)


def test_cusum_of_squares_detects_variance_jump_not_mean_shift():
    """Тест ловит смену дисперсии: постоянный дрейф не должен закрывать позицию."""
    rng = np.random.default_rng(5)
    drift_only = 0.5 + rng.normal(0.0, 0.01, 200)
    variance_jump = np.concatenate(
        [rng.normal(0.0, 0.001, 100), rng.normal(0.0, 0.02, 100)]
    )

    assert cusum_breaker(drift_only) is False
    assert cusum_breaker(variance_jump) is True


def test_cusum_requires_enough_points():
    assert cusum_breaker([0.1] * 5) is False
    assert cusum_breaker([0.0] * 50) is False


# --- таблица триггеров ---------------------------------------------------


def test_table_has_eleven_triggers_with_declared_kinds():
    assert len(EXIT_TRIGGERS) == 11
    kinds = {spec.name: spec.kind for spec in EXIT_TRIGGERS}
    assert kinds["tp_sl"] == KIND_HARD
    assert kinds["model_invalid"] == KIND_HARD
    assert kinds["close_cost_drift"] == KIND_COST
    assert kinds["vol_spike"] == KIND_REDUCTION
    assert kinds["anchor_edge"] != KIND_HARD


def test_hard_and_reducing_sets_partition_the_table():
    hard = {spec.name for spec in EXIT_TRIGGERS if spec.kind == KIND_HARD}
    reducing = {spec.name for spec in EXIT_TRIGGERS if spec.kind == KIND_REDUCTION}

    assert reducing == set(REDUCING_TRIGGERS)
    assert hard.issubset(set(CLOSING_TRIGGERS))
    assert not (hard & reducing)
    assert CUSUM_THRESHOLD > 0
