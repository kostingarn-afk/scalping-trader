"""Тесты нулевой модели: детерминизм, границы p-value, разбор формул."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from analyzer.backtest.commission import CommissionModel
from analyzer.backtest.models import Bar, Instrument, RunConfig
from analyzer.backtest.nullmodel import format_null_markdown, run_null

START = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)
INSTRUMENT = Instrument("SBER", "TQBR", lot_size=1)


def _bars(count: int) -> List[Bar]:
    """Плоский ряд: случайные входы закрываются таймаутом без движения цены."""
    return [
        Bar(
            ts=START + timedelta(minutes=index),
            open=100.0,
            high=100.2,
            low=99.8,
            close=100.0,
            volume=100,
        )
        for index in range(count)
    ]


def _config() -> RunConfig:
    return RunConfig(max_bars_in_trade=3, min_candles=25, window=25, fill_on_next_bar_open=False)


def _run(actual=(0.0, 10), seeds: int = 10, seed0: int = 0, rate: float = 0.1):
    return run_null(
        bars=_bars(120),
        instrument=INSTRUMENT,
        config=_config(),
        commission=CommissionModel("trader"),
        interval_minutes=1,
        actual=actual,
        rate=rate,
        seeds=seeds,
        seed0=seed0,
    )


def test_null_is_deterministic_for_same_seed():
    first = _run(seeds=5, seed0=7)
    second = _run(seeds=5, seed0=7)

    assert first.nets == second.nets
    assert first.trades == second.trades


def test_null_differs_for_other_seed():
    assert _run(seeds=10, seed0=7).nets != _run(seeds=10, seed0=99).nets


def test_null_produces_trades_and_valid_p_value():
    result = _run(seeds=10, rate=0.2)

    assert result.seeds == 10
    assert len(result.nets) == 10
    assert sum(result.trades) > 0, "при rate=0.2 сделки должны появляться"
    assert 0.0 < result.p_value <= 1.0
    assert 0.0 <= result.percentile <= 100.0


def test_p_value_uses_plus_one_correction():
    """Фактический результат лучше всех случайных: p = 1/(N+1), а не 0."""
    result = _run(actual=(10_000_000.0, 10), seeds=20)

    assert result.p_value == pytest.approx(1 / 21)
    assert result.percentile == pytest.approx(100.0)


def test_p_value_is_high_when_actual_matches_null():
    # На плоском ряду случайная сделка даёт ровно −200 ₽ (только комиссия): −2000 за 10 сделок
    # это ровно медиана распределения, то есть преимущества нет.
    result = _run(actual=(-2000.0, 10), seeds=20)

    assert result.p_value > 0.2, "факт в теле распределения — сигнала нет"


def test_rate_defaults_to_actual_trade_share():
    result = run_null(
        bars=_bars(120),
        instrument=INSTRUMENT,
        config=_config(),
        commission=CommissionModel("trader"),
        interval_minutes=1,
        actual=(0.0, 10),
        seeds=2,
    )

    # 120 баров − min_candles 25 = 95 доступных, 10 сделок → 10/95.
    assert result.rate == pytest.approx(10 / 95)


def test_zero_seeds_is_rejected():
    with pytest.raises(ValueError):
        _run(seeds=0)


def test_zero_rate_is_rejected():
    """Прогон без сделок не задаёт частоту входа — сравнивать нечего."""
    with pytest.raises(ValueError):
        run_null(
            bars=_bars(120),
            instrument=INSTRUMENT,
            config=_config(),
            commission=CommissionModel("trader"),
            interval_minutes=1,
            actual=(0.0, 0),
            seeds=2,
        )


def test_markdown_lists_p_value_and_percentile():
    report = format_null_markdown([_run(seeds=5)], "Нулевая модель")

    assert "p-value" in report
    assert "Перцентиль" in report
    assert "SBER@TQBR 1min trader" in report


def test_markdown_without_results_reports_empty():
    assert "Прогонов нет" in format_null_markdown([])
