"""Тесты гейта §1 (`backtest/gate.py`)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.backtest.gate import (
    MIN_DAYS,
    MIN_OOS_TRADES,
    evaluate_gate,
    evaluate_trades,
)
from analyzer.backtest.models import (
    BacktestResult,
    Direction,
    ExitReason,
    Instrument,
    RunConfig,
    Trade,
)

START = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
DEPOSIT = 200_000.0
LOTS = 10
LOT_SIZE = 1


def _trade(net: float, when: datetime, commission: float = 50.0) -> Trade:
    """Сделка с заданным net; gross = net + commission, `exit_price` согласован с gross."""
    entry_price = 100.0
    gross = net + commission
    return Trade(
        direction=Direction.LONG,
        entry_ts=when,
        exit_ts=when + timedelta(minutes=3),
        entry_price=entry_price,
        exit_price=entry_price + gross / (LOTS * LOT_SIZE),
        sl=99.0,
        tp=None,
        lots=LOTS,
        lot_size=LOT_SIZE,
        exit_reason=ExitReason.TIMEOUT,
        bars_held=3,
        notional=entry_price * LOTS * LOT_SIZE,
        gross_pnl=gross,
        commission=commission,
        net_pnl=net,
    )


def _days(daily: list[float], commission: float = 50.0) -> list[Trade]:
    """По одной сделке в день: дневной net задаётся напрямую."""
    return [_trade(value, START + timedelta(days=day), commission)
            for day, value in enumerate(daily)]


def _series(nets: list[float], per_day: int = 3, commission: float = 50.0) -> list[Trade]:
    """Разложить net по дням по `per_day` сделок — даёт дни и разброс для t-статистики."""
    trades = []
    for index, net in enumerate(nets):
        when = START + timedelta(days=index // per_day, minutes=index % per_day)
        trades.append(_trade(net, when, commission))
    return trades


def _result(trades: list[Trade], deposit: float = DEPOSIT) -> BacktestResult:
    return BacktestResult(
        instrument=Instrument("SBER", "TQBR", lot_size=LOT_SIZE),
        interval_minutes=1,
        tariff="trader",
        config=RunConfig(deposit=deposit),
        bars=1_000,
        signals=len(trades),
        filtered_signals=0,
        skipped_by_notional=0,
        trades=tuple(trades),
        equity_final=deposit,
        max_drawdown_pct=0.0,
    )


def _passing_trades(count: int = 60, per_day: int = 3) -> list[Trade]:
    """Прогон, проходящий все четыре условия §1: gate > 1, t OOS ≥ 2, сделок ≥ 50,
    прибыль переживает удаление трёх лучших дней."""
    nets = [90.0 if index % 2 == 0 else 110.0 for index in range(count)]
    return _series(nets, per_day=per_day)


def test_gate_is_gross_over_commission():
    metrics = evaluate_trades(_days([100.0] * 60), DEPOSIT)

    # gross = 150 на сделку, commission = 50 → gate = 3
    assert metrics.gate == pytest.approx(3.0)
    assert metrics.gross_rub == pytest.approx(60 * 150.0)
    assert metrics.commission_rub == pytest.approx(60 * 50.0)


def test_gate_is_none_without_commission():
    metrics = evaluate_trades([_trade(100.0, START, commission=0.0)], DEPOSIT)

    assert metrics.gate is None
    assert any("gate" in check for check in metrics.failed_checks)


def test_gate_below_one_fails_verdict():
    # net = −100 при комиссии 50 → gross = −50: комиссия не покрыта
    metrics = evaluate_trades(_days([-100.0] * 60), DEPOSIT)

    assert metrics.gate is not None and metrics.gate < 1
    assert metrics.passed is False
    assert any("gate" in check for check in metrics.failed_checks)


def test_passing_run_satisfies_all_checks():
    metrics = evaluate_trades(_passing_trades(), DEPOSIT)

    assert metrics.passed is True, metrics.failed_checks
    assert metrics.failed_checks == ()
    assert metrics.trades >= 50
    assert metrics.oos_trades >= MIN_OOS_TRADES
    assert metrics.t_oos is not None and metrics.t_oos >= 2
    assert metrics.net_trimmed_rub > 0


def test_t_oos_is_none_when_oos_sample_is_small():
    # Все сделки в одном дне: OOS по времени собирает меньше MIN_OOS_TRADES наблюдений.
    metrics = evaluate_trades(_series([100.0] * 12, per_day=12), DEPOSIT)

    assert metrics.oos_trades < MIN_OOS_TRADES
    assert metrics.t_oos is None
    assert any("t OOS" in check for check in metrics.failed_checks)


def test_oos_uses_last_thirty_percent_of_time_not_trade_count():
    # Плотный первый день (20 сделок) и редкий хвост: OOS берёт хвост, а не счёт.
    dense = [_trade(100.0, START + timedelta(minutes=minute)) for minute in range(20)]
    tail = [_trade(100.0, START + timedelta(days=day)) for day in range(1, 15)]

    metrics = evaluate_trades(dense + tail, DEPOSIT)

    # Граница — 70 % от 14 дней ≈ 9.8 дня: в OOS попадают дни 10–14 (5 сделок).
    assert metrics.oos_trades == 5


def test_trimming_removes_three_best_days():
    # 5 дней: три по +300 и два по −50 → после удаления трёх лучших остаётся −100
    metrics = evaluate_trades(_days([300.0, 300.0, 300.0, -50.0, -50.0]), DEPOSIT)

    assert metrics.days == 5
    assert metrics.net_trimmed_rub == pytest.approx(-100.0)
    assert metrics.passed is False
    assert any("без 3 лучших" in check for check in metrics.failed_checks)


def test_trimming_keeps_profit_when_more_than_four_days():
    # 4 дня по +300: удаляем три лучших → остаётся +300
    metrics = evaluate_trades(_days([300.0, 300.0, 300.0, 300.0]), DEPOSIT)

    assert metrics.days == 4
    assert metrics.net_trimmed_rub == pytest.approx(300.0)


def test_diagnostics_missing_below_minimum_days():
    metrics = evaluate_trades(_days([100.0] * (MIN_DAYS - 1)), DEPOSIT)

    assert metrics.days < MIN_DAYS
    assert metrics.adf_p is None
    assert metrics.ljung_box_p is None
    assert metrics.jarque_bera_p is None
    assert metrics.excess_kurtosis is None


def test_diagnostics_computed_with_enough_days():
    metrics = evaluate_trades(_days([90.0, 110.0, 95.0, 105.0, 100.0] * 6), DEPOSIT)

    assert metrics.days >= MIN_DAYS
    assert metrics.adf_p is not None
    assert metrics.ljung_box_p is not None
    assert metrics.jarque_bera_p is not None
    assert metrics.excess_kurtosis is not None


def test_diagnostics_missing_for_flat_series():
    # Ряд без разброса: стандартное отклонение нулевое, тесты неопределимы.
    metrics = evaluate_trades(_days([100.0] * 30), DEPOSIT)

    assert metrics.adf_p is None
    assert metrics.excess_kurtosis is None


def test_empty_run_is_not_passed():
    metrics = evaluate_trades([], DEPOSIT)

    assert metrics.trades == 0
    assert metrics.gate is None
    assert metrics.net_trimmed_rub == 0.0
    assert metrics.t_oos is None
    assert metrics.passed is False


def test_evaluate_gate_reads_deposit_from_result():
    metrics = evaluate_gate(_result(_passing_trades()))

    assert metrics.net_trimmed_pct > 0
    assert metrics.passed is True


def test_gate_block_present_in_report():
    from analyzer.backtest.report import format_markdown

    report = format_markdown([_result(_passing_trades())], title="Прогон")

    assert "## Гейт §1" in report
    assert "Gate" in report
    assert "t всего" in report
    assert "пройден" in report


def test_gate_block_lists_failed_checks():
    from analyzer.backtest.report import format_markdown

    report = format_markdown([_result(_series([10.0] * 12, commission=200.0))])

    assert "не пройден" in report
    assert "gate" in report
