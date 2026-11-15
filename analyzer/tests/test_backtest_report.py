"""Тесты отчётности бэктеста."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from analyzer.backtest.models import (
    BacktestResult,
    Direction,
    ExitReason,
    Instrument,
    RunConfig,
    Trade,
)
from analyzer.backtest.report import (
    format_markdown,
    summarize,
    summary_json,
    trades_csv,
    write_report,
)

START = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)


def _trade(exit_price: float, reason: ExitReason) -> Trade:
    """Сделка с согласованной арифметикой: gross = (exit − entry) × lots × lot_size."""
    entry_price = 100.0
    lots = 10
    lot_size = 1
    commission = 5.0
    gross = (exit_price - entry_price) * lots * lot_size
    return Trade(
        direction=Direction.LONG,
        entry_ts=START,
        exit_ts=START + timedelta(minutes=3),
        entry_price=entry_price,
        exit_price=exit_price,
        sl=99.0,
        tp=102.0,
        lots=lots,
        lot_size=lot_size,
        exit_reason=reason,
        bars_held=3,
        notional=entry_price * lots * lot_size,
        gross_pnl=gross,
        commission=commission,
        net_pnl=gross - commission,
    )


def _result(trades: tuple[Trade, ...]) -> BacktestResult:
    return BacktestResult(
        instrument=Instrument("SBER", "TQBR", lot_size=1),
        interval_minutes=1,
        tariff="trader",
        config=RunConfig(),
        bars=100,
        signals=5,
        filtered_signals=2,
        skipped_by_notional=1,
        trades=trades,
        equity_final=200_200.0,
        max_drawdown_pct=0.15,
    )


def test_exits_block_shows_net_per_reason():
    """Блок показывает, сколько net принёс каждый тип выхода."""
    result = _result(
        (
            _trade(102.5, ExitReason.TAKE_PROFIT),
            _trade(99.0, ExitReason.STOP_LOSS),
        )
    )

    report = format_markdown([result])

    assert "Выходы: вклад каждого типа закрытия" in report
    assert "| SBER@TQBR 1min trader | TP | 1 | 100.0 | 20.00 | 400 |" in report
    assert "| SBER@TQBR 1min trader | SL | 1 | 0.0 | -15.00 | -300 |" in report


def test_exits_block_skips_missing_reasons():
    report = format_markdown([_result((_trade(102.5, ExitReason.TAKE_PROFIT),))])

    assert "| TP | 1 |" in report
    assert "| TIMEOUT |" not in report


def test_csv_leaves_tp_empty_when_disabled():
    trade = replace(_trade(101.0, ExitReason.TIMEOUT), tp=None)

    row = trades_csv([_result((trade,))]).strip().splitlines()[1].split(",")

    assert row[7] == "", "при выключенной цели поле tp должно быть пустым, а не нулём"


def test_parameters_line_marks_disabled_tp():
    result = replace(_result((_trade(102.5, ExitReason.TAKE_PROFIT),)), config=RunConfig(no_tp=True))

    report = format_markdown([result])

    assert "TP выключен" in report


def test_summary_aggregates_trades():
    # TP: (102.5 − 100) × 10 = +25 gross, −5 комиссии = +20 net.
    # SL: (99 − 100) × 10 = −10 gross, −5 комиссии = −15 net.
    result = _result(
        (
            _trade(102.5, ExitReason.TAKE_PROFIT),
            _trade(99.0, ExitReason.STOP_LOSS),
        )
    )

    summary = summarize(result)

    assert summary.trades == 2
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.win_rate_pct == pytest.approx(50.0)
    assert summary.take_profits == 1
    assert summary.stop_losses == 1
    assert summary.total_gross_rub == pytest.approx(15.0)
    assert summary.total_net_rub == pytest.approx(5.0)
    assert summary.total_net_pct == pytest.approx(0.0025)
    assert summary.avg_net_rub == pytest.approx(2.5)
    assert summary.expectancy_pct == pytest.approx(0.00125)
    assert summary.total_commission_rub == pytest.approx(10.0)
    assert summary.avg_bars_held == pytest.approx(3.0)
    assert summary.open_at_end is False


def test_zero_net_trade_is_not_counted_as_loss():
    # (100.5 − 100) × 10 = +5 gross, ровно 5 комиссии → net = 0.
    summary = summarize(_result((_trade(100.5, ExitReason.TIMEOUT),)))

    assert summary.trades == 1
    assert summary.wins == 0
    assert summary.losses == 0
    assert summary.total_net_rub == pytest.approx(0.0)


def test_summary_without_trades_is_zeroed():
    summary = summarize(_result(()))

    assert summary.trades == 0
    assert summary.win_rate_pct == 0.0
    assert summary.avg_net_rub == 0.0
    assert summary.expectancy_pct == 0.0


def test_markdown_contains_run_row_and_parameters():
    report = format_markdown([_result((_trade(102.5, ExitReason.TAKE_PROFIT),))])

    assert "SBER@TQBR 1min trader" in report
    assert "| Бары | Сигналы |" in report
    assert "| Открыта |" in report
    assert "депозит 200 000.00 ₽" in report
    assert "SL 1.50×ATR / TP 2.00×ATR" in report


def test_markdown_without_results_reports_empty_run():
    assert "Прогонов нет" in format_markdown([])


def test_markdown_marks_sweep_and_lists_leaders():
    """Перебор TP/SL: в заголовке — список пар, в конце — таблица лучших прогонов."""
    base = _result((_trade(102.5, ExitReason.TAKE_PROFIT),))
    wide = replace(
        base, config=replace(base.config, atr_mult_tp=3.0, atr_mult_sl=2.0), label="tp3/sl2"
    )
    narrow = replace(
        base, config=replace(base.config, atr_mult_tp=2.0, atr_mult_sl=1.5), label="tp2/sl1.5"
    )

    report = format_markdown([wide, narrow])

    assert "перебор TP:SL 3:2, 2:1.5" in report
    assert "SBER@TQBR 1min trader tp2/sl1.5" in report
    assert "### Лучшие прогоны по итогу" in report
    assert "| Gross/комиссия |" in report


def test_markdown_without_sweep_has_no_leaders_block():
    report = format_markdown([_result((_trade(102.5, ExitReason.TAKE_PROFIT),))])

    assert "### Лучшие прогоны" not in report


def test_markdown_lists_strategy_variants_with_equal_configs():
    """Варианты v2 различаются порогами стратегии, а не TP/SL: их печатает отдельная строка."""
    base = _result((_trade(102.5, ExitReason.TAKE_PROFIT),))
    first = replace(base, label="v2-z2-adx25", strategy="v2")
    second = replace(base, label="v2-z2.5-adx20", strategy="v2")

    report = format_markdown([first, second])

    assert "Стратегия: **v2**" in report
    assert "Варианты: v2-z2-adx25, v2-z2.5-adx20." in report
    assert "### Лучшие прогоны по итогу" in report


def test_markdown_prints_cooldown_when_set():
    base = _result((_trade(102.5, ExitReason.TAKE_PROFIT),))
    result = replace(base, config=replace(base.config, cooldown_bars=5))

    report = format_markdown([result])

    assert "cooldown 5 баров" in report


def test_trades_csv_has_header_and_rows():
    lines = trades_csv([_result((_trade(102.5, ExitReason.TAKE_PROFIT),))]).strip().splitlines()

    assert lines[0].startswith("run,direction,entry_ts")
    assert len(lines) == 2
    assert lines[1].startswith("SBER@TQBR 1min trader,LONG,")
    assert lines[1].endswith(",20.00")


def test_summary_json_is_machine_readable():
    payload = json.loads(summary_json([_result(())]))

    assert len(payload) == 1
    assert payload[0]["run"] == "SBER@TQBR 1min trader"
    assert payload[0]["trades"] == 0


def test_write_report_dispatch_by_suffix(tmp_path):
    result = _result((_trade(500.0, ExitReason.TAKE_PROFIT),))

    for name in ("report.md", "report.json", "report.csv"):
        path = tmp_path / name
        write_report(path, [result])
        assert path.exists() and path.stat().st_size > 0

    assert "| Прогон |" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert (tmp_path / "report.json").read_text(encoding="utf-8").lstrip().startswith("[")
