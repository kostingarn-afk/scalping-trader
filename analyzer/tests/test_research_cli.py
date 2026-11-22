"""Тесты CLI исследования (шаги R0: `integrity`, `report`)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from analyzer.research.cli import build_parser, main

HEADER = "ts,open,high,low,close,volume"
META = {
    "ticker": "AFLT",
    "class_code": "TQBR",
    "lot_size": 10,
    "interval_minutes": 1,
    "min_price_increment": 0.01,
}


def _write_dataset(directory: Path, close_overrides: dict[int, float] | None = None) -> None:
    rows = []
    price = 300.00
    for minute in range(40):
        close = price + 0.01 * (minute % 5)
        if close_overrides and minute in close_overrides:
            close = close_overrides[minute]
        rows.append(
            f"2026-11-18T07:{minute:02d}:00Z,{close - 0.01:.3f},{close + 0.02:.3f},"
            f"{close - 0.02:.3f},{close:.3f},{100 + minute}"
        )
    (directory / "AFLT_1min.csv").write_text(
        "\n".join([HEADER, *rows]) + "\n", encoding="utf-8"
    )
    (directory / "AFLT_1min.meta.json").write_text(json.dumps(META), encoding="utf-8")


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    _write_dataset(directory)
    return directory


def test_out_is_required():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["integrity", "--data-dir", "."])


def test_integrity_writes_artifacts(data_dir: Path, tmp_path: Path):
    out = tmp_path / "out"

    code = main(["integrity", "--data-dir", str(data_dir), "--out", str(out)])

    assert code == 0
    assert (out / "integrity.md").exists()
    payload = json.loads((out / "integrity.json").read_text(encoding="utf-8"))
    assert payload["name"] == "data_integrity_report"
    assert payload["units"]["price"] == "ticks"
    assert payload["n"]["AFLT@TQBR"] > 0
    assert payload["payload"]["instruments"][0]["instrument"] == "AFLT@TQBR"
    assert payload["payload"]["critical"] == 0


def test_integrity_reports_critical_and_returns_gate_code(tmp_path: Path):
    directory = tmp_path / "data"
    directory.mkdir()
    _write_dataset(directory, close_overrides={5: 300.005})
    out = tmp_path / "out"

    code = main(["integrity", "--data-dir", str(directory), "--out", str(out)])

    assert code == 1
    payload = json.loads((out / "integrity.json").read_text(encoding="utf-8"))
    assert payload["payload"]["critical"] == 1
    assert payload["flags"]["gate_d"] == "CRITICAL"


def test_missing_data_dir_returns_input_error(tmp_path: Path):
    code = main(["integrity", "--data-dir", str(tmp_path / "nope"), "--out", str(tmp_path)])

    assert code == 2


def test_report_registers_trial_and_writes_artifacts(data_dir: Path, tmp_path: Path):
    out = tmp_path / "out"
    registry = tmp_path / "registry.jsonl"

    code = main(
        [
            "report",
            "--data-dir",
            str(data_dir),
            "--out",
            str(out),
            "--registry",
            str(registry),
            "--spread-bps",
            "5",
        ]
    )

    assert code == 0
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    body = payload["payload"]
    assert body["trial_id"]
    assert body["datasets"][0]["tick_source"] == "metadata"
    assert payload["n"]["AFLT@TQBR"] > 0
    assert registry.exists()
    assert len(registry.read_text(encoding="utf-8").strip().splitlines()) == 1
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert body["trial_id"] in markdown
    assert "Круг:" in markdown


def test_report_rejects_invalid_taus(data_dir: Path, tmp_path: Path):
    code = main(
        [
            "report",
            "--data-dir",
            str(data_dir),
            "--out",
            str(tmp_path / "out"),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--taus",
            "0,5",
        ]
    )

    assert code == 2


def test_phi_star_stress_is_diagnostic(data_dir: Path, tmp_path: Path):
    """Стресс печатается рядом и не меняет вердикт Gate 0a."""
    out = tmp_path / "out"

    code = main(
        [
            "phi-star",
            "--data-dir",
            str(data_dir),
            "--out",
            str(out),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--stress",
            "1.5",
            "--taus",
            "1,3",
        ]
    )

    assert code in (0, 1)
    payload = json.loads((out / "phi-star.json").read_text(encoding="utf-8"))
    assert payload["payload"]["verdict"] in ("PASS", "IMPOSSIBLE")
    assert payload["payload"]["stress"]["multiplier"] == pytest.approx(1.5)
    base_phi = payload["payload"]["instruments"][0]["min_phi"]
    stress_phi = payload["payload"]["stress"]["instruments"][0]["min_phi"]
    if base_phi is not None:
        assert stress_phi == pytest.approx(base_phi * 1.5)
    assert "Стресс издержек" in (out / "phi-star.md").read_text(encoding="utf-8")


def test_theta_rejects_deltas_with_escalate(data_dir: Path, tmp_path: Path):
    code = main(
        [
            "theta",
            "--data-dir",
            str(data_dir),
            "--out",
            str(tmp_path / "out"),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--deltas",
            "1,5",
            "--escalate",
        ]
    )

    assert code == 2


def test_theta_rejects_short_null_reps(data_dir: Path, tmp_path: Path):
    code = main(
        [
            "theta",
            "--data-dir",
            str(data_dir),
            "--out",
            str(tmp_path / "out"),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--null-reps",
            "10",
        ]
    )

    assert code == 2


def test_theta_rejects_out_of_range_weeks(data_dir: Path, tmp_path: Path):
    for weeks in ("1", "12", "-1"):
        code = main(
            [
                "theta",
                "--data-dir",
                str(data_dir),
                "--out",
                str(tmp_path / "out"),
                "--registry",
                str(tmp_path / "registry.jsonl"),
                "--weeks",
                weeks,
            ]
        )
        assert code == 2, weeks


def test_theta_writes_artifacts_even_when_gate_fails(data_dir: Path, tmp_path: Path):
    """40 баров — мало для регрессии: Gate 1 не пройден, но артефакты обязаны быть записаны."""
    out = tmp_path / "out"

    code = main(
        [
            "theta",
            "--data-dir",
            str(data_dir),
            "--out",
            str(out),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--bootstrap",
            "20",
            "--null-reps",
            "50",
        ]
    )

    assert code == 1
    for name in ("theta-invariance.json", "signature-plot.json", "null-band.json", "ou-fit.json"):
        assert (out / name).exists(), name
    payload = json.loads((out / "theta-invariance.json").read_text(encoding="utf-8"))
    assert payload["name"] == "theta_invariance_regression"
    assert payload["units"]["theta"] == "min^-1"
    assert payload["payload"]["gate1"] == "THETA_UNDEFINED"
    entry = payload["payload"]["instruments"][0]
    assert entry["gate1"] == "THETA_UNDEFINED"
    assert entry["gate1_reasons"]
    assert payload["payload"]["estimation_weeks"] == 8
    assert entry["estimation_window"]["mode"] == "trailing-weeks"
    assert "Gate 1" in (out / "theta.md").read_text(encoding="utf-8")
    records = (tmp_path / "registry.jsonl").read_text(encoding="utf-8").splitlines()
    params = json.loads(records[-1])["params"]
    assert params["model"] == "gate1"
    assert params["estimation_weeks"] == 8


def _write_ou_dataset(directory: Path, sessions: int = 30, minutes: int = 400) -> None:
    """Многосессионная OU-серия, на которой Gate 1 проходит."""
    rng = np.random.default_rng(3)
    theta, sigma = 0.05, 0.002
    rows = []
    day = datetime(2026, 9, 1, tzinfo=timezone(timedelta(hours=3)))
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
                f"{stamp.strftime('%Y-%m-%dT%H:%M:%SZ')},{price:.4f},{price * 1.0001:.4f},"
                f"{price * 0.9999:.4f},{price:.4f},100"
            )
        day += timedelta(days=1)
        built += 1
    (directory / "TEST_1min.csv").write_text(
        "\n".join([HEADER, *rows]) + "\n", encoding="utf-8"
    )
    (directory / "TEST_1min.meta.json").write_text(
        json.dumps(dict(META, ticker="TEST")), encoding="utf-8"
    )


def test_theta_end_to_end_passes_and_records_trial_id(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    _write_ou_dataset(data)
    out = tmp_path / "out"

    code = main(
        [
            "theta",
            "--data-dir",
            str(data),
            "--out",
            str(out),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--bootstrap",
            "20",
            "--null-reps",
            "50",
            "--weeks",
            "0",
        ]
    )

    assert code == 0
    payload = json.loads((out / "theta-invariance.json").read_text(encoding="utf-8"))
    assert payload["payload"]["gate1"] == "PASS"
    trial_id = payload["payload"]["trial_id"]
    ou = json.loads((out / "ou-fit.json").read_text(encoding="utf-8"))
    assert ou["payload"]["instruments"], "ou_fit должен содержать прошедший инструмент"
    fitted = ou["payload"]["instruments"][0]
    assert fitted["registry_id"] == trial_id
    theta_used = fitted["theta_used"]
    assert fitted["sigma_p"] == pytest.approx(
        (fitted["theta"]["variance_estimate"]["sigma_sq"] / (2 * theta_used)) ** 0.5,
        rel=1e-4,
    )
    assert ou["units"]["mu"] == "log_price"


def test_theta_continues_when_one_instrument_has_no_sessions(tmp_path: Path):
    """Ряд без баров внутри окон не обрывает прогон: инструмент получает THETA_UNDEFINED."""
    data = tmp_path / "data"
    data.mkdir()
    rows = [
        f"2026-11-18T03:{minute:02d}:00Z,100.000,100.010,99.990,100.000,10"
        for minute in range(10)
    ]
    (data / "NIGHT_1min.csv").write_text(
        "\n".join([HEADER, *rows]) + "\n", encoding="utf-8"
    )
    (data / "NIGHT_1min.meta.json").write_text(
        json.dumps(dict(META, ticker="NIGHT")), encoding="utf-8"
    )
    out = tmp_path / "out"

    code = main(
        [
            "theta",
            "--data-dir",
            str(data),
            "--out",
            str(out),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--null-reps",
            "50",
        ]
    )

    assert code == 1
    payload = json.loads((out / "theta-invariance.json").read_text(encoding="utf-8"))
    entry = payload["payload"]["instruments"][0]
    assert entry["gate1"] == "THETA_UNDEFINED"
    assert entry["gate1_reasons"]
    signature = json.loads((out / "signature-plot.json").read_text(encoding="utf-8"))
    assert signature["payload"]["instruments"][0]["points"] == []
    ou = json.loads((out / "ou-fit.json").read_text(encoding="utf-8"))
    assert ou["payload"]["instruments"] == []


def test_theta_ou_flags_reach_artifact_envelope(tmp_path: Path, monkeypatch):
    """Флаги OU-параметров обязаны попадать в конверт, а не теряться в payload."""
    data = tmp_path / "data"
    data.mkdir()
    _write_ou_dataset(data)
    out = tmp_path / "out"

    class _StubOuFit:
        flags = {"sigma": "LOW_CONFIDENCE: тестовая заглушка"}

        def to_dict(self):
            return {"instrument": "TEST@TQBR", "flags": dict(self.flags)}

    monkeypatch.setattr("analyzer.research.cli.fit_ou", lambda *args, **kwargs: _StubOuFit())

    code = main(
        [
            "theta",
            "--data-dir",
            str(data),
            "--out",
            str(out),
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--bootstrap",
            "20",
            "--null-reps",
            "50",
            "--weeks",
            "0",
        ]
    )

    assert code == 0
    ou = json.loads((out / "ou-fit.json").read_text(encoding="utf-8"))
    assert ou["flags"]["sigma:TEST@TQBR"] == "LOW_CONFIDENCE: тестовая заглушка"
