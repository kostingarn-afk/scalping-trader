"""Тесты офлайн-исполнения политики (волна 4 R3)."""
from __future__ import annotations

import numpy as np
import pytest
from datetime import datetime, timezone

from analyzer.research.ev import COMPONENT_OK, TauComponent, TauMax, select_tau
from analyzer.research.first_passage import EVCurve, EVPoint
from analyzer.research.holding import RemainingMoments
from analyzer.research.policy import PolicyParams
from analyzer.research.policy_exec import (
    NO_THETA,
    POSITION_OPEN,
    REGIME_BLOCK,
    SANITY_BLOCK,
    ExecutionConfig,
    ExecutionError,
    execute_policy,
)
from analyzer.research.sessions import SessionSchedule
from analyzer.research.signals import Signal


def _arrays(closes, *, spread: float = 0.0):
    close = np.asarray(closes, dtype=float)
    # 10:00 MSK = 07:00 UTC — внутри торгового окна, иначе вход блокируется по сессии.
    start = int(datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc).timestamp())
    return {
        "ts": np.asarray([start + 60 * i for i in range(len(close))], dtype=np.int64),
        "open": close.copy(),
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": np.ones(len(close)),
    }


def _curve() -> EVCurve:
    points = (
        EVPoint(tau=1, p_tp=0.5, p_sl=0.3, p_n=0.2, ev=0.4, var=1.0, ev_lcb=0.2,
                ev_ucb=0.6, paths=200, clusters=20),
        EVPoint(tau=5, p_tp=0.6, p_sl=0.3, p_n=0.1, ev=1.0, var=1.0, ev_lcb=0.8,
                ev_ucb=1.2, paths=200, clusters=20),
    )
    return EVCurve(
        tp_ticks=4, sl_ticks=2, cost_ticks=0.5, protocol="SL_FIRST_EMPIRICAL_1M",
        block_rule="test", bootstrap=20, min_paths=2, min_cluster_bars=600, points=points,
    )


def _tau_max(value: int | None = 10) -> TauMax:
    return TauMax(
        value=value,
        components=(TauComponent("edge", value, COMPONENT_OK),) if value else (),
        complete=value is not None,
        reasons=() if value else ("τ_edge не определён",),
    )


def _config(**overrides) -> ExecutionConfig:
    base = dict(
        instrument="TEST@TQBR",
        tick_value=0.01,
        sigma_p_ticks=20.0,
        cost_ticks=0.5,
        theta_lcb=0.05,
        theta_ucb=0.06,
        tau_liq=1,
        d_ticks=40.0,
        regime_gate=True,
        tp_ticks=4,
        sl_ticks=2,
    )
    base.update(overrides)
    return ExecutionConfig(**base)


def _moments(m: float = 1.0, s: float = 0.5) -> RemainingMoments:
    return RemainingMoments(
        m_rem=m, s_rem=s, p_tp=0.5, p_sl=0.3, p_n=0.2, paths=100,
        to_tp=4, to_sl=2, tau_rem=5, stride=1,
    )


def _run(sessions, signal, *, config=None, curve=None, tau_max=None, policy=None):
    curve = curve or _curve()
    selection = select_tau(curve, waiting_bars=2.0, tau_cap=60)
    return execute_policy(
        sessions,
        [signal],
        curve=curve,
        selection=selection,
        tau_max=tau_max or _tau_max(),
        policy=policy or PolicyParams(),
        schedule=SessionSchedule.single(10 * 60, 18 * 60 + 40),
        config=config or _config(),
    )


@pytest.fixture()
def hold_moments(monkeypatch):
    monkeypatch.setattr(
        "analyzer.research.policy_exec.remaining_moments",
        lambda *args, **kwargs: _moments(),
    )


def test_tp_closes_position(hold_moments):
    prices = [100.0, 100.0, 100.0, 100.02, 100.04, 100.06, 100.08]
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    result = _run([_arrays(prices)], signal)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "TP"
    assert trade.tau_actual == 2
    assert trade.gross_ticks == pytest.approx(4.0)
    assert trade.net_ticks == pytest.approx(3.5)
    assert result.exit_counts == {"TP": 1}


def test_sl_wins_when_both_barriers_hit_in_one_bar(hold_moments):
    session = _arrays([100.0, 100.0, 100.0, 100.02], spread=0.05)
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    result = _run([session], signal)

    assert result.trades[0].exit_reason == "SL"
    assert result.trades[0].gross_ticks == pytest.approx(-2.0)


