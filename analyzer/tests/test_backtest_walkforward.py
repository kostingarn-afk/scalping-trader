"""Тесты walk-forward: границы окон, отсечка прогрева, выбор кандидата, сводка."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from typing import Dict, List

import pytest

from analyzer.backtest.commission import CommissionModel
from analyzer.backtest.models import Bar, Direction, ExitReason, Instrument, RunConfig, Trade
from analyzer.backtest.walkforward import (
    TradeStats,
    build_folds,
    format_walkforward_markdown,
    run_walkforward,
    trades_from,
)
from analyzer.strategy import Signal, SignalType

START = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)
INSTRUMENT = Instrument("SBER", "TQBR", lot_size=1)


def _bars(count: int) -> List[Bar]:
    """Плоский ряд, где TP достигается на каждом баре: сделка живёт один бар."""
    return [
        Bar(
            ts=START + timedelta(minutes=index),
            open=100.0,
            high=103.0,
            low=99.5,
            close=100.0,
            volume=100,
        )
        for index in range(count)
    ]


def _config() -> RunConfig:
    return RunConfig(
        max_bars_in_trade=3,
        min_candles=25,
        window=25,
        fill_on_next_bar_open=False,
    )


def _builder(tp_width: float):  # noqa: ANN001
    """Фабрика: широкая цель даёт прибыль, узкая — net < 0 из-за комиссии."""

    def build(params: Dict[str, float]):  # noqa: ANN001
        width = params["tp_width"]

        def strategy(ind, price, atr_mult_sl, atr_mult_tp):  # noqa: ANN001
            return Signal(
                SignalType.BUY,
                0.9,
                price,
                round(price * 0.99, 9),
                round(price * (1 + width), 9),
            )

        return strategy, f"tp{width:g}"

    return build


def test_build_folds_uses_full_windows_only():
    folds = build_folds(total_bars=100, train_bars=40, test_bars=20)

    assert [(fold.train_start, fold.train_end, fold.test_start, fold.test_end) for fold in folds] == [
        (0, 40, 40, 60),
        (20, 60, 60, 80),
        (40, 80, 80, 100),
    ]
    # 100 + 20 = 120 > 100: следующий блок не помещается и отбрасывается.
    assert len(folds) == 3


def test_build_folds_rejects_invalid_sizes():
    with pytest.raises(ValueError):
        build_folds(total_bars=100, train_bars=0, test_bars=10)
    with pytest.raises(ValueError):
        build_folds(total_bars=100, train_bars=10, test_bars=0)


def _trade(entry_minute: int, net: float) -> Trade:
    entry_ts = START + timedelta(minutes=entry_minute)
    return Trade(
        direction=Direction.LONG,
        entry_ts=entry_ts,
        exit_ts=entry_ts + timedelta(minutes=1),
        entry_price=100.0,
        exit_price=100.0 + net,
        sl=99.0,
        tp=102.0,
        lots=10,
        lot_size=1,
        exit_reason=ExitReason.TAKE_PROFIT if net > 0 else ExitReason.STOP_LOSS,
        bars_held=1,
        notional=1000.0,
        gross_pnl=net + 5.0,
        commission=5.0,
        net_pnl=net,
    )


def test_trades_from_drops_warmup_trades():
    trades = [_trade(1, -5.0), _trade(10, 5.0), _trade(20, 7.0)]

    kept = trades_from(trades, START + timedelta(minutes=10))

    assert [trade.net_pnl for trade in kept] == [5.0, 7.0]


def test_trade_stats_metrics():
    stats = TradeStats.of([_trade(1, 10.0), _trade(2, -5.0), _trade(3, 5.0)])

    assert stats.trades == 3
    assert stats.wins == 2
    assert stats.net == pytest.approx(10.0)
    assert stats.win_pct == pytest.approx(200.0 / 3)
    assert stats.ratio == pytest.approx(stats.gross / stats.commission)
    assert stats.t_stat != 0.0


def test_trade_stats_without_trades():
    stats = TradeStats.of([])

    assert stats.trades == 0
    assert stats.ratio is None
    assert stats.win_pct == 0.0
    assert stats.t_stat == 0.0


def test_merge_sums_and_keeps_pnls():
    merged = TradeStats.of([_trade(1, 10.0)]).merge(TradeStats.of([_trade(2, -4.0)]))

    assert merged.trades == 2
    assert merged.net == pytest.approx(6.0)
    assert merged.pnls == (10.0, -4.0)


def test_walkforward_picks_profitable_variant_on_train():
    """Узкая цель даёт net < 0 (комиссия больше прибыли) — выбор должен падать на широкую."""
    result = run_walkforward(
        bars=_bars(120),
        instrument=INSTRUMENT,
        config=_config(),
        commission=CommissionModel("trader"),
        interval_minutes=1,
        grid=[{"tp_width": 0.02}, {"tp_width": 0.0001}],
        build_strategy=_builder(0.02),
        train_bars=60,
        test_bars=20,
        min_train_trades=5,
    )

    assert len(result.folds) == 3
    assert all(fold.label == "tp0.02" for fold in result.folds)
    assert all(fold.train.net > 0 for fold in result.folds)
    assert result.out_of_sample.trades > 0


def test_walkforward_marks_fold_without_candidate():
    result = run_walkforward(
        bars=_bars(120),
        instrument=INSTRUMENT,
        config=_config(),
        commission=CommissionModel("trader"),
        interval_minutes=1,
        grid=[{"tp_width": 0.02}],
        build_strategy=_builder(0.02),
        train_bars=60,
        test_bars=20,
        min_train_trades=10_000,
    )

    assert all(not fold.chosen for fold in result.folds)
    assert "нет кандидата" in result.folds[0].note
    assert result.out_of_sample.trades == 0


def test_walkforward_rejects_insufficient_bars():
    with pytest.raises(ValueError):
        run_walkforward(
            bars=_bars(30),
            instrument=INSTRUMENT,
            config=_config(),
            commission=CommissionModel("trader"),
            interval_minutes=1,
            grid=[{"tp_width": 0.02}],
            build_strategy=_builder(0.02),
            train_bars=60,
            test_bars=20,
        )


def test_walkforward_rejects_empty_grid():
    with pytest.raises(ValueError):
        run_walkforward(
            bars=_bars(120),
            instrument=INSTRUMENT,
            config=_config(),
            commission=CommissionModel("trader"),
            interval_minutes=1,
            grid=[],
            build_strategy=_builder(0.02),
            train_bars=60,
            test_bars=20,
        )


def test_markdown_lists_folds_and_out_of_sample():
    result = run_walkforward(
        bars=_bars(120),
        instrument=INSTRUMENT,
        config=_config(),
        commission=CommissionModel("trader"),
        interval_minutes=1,
        grid=[{"tp_width": 0.02}],
        build_strategy=_builder(0.02),
        train_bars=60,
        test_bars=20,
        min_train_trades=5,
    )

    report = format_walkforward_markdown([result])

    assert "walk-forward" in report.lower()
    assert "SBER@TQBR 1min trader" in report
    assert "OOS" in report
    assert "Гейт" in report


def test_markdown_without_results_reports_empty():
    assert "Прогонов нет" in format_walkforward_markdown([])


def test_walkforward_carries_target_policy_into_result_and_report():
    """Политика цели `k×ATR` видна в результате и в шапке: иначе прогон не отличить от прежних."""
    result = run_walkforward(
        bars=_bars(120),
        instrument=INSTRUMENT,
        config=replace(_config(), min_tp_pct=0.5),
        commission=CommissionModel("trader"),
        interval_minutes=1,
        grid=[{"tp_width": 0.02}],
        build_strategy=_builder(0.02),
        train_bars=60,
        test_bars=20,
        min_train_trades=5,
    )

    report = format_walkforward_markdown([result])

    assert result.min_tp_pct == pytest.approx(0.5)
    assert "TP k×ATR от порога комиссии 0.50 %" in report


def test_walkforward_reports_truncated_warmup():
    """Прогрев короче `warmup` модели не должен оставаться незаметным."""

    def build_with_warmup(params: Dict[str, float]):  # noqa: ANN001
        strategy, label = _builder(0.02)(params)
        strategy.warmup = 999  # больше длины train=60
        return strategy, label

    result = run_walkforward(
        bars=_bars(120),
        instrument=INSTRUMENT,
        config=_config(),
        commission=CommissionModel("trader"),
        interval_minutes=1,
        grid=[{"tp_width": 0.02}],
        build_strategy=build_with_warmup,
        train_bars=60,
        test_bars=20,
        min_train_trades=5,
    )

    report = format_walkforward_markdown([result])

    assert result.truncated_folds
    assert "Прогрев модели усечён" in report
