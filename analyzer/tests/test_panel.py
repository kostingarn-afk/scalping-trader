"""Тесты кросс-секционной панели: ранжирование, издержки, пропуски вселенной, нулевая модель."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from analyzer.backtest.loader import HistoryDataset
from analyzer.backtest.models import Bar, Instrument
from analyzer.backtest.panel import (
    SIGNAL_MODES,
    PanelConfig,
    PanelPeriod,
    PanelResult,
    aggregate_panel,
    apply_daily_stop,
    build_daily_closes,
    format_panel_markdown,
    format_panel_walkforward_markdown,
    rank_scores,
    run_panel,
    run_panel_null,
    run_panel_walkforward,
    select_universe,
)

START = date(2026, 1, 5)
GROWTH = {"A": 0.010, "B": 0.005, "C": 0.002, "D": -0.002, "E": -0.005, "F": -0.010}


def _dates(count: int) -> List[date]:
    return [START + timedelta(days=index) for index in range(count)]


def _panel(prices: Dict[str, List[float]]) -> pd.DataFrame:
    length = len(next(iter(prices.values())))
    return pd.DataFrame(prices, index=pd.Index(_dates(length), name="date"))


def _trending(days: int = 20) -> pd.DataFrame:
    """Устойчивое разделение: A растёт быстрее всех, F падает быстрее всех."""
    return _panel(
        {
            name: [100.0 * (1.0 + rate) ** step for step in range(days)]
            for name, rate in GROWTH.items()
        }
    )


def _config(**overrides) -> PanelConfig:
    params = {"lookback_days": 2, "rebalance_days": 2, "legs": 1, "cost_per_side": 0.0}
    params.update(overrides)
    return PanelConfig(**params)


def test_ranks_long_winners_and_shorts_losers():
    result = run_panel(_trending(), _config())

    first = result.periods[0]
    assert first.longs == ("A",)
    assert first.shorts == ("F",)
    assert first.gross > 0
    assert first.universe == 6


def test_net_is_gross_minus_cost():
    result = run_panel(_trending(), _config(cost_per_side=0.001))

    assert result.cost > 0
    assert result.net == pytest.approx(result.gross - result.cost)


def test_cost_is_charged_only_for_changed_legs():
    """Ранжирование устойчиво: имя остаётся в своей стороне и не переторговывается."""
    result = run_panel(_trending(), _config(cost_per_side=0.001))

    # Первый период открывает обе ноги: 2 ноги × 2 стороны × 0.1 % / (2 × 1) = 0.2 %.
    assert result.periods[0].cost == pytest.approx(0.002)
    assert all(period.cost == 0.0 for period in result.periods[1:])


def test_small_universe_is_skipped():
    frame = _panel({"A": [100.0 + step for step in range(10)], "B": [100.0 - step for step in range(10)]})

    result = run_panel(frame, _config(legs=2))

    assert result.periods == ()
    assert result.skipped > 0


def test_equal_returns_give_zero_gross():
    """Все бумаги растут одинаково — разделять нечего, книга нейтральна по построению."""
    frame = _panel(
        {name: [100.0 * 1.01**step for step in range(12)] for name in ("A", "B", "C", "D")}
    )

    result = run_panel(frame, _config())

    assert result.periods
    assert result.gross == pytest.approx(0.0, abs=1e-12)
    assert result.benchmark > 0, "корзина при этом растёт — сравнение с рынком осмысленно"


def test_null_shuffling_destroys_the_signal():
    frame = _trending(days=40)
    config = _config(cost_per_side=0.0005)
    actual = run_panel(frame, config)

    null = run_panel_null(frame, config, actual, seeds=50)

    assert null.seeds == 50
    assert 0.0 < null.p_value <= 1.0
    assert null.median < actual.net
    assert null.percentile >= 80, "факт должен стоять в верхней части распределения"


def test_null_is_deterministic_for_same_seed():
    frame = _trending(days=40)
    config = _config()
    actual = run_panel(frame, config)

    first = run_panel_null(frame, config, actual, seeds=5, seed0=11)
    second = run_panel_null(frame, config, actual, seeds=5, seed0=11)

    assert first.means == second.means


def test_null_rejects_zero_seeds():
    config = _config()
    actual = run_panel(_trending(), config)

    with pytest.raises(ValueError):
        run_panel_null(_trending(), config, actual, seeds=0)


def test_walkforward_prefers_configuration_that_survives_costs():
    frame = _trending(days=40)
    cheap = _config(cost_per_side=0.0)
    expensive = _config(cost_per_side=0.05)

    folds = run_panel_walkforward(
        frame, [expensive, cheap], train_days=15, test_days=8, min_periods=2
    )

    assert folds
    assert all(fold.chosen for fold in folds)
    assert all(fold.label == cheap.label for fold in folds)
    oos = aggregate_panel([fold.test for fold in folds])
    assert oos.net > 0


def test_walkforward_requires_enough_days():
    with pytest.raises(ValueError):
        run_panel_walkforward(_trending(days=10), [_config()], train_days=50, test_days=20)


def test_walkforward_rejects_empty_grid():
    with pytest.raises(ValueError):
        run_panel_walkforward(_trending(), [], train_days=10, test_days=5)


def test_build_daily_closes_takes_last_close_of_day():
    day = datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)
    bars = tuple(
        Bar(
            ts=day + timedelta(hours=hour),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0 + hour,
            volume=10,
        )
        for hour in range(3)
    )
    dataset = HistoryDataset(
        instrument=Instrument("SBER"), interval_minutes=60, bars=bars, source=Path("sber.csv")
    )

    closes = build_daily_closes([dataset])

    assert list(closes.index) == [day.date()]
    assert closes.loc[day.date(), "SBER"] == pytest.approx(102.0)


def test_build_daily_closes_without_bars_is_empty():
    dataset = HistoryDataset(
        instrument=Instrument("SBER"), interval_minutes=60, bars=(), source=Path("sber.csv")
    )

    assert build_daily_closes([dataset]).empty


def test_markdown_lists_runs_benchmark_and_null():
    frame = _trending(days=40)
    config = _config(cost_per_side=0.0005)
    result = run_panel(frame, config)
    null = run_panel_null(frame, config, result, seeds=20)

    report = format_panel_markdown([result], [null])

    assert "Кросс-секционный momentum" in report
    assert "Равновзвешенная корзина" in report
    assert "panel L2-R2-k1" in report
    assert "p-value" in report


def test_walkforward_markdown_has_oos_row():
    frame = _trending(days=40)
    folds = run_panel_walkforward(frame, [_config()], train_days=15, test_days=8, min_periods=2)

    report = format_panel_walkforward_markdown(folds)

    assert "OOS" in report
    assert "Гейт" in report


@pytest.mark.parametrize(
    "kwargs",
    [{"lookback_days": 0}, {"rebalance_days": 0}, {"legs": 0}, {"cost_per_side": -0.1}],
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        PanelConfig(**kwargs)


def test_label_includes_non_default_cost():
    assert PanelConfig().label == "L5-R5-k4"
    assert PanelConfig(cost_per_side=0.0).label == "L5-R5-k4-c0"


def test_select_universe_keeps_named_columns_only():
    frame = _panel({"A": [1.0, 2.0], "B": [3.0, 4.0], "C": [5.0, 6.0]})

    selected = select_universe(frame, "a, C")

    assert list(selected.columns) == ["A", "C"]
    assert select_universe(frame, "") is frame


def test_select_universe_rejects_unknown_ticker():
    frame = _panel({"A": [1.0, 2.0]})

    with pytest.raises(ValueError):
        select_universe(frame, "A,NOPE")


# --- Ранжирование с очисткой от рынка -------------------------------------------------


def test_rank_scores_raw_returns_momentum_unchanged():
    momentum = np.array([0.05, -0.02])
    window = pd.DataFrame({"A": [0.01, 0.02], "B": [-0.01, 0.0]})

    scores = rank_scores("raw", momentum, window)

    assert scores is momentum


def test_rank_scores_residual_strips_market_beta():
    """Бумага с высокой бетой выше по сырой доходности, но по остатку — ниже."""
    market = np.array([0.02, -0.01, 0.015, -0.005, 0.01, -0.02])
    window = pd.DataFrame(
        {
            "BETA": 1.8 * market,
            "ALPHA": 0.2 * market + 0.004,
        }
    )
    momentum = np.array([1.8 * (0.02 - 0.01), 0.2 * (0.02 - 0.01) + 2 * 0.004])

    scores = rank_scores("residual", momentum, window)

    assert momentum[0] > momentum[1], "сырая доходность ставит BETA выше"
    assert scores[1] > scores[0], "после вычета рынка выше оказывается ALPHA"


def test_rank_scores_residual_falls_back_to_momentum_without_market_variance():
    momentum = np.array([0.03, 0.01])
    window = pd.DataFrame({"A": [0.01, 0.01], "B": [0.01, 0.01]})

    scores = rank_scores("residual", momentum, window)

    assert scores == pytest.approx(momentum)


def test_rank_scores_volscaled_penalises_volatility():
    momentum = np.array([0.04, 0.04])
    window = pd.DataFrame({"CALM": [0.01, -0.005, 0.01], "WILD": [0.05, -0.03, 0.05]})

    scores = rank_scores("volscaled", momentum, window)

    assert scores[0] > scores[1]


def test_rank_scores_volscaled_puts_zero_risk_on_the_edge():
    momentum = np.array([0.04, -0.02])
    window = pd.DataFrame({"STEADY": [0.01, 0.01, 0.01], "SHAKY": [0.05, -0.03, 0.05]})

    scores = rank_scores("volscaled", momentum, window)

    assert scores[0] == np.inf
    assert scores[1] < 0


def test_rank_scores_rejects_unknown_mode():
    with pytest.raises(ValueError):
        rank_scores("excess", np.array([0.1]), pd.DataFrame({"A": [0.1, 0.2]}))


def test_signal_mode_is_part_of_label_and_validated():
    assert PanelConfig(signal="residual").label == "L5-R5-k4-resi"
    assert PanelConfig(signal="volscaled").label == "L5-R5-k4-vols"
    assert set(SIGNAL_MODES) == {"raw", "residual", "volscaled"}
    with pytest.raises(ValueError):
        PanelConfig(signal="excess")


# --- Дневной стоп книги --------------------------------------------------------------


def test_daily_stop_truncates_period_on_breach():
    """Книга теряет 10 % за первый день периода при лимите 1 % — выход в тот же день."""
    frame = _panel(
        {
            "WIN": [100.0, 110.0, 99.0, 98.0, 97.0],
            "LOSE": [100.0, 90.0, 100.0, 100.0, 100.0],
            "FLAT1": [100.0] * 5,
            "FLAT2": [100.0] * 5,
        }
    )
    config = _config(lookback_days=1, rebalance_days=3, legs=1)

    without_stop = run_panel(frame, config)
    with_stop = run_panel(frame, _config(lookback_days=1, rebalance_days=3, legs=1, daily_stop_pct=1.0))

    assert without_stop.periods[0].longs == ("WIN",)
    assert without_stop.periods[0].end == _dates(5)[4]
    assert with_stop.periods[0].end == _dates(5)[2]
    assert with_stop.periods[0].gross < 0


def test_daily_stop_disabled_keeps_period_end():
    frame = _panel({"A": [100.0, 104.0, 108.0], "B": [100.0, 96.0, 92.0]})

    stopped = apply_daily_stop(frame, ("A",), ("B",), 0, 2, stop_pct=0.0)

    assert stopped == 2


# --- Правило проверки на концентрацию ------------------------------------------------


def _period(net: float) -> PanelPeriod:
    return PanelPeriod(
        start=date(2026, 1, 5),
        end=date(2026, 1, 15),
        universe=6,
        longs=("A",),
        shorts=("B",),
        gross=net,
        cost=0.0,
        net=net,
        benchmark=0.0,
    )


def test_trimmed_removes_best_periods():
    """Прибыль держится на трёх периодах — после обрезки почти ничего не остаётся."""
    result = PanelResult(
        config=PanelConfig(),
        periods=tuple(
            [_period(0.10), _period(0.10), _period(0.10)] + [_period(0.001) for _ in range(7)]
        ),
    )

    trimmed_net, trimmed_t = result.trimmed()

    assert result.net == pytest.approx(0.307)
    assert trimmed_net == pytest.approx(0.007)
    assert trimmed_t < 1.0


def test_trimmed_reports_missing_periods_as_zero():
    result = PanelResult(config=PanelConfig(), periods=(_period(0.05), _period(0.04)))

    assert result.trimmed() == (0.0, 0.0)


def test_trimmed_keeps_broad_result():
    """Ровный плюс по всем периодам обрезка не ломает — это и есть устойчивый результат."""
    steady = [0.010, 0.012, 0.008, 0.011, 0.009, 0.010, 0.012, 0.008, 0.010]
    result = PanelResult(
        config=PanelConfig(),
        periods=tuple([_period(0.020), _period(0.018), _period(0.016)]
                      + [_period(value) for value in steady]),
    )

    trimmed_net, trimmed_t = result.trimmed()

    assert trimmed_net == pytest.approx(sum(steady))
    assert trimmed_t > 5


def test_report_contains_concentration_columns():
    result = run_panel(_trending(), _config(cost_per_side=0.0005))

    report = format_panel_markdown([result])

    assert "Net без 3 лучших %" in report
    assert "проверки на концентрацию" in report
