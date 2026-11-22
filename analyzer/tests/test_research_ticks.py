"""Тесты шага цены и контракта `.meta.json` (шаги R0.2 плана)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyzer.backtest.loader import HistoryMeta, load_dataset
from analyzer.research.ticks import (
    SOURCE_METADATA,
    SOURCE_PRICE_GRID,
    SOURCE_REFERENCE,
    TickSize,
    TickSizeError,
    describe,
    infer_tick_from_prices,
    resolve_tick_size,
)

HEADER = "ts,open,high,low,close,volume"
META = {
    "ticker": "AFLT",
    "class_code": "TQBR",
    "lot_size": 10,
    "interval_minutes": 1,
}


def test_metadata_wins_over_other_sources():
    tick = resolve_tick_size(meta_value=0.01, reference_value=0.05, prices=[300.0, 300.07])

    assert tick.value == pytest.approx(0.01)
    assert tick.source == SOURCE_METADATA
    assert tick.verified is True
    assert tick.flag is None


def test_reference_used_without_metadata():
    tick = resolve_tick_size(reference_value=0.02)

    assert tick.source == SOURCE_REFERENCE
    assert tick.verified is True


def test_fallback_infers_tick_from_price_grid():
    tick = resolve_tick_size(prices=[300.00, 300.01, 300.03, 300.04])

    assert tick.value == pytest.approx(0.01)
    assert tick.source == SOURCE_PRICE_GRID
    assert tick.verified is False
    assert tick.flag == "UNVERIFIED"


def test_inference_ignores_float_noise():
    tick = infer_tick_from_prices([100.0, 100.1, 100.30000000000001])

    assert tick == pytest.approx(0.1)


def test_inference_requires_two_distinct_prices():
    with pytest.raises(TickSizeError):
        resolve_tick_size(prices=[300.0, 300.0])


def test_resolve_without_sources_fails():
    with pytest.raises(TickSizeError):
        resolve_tick_size()


def test_to_ticks_rounds_half_away_from_zero():
    tick = TickSize(0.01, SOURCE_METADATA)

    assert tick.to_ticks(0.014) == 1
    assert tick.to_ticks(0.015) == 2
    assert tick.to_ticks(-0.015) == -2
    assert tick.to_ticks(0.0) == 0


def test_from_ticks_is_inverse():
    tick = TickSize(0.05, SOURCE_METADATA)

    assert tick.from_ticks(3) == pytest.approx(0.15)


def test_is_on_grid_detects_off_grid_price():
    tick = TickSize(0.01, SOURCE_METADATA)

    assert tick.is_on_grid(300.02) is True
    assert tick.is_on_grid(300.005) is False


def test_zero_tick_rejected():
    with pytest.raises(TickSizeError):
        TickSize(0.0, SOURCE_METADATA)


def test_describe_marks_unverified_source():
    tick = resolve_tick_size(prices=[10.0, 10.5])

    assert describe(tick) == "0.5 [price-grid] (UNVERIFIED)"


def test_meta_reads_min_price_increment(tmp_path: Path):
    payload = dict(META, min_price_increment=0.01)
    payload["source"] = "tinvest:GetCandles"

    meta = HistoryMeta.from_dict(payload)

    assert meta.min_price_increment == pytest.approx(0.01)


def test_meta_without_min_price_increment_is_optional():
    assert HistoryMeta.from_dict(META).min_price_increment is None


def test_meta_rejects_non_positive_increment():
    with pytest.raises(ValueError):
        HistoryMeta.from_dict(dict(META, min_price_increment=0))


def test_load_dataset_exposes_meta_tick(tmp_path: Path):
    csv_path = tmp_path / "AFLT_1min.csv"
    csv_path.write_text(
        "\n".join(
            [
                HEADER,
                "2026-11-13T07:00:00Z,300.00,300.05,299.98,300.02,100",
                "2026-11-13T07:01:00Z,300.02,300.06,300.00,300.04,120",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "AFLT_1min.meta.json").write_text(
        json.dumps(dict(META, min_price_increment=0.01)), encoding="utf-8"
    )

    dataset = load_dataset(csv_path)

    assert dataset.meta is not None
    assert dataset.meta.min_price_increment == pytest.approx(0.01)

    tick = resolve_tick_size(meta_value=dataset.meta.min_price_increment)
    assert tick.source == SOURCE_METADATA
    assert tick.to_ticks(0.03) == 3
