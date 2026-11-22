"""Тесты trial registry (шаг R0.9 плана)."""
from __future__ import annotations

from pathlib import Path

import pytest

from analyzer.research.registry import RegistryError, TrialRegistry

PARAMS = {
    "model": "ou_first_passage",
    "frame": "1m",
    "window": "2026-10-01..2026-11-01",
}


def test_register_appends_record_with_id(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")

    record = registry.register(dict(PARAMS, barriers={"tp": 12, "sl": 8}))

    assert record.id
    assert record.params["model"] == "ou_first_passage"
    assert registry.n_trials == 1
    assert registry.find(record.id) is not None


def test_registry_deduplicates_nothing_but_never_rewrites(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")

    first = registry.register(PARAMS, trial_id="t1")
    second = registry.register(dict(PARAMS, window="other"), trial_id="t2")

    assert registry.n_trials == 2
    assert [record.id for record in registry.records()] == [first.id, second.id]


def test_missing_required_field_rejected(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")

    with pytest.raises(RegistryError):
        registry.register({"model": "ou", "frame": "1m"})


def test_empty_registry_cannot_feed_dsr(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")

    with pytest.raises(RegistryError):
        registry.n_trials_for_dsr()


def test_n_trials_for_dsr_counts_records(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    registry.register(PARAMS, trial_id="a")
    registry.register(PARAMS, trial_id="b")

    assert registry.n_trials_for_dsr() == 2


def test_corrupted_line_reported(tmp_path: Path):
    path = tmp_path / "registry.jsonl"
    path.write_text('{"id": "x", "created_at": "now", "params": {}}\nnot json\n', encoding="utf-8")

    with pytest.raises(RegistryError):
        TrialRegistry(path).records()
