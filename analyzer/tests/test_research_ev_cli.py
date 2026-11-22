"""Тесты подкоманды `ev` (EV-ядро R3)."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from analyzer.research.cli import main

HEADER = "ts,open,high,low,close,volume"
META = {
    "ticker": "TEST",
    "class_code": "TQBR",
    "lot_size": 1,
    "interval_minutes": 1,
    "min_price_increment": 0.01,
}
MSK = timezone(timedelta(hours=3))


def _write_dataset(directory: Path, *, sessions: int = 30, minutes: int = 400,
                   seed: int = 3) -> None:
    """OU-серия вокруг 100 с шагом 1 мин: цены дают сигналы при σ_P из артефакта."""
    rng = np.random.default_rng(seed)
    theta, sigma = 0.05, 0.002
    rows = []
    day = datetime(2026, 9, 1, tzinfo=MSK)
    built = 0
    while built < sessions:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        level = 0.0
        for minute in range(minutes):
            level += -theta * level + sigma * rng.normal()
            price = 100.0 * float(np.exp(level))
            stamp = (day.replace(hour=10) + timedelta(minutes=1 + minute)).astimezone(
                timezone.utc
            )
            rows.append(
                f"{stamp.strftime('%Y-%m-%dT%H:%M:%SZ')},{price:.4f},{price * 1.0002:.4f},"
                f"{price * 0.9998:.4f},{price:.4f},100"
            )
        day += timedelta(days=1)
        built += 1
    (directory / "TEST_1min.csv").write_text(
        "\n".join([HEADER, *rows]) + "\n", encoding="utf-8"
    )
    (directory / "TEST_1min.meta.json").write_text(json.dumps(META), encoding="utf-8")


def _write_theta_artifacts(
    directory: Path,
    *,
    gate1: str = "PASS",
    instrument: str = "TEST@TQBR",
    sigma: float = 0.002,
    sigma_p: float = 0.0063,
    theta: float = 0.05,
    trial_id: str = "abcd1234",
) -> None:
    theta_artifact = {
        "name": "theta_invariance_regression",
        "units": {"theta": "min^-1"},
        "n": {instrument: 12000},
        "method": "test",
        "flags": {},
        "payload": {
            "trial_id": trial_id,
            "instruments": [
                {
                    "instrument": instrument,
                    "gate1": gate1,
                    "gate1_reasons": [] if gate1 == "PASS" else ["CI t_1/2 шире двукратного"],
                    "theta_used": theta,
                    "t_half_minutes": 13.9,
                }
            ],
        },
    }
    ou_artifact = {
        "name": "ou_fit",
        "units": {"sigma": "log_price/sqrt(min)", "sigma_p": "log_price", "mu": "log_price"},
        "n": {instrument: 12000},
        "method": "test",
        "flags": {},
        "payload": {
            "trial_id": trial_id,
            "instruments": (
                [
                    {
                        "instrument": instrument,
                        "theta": theta,
                        "sigma": sigma,
                        "sigma_p": sigma_p,
                        "mu": 4.6,
                    }
                ]
                if gate1 == "PASS"
                else []
            ),
        },
    }
    (directory / "theta-invariance.json").write_text(
        json.dumps(theta_artifact), encoding="utf-8"
    )
    (directory / "ou-fit.json").write_text(json.dumps(ou_artifact), encoding="utf-8")


@pytest.fixture()
def paths(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    _write_dataset(data)
    theta_dir = tmp_path / "theta"
    theta_dir.mkdir()
    _write_theta_artifacts(theta_dir)
    return data, theta_dir, tmp_path / "out", tmp_path / "registry.jsonl"


def _args(data: Path, theta_dir: Path, out: Path, registry: Path, *extra: str):
    return [
        "ev",
        "--data-dir",
        str(data),
        "--out",
        str(out),
        "--theta-dir",
        str(theta_dir),
        "--registry",
        str(registry),
        *extra,
    ]


def test_ev_writes_artifacts_and_registers_trial(paths):
    data, theta_dir, out, registry = paths

    code = main(_args(data, theta_dir, out, registry, "--bootstrap", "20"))

    assert code in (0, 1)
    protocol = json.loads((out / "first_passage-protocol.json").read_text(encoding="utf-8"))
    curve = json.loads((out / "ev-curve.json").read_text(encoding="utf-8"))
    star = json.loads((out / "tau-star.json").read_text(encoding="utf-8"))
    assert (out / "ev.md").is_file()
    assert protocol["payload"]["theta_trial_id"] == "abcd1234"
    assert curve["payload"]["instruments"][0]["instrument"] == "TEST@TQBR"
    tau_max = star["payload"]["instruments"][0]["tau_max"]
    assert len(tau_max["components"]) == 7
    assert tau_max["value"] is not None
    assert "tau_max:TEST@TQBR" not in protocol["flags"]
    assert (out / "tau-max.json").is_file()
    assert (out / "regime.json").is_file()

    records = [
        json.loads(line)
        for line in registry.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    assert records[0]["params"]["model"] == "ev_tau_star"
    assert records[0]["params"]["protocol"] == "SL_FIRST_EMPIRICAL_1M"


def test_ev_barriers_follow_policy_multipliers(paths):
    data, theta_dir, out, registry = paths

    main(_args(data, theta_dir, out, registry, "--bootstrap", "20"))

    protocol = json.loads((out / "first_passage-protocol.json").read_text(encoding="utf-8"))
    entry = protocol["payload"]["instruments"][0]
    # σ_1 = 0.002·цена/0.01 ≈ 20 тиков → TP = 2·20 = 40, SL = round(1.5·20) = 30
    assert entry["sigma_ticks"] == pytest.approx(20.0, rel=1e-3)
    assert entry["barriers"]["tp_ticks"] == 40
    assert entry["barriers"]["sl_ticks"] == 30
    assert entry["sanity"]["max_ev_upper_bound"] > 0


def test_ev_skips_instrument_that_failed_gate1(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    _write_dataset(data)
    theta_dir = tmp_path / "theta"
    theta_dir.mkdir()
    _write_theta_artifacts(theta_dir, gate1="THETA_UNDEFINED")
    out = tmp_path / "out"

    code = main(_args(data, theta_dir, out, tmp_path / "registry.jsonl",
                      "--bootstrap", "20"))

    assert code == 1
    star = json.loads((out / "tau-star.json").read_text(encoding="utf-8"))
    assert star["payload"]["instruments"] == []
    skipped = star["payload"]["skipped"]
    assert skipped[0]["instrument"] == "TEST@TQBR"
    assert skipped[0]["reason"].startswith("GATE1:THETA_UNDEFINED")


def test_ev_requires_theta_artifacts(tmp_path: Path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    _write_dataset(data)
    empty = tmp_path / "theta"
    empty.mkdir()

    code = main(_args(data, empty, tmp_path / "out", tmp_path / "registry.jsonl"))

    assert code == 2
    assert "theta-invariance.json" in capsys.readouterr().err


def test_ev_rejects_bad_inputs(paths):
    data, theta_dir, out, registry = paths

    assert main(_args(data, theta_dir, out, registry, "--weeks", "2")) == 2
    assert main(_args(data, theta_dir, out, registry, "--taus", "0,5")) == 2
    assert main(_args(data, theta_dir, out, registry, "--bootstrap", "10")) == 2


def test_ev_cost_floor_raises_tp_above_cost(tmp_path: Path):
    """Дешёвый σ₁ при дорогом круге: TP поднимается до `k_cost·Cost`, граница становится > 0."""
    data = tmp_path / "data"
    data.mkdir()
    _write_dataset(data, sessions=20, minutes=200)
    theta_dir = tmp_path / "theta"
    theta_dir.mkdir()
    # σ_1 = 0.0005·100/0.01 = 5 тиков → TP от σ = 10 тиков; комиссия 0.05 % от 100 = 5 тиков/сторона
    _write_theta_artifacts(theta_dir, sigma=0.0005, sigma_p=0.0016)
    out = tmp_path / "out"

    main(_args(data, theta_dir, out, tmp_path / "registry.jsonl", "--bootstrap", "20"))

    protocol = json.loads((out / "first_passage-protocol.json").read_text(encoding="utf-8"))
    entry = protocol["payload"]["instruments"][0]
    assert entry["barriers"]["floor_applied"] is True
    assert entry["barriers"]["tp_ticks"] == math.ceil(
        3.0 * entry["cost"]["round_trip_ticks"]
    )
    assert entry["sanity"]["viable"] is True
    assert "COST_FLOOR" in protocol["flags"]["barrier_floor:TEST@TQBR"]


def test_ev_negative_bound_is_flagged_when_floor_is_weaker_than_cost(tmp_path: Path):
    """Флаг `NEGATIVE_BOUND` остаётся достижимым: k_cost < 1 не покрывает круг."""
    data = tmp_path / "data"
    data.mkdir()
    _write_dataset(data, sessions=20, minutes=200)
    theta_dir = tmp_path / "theta"
    theta_dir.mkdir()
    _write_theta_artifacts(theta_dir, sigma=0.0005, sigma_p=0.0016)
    policy = tmp_path / "policy.yaml"
    policy.write_text("policy:\n  k_cost: 0.5\n", encoding="utf-8")
    out = tmp_path / "out"

    main(_args(data, theta_dir, out, tmp_path / "registry.jsonl", "--bootstrap", "20",
               "--policy", str(policy)))

    protocol = json.loads((out / "first_passage-protocol.json").read_text(encoding="utf-8"))
    entry = protocol["payload"]["instruments"][0]
    assert entry["sanity"]["viable"] is False
    assert "NEGATIVE_BOUND" in protocol["flags"]["sanity:TEST@TQBR"]
