"""Тесты издержек круга в тиках (шаг R0.5 плана)."""
from __future__ import annotations

import pytest

from analyzer.research.costs import CostError, TARIFF_RATES, estimate_cost, stress_cost
from analyzer.research.ticks import TickSize

TICK = TickSize(0.01, "metadata")
PRICE = 300.0


def test_trader_round_trip_is_commission_and_spread():
    cost = estimate_cost(PRICE, TICK, tariff="trader", spread_bps=5.0)

    # 0.0005 * 300 = 0.15 ₽ = 15 тиков за сторону, круг 30 тиков; спред 5 б.п. = 0.15 ₽ = 15 тиков
    assert cost.commission_ticks_per_side == pytest.approx(15.0)
    assert cost.spread_ticks == pytest.approx(15.0)
    assert cost.round_trip_ticks == pytest.approx(45.0)


def test_investor_tariff_is_six_times_higher():
    trader = estimate_cost(PRICE, TICK, tariff="trader")
    investor = estimate_cost(PRICE, TICK, tariff="investor")

    assert investor.round_trip_ticks == pytest.approx(trader.round_trip_ticks * 6)


def test_min_commission_applied():
    cost = estimate_cost(10.0, TICK, tariff="trader", min_commission_rub=0.05)

    # 0.0005 * 10 = 0.005 ₽, минимальная комиссия 0.05 ₽ = 5 тиков за сторону
    assert cost.commission_ticks_per_side == pytest.approx(5.0)
    assert cost.flags["min_commission"] == "APPLIED"


def test_spread_without_measurement_is_unverified():
    cost = estimate_cost(PRICE, TICK, tariff="trader", spread_bps=5.0)

    assert cost.flags["spread"] == "UNVERIFIED"
    assert cost.to_parameter().flag == "UNVERIFIED"


def test_verified_spread_has_no_flag():
    cost = estimate_cost(PRICE, TICK, tariff="trader", spread_bps=5.0, spread_verified=True)

    assert "spread" not in cost.flags
    assert cost.to_parameter().flag is None


def test_stress_multiplies_round_trip():
    base = estimate_cost(PRICE, TICK, tariff="trader", spread_bps=5.0)
    stressed = stress_cost(PRICE, TICK, tariff="trader", spread_bps=5.0)

    assert stressed.round_trip_ticks == pytest.approx(base.round_trip_ticks * 1.5)
    assert stressed.stress_multiplier == pytest.approx(1.5)


def test_custom_rate_allowed_and_flagged():
    cost = estimate_cost(PRICE, TICK, tariff="custom", rate_per_side=0.001)

    assert cost.rate_per_side == pytest.approx(0.001)
    assert cost.flags["tariff"] == "CUSTOM"


def test_unknown_tariff_rejected():
    with pytest.raises(CostError):
        estimate_cost(PRICE, TICK, tariff="wizard")


def test_invalid_inputs_rejected():
    with pytest.raises(CostError):
        estimate_cost(0.0, TICK, tariff="trader")
    with pytest.raises(CostError):
        estimate_cost(PRICE, TICK, tariff="trader", spread_bps=-1.0)
    with pytest.raises(CostError):
        estimate_cost(PRICE, TICK, tariff="trader", stress=0.0)
    with pytest.raises(CostError):
        estimate_cost(PRICE, TICK, tariff="trader", rate_per_side=0.5)


def test_tariff_table_matches_known_rates():
    assert TARIFF_RATES["trader"] == pytest.approx(0.0005)
    assert TARIFF_RATES["investor"] == pytest.approx(0.003)
