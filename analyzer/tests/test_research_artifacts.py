"""Тесты конверта артефакта (обязательные поля §14)."""
from __future__ import annotations

import pytest

from analyzer.research.artifacts import (
    ArtifactError,
    build_artifact,
    collect_flags,
    validate_artifact,
)

UNITS = {"price": "ticks"}
N = {"AFLT": 100}
FLAGS = {"spread": "UNVERIFIED"}


def test_artifact_requires_units_n_method_flags():
    artifact = build_artifact(
        name="report", units=UNITS, n=N, method="Parkinson", flags=FLAGS, payload={"x": 1}
    )

    assert artifact["units"] == UNITS
    assert artifact["n"] == N
    assert artifact["flags"] == FLAGS
    validate_artifact(artifact)


def test_missing_field_rejected():
    with pytest.raises(ArtifactError):
        build_artifact(name="report", units=UNITS, n=N, method="", flags=FLAGS, payload={})


def test_empty_units_rejected():
    with pytest.raises(ArtifactError):
        build_artifact(
            name="report", units={}, n=N, method="Parkinson", flags=FLAGS, payload={}
        )


def test_empty_flags_allowed_but_mapping_required():
    artifact = build_artifact(
        name="report", units=UNITS, n=N, method="Parkinson", flags={}, payload={}
    )

    assert artifact["flags"] == {}

    with pytest.raises(ArtifactError):
        validate_artifact({"units": UNITS, "n": N, "method": "m", "flags": "none"})


def test_collect_flags_keeps_first_value():
    merged = collect_flags({"tick": "UNVERIFIED"}, {"tick": "OK"}, {"spread": "UNVERIFIED"})

    assert merged == {"tick": "UNVERIFIED", "spread": "UNVERIFIED"}
