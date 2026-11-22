"""Тесты `τ_max` из компонент (волна 2 R3)."""
from __future__ import annotations

import pytest

from analyzer.research.ev import (
    COMPONENT_MISSING,
    EVCurve,
    EVPoint,
    tau_max_components,
)


def _point(tau: int, lcb: float) -> EVPoint:
    return EVPoint(
        tau=tau,
        p_tp=0.4,
        p_sl=0.4,
        p_n=0.2,
        ev=lcb + 0.1,
        var=1.0,
        ev_lcb=lcb,
        ev_ucb=lcb + 0.2,
        paths=100,
        clusters=10,
    )


def _curve(*points: EVPoint) -> EVCurve:
    return EVCurve(
        tp_ticks=4,
        sl_ticks=2,
        cost_ticks=1.0,
        protocol="SL_FIRST_EMPIRICAL_1M",
        block_rule="test",
        bootstrap=20,
        min_paths=2,
        min_cluster_bars=600,
        points=points,
    )


def test_tau_max_is_minimum_of_components():
    curve = _curve(_point(1, -0.1), _point(30, 0.2), _point(60, 0.1))

    result = tau_max_components(
        curve, tau_model=30, tau_liq=5, tau_session=200, tau_cap=60,
        tau_regime=45, tau_mtf=30,
    )

    assert result.value == 5
    assert result.complete is True
    assert {item.name for item in result.components} == {
        "edge", "regime", "mtf", "model", "liq", "session", "cap"
    }


def test_missing_edge_blocks_everything():
    curve = _curve(_point(1, -0.9), _point(30, -0.5))

    result = tau_max_components(
        curve, tau_model=30, tau_liq=5, tau_session=200, tau_cap=60, tau_regime=45, tau_mtf=30
    )

    assert result.value is None
    assert any("τ_edge" in reason for reason in result.reasons)


def test_missing_regime_keeps_completeness_false():
    curve = _curve(_point(5, 0.3))

    result = tau_max_components(
        curve, tau_model=30, tau_liq=5, tau_session=200, tau_cap=60
    )

    regime = next(item for item in result.components if item.name == "regime")
    assert regime.status == COMPONENT_MISSING
    assert result.complete is False
    assert result.value == 5
    assert any("τ_regime" in reason for reason in result.reasons)


def test_zero_session_horizon_blocks_entry():
    curve = _curve(_point(5, 0.3))

    result = tau_max_components(
        curve, tau_model=30, tau_liq=1, tau_session=0, tau_cap=60, tau_regime=15, tau_mtf=15
    )

    assert result.value == 0
    assert any("τ_max = 0" in reason for reason in result.reasons)


def test_component_rounding_follows_declared_direction():
    curve = _curve(_point(7, 0.3))

    result = tau_max_components(
        curve, tau_model=13, tau_liq=7, tau_session=120, tau_cap=60,
        tau_regime=22, tau_mtf=22, step=5,
    )

    values = {item.name: item.value for item in result.components}
    assert values["edge"] == 10  # вверх
    assert values["regime"] == 25
    assert values["model"] == 15
    assert values["session"] == 120
    assert result.value == 10  # минимум, кратный шагу


def test_invalid_arguments_rejected():
    curve = _curve(_point(5, 0.3))

    with pytest.raises(ValueError):
        tau_max_components(curve, tau_model=30, tau_liq=1, tau_session=1, tau_cap=60, step=0)
    with pytest.raises(ValueError):
        tau_max_components(curve, tau_model=0, tau_liq=1, tau_session=1, tau_cap=60)
    with pytest.raises(ValueError):
        tau_max_components(curve, tau_model=30, tau_liq=1, tau_session=-1, tau_cap=60)