def test_time_stop_closes_at_planned_horizon(hold_moments):
    prices = [100.0] * 12
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    result = _run([_arrays(prices)], signal, tau_max=_tau_max(5))

    trade = result.trades[0]
    assert trade.exit_reason == "TIME_STOP"
    assert trade.tau_planned == 5
    assert trade.tau_actual == 5


def test_overshoot_closes_when_anchor_reached(hold_moments):
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.0)

    result = _run([_arrays([100.0] * 12)], signal)

    assert result.trades[0].exit_reason == "OVERSHOOT"


def test_hold_rule_closes_when_edge_is_insufficient(monkeypatch):
    monkeypatch.setattr(
        "analyzer.research.policy_exec.remaining_moments",
        lambda *args, **kwargs: _moments(m=0.1, s=1.0),
    )
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    result = _run([_arrays([100.0] * 12)], signal)

    assert result.trades[0].exit_reason == "ANCHOR_EDGE"


def test_model_invalid_below_band(hold_moments):
    prices = [100.0, 100.0, 100.0, 100.06, 100.06]
    config = _config(theta_lcb=0.0005, theta_ucb=0.0006, tp_ticks=30, sl_ticks=20)
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.20)

    result = _run([_arrays(prices)], signal, config=config)

    assert result.trades[0].exit_reason == "MODEL_INVALID"


def test_session_limit_closes_before_close(hold_moments):
    schedule = SessionSchedule.single(10 * 60, 10 * 60 + 12)
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)
    curve = _curve()
    selection = select_tau(curve, waiting_bars=2.0, tau_cap=60)

    result = execute_policy(
        [_arrays([100.0] * 12)],
        [signal],
        curve=curve,
        selection=selection,
        tau_max=_tau_max(5),
        policy=PolicyParams(),
        schedule=schedule,
        config=_config(tau_liq=1),
    )

    # Окно 12 минут: сигнал на 3-й минуте оставляет 12−2−1−5 = 4 минуты, а τ=5 не помещается.
    assert result.no_trade_counts.get("TAU_EXCEEDS_SESSION") == 1
    assert result.trades == ()


def test_entry_blocked_without_tau(hold_moments):
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    result = _run([_arrays([100.0] * 12)], signal, tau_max=_tau_max(None))

    assert result.trades == ()
    assert result.no_trade_counts[NO_THETA] == 1
    assert result.tau_max == 0


def test_entry_blocked_by_sanity_when_d_not_above_cost(hold_moments):
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    result = _run([_arrays([100.0] * 12)], signal, config=_config(d_ticks=0.4, cost_ticks=0.5))

    assert result.no_trade_counts[SANITY_BLOCK] == 1


def test_entry_blocked_by_regime_confidence(hold_moments):
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    result = _run([_arrays([100.0] * 12)], signal, config=_config(regime_gate=False))

    assert result.no_trade_counts[REGIME_BLOCK] == 1


def test_second_signal_in_open_session_is_skipped(hold_moments):
    prices = [100.0] * 20
    first = Signal(session=0, index=4, direction=1, z=-3.0, price=100.0, anchor=100.40)
    second = Signal(session=0, index=8, direction=1, z=-3.0, price=100.0, anchor=100.40)
    curve = _curve()
    selection = select_tau(curve, waiting_bars=2.0, tau_cap=60)

    result = execute_policy(
        [_arrays(prices)],
        [first, second],
        curve=curve,
        selection=selection,
        tau_max=_tau_max(5),
        policy=PolicyParams(),
        schedule=SessionSchedule.single(10 * 60, 18 * 60 + 40),
        config=_config(),
    )

    assert len(result.trades) == 1
    assert result.no_trade_counts[POSITION_OPEN] == 1


def test_trade_log_records_costs_and_units(hold_moments):
    prices = [100.0, 100.0, 100.0, 100.02, 100.04, 100.06]
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    payload = _run([_arrays(prices)], signal).to_dict()

    trade = payload["trades"][0]
    assert trade["units"]["net_ticks"] == "ticks"
    assert trade["cost_ticks"] == pytest.approx(0.5)
    assert trade["entry_ts"].endswith("+00:00")
    assert payload["flags"]["close_cost"] == "NO_SPREAD_FORECAST"


def test_execution_rejects_bad_config(hold_moments):
    signal = Signal(session=0, index=2, direction=1, z=-3.0, price=100.0, anchor=100.40)

    with pytest.raises(ExecutionError):
        _run([_arrays([100.0] * 12)], signal, config=_config(tick_value=0.0))
    with pytest.raises(ExecutionError):
        _run([_arrays([100.0] * 12)], signal, config=_config(cost_ticks=-1.0))
