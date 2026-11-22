"""Тесты Gate D (шаг R0.4 плана)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.backtest.models import Bar
from analyzer.research.integrity import CRITICAL, WARNING, check_integrity
from analyzer.research.sessions import SessionSchedule
from analyzer.research.ticks import TickSize

MSK = timezone(timedelta(hours=3))
TICK = TickSize(0.01, "metadata")


def _bar(hour: int, minute: int, close: float, volume: int = 100, day: int = 18) -> Bar:
    ts = datetime(2026, 11, day, hour, minute, tzinfo=MSK).astimezone(timezone.utc)
    step = 0.01
    return Bar(
        ts=ts,
        open=round(close - step, 2),
        high=round(close + step, 2),
        low=round(close - 2 * step, 2),
        close=close,
        volume=volume,
    )


def _series(count: int = 5, day: int = 18) -> list[Bar]:
    return [_bar(10, minute, 300.00 + 0.01 * minute, day=day) for minute in range(count)]


def test_clean_series_has_no_findings():
    report = check_integrity(_series(), TICK, instrument="AFLT", schedule=SessionSchedule())

    assert report.instrument == "AFLT"
    assert report.findings == ()
    assert report.has_critical is False
    assert report.stats["missing_bars"] == 0
    assert report.stats["bars_outside_session"] == 0


def test_off_grid_price_is_critical():
    bars = _series()
    broken = bars[2]
    bars[2] = Bar(
        ts=broken.ts,
        open=broken.open,
        high=broken.high,
        low=broken.low,
        close=300.005,
        volume=broken.volume,
    )

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.has_critical is True
    assert any(finding.check == "price_grid" for finding in report.of_level(CRITICAL))


def test_gap_inside_session_is_reported():
    bars = _series(4)
    bars[2] = Bar(
        ts=bars[2].ts + timedelta(minutes=5),
        open=bars[2].open,
        high=bars[2].high,
        low=bars[2].low,
        close=bars[2].close,
        volume=bars[2].volume,
    )

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.stats["missing_bars"] >= 4
    assert any(finding.check == "missing_bars" for finding in report.findings)


def test_bars_outside_session_reported():
    bars = _series(3, day=21)  # 21 ноября 2026 — суббота

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.stats["bars_outside_session"] == 3
    assert any(finding.check == "bars_outside_session" for finding in report.findings)


def test_overnight_transition_counted():
    bars = _series(3, day=18) + _series(3, day=19)

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.stats["overnight_returns"] == 1
    assert any(finding.check == "overnight_returns" for finding in report.findings)


def test_zero_volume_ratio_reported():
    bars = [bar for bar in _series(5)]
    bars[0] = Bar(ts=bars[0].ts, open=bars[0].open, high=bars[0].high, low=bars[0].low,
                  close=bars[0].close, volume=0)

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.stats["zero_volume_bars"] == 1
    assert report.stats["zero_volume_ratio"] == pytest.approx(0.2)


def test_duplicate_timestamps_are_critical():
    bars = _series(3)
    bars[2] = Bar(ts=bars[1].ts, open=bars[2].open, high=bars[2].high, low=bars[2].low,
                  close=bars[2].close, volume=bars[2].volume)

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.has_critical is True
    assert any(finding.check == "timestamp_order" for finding in report.findings)


def test_move_anomaly_reported():
    bars = _series(10)
    spike = bars[-1]
    bars[-1] = Bar(
        ts=spike.ts,
        open=spike.open,
        high=310.0,
        low=spike.low,
        close=310.0,
        volume=spike.volume,
    )

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert any(finding.check == "move_anomaly" for finding in report.of_level(WARNING))


def test_without_schedule_sessions_checks_are_skipped_with_note():
    report = check_integrity(_series(), TICK)

    assert any(finding.check == "sessions" for finding in report.findings)


def test_zero_volume_at_session_edges_counted_as_auction_print():
    bars = [
        _bar(10, 0, 300.00, volume=0),
        _bar(10, 1, 300.01),
        _bar(18, 39, 300.02, volume=0),
    ]

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.stats["auction_prints"] == 2
    assert any(finding.check == "auction_prints" for finding in report.findings)


def test_relative_ticks_reported_for_full_series():
    bars = [_bar(10, minute, 300.00 + 0.05 * minute) for minute in range(10)]

    report = check_integrity(bars, TICK, schedule=SessionSchedule())

    assert report.stats["dp_median_ticks"] == pytest.approx(5.0)
    assert report.stats["dp_max_ticks"] >= report.stats["dp_median_ticks"]


def test_empty_series_rejected():
    with pytest.raises(ValueError):
        check_integrity([], TICK, schedule=SessionSchedule())


def test_markdown_contains_stats_and_findings():
    report = check_integrity(_series(2, day=21), TICK, instrument="AFLT", schedule=SessionSchedule())

    markdown = report.format_markdown()

    assert "AFLT" in markdown
    assert "bars_outside_session" in markdown
    assert "Уровень" in markdown
