"""Тесты дисперсий, VR и σ_τ (шаги R0.7–R0.8 плана)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import log, sqrt

import pytest

from analyzer.backtest.models import Bar
from analyzer.research.sessions import SessionSchedule
from analyzer.research.variance import (
    BOUNCE,
    VarianceError,
    deseasonalize_returns,
    estimate_variances,
    sigma_tau,
    variance_ratio,
    variance_ratio_curve,
)

MSK = timezone(timedelta(hours=3))


def _bar(minute: int, open_: float, high: float, low: float, close: float) -> Bar:
    ts = datetime(2026, 11, 18, 10, 0, tzinfo=MSK) + timedelta(minutes=minute)
    return Bar(ts=ts.astimezone(timezone.utc), open=open_, high=high, low=low, close=close,
               volume=100)


def test_range_variances_positive_and_ordered():
    bars = [
        _bar(i, 100.0 + 0.01 * (i % 5), 100.5, 99.5, 100.1 + 0.01 * (i % 3)) for i in range(50)
    ]

    estimate = estimate_variances(bars)

    assert estimate.parkinson > 0
    assert estimate.garman_klass > 0
    assert estimate.rogers_satchell > 0
    assert estimate.close_to_close > 0
    assert estimate.bars == 50


def test_range_wider_than_close_marks_bounce():
    # широкий диапазон при почти неизменном закрытии — типичный bounce
    bars = [_bar(i, 100.0, 101.0, 99.0, 100.01 if i % 2 else 100.0) for i in range(40)]

    estimate = estimate_variances(bars)

    assert estimate.noise_ratio > 2
    assert estimate.flag == BOUNCE


def test_flat_series_rejected():
    bars = [_bar(i, 100.0, 100.0, 100.0, 100.0) for i in range(5)]

    with pytest.raises(VarianceError):
        estimate_variances(bars)


def test_variance_ratio_is_near_one_for_noise_like_returns():
    returns = [((index * 7919) % 101 - 50) / 100_000 for index in range(400)]

    ratio = variance_ratio(returns, 2)

    assert 0.5 <= ratio <= 2.0


def test_variance_ratio_grows_for_trending_series():
    returns = [0.001] * 200

    with pytest.raises(VarianceError):
        variance_ratio(returns, 2)  # нулевая дисперсия базовых доходностей


def test_variance_ratio_requires_enough_data():
    with pytest.raises(VarianceError):
        variance_ratio([0.001, -0.001, 0.002], 4)


def test_sigma_tau_uses_variance_ratio():
    assert sigma_tau(0.01, 10, 0.5) == pytest.approx(0.01 * sqrt(10 * 0.5))


def test_sigma_tau_rejects_non_positive_vr():
    with pytest.raises(VarianceError):
        sigma_tau(0.01, 10, 0.0)


def test_variance_ratio_curve_covers_requested_taus():
    returns = []
    for index in range(600):
        returns.append(0.001 if index % 3 else -0.0005)

    curve = variance_ratio_curve(returns, (1, 5, 10))

    assert set(curve) == {1, 5, 10}
    assert curve[1] == pytest.approx(1.0)


def test_deseasonalize_returns_uses_minute_profile():
    schedule = SessionSchedule()
    bars = []
    price = 100.0
    for day in range(3):
        for minute in range(60):
            if minute == 1:
                price *= 1.01  # открытие сессии: доходность первой минуты выше типичной
            else:
                price *= 1.0001
            bars.append(
                Bar(
                    ts=datetime(2026, 11, 16 + day, 10, minute, tzinfo=MSK).astimezone(
                        timezone.utc
                    ),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=100,
                )
            )

    result = deseasonalize_returns(bars, schedule, min_observations=2)

    assert len(result.returns) > 0
    assert result.profile[1] > 1.5
    assert result.profile[2] == pytest.approx(1.0, abs=0.2)
    assert result.flags == {}


def test_deseasonalize_flags_minutes_with_few_observations():
    schedule = SessionSchedule()
    bars = []
    price = 100.0
    for minute in range(10):
        price *= 1.0005
        bars.append(
            Bar(
                ts=datetime(2026, 11, 18, 10, minute, tzinfo=MSK).astimezone(timezone.utc),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=100,
            )
        )

    result = deseasonalize_returns(bars, schedule, min_observations=5)

    assert "deseasonalization" in result.flags
    assert all(value == 1.0 for value in result.profile.values())


def test_deseasonalize_requires_in_session_returns():
    schedule = SessionSchedule()
    bars = [
        Bar(
            ts=datetime(2026, 11, 21, 12, minute, tzinfo=MSK).astimezone(timezone.utc),
            open=100.0,
            high=100.1,
            low=99.9,
            close=100.0,
            volume=100,
        )
        for minute in range(20)
    ]

    with pytest.raises(VarianceError):
        deseasonalize_returns(bars, schedule, min_observations=5)


def test_parkinson_matches_closed_form():
    high, low = 100.2, 99.9
    bars = [_bar(i, 100.0, high, low, 100.0 + 0.01 * (i % 3)) for i in range(20)]

    estimate = estimate_variances(bars)

    expected = log(high / low) ** 2 / (4 * log(2.0))
    assert estimate.parkinson == pytest.approx(expected, rel=1e-9)
