"""Офлайн-исполнение политики: бар за баром, без look-ahead.

Порядок проверок на баре (`holding_exiting.md` §9): SL → TP → time stop → model invalid →
regime breaker → session → vol spike → правило удержания. Жёсткие триггеры закрывают позицию
раньше «штатных»: если бар задел барьер, вопрос «держать ли» уже не стоит.

Вход — по close сигнального бара. Барьеры считаются от цены входа, поэтому бар, на котором
появился сигнал, не может закрыть позицию: путь начинается со следующего бара.

Допущения, которые видны в артефакте: спред будущего момента неизвестен, поэтому
`close_cost_drift` не срабатывает (`NO_SPREAD_FORECAST`); `σ_P` берётся точечной оценкой R2
(`NO_SIGMA_CI`), CI есть только у `θ`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .ev import COMPONENT_OK, EntryDecision, TauMax, TauStarSelection, entry_decision
from .first_passage import EVCurve
from .holding import (
    EXIT_TRIGGERS,
    a0_bounds,
    a_coordinate,
    cusum_breaker,
    hold_decision,
    online_band,
    remaining_moments,
)
from .policy import PolicyParams
from .sessions import SessionSchedule
from .signals import SessionArrays, Signal

VOL_SPIKE_MULTIPLIER = 3.0
DEFAULT_STRIDE = 5

NO_THETA = "NO_TAU"
POSITION_OPEN = "POSITION_OPEN"
SESSION_LIMIT = "SESSION"
TAU_EXCEEDS_SESSION = "TAU_EXCEEDS_SESSION"
REGIME_BLOCK = "REGIME_CONFIDENCE"
SANITY_BLOCK = "SANITY"
SCOPE_BLOCK = "SCOPE_VIOLATION"


class ExecutionError(ValueError):
    """Политику нельзя исполнить на заданных данных."""


@dataclass(frozen=True)
class ExecutionConfig:
    """Всё, что нужно исполнителю и что видно в артефакте."""

    instrument: str
    tick_value: float
    sigma_p_ticks: float
    cost_ticks: float
    theta_lcb: float
    theta_ucb: float
    tau_liq: int
    d_ticks: float
    regime_gate: bool
    tp_ticks: int
    sl_ticks: int
    position_rub: float = 200_000.0
    stride: int = DEFAULT_STRIDE
    step_minutes: int = 1

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "tick_value": self.tick_value,
            "sigma_p_ticks": round(self.sigma_p_ticks, 6),
            "cost_ticks": round(self.cost_ticks, 6),
            "theta_lcb": self.theta_lcb,
            "theta_ucb": self.theta_ucb,
            "tau_liq": self.tau_liq,
            "d_ticks": round(self.d_ticks, 6),
            "regime_gate": self.regime_gate,
            "tp_ticks": self.tp_ticks,
            "sl_ticks": self.sl_ticks,
            "position_rub": self.position_rub,
            "stride": self.stride,
            "step_minutes": self.step_minutes,
        }


@dataclass(frozen=True)
class Trade:
    """Сделка в журнале: вход, выход, издержки и причина."""

    instrument: str
    direction: int
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    tau_planned: int
    tau_actual: int
    exit_reason: str
    gross_ticks: float
    cost_ticks: float
    net_ticks: float
    a_entry: float
    a_exit: float
    band_lower: Optional[float]
    hold: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "direction": self.direction,
            "entry_ts": self.entry_ts,
            "exit_ts": self.exit_ts,
            "entry_price": round(self.entry_price, 6),
            "exit_price": round(self.exit_price, 6),
            "tau_planned": self.tau_planned,
            "tau_actual": self.tau_actual,
            "exit_reason": self.exit_reason,
            "gross_ticks": round(self.gross_ticks, 6),
            "cost_ticks": round(self.cost_ticks, 6),
            "net_ticks": round(self.net_ticks, 6),
            "a_entry": round(self.a_entry, 6),
            "a_exit": round(self.a_exit, 6),
            "band_lower": None if self.band_lower is None else round(self.band_lower, 6),
            "hold": self.hold,
            "units": {"gross_ticks": "ticks", "cost_ticks": "ticks", "net_ticks": "ticks"},
        }


@dataclass(frozen=True)
class NoTrade:
    """Отказ входа с причиной; пороги при этом не ослабляются."""

    instrument: str
    ts: str
    reason: str
    detail: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "ts": self.ts,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ExecutionResult:
    """Итог исполнения: журнал сделок, отказы и счётчики причин."""

    instrument: str
    trades: Tuple[Trade, ...]
    no_trades: Tuple[NoTrade, ...]
    exit_counts: Dict[str, int]
    no_trade_counts: Dict[str, int]
    tau_max: int
    flags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "trades": [trade.to_dict() for trade in self.trades],
            "no_trades": [item.to_dict() for item in self.no_trades],
            "exit_counts": dict(self.exit_counts),
            "no_trade_counts": dict(self.no_trade_counts),
            "tau_max": self.tau_max,
            "flags": dict(self.flags),
        }


def _moment(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def execute_policy(
    sessions: Sequence[SessionArrays],
    signals: Sequence[Signal],
    *,
    curve: EVCurve,
    selection: TauStarSelection,
    tau_max: TauMax,
    policy: PolicyParams,
    schedule: SessionSchedule,
    config: ExecutionConfig,
) -> ExecutionResult:
    """Исполнить политику на истории: одна позиция за раз, без подгонки порогов."""
    if config.tick_value <= 0 or config.sigma_p_ticks <= 0:
        raise ExecutionError("tick_value and sigma_p_ticks must be positive")
    if config.cost_ticks < 0:
        raise ExecutionError("cost must be non-negative")
    if tau_max.value is None:
        return ExecutionResult(
            instrument=config.instrument,
            trades=(),
            no_trades=tuple(
                NoTrade(
                    config.instrument,
                    _signal_ts(sessions, signal),
                    NO_THETA if tau_max.value is None else SCOPE_BLOCK,
                    "; ".join(tau_max.reasons),
                )
                for signal in signals
            ),
            exit_counts={},
            no_trade_counts={NO_THETA: len(signals)} if signals else {},
            tau_max=0,
            flags={"tau_max": "MISSING"},
        )

    decision = entry_decision(curve, selection, tau_max=tau_max.value)
    trades: List[Trade] = []
    no_trades: List[NoTrade] = []
    exit_counts: Dict[str, int] = {}
    no_trade_counts: Dict[str, int] = {}
    occupied: set[int] = set()

    for signal in signals:
        ts = _signal_ts(sessions, signal)
        block = _entry_block(
            signal, sessions, schedule, decision, config, tau_max.value, occupied
        )
        if block is not None:
            reason, detail = block
            no_trades.append(NoTrade(config.instrument, ts, reason, detail))
            no_trade_counts[reason] = no_trade_counts.get(reason, 0) + 1
            continue

        trade = _run_position(sessions, signal, schedule, policy, config, decision)
        if trade is None:
            no_trades.append(
                NoTrade(config.instrument, ts, NO_THETA, "путь не умещается в окно")
            )
            no_trade_counts[NO_THETA] = no_trade_counts.get(NO_THETA, 0) + 1
            continue
        trades.append(trade)
        occupied.add(signal.session)
        exit_counts[trade.exit_reason] = exit_counts.get(trade.exit_reason, 0) + 1

    unknown = {trade.exit_reason for trade in trades} - {
        reason for spec in EXIT_TRIGGERS for reason in spec.reasons
    }
    flags: Dict[str, str] = {}
    if unknown:
        flags["exit_reason"] = f"UNKNOWN: {', '.join(sorted(unknown))}"
    flags["close_cost"] = "NO_SPREAD_FORECAST"
    flags["sigma_p"] = "NO_SIGMA_CI"
    if not tau_max.complete:
        flags["tau_max"] = "INCOMPLETE"
    if tau_max.components and any(
        item.name == "regime" and item.status != COMPONENT_OK for item in tau_max.components
    ):
        flags["regime"] = "REFERENCE_ONLY"

    return ExecutionResult(
        instrument=config.instrument,
        trades=tuple(trades),
        no_trades=tuple(no_trades),
        exit_counts=exit_counts,
        no_trade_counts=no_trade_counts,
        tau_max=tau_max.value,
        flags=flags,
    )


def _signal_ts(sessions: Sequence[SessionArrays], signal: Signal) -> str:
    stamps = sessions[signal.session]["ts"]
    if signal.index >= len(stamps):
        return ""
    return _moment(int(stamps[signal.index])).isoformat()


def _entry_block(
    signal: Signal,
    sessions: Sequence[SessionArrays],
    schedule: SessionSchedule,
    decision: EntryDecision,
    config: ExecutionConfig,
    tau_max_value: int,
    occupied: set[int],
) -> Optional[Tuple[str, str]]:
    """Причины отказа входа в порядке §10; `None` — вход разрешён."""
    if signal.session in occupied:
        return POSITION_OPEN, "в окне уже открыта позиция"
    if not decision.allowed:
        return decision.reason, "входной гейт EV_LCB/τ_max"
    if not config.regime_gate:
        return REGIME_BLOCK, "γ_t(fav) < 1−q"
    if config.d_ticks <= config.cost_ticks:
        return SANITY_BLOCK, f"D={config.d_ticks:.2f} ≤ Cost={config.cost_ticks:.2f}"
    stamps = sessions[signal.session]["ts"]
    if signal.index >= len(stamps):
        return NO_THETA, "нет метки времени бара"
    available = schedule.tau_session(
        _moment(int(stamps[signal.index])),
        tau_liq_minutes=config.tau_liq,
        bar_minutes=config.step_minutes,
    )
    if available <= 0:
        return SESSION_LIMIT, "окно закрывается раньше, чем можно выйти"
    tau = decision.tau if decision.tau is not None else tau_max_value
    if tau > available:
        return TAU_EXCEEDS_SESSION, f"τ={tau} > τ_session={available}"
    return None


def _run_position(
    sessions: Sequence[SessionArrays],
    signal: Signal,
    schedule: SessionSchedule,
    policy: PolicyParams,
    config: ExecutionConfig,
    decision: EntryDecision,
) -> Optional[Trade]:
    """Провести позицию бар за баром и записать исход."""
    arrays = sessions[signal.session]
    close = arrays["close"]
    high = arrays["high"]
    low = arrays["low"]
    stamps = arrays["ts"]
    entry_index = signal.index
    if entry_index + 1 >= len(close):
        return None
    tau = int(decision.tau or config.tau_liq)
    horizon = min(tau, len(close) - entry_index - 1)
    if horizon < 1:
        return None

    direction = signal.direction
    entry = float(close[entry_index])
    anchor = signal.anchor if signal.anchor else entry
    a_entry, a0_lcb = a0_bounds(
        entry,
        anchor,
        sigma_p=config.sigma_p_ticks * config.tick_value,
        sigma_p_lcb=config.sigma_p_ticks * config.tick_value,
        sigma_p_ucb=config.sigma_p_ticks * config.tick_value,
        direction=direction,
    )
    tp_price = entry + direction * config.tp_ticks * config.tick_value
    sl_price = entry - direction * config.sl_ticks * config.tick_value
    lambda_risk = policy.lambda_risk
    returns: List[float] = []

    exit_reason = "TIME_STOP"
    exit_price = float(close[entry_index + horizon])
    exit_index = entry_index + horizon
    band_lower: Optional[float] = None
    hold_flag = False

    for step in range(1, horizon + 1):
        index = entry_index + step
        returns.append(math.log(close[index] / close[index - 1]) if close[index - 1] > 0 else 0.0)
        if direction > 0:
            sl_hit = low[index] <= sl_price
            tp_hit = high[index] >= tp_price
        else:
            sl_hit = high[index] >= sl_price
            tp_hit = low[index] <= tp_price
        if sl_hit:
            exit_reason, exit_price, exit_index = "SL", float(sl_price), index
            break
        if tp_hit:
            exit_reason, exit_price, exit_index = "TP", float(tp_price), index
            break
        if step >= tau:
            exit_reason, exit_price, exit_index = "TIME_STOP", float(close[index]), index
            break
        if cusum_breaker(returns):
            exit_reason, exit_price, exit_index = "REGIME_BREAKER", float(close[index]), index
            break
        minutes_to_close = schedule.minutes_to_close(_moment(int(stamps[index])))
        if 0 <= minutes_to_close <= config.tau_liq + policy.buffer_minutes:
            exit_reason, exit_price, exit_index = "SESSION", float(close[index]), index
            break
        if _vol_spike(arrays, index, entry_index):
            exit_reason, exit_price, exit_index = "VOL_SPIKE", float(close[index]), index
            break

        a_t = a_coordinate(
            float(close[index]), anchor, config.sigma_p_ticks * config.tick_value, direction
        )
        band_lower = online_band(
            a0_lcb=a0_lcb,
            theta_lcb=config.theta_lcb,
            theta_ucb=config.theta_ucb,
            minutes=step * config.step_minutes,
            tau_max=tau,
            alpha_total=policy.alpha_total,
        ).lower
        if a_t < band_lower:
            exit_reason, exit_price, exit_index = "MODEL_INVALID", float(close[index]), index
            break

        to_tp = max(1, int(round((tp_price - float(close[index])) * direction / config.tick_value)))
        to_sl = max(1, int(round((float(close[index]) - sl_price) * direction / config.tick_value)))
        tau_rem = max(1, tau - step)
        moments = remaining_moments(
            sessions,
            to_tp=to_tp,
            to_sl=to_sl,
            tau_rem=tau_rem,
            tick_value=config.tick_value,
            stride=config.stride,
        )
        verdict = hold_decision(
            a_t=a_t,
            to_tp=to_tp,
            to_sl=to_sl,
            tau_rem=tau_rem,
            lambda_risk=lambda_risk,
            cost_drift=0.0,
            moments=moments,
        )
        hold_flag = verdict.hold
        if not verdict.hold:
            exit_reason = verdict.reason
            exit_price, exit_index = float(close[index]), index
            break

    gross = direction * (exit_price - entry) / config.tick_value
    net = gross - config.cost_ticks
    a_exit = a_coordinate(
        exit_price, anchor, config.sigma_p_ticks * config.tick_value, direction
    )
    return Trade(
        instrument=config.instrument,
        direction=direction,
        entry_ts=_moment(int(stamps[entry_index])).isoformat(),
        exit_ts=_moment(int(stamps[exit_index])).isoformat(),
        entry_price=entry,
        exit_price=exit_price,
        tau_planned=tau,
        tau_actual=exit_index - entry_index,
        exit_reason=exit_reason,
        gross_ticks=gross,
        cost_ticks=config.cost_ticks,
        net_ticks=net,
        a_entry=a_entry,
        a_exit=a_exit,
        band_lower=band_lower,
        hold=hold_flag,
    )


def _vol_spike(arrays: SessionArrays, index: int, entry_index: int) -> bool:
    """Выброс range-based σ текущего бара относительно медианы с начала окна."""
    start = max(0, entry_index)
    highs = arrays["high"][start : index + 1]
    lows = arrays["low"][start : index + 1]
    if len(highs) < 5:
        return False
    ranges = highs - lows
    median = float(np.median(ranges))
    if median <= 0:
        return False
    return bool(ranges[-1] > VOL_SPIKE_MULTIPLIER * median)


__all__ = [
    "DEFAULT_STRIDE",
    "ExecutionConfig",
    "ExecutionError",
    "ExecutionResult",
    "NO_THETA",
    "NoTrade",
    "POSITION_OPEN",
    "REGIME_BLOCK",
    "SANITY_BLOCK",
    "SCOPE_BLOCK",
    "SESSION_LIMIT",
    "TAU_EXCEEDS_SESSION",
    "Trade",
    "VOL_SPIKE_MULTIPLIER",
    "execute_policy",
]
