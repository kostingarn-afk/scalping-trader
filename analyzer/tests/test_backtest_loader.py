"""Тесты чтения выгрузки истории Collector'а."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyzer.backtest.loader import (
    aggregate,
    expected_file_names,
    load_dataset,
    read_bars,
    read_meta,
    scan_dir,
)
from analyzer.backtest.models import Instrument

HEADER = "ts,open,high,low,close,volume"


def _write_csv(tmp_path: Path, name: str, rows: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def _write_meta(tmp_path: Path, name: str, payload: dict) -> Path:
    base = name[: -len(".csv")] if name.endswith(".csv") else name
    path = tmp_path / f"{base}.meta.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


META = {
    "ticker": "SBER",
    "class_code": "TQBR",
    "lot_size": 10,
    "interval_minutes": 1,
    "fetched_at": "2026-11-14T10:00:00Z",
    "source": "tinvest:GetCandles",
}


def test_load_dataset_uses_meta(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path,
        "SBER_1min.csv",
        [
            "2026-11-13T07:00:00Z,270.0,270.5,269.5,270.2,100",
            "2026-11-13T07:01:00Z,270.2,270.9,270.0,270.8,120",
        ],
    )
    _write_meta(tmp_path, "SBER_1min.csv", META)

    dataset = load_dataset(csv_path)

    assert dataset.instrument == Instrument("SBER", "TQBR", lot_size=10)
    assert dataset.instrument.is_future is False
    assert dataset.interval_minutes == 1
    assert len(dataset) == 2
    assert dataset.bars[0].ts.isoformat() == "2026-11-13T07:00:00+00:00"


def test_load_dataset_without_meta_requires_explicit_instrument(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path, "SiZ6_1min.csv", ["2026-11-13T07:00:00Z,80000,80100,79900,80050,5"]
    )

    with pytest.raises(ValueError, match="meta"):
        load_dataset(csv_path)

    dataset = load_dataset(
        csv_path,
        instrument=Instrument("SiZ6", "SPBFUT", 1),
        interval_minutes=1,
    )
    assert dataset.instrument.is_future is True
    assert dataset.interval_minutes == 1


def test_rows_must_be_strictly_increasing(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path,
        "SBER_1min.csv",
        [
            "2026-11-13T07:01:00Z,1,1,1,1,1",
            "2026-11-13T07:01:00Z,1,1,1,1,1",
        ],
    )

    with pytest.raises(ValueError, match="не возрастает"):
        read_bars(csv_path)


def test_non_numeric_value_reports_line(tmp_path: Path):
    csv_path = _write_csv(tmp_path, "SBER_1min.csv", ["2026-11-13T07:00:00Z,abc,1,1,1,1"])

    with pytest.raises(ValueError, match=":2:"):
        read_bars(csv_path)


def test_header_accepts_timestamp_column(tmp_path: Path):
    path = tmp_path / "SBER_1min.csv"
    path.write_text(
        "timestamp,open,high,low,close\n2026-11-13T07:00:00Z,1,2,0.5,1.5\n", encoding="utf-8"
    )

    bars = read_bars(path)

    assert len(bars) == 1
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (1.0, 2.0, 0.5, 1.5)
    assert bars[0].volume == 0
    assert bars[0].ts.isoformat() == "2026-11-13T07:00:00+00:00"


def test_read_meta_returns_none_without_sidecar(tmp_path: Path):
    csv_path = _write_csv(tmp_path, "SBER_1min.csv", ["2026-11-13T07:00:00Z,1,1,1,1,1"])

    assert read_meta(csv_path) is None


def test_read_meta_rejects_incomplete_payload(tmp_path: Path):
    csv_path = _write_csv(tmp_path, "SBER_1min.csv", ["2026-11-13T07:00:00Z,1,1,1,1,1"])
    _write_meta(tmp_path, "SBER_1min.csv", {"ticker": "SBER"})

    with pytest.raises(ValueError, match="class_code"):
        read_meta(csv_path)


def test_aggregate_one_minute_to_five(tmp_path: Path):
    """Свечи закрытий 07:01…07:06 (нативные интервалы 07:00–07:06) → 5-мин свеча + остаток."""
    rows = [
        f"2026-11-13T07:0{minute}:00Z,"
        f"{100 + minute},{101 + minute},{99 + minute},{100.5 + minute},10"
        for minute in range(1, 7)
    ]
    csv_path = _write_csv(tmp_path, "SBER_1min.csv", rows)

    aggregated = aggregate(read_bars(csv_path), target_minutes=5, native_minutes=1)

    assert len(aggregated) == 2
    first = aggregated[0]
    assert first.ts.isoformat() == "2026-11-13T07:05:00+00:00"
    assert first.open == pytest.approx(101.0)
    assert first.high == pytest.approx(106.0)
    assert first.low == pytest.approx(100.0)
    assert first.close == pytest.approx(105.5)
    assert first.volume == 50
    assert aggregated[1].volume == 10


def test_aggregate_rejects_non_multiple_target(tmp_path: Path):
    """Целевой ТФ должен быть кратен нативному: 7 мин из 2-мин баров не собирается."""
    csv_path = _write_csv(tmp_path, "SBER_2min.csv", ["2026-11-13T07:02:00Z,1,1,1,1,1"])

    with pytest.raises(ValueError, match="multiple"):
        aggregate(read_bars(csv_path), target_minutes=7, native_minutes=2)


def test_broken_ohlc_is_rejected(tmp_path: Path):
    csv_path = _write_csv(tmp_path, "SBER_1min.csv", ["2026-11-13T07:00:00Z,100,99,101,100,10"])

    with pytest.raises(ValueError, match="OHLC"):
        read_bars(csv_path)


def test_non_positive_price_is_rejected(tmp_path: Path):
    csv_path = _write_csv(tmp_path, "SBER_1min.csv", ["2026-11-13T07:00:00Z,0,0,0,0,10"])

    with pytest.raises(ValueError, match="положительн"):
        read_bars(csv_path)


def test_negative_volume_is_rejected(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path, "SBER_1min.csv", ["2026-11-13T07:00:00Z,100,101,99,100,-5"]
    )

    with pytest.raises(ValueError, match="объём"):
        read_bars(csv_path)


def test_step_must_match_declared_interval(tmp_path: Path):
    """Минутные строки, помеченные как 5-мин выгрузка, не должны доезжать до прогона."""
    csv_path = _write_csv(
        tmp_path,
        "SBER_5min.csv",
        [
            "2026-11-13T07:05:00Z,100,101,99,100,10",
            "2026-11-13T07:06:00Z,100,101,99,100,10",
        ],
    )
    _write_meta(tmp_path, "SBER_5min.csv", {**META, "interval_minutes": 5})

    with pytest.raises(ValueError, match="не кратен"):
        load_dataset(csv_path)


def test_scan_dir_loads_all_datasets(tmp_path: Path):
    for ticker, class_code in (("SBER", "TQBR"), ("SiZ6", "SPBFUT")):
        _write_csv(tmp_path, f"{ticker}_1min.csv", ["2026-11-13T07:00:00Z,1,1,1,1,1"])
        _write_meta(
            tmp_path,
            f"{ticker}_1min.csv",
            {**META, "ticker": ticker, "class_code": class_code},
        )

    datasets = scan_dir(tmp_path)

    assert [dataset.instrument.name for dataset in datasets] == ["SBER@TQBR", "SiZ6@SPBFUT"]


def test_expected_file_names_follow_contract():
    assert expected_file_names("SBER", 5) == ("SBER_5min.csv", "SBER_5min.meta.json")
