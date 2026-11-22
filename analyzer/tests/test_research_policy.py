"""Тесты политики фазы A (шаг R1.1 плана)."""
from __future__ import annotations

from pathlib import Path

import pytest

from analyzer.research.policy import (
    NO_TRADE_CONDITIONS,
    PolicyError,
    PolicyParams,
    anchor_freeze_entry,
    declare_barriers,
    freeze_policy,
    load_policy,
)
from analyzer.research.registry import TrialRegistry


def test_defaults_match_plan():
    params = PolicyParams()

    assert params.q == pytest.approx(0.10)
    assert params.alpha_total == pytest.approx(0.05)
    assert params.z_entry == pytest.approx(2.0)
    assert params.rho_max == pytest.approx(0.25)
    assert params.tau_cap_minutes == 60
    assert params.buffer_minutes == 5


def test_lambda_is_derived_from_z_entry():
    params = PolicyParams(z_entry=2.0)

    assert params.lambda_risk == pytest.approx(2.0 / (2 * 2 ** 0.5))
    assert "lambda" not in params.to_mapping()


@pytest.mark.parametrize(
    "payload",
    [
        {"q": 1.0},
        {"q": 0.0},
        {"alpha_total": 0.0},
        {"z_entry": 0.0},
        {"rho_max": 0.0},
        {"rho_max": 1.5},
        {"impact_budget": 1.0},
        {"tau_cap_minutes": 0},
        {"buffer_minutes": -1},
        {"k_barrier_tp": 0.0},
    ],
)
def test_invalid_values_rejected(payload):
    with pytest.raises(PolicyError):
        PolicyParams.from_mapping(payload)


def test_unknown_field_rejected():
    with pytest.raises(PolicyError):
        PolicyParams.from_mapping({"lambd": 0.5})


def test_env_overrides_yaml(tmp_path: Path):
    config = tmp_path / "policy.yaml"
    config.write_text("policy:\n  q: 0.2\n  tau_cap_minutes: 30\n", encoding="utf-8")

    params, sources = load_policy(config, environ={"RESEARCH_Q": "0.15"})

    assert params.q == pytest.approx(0.15)
    assert params.tau_cap_minutes == 30
    assert sources["q"] == "env"
    assert sources["tau_cap_minutes"] == "yaml"


def test_defaults_have_no_source_entries():
    params, sources = load_policy(None, environ={})

    assert params == PolicyParams()
    assert sources == {}


def test_unknown_env_var_rejected():
    with pytest.raises(PolicyError):
        PolicyParams.from_env({"RESEARCH_LAMBDA": "0.7"})


def test_env_int_fields_parsed_as_int():
    params = PolicyParams.from_env({"RESEARCH_TAU_CAP_MINUTES": "45"})

    assert params.tau_cap_minutes == 45
    assert isinstance(params.tau_cap_minutes, int)


def test_barriers_are_integer_ticks():
    params = PolicyParams(z_entry=2.0)
    barriers = declare_barriers(3.0, params)

    assert barriers.tp_ticks == 6
    assert barriers.sl_ticks == 5  # 1.5 * 3 = 4.5 → 5
    assert isinstance(barriers.tp_ticks, int)


def test_barriers_never_below_one_tick():
    barriers = declare_barriers(0.1, PolicyParams())

    assert barriers.tp_ticks == 1
    assert barriers.sl_ticks == 1


def test_non_positive_sigma_rejected():
    with pytest.raises(PolicyError):
        declare_barriers(0.0, PolicyParams())


def test_freeze_registers_and_returns_artifact(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")

    frozen = freeze_policy(
        registry, PolicyParams(), "AFLT@TQBR", sigma_1_ticks=3.0, anchor_mu=39.5
    )

    assert frozen.registry_id
    assert registry.n_trials == 1
    artifact = frozen.to_artifact()
    assert artifact["registry_id"] == frozen.registry_id
    assert artifact["barriers"]["tp_ticks"] == 6
    assert artifact["no_trade"] == list(NO_TRADE_CONDITIONS)
    assert artifact["anchor_mu"] == pytest.approx(39.5)


def test_unregistered_policy_rejected():
    from analyzer.research.policy import Barriers, FrozenPolicy

    frozen = FrozenPolicy(
        instrument="AFLT@TQBR",
        params=PolicyParams(),
        barriers=Barriers(6, 5, 2.0, 1.5, 3.0),
        registry_id="",
        frozen_at="2026-11-22T00:00:00+00:00",
    )

    with pytest.raises(PolicyError):
        frozen.require_registered()


def test_anchor_log_entry_links_registry(tmp_path: Path):
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    frozen = freeze_policy(registry, PolicyParams(), "AFLT@TQBR", sigma_1_ticks=3.0, anchor_mu=39.5)

    entry = anchor_freeze_entry(frozen, registry.find(frozen.registry_id))

    assert entry["registry_id"] == frozen.registry_id
    assert entry["anchor_mu"] == pytest.approx(39.5)
    assert entry["registry_created_at"] == frozen.frozen_at
