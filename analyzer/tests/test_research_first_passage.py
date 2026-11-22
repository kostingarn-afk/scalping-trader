"""Тесты сигналов, первого прохода и выбора горизонта (волна 1 R3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from analyzer.backtest.models import Bar
from analyzer.research.ev import (
    BOUNDARY,
    NO_TAU,
    SCOPE_VIOLATION,
    entry_decision,
    objective,
    select_tau,
)
from analyzer.research.first_passage import (
    STATUS_DEGENERATE,
    STATUS_NO_DATA,
    STATUS_OK,
    NEUTRAL,
    SL_WIN,
    TP_WIN,
    EVCurve,
    EVPoint,
    FirstPassageError,
    build_curve,
    resolve_path,
)
from analyzer.research.sessions import SessionSchedule
from analyzer.research.signals import (
    MIN_HISTORY,
    Signal,
    SignalError,
    ou_signals,
    session_ohlcv,
)

MSK = timezone(timedelta(hours=3))


# --- построители ----------------------------------------------------------


def _bar(ts: datetime, price: float, *, high: float | None = None, low: float | None = None,
         volume: int = 100) -> Bar:
    return Bar(
        ts=ts,
        open=price,
        high=price if high is None else high,
        low=price if low is None else low,
        close=price,
        volume=volume,
    )


def _schedule() -> SessionSchedule:
    return SessionSchedule.single(10 * 60, 18 * 60 + 40)


def _session(closes, *, highs=None, lows=None, volume: float = 100.0):
    close = np.asarray(closes, dtype=float)
    return {
        "open": close.copy(),
        "high": close.copy() if highs is None else np.asarray(highs, dtype=float),
        "low": close.copy() if lows is None else np.asarray(lows, dtype=float),
        "close": close,
        "volume": np.full(len(close), volume),
    }


def _signal(session: int, index: int, direction: int, z: float = -3.0) -> Signal:
    return Signal(session=session, index=index, direction=direction, z=z, price=100.0)


def _point(tau: int, ev: float, lcb: float, var: float = 1.0) -> EVPoint:
    return EVPoint(
        tau=tau,
        p_tp=0.4,
        p_sl=0.4,
        p_n=0.2,
        ev=ev,
        var=var,
        ev_lcb=lcb,
        ev_ucb=ev,
        paths=100,
        clusters=10,
    )


def _curve(points) -> EVCurve:
    return EVCurve(
        tp_ticks=2,
        sl_ticks=2,
        cost_ticks=0.5,
        protocol="SL_FIRST_EMPIRICAL_1M",
        block_rule="test",
        bootstrap=20,
        min_paths=2,
        min_cluster_bars=600,
        points=tuple(points),
    )


# --- первое касание -------------------------------------------------------


def test_tp_hit_before_sl():
    arrays = _session([100, 101, 102], highs=[100, 101, 102.5], lows=[100, 100.5, 101.5])

    kind, value = resolve_path(arrays, 0, 2, direction=1, tp_ticks=2, sl_ticks=2, tick_value=1.0)

    assert kind == TP_WIN
    assert value == pytest.approx(2.0)


def test_sl_hit_before_tp():
    arrays = _session([100, 98], highs=[100, 98.5], lows=[100, 97.5])

    kind, value = resolve_path(arrays, 0, 1, direction=1, tp_ticks=2, sl_ticks=2, tick_value=1.0)

    assert kind == SL_WIN
    assert value == pytest.approx(-2.0)


def test_sl_wins_when_both_hit_in_same_bar():
    """Конвенция §4: бар задел оба барьера — исход SL (смещение EV вниз)."""
    arrays = _session([100, 101], highs=[100, 103], lows=[100, 97])

    kind, _ = resolve_path(arrays, 0, 1, direction=1, tp_ticks=2, sl_ticks=2, tick_value=1.0)

    assert kind == SL_WIN


def test_short_direction_mirrors():
    arrays = _session([100, 98], highs=[100, 99], lows=[100, 97.5])

    kind, value = resolve_path(arrays, 0, 1, direction=-1, tp_ticks=2, sl_ticks=2, tick_value=1.0)

    assert kind == TP_WIN
    assert value == pytest.approx(2.0)


def test_short_stopped_out_by_upper_barrier():
    """Для шорта убыточный бар — рост: тот же бар, что даёт прибыль лонгу, закрывает шорт по SL."""
    arrays = _session([100, 101], highs=[100, 103], lows=[100, 100])

    kind, _ = resolve_path(arrays, 0, 1, direction=-1, tp_ticks=2, sl_ticks=2, tick_value=1.0)

    assert kind == SL_WIN


def test_path_not_fitting_window_returns_none():
    arrays = _session([100, 101])

    assert resolve_path(arrays, 0, 5, direction=1, tp_ticks=2, sl_ticks=2, tick_value=1.0) is None


def test_neutral_outcome_uses_close_move():
    arrays = _session([100, 100.5, 101])

    kind, value = resolve_path(arrays, 0, 2, direction=1, tp_ticks=10, sl_ticks=10, tick_value=1.0)

    assert kind == NEUTRAL
    assert value == pytest.approx(1.0)


def test_resolve_path_rejects_bad_inputs():
    arrays = _session([100, 100])

    with pytest.raises(FirstPassageError):
        resolve_path(arrays, 0, 0, direction=1, tp_ticks=1, sl_ticks=1, tick_value=1.0)
    with pytest.raises(FirstPassageError):
        resolve_path(arrays, 0, 1, direction=0, tp_ticks=1, sl_ticks=1, tick_value=1.0)
    with pytest.raises(FirstPassageError):
        resolve_path(arrays, 5, 1, direction=1, tp_ticks=1, sl_ticks=1, tick_value=1.0)
    with pytest.raises(FirstPassageError):
        resolve_path(arrays, 0, 1, direction=1, tp_ticks=0, sl_ticks=1, tick_value=1.0)


# --- EV-кривая ------------------------------------------------------------


def _two_session_curve(*, taus=(2,), min_paths: int = 2, session_len: int = 10):
    """Сессия 0 ведёт к TP, сессия 1 — к SL: дисперсия исходов ненулевая."""
    tp_session = _session([100] * session_len, highs=[102] * session_len)
    sl_session = _session([100] * session_len, lows=[98] * session_len)
    signals = [_signal(0, 0, 1), _signal(1, 0, 1)]
    return build_curve(
        [tp_session, sl_session],
        signals,
        tp_ticks=2,
        sl_ticks=2,
        cost_ticks=0.5,
        tick_value=1.0,
        taus=taus,
        bootstrap=50,
        min_paths=min_paths,
    )


def test_curve_probabilities_and_ev_subtract_cost():
    curve = _two_session_curve()

    point = curve.points[0]

    assert point.status == STATUS_OK
    assert point.p_tp == pytest.approx(0.5)
    assert point.p_sl == pytest.approx(0.5)
    assert point.p_n == pytest.approx(0.0)
    assert point.ev == pytest.approx(0.0 - 0.5)
    assert point.var == pytest.approx(8.0)  # исходы +2 и −2, ddof=1


def test_curve_bootstrap_interval_brackets_estimate():
    curve = _two_session_curve()

    point = curve.points[0]

    assert point.clusters == 2
    assert point.ev_lcb < point.ev < point.ev_ucb


def test_single_cluster_marks_interval_degenerate():
    """Один торговый окно — один блок: интервал вырождается, и это видно в статусе."""
    session = _session([100] * 10, highs=[102] * 10)
    signals = [_signal(0, 0, 1), _signal(0, 1, 1)]

    curve = build_curve(
        [session], signals, tp_ticks=2, sl_ticks=2, cost_ticks=0.0, tick_value=1.0,
        taus=(2,), bootstrap=50, min_paths=2,
    )

    point = curve.points[0]
    assert point.status == STATUS_DEGENERATE
    assert point.ev_lcb == pytest.approx(point.ev)
    assert not point.usable


def test_short_cluster_marks_interval_degenerate():
    """Кластер короче `2τ` не даёт права на интервал: статус деградирует, а не молчит."""
    curve = _two_session_curve(taus=(6,), session_len=10)

    point = curve.points[0]

    assert curve.min_cluster_bars == 10
    assert point.status == STATUS_DEGENERATE


def test_curve_marks_no_data_below_minimum():
    session = _session([100] * 10)

    curve = build_curve(
        [session], [_signal(0, 0, 1)], tp_ticks=1, sl_ticks=1, cost_ticks=0.1, tick_value=1.0,
        taus=(2,), bootstrap=50, min_paths=5,
    )

    point = curve.points[0]
    assert point.status == STATUS_NO_DATA
    assert point.paths == 1


def test_curve_rejects_unknown_session_and_bad_geometry():
    session = _session([100] * 10)

    with pytest.raises(FirstPassageError):
        build_curve([session], [_signal(3, 0, 1)], tp_ticks=1, sl_ticks=1, cost_ticks=0.0,
                    tick_value=1.0, taus=(2,), bootstrap=50, min_paths=2)
    with pytest.raises(FirstPassageError):
        build_curve([session], [_signal(0, 0, 1)], tp_ticks=0, sl_ticks=1, cost_ticks=0.0,
                    tick_value=1.0, taus=(2,), bootstrap=50, min_paths=2)
    with pytest.raises(FirstPassageError):
        build_curve([session], [_signal(0, 0, 1)], tp_ticks=1, sl_ticks=1, cost_ticks=-0.1,
                    tick_value=1.0, taus=(2,), bootstrap=50, min_paths=2)
    with pytest.raises(FirstPassageError):
        build_curve([session], [], tp_ticks=1, sl_ticks=1, cost_ticks=0.0, tick_value=1.0)


def test_ev_point_serialises_nan_as_null():
    point = EVPoint(
        tau=5, p_tp=float("nan"), p_sl=float("nan"), p_n=float("nan"), ev=float("nan"),
        var=float("nan"), ev_lcb=float("nan"), ev_ucb=float("nan"), paths=0, clusters=0,
        status=STATUS_NO_DATA,
    )

    payload = point.to_dict()

    assert payload["ev"] is None
    assert payload["p_tp"] is None
    assert payload["status"] == STATUS_NO_DATA


# --- выбор τ* -------------------------------------------------------------


def test_tau_star_is_interior_optimum():
    curve = _curve([_point(1, 0.2, 0.1), _point(5, 0.9, 0.5), _point(10, 0.4, 0.2)])

    selection = select_tau(curve, waiting_bars=10.0, tau_cap=30)

    assert selection.tau == 5
    assert selection.verdict == "OK"
    assert selection.entry_allowed is True
    assert selection.objective == pytest.approx(objective(_point(5, 0.9, 0.5), 10.0))


def test_tau_star_on_cap_is_scope_violation():
    curve = _curve([_point(1, 0.1, -0.1), _point(30, 2.0, 1.5)])

    selection = select_tau(curve, waiting_bars=10.0, tau_cap=30)

    assert selection.verdict == SCOPE_VIOLATION
    assert selection.entry_allowed is False


def test_tau_star_beyond_cap_is_scope_violation():
    """Лучший горизонт за пределами `τ_cap` нельзя заменить «лучшим из допустимых» молча."""
    curve = _curve([_point(1, 0.1, 0.05), _point(5, 0.5, 0.3), _point(30, 5.0, 4.0)])

    selection = select_tau(curve, waiting_bars=5.0, tau_cap=10)

    assert selection.verdict == SCOPE_VIOLATION
    assert selection.tau == 5
    assert selection.entry_allowed is False


def test_tau_star_on_edge_below_cap_is_boundary():
    curve = _curve([_point(1, 0.1, 0.05), _point(10, 1.0, 0.7)])

    selection = select_tau(curve, waiting_bars=0.0, tau_cap=30)

    assert selection.verdict == BOUNDARY
    assert selection.entry_allowed is True


def test_no_positive_lcb_blocks_entry():
    curve = _curve([_point(1, -0.2, -0.5), _point(5, -0.1, -0.3), _point(10, -0.3, -0.6)])

    selection = select_tau(curve, waiting_bars=5.0, tau_cap=30)

    assert selection.verdict == "OK"
    assert selection.entry_allowed is False
    assert any("EV_LCB" in reason for reason in selection.reasons)


def test_no_usable_points_gives_no_tau():
    curve = _curve([_point(1, float("nan"), float("nan"), var=0.0)])

    selection = select_tau(curve, waiting_bars=5.0, tau_cap=30)

    assert selection.verdict == NO_TAU
    assert selection.tau is None


def test_grid_above_cap_gives_no_tau():
    curve = _curve([_point(30, 1.0, 0.5), _point(60, 2.0, 1.0)])

    selection = select_tau(curve, waiting_bars=5.0, tau_cap=10)

    assert selection.verdict == NO_TAU
    assert any("τ_cap" in reason for reason in selection.reasons)


def test_infinite_waiting_is_reported_not_hidden():
    curve = _curve([_point(1, 0.1, 0.05), _point(5, 0.9, 0.5)])

    selection = select_tau(curve, waiting_bars=float("inf"), tau_cap=30)

    assert any("w не оценено" in reason for reason in selection.reasons)
    assert selection.to_dict()["waiting_bars"] is None


def test_entry_decision_respects_tau_max():
    curve = _curve([_point(1, 0.1, 0.05), _point(5, 0.9, 0.5), _point(10, 0.4, 0.2)])
    selection = select_tau(curve, waiting_bars=1.0, tau_cap=30)

    blocked = entry_decision(curve, selection, tau_max=3)
    allowed = entry_decision(curve, selection, tau_max=60)

    assert blocked.allowed is False
    assert blocked.reason == "TAU_EXCEEDS_MAX"
    assert allowed.allowed is True
    assert allowed.tau == 5
    assert allowed.reason == "OK"


def test_entry_decision_blocks_nonpositive_lcb():
    curve = _curve([_point(1, 0.1, -0.05), _point(5, 0.9, -0.2)])
    selection = select_tau(curve, waiting_bars=1.0, tau_cap=30)

    decision = entry_decision(curve, selection, tau_max=60)

    assert decision.allowed is False
    assert decision.reason == "EV_LCB_NONPOSITIVE"


def test_select_tau_rejects_bad_arguments():
    curve = _curve([_point(1, 0.1, 0.05)])

    with pytest.raises(ValueError):
        select_tau(curve, waiting_bars=1.0, tau_cap=0)
    with pytest.raises(ValueError):
        select_tau(curve, waiting_bars=-1.0, tau_cap=30)


# --- сигналы --------------------------------------------------------------


def _session_bars(closes, *, start=(10, 1)):
    day = datetime(2026, 9, 1, tzinfo=MSK)
    base = day.replace(hour=start[0], minute=start[1])
    return [_bar(base + timedelta(minutes=offset), price) for offset, price in enumerate(closes)]


def test_signals_start_after_min_history():
    closes = [100.0] * MIN_HISTORY + [100.0] * 4 + [106.0]
    sessions = session_ohlcv(_session_bars(closes), _schedule())

    signals = ou_signals(sessions, sigma_p_ticks=1.0, z_entry=2.0, tick_value=1.0)

    assert len(signals.signals) == 1
    assert signals.signals[0].index == MIN_HISTORY + 4


def test_signal_direction_is_against_deviation():
    closes = [100.0] * MIN_HISTORY + [105.0, 106.0]
    sessions = session_ohlcv(_session_bars(closes), _schedule())

    signals = ou_signals(sessions, sigma_p_ticks=1.0, z_entry=2.0, tick_value=1.0)

    assert signals.signals
    assert all(signal.direction == -1 for signal in signals.signals)
    assert signals.flags["signal"] == "PROVISIONAL_SIGNAL"
    assert signals.flags["series"] == "UNVERIFIED:signal-series"


def test_waiting_bars_computed_within_session():
    closes = [100.0] * MIN_HISTORY + [106.0] + [100.0] * 5 + [94.0]
    sessions = session_ohlcv(_session_bars(closes), _schedule())

    signals = ou_signals(sessions, sigma_p_ticks=1.0, z_entry=2.0, tick_value=1.0)

    assert len(signals.signals) == 2
    assert signals.waiting_bars == pytest.approx(6.0)


def test_waiting_bars_is_infinite_with_single_signal():
    closes = [100.0] * MIN_HISTORY + [106.0]
    sessions = session_ohlcv(_session_bars(closes), _schedule())

    signals = ou_signals(sessions, sigma_p_ticks=1.0, z_entry=2.0, tick_value=1.0)

    assert not np.isfinite(signals.waiting_bars)
    assert signals.to_dict()["waiting_bars"] is None


def test_signal_inputs_validated():
    with pytest.raises(SignalError):
        ou_signals([], sigma_p_ticks=0.0, z_entry=2.0, tick_value=1.0)
    with pytest.raises(SignalError):
        ou_signals([], sigma_p_ticks=1.0, z_entry=0.0, tick_value=1.0)
    with pytest.raises(SignalError):
        ou_signals([], sigma_p_ticks=1.0, z_entry=2.0, tick_value=0.0)
    with pytest.raises(SignalError):
        ou_signals([], sigma_p_ticks=1.0, z_entry=2.0, tick_value=1.0, min_history=0)


def test_session_without_volume_is_skipped():
    bars = _session_bars([100.0] * (MIN_HISTORY + 5))
    bars = [Bar(ts=bar.ts, open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=0)
            for bar in bars]
    sessions = session_ohlcv(bars, _schedule())

    signals = ou_signals(sessions, sigma_p_ticks=1.0, z_entry=0.001, tick_value=1.0)

    assert signals.signals == ()


def test_sessions_split_by_window_and_ordered_chronologically():
    day = datetime(2026, 9, 1, tzinfo=MSK)
    morning = _bar(day.replace(hour=9, minute=49), 100.0)
    outside = _bar(day.replace(hour=9, minute=55), 100.0)
    main = _bar(day.replace(hour=10, minute=1), 100.0)

    sessions = session_ohlcv([main, morning, outside], SessionSchedule())

    assert len(sessions) == 2
    assert len(sessions[0]["close"]) == 1
    assert sessions[0]["close"][0] == 100.0


def test_duplicate_timestamps_collapse():
    day = datetime(2026, 9, 1, 10, 1, tzinfo=MSK)
    bars = [_bar(day, 100.0), _bar(day, 101.0)]

    sessions = session_ohlcv(bars, _schedule())

    assert len(sessions) == 1
    assert len(sessions[0]["close"]) == 1
    assert sessions[0]["close"][0] == 101.0
