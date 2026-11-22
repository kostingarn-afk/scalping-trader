"""Тесты CLI исследования (шаги R0: `integrity`, `report`)."""
from __future__ import annotations

import json
from pathlib import Path

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
