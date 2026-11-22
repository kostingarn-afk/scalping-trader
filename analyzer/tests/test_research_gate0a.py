"""Тесты Gate 0a (шаг R1.2 плана)."""
from __future__ import annotations

import pytest

from analyzer.research.costs import estimate_cost
from analyzer.research.gate0a import (
    IMPOSSIBLE,
    NO_DATA,
    PASS,
    GateError,
    aggregate_verdict,
    build_phi_star_table,
    sigma_tau_ticks,
)
from analyzer.research.ticks import TickSize

TICK = TickSize(0.01, "metadata")
PRICE = 300.0


def _cost(spread_bps: float = 5.0, stress: float = 1.0):
    return estimate_cost(PRICE, TICK, tariff="trader", spread_bps=spread_bps, stress=stress)


def test_sigma_tau_in_ticks_formula():
    # σ_1 = 1e-3 (лог), τ=10, VR=0.5, цена 300, шаг 0.01
    expected = 1e-3 * (10 * 0.5) ** 0.5 * 300 / 0.01

    assert sigma_tau_ticks(1e-3, 10, 0.5, PRICE, 0.01) == pytest.approx(expected)


def test_phi_star_is_cost_over_sigma():
    table = build_phi_star_table(
        "AFLT@TQBR",
        PRICE,
        TICK.value,
        1e-3,
        {1: 1.0, 3: 0.8, 5: 0.7},
        _cost(),
        taus=(1, 3, 5),
    )

    row = table.rows[0]
    expected_sigma = 1e-3 * 1.0 * PRICE / TICK.value
    assert row.sigma_tau_ticks == pytest.approx(expected_sigma)
    assert row.phi_star == pytest.approx(row.cost_ticks / expected_sigma)


def test_verdict_pass_when_any_tau_below_one():
    # σ_1 мал → φ* велик на τ=1, но при τ=30 и VR=3 значение падает ниже 1
    table = build_phi_star_table(
        "X", PRICE, TICK.value, 1e-3, {1: 0.1, 30: 3.0}, _cost(), taus=(1, 30)
    )

    assert table.rows[1].phi_star < 1
    assert table.verdict == PASS
    assert table.best_tau == 30


def test_verdict_impossible_when_all_tau_above_one():
    table = build_phi_star_table(
        "X", PRICE, TICK.value, 1e-5, {1: 0.01, 30: 0.01}, _cost(), taus=(1, 30)
    )

    assert table.verdict == IMPOSSIBLE
    assert table.min_phi is not None and table.min_phi >= 1


def test_missing_vr_marked_no_data():
    table = build_phi_star_table(
        "X", PRICE, TICK.value, 1e-3, {1: 1.0}, _cost(), taus=(1, 30)
    )

    assert [row.status for row in table.rows] == ["OK", NO_DATA]
    assert len(table.usable_rows) == 1


def test_all_rows_no_data_gives_no_data_verdict():
    table = build_phi_star_table("X", PRICE, TICK.value, 1e-3, {}, _cost(), taus=(1,))

    assert table.verdict == NO_DATA


def test_stress_multiplier_scales_cost_but_verdict_uses_plain():
    plain = build_phi_star_table("X", PRICE, TICK.value, 1e-3, {1: 1.0}, _cost(), taus=(1,))
    stressed = build_phi_star_table(
        "X", PRICE, TICK.value, 1e-3, {1: 1.0}, _cost(stress=1.5), taus=(1,)
    )

    assert stressed.rows[0].cost_ticks == pytest.approx(plain.rows[0].cost_ticks * 1.5)
    assert stressed.rows[0].phi_star == pytest.approx(plain.rows[0].phi_star * 1.5)


def test_aggregate_verdict_needs_single_pass():
    passing = build_phi_star_table(
        "A", PRICE, TICK.value, 2e-3, {1: 1.0}, _cost(spread_bps=0.0), taus=(1,)
    )
    failing = build_phi_star_table(
        "B", PRICE, TICK.value, 1e-9, {1: 1.0}, _cost(), taus=(1,)
    )

    assert passing.verdict == PASS
    assert aggregate_verdict([passing, failing]) == PASS
    assert aggregate_verdict([failing]) == IMPOSSIBLE


def test_sigma_tau_rejects_bad_inputs():
    with pytest.raises(GateError):
        sigma_tau_ticks(0.0, 1, 1.0, PRICE, 0.01)
    with pytest.raises(GateError):
        sigma_tau_ticks(1e-3, 1, 0.0, PRICE, 0.01)
    with pytest.raises(GateError):
        sigma_tau_ticks(1e-3, 0, 1.0, PRICE, 0.01)


def test_markdown_mentions_verdict_and_rows():
    table = build_phi_star_table(
        "X", PRICE, TICK.value, 2e-3, {1: 1.0}, _cost(), taus=(1,)
    )

    text = table.format_markdown()

    assert "PASS" in text
    assert "σ_τ" in text
