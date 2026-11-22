"""Тесты контракта единиц измерения (шаг R0.1 плана)."""
from __future__ import annotations

import pytest

from analyzer.research.units import (
    MINUTE_INVERSE,
    PCT,
    TICKS,
    Parameter,
    UnitsError,
    UnitsRegistry,
    split_flags,
)


def test_parameter_without_units_rejected():
    with pytest.raises(UnitsError):
        Parameter(name="theta", value=0.5, units="")


def test_parameter_with_unknown_units_rejected():
    with pytest.raises(UnitsError):
        Parameter(name="barrier", value=10, units="tick")


def test_parameter_with_non_numeric_value_rejected():
    with pytest.raises(UnitsError):
        Parameter(name="barrier", value="10", units=TICKS)  # type: ignore[arg-type]


def test_expect_rejects_foreign_units():
    barrier = Parameter(name="barrier_tp", value=12, units=TICKS)

    assert barrier.expect(TICKS) is barrier
    with pytest.raises(UnitsError):
        barrier.expect(PCT)


def test_as_ticks_requires_integer():
    assert Parameter(name="barrier_sl", value=7, units=TICKS).as_ticks() == 7
    with pytest.raises(UnitsError):
        Parameter(name="barrier_sl", value=7.5, units=TICKS).as_ticks()
    with pytest.raises(UnitsError):
        Parameter(name="theta", value=0.5, units=MINUTE_INVERSE).as_ticks()


def test_registry_rejects_conflicting_declarations():
    registry = UnitsRegistry()
    registry.add(Parameter(name="cost", value=30, units=TICKS))
    registry.add(Parameter(name="cost", value=30, units=TICKS))

    with pytest.raises(UnitsError):
        registry.add(Parameter(name="cost", value=31, units=TICKS))


def test_registry_require_missing_parameter():
    registry = UnitsRegistry()

    with pytest.raises(UnitsError):
        registry.require("theta", MINUTE_INVERSE)


def test_registry_require_wrong_units():
    registry = UnitsRegistry([Parameter(name="theta", value=0.5, units=MINUTE_INVERSE)])

    with pytest.raises(UnitsError):
        registry.require("theta", PCT)


def test_registry_from_mapping_uses_expected_units():
    registry = UnitsRegistry.from_mapping(
        {"theta": 0.5, "cost": 30, "barrier_tp": 12}, source="phase-A"
    )

    assert registry.require("theta", MINUTE_INVERSE).value == 0.5
    assert registry.as_ticks("cost") == 30
    assert registry.as_ticks("barrier_tp") == 12
    assert registry.get("barrier_tp").source == "phase-A"


def test_registry_from_mapping_rejects_unknown_parameter():
    with pytest.raises(UnitsError):
        UnitsRegistry.from_mapping({"mystery": 1.0})


def test_registry_to_dict_contains_units_and_source():
    registry = UnitsRegistry.from_mapping({"theta": 0.5}, source="ou-fit")

    payload = registry.to_dict()

    assert payload["theta"]["units"] == MINUTE_INVERSE
    assert payload["theta"]["source"] == "ou-fit"


def test_split_flags_separates_verified_and_flagged():
    verified, flagged = split_flags(
        [
            Parameter(name="theta", value=0.5, units=MINUTE_INVERSE),
            Parameter(name="spread", value=3, units=TICKS, flag="UNVERIFIED"),
        ]
    )

    assert verified == ("theta",)
    assert flagged == ("spread (UNVERIFIED)",)
