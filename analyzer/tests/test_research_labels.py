"""Тесты метки и горизонта purging (шаг R0.6 плана)."""
from __future__ import annotations

import pytest

from analyzer.research.labels import (
    FIXED_HORIZON,
    TRIPLE_BARRIER,
    LabelError,
    LabelSpec,
)


def test_fixed_horizon_hmax_equals_horizon():
    spec = LabelSpec(kind=FIXED_HORIZON, horizon_bars=15)

    assert spec.h_max == 15


def test_triple_barrier_hmax_is_max_of_barriers():
    spec = LabelSpec(kind=TRIPLE_BARRIER, horizon_bars=30, barrier_horizons=(5, 15, 60))

    assert spec.h_max == 60


def test_triple_barrier_requires_barriers():
    with pytest.raises(LabelError):
        LabelSpec(kind=TRIPLE_BARRIER, horizon_bars=30)


def test_fixed_horizon_rejects_barriers():
    with pytest.raises(LabelError):
        LabelSpec(kind=FIXED_HORIZON, horizon_bars=10, barrier_horizons=(5,))


def test_unknown_kind_rejected():
    with pytest.raises(LabelError):
        LabelSpec(kind="magic", horizon_bars=5)


def test_non_positive_horizon_rejected():
    with pytest.raises(LabelError):
        LabelSpec(kind=FIXED_HORIZON, horizon_bars=0)


def test_label_must_be_registered_before_selection():
    spec = LabelSpec(kind=FIXED_HORIZON, horizon_bars=10)

    with pytest.raises(LabelError):
        spec.require_registered()

    registered = spec.with_registry_id("abc123")
    assert registered.require_registered().registry_id == "abc123"


def test_round_trip_mapping():
    spec = LabelSpec(kind=TRIPLE_BARRIER, horizon_bars=30, barrier_horizons=(5, 60), registry_id="t1")

    restored = LabelSpec.from_mapping(spec.to_mapping())

    assert restored == spec
    assert restored.h_max == 60
