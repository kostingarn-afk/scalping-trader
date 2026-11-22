"""Первое касание барьеров, `EV(τ)`, `V(τ)` и CI.

Исход в тиках (`holding_exiting.md` §4): `X(τ) = a·1_TP − b·1_SL + ΔP_τ·1_N`.
Протокол — эмпирический по 1m с конвенцией **«SL первым»**: если бар задел оба барьера,
исход считается SL. Конвенция смещает EV вниз, то есть все ворота консервативны.

Путь, который не умещается в торговое окно, отбрасывается, а не укорачивается: усечение
меняет `p_N` и `EV` в оптимистичную сторону.

CI — кластерный bootstrap **по торговым окнам**: исходы внутри окна зависимы (общий режим,
перекрывающиеся пути), между окнами — нет. Блочный bootstrap по подряд идущим сигналам
неприменим: сигналы разрежены, и блок `2τ` подряд идущих входов почти никогда не набирается,
то есть интервал выродился бы в точечную оценку молча.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .signals import SessionArrays, Signal

DEFAULT_BOOTSTRAP = 200
DEFAULT_SEED = 20261123
DEFAULT_MIN_PATHS = 30
#: Минимальная длина блока в единицах баров: блок ≥ `2τ` (`holding_exiting.md` §4).
BLOCK_MULTIPLIER = 2

TP_WIN = 1
SL_WIN = -1
NEUTRAL = 0

PROTOCOL_SL_FIRST = "SL_FIRST_EMPIRICAL_1M"
BLOCK_RULE = "cluster = trading session (>= 2*tau bars); resample sessions"

STATUS_OK = "OK"
STATUS_NO_DATA = "NO_DATA"
STATUS_DEGENERATE = "CI_DEGENERATE"


class FirstPassageError(ValueError):
    """Первый проход нельзя оценить по заданным данным."""


@dataclass(frozen=True)
class EVPoint:
    """Точка EV-кривой: вероятности исходов, `EV`, дисперсия и интервал."""

    tau: int
    p_tp: float
    p_sl: float
    p_n: float
    ev: float
    var: float
    ev_lcb: float
    ev_ucb: float
    paths: int
    clusters: int
    status: str = STATUS_OK

    @property
    def sd(self) -> float:
        return float(np.sqrt(self.var)) if self.var > 0 else 0.0

    @property
    def usable(self) -> bool:
        return self.status == STATUS_OK and np.isfinite(self.ev) and self.var > 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "tau": self.tau,
            "p_tp": _number(self.p_tp),
            "p_sl": _number(self.p_sl),
            "p_n": _number(self.p_n),
            "ev": _number(self.ev),
            "var": _number(self.var),
            "sd": _number(self.sd),
            "ev_lcb": _number(self.ev_lcb),
            "ev_ucb": _number(self.ev_ucb),
            "paths": self.paths,
            "clusters": self.clusters,
            "status": self.status,
            "units": {"ev": "ticks", "var": "ticks^2", "sd": "ticks"},
        }


@dataclass(frozen=True)
class EVCurve:
    """EV-кривая на сетке τ при фиксированной геометрии барьеров."""

    tp_ticks: int
    sl_ticks: int
    cost_ticks: float
    protocol: str
    block_rule: str
    bootstrap: int
    min_paths: int
    min_cluster_bars: int
    points: Tuple[EVPoint, ...]

    def usable(self) -> List[EVPoint]:
        return [point for point in self.points if point.usable]

    def best_tau(self) -> Optional[EVPoint]:
        usable = self.usable()
        if not usable:
            return None
        return max(usable, key=lambda point: point.ev_lcb)

    def positive(self) -> List[EVPoint]:
        return [point for point in self.usable() if point.ev_lcb > 0]

    def to_dict(self) -> Dict[str, object]:
        return {
            "tp_ticks": self.tp_ticks,
            "sl_ticks": self.sl_ticks,
            "cost_ticks": _number(self.cost_ticks),
            "protocol": self.protocol,
            "block_rule": self.block_rule,
            "bootstrap": self.bootstrap,
            "min_paths": self.min_paths,
            "min_cluster_bars": self.min_cluster_bars,
            "points": [point.to_dict() for point in self.points],
        }


def _number(value: float) -> Optional[float]:
    """NaN/inf не бывают валидным JSON — в артефакте им место как `null`."""
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), 6)


def resolve_path(
    arrays: SessionArrays,
    index: int,
    tau: int,
    *,
    direction: int,
    tp_ticks: int,
    sl_ticks: int,
    tick_value: float,
) -> Optional[Tuple[int, float]]:
    """Исход одного пути: (`TP_WIN`/`SL_WIN`/`NEUTRAL`, результат в тиках до издержек).

    `None`, если путь не умещается в окно. В баре, где задеты оба барьера, побеждает SL.
    """
    if tau < 1:
        raise FirstPassageError("tau must be at least 1")
    if direction not in (1, -1):
        raise FirstPassageError("direction must be +1 or -1")
    if tp_ticks < 1 or sl_ticks < 1:
        raise FirstPassageError("barriers must be at least one tick")
    if tick_value <= 0:
        raise FirstPassageError("tick_value must be positive")
    if index < 0:
        raise FirstPassageError("index must be non-negative")

    close = arrays["close"]
    if index >= len(close):
        raise FirstPassageError(f"index {index} is outside the session ({len(close)} bars)")
    if index + tau >= len(close):
        return None

    entry = float(close[index])
    tp_price = entry + direction * tp_ticks * tick_value
    sl_price = entry - direction * sl_ticks * tick_value
    high = arrays["high"]
    low = arrays["low"]
    for step in range(index + 1, index + tau + 1):
        if direction > 0:
            sl_hit = low[step] <= sl_price
            tp_hit = high[step] >= tp_price
        else:
            sl_hit = high[step] >= sl_price
            tp_hit = low[step] <= tp_price
        if sl_hit:
            return (SL_WIN, -float(sl_ticks))
        if tp_hit:
            return (TP_WIN, float(tp_ticks))
    move = direction * (float(close[index + tau]) - entry) / tick_value
    return (NEUTRAL, move)


def build_curve(
    sessions: Sequence[SessionArrays],
    signals: Sequence[Signal],
    *,
    tp_ticks: int,
    sl_ticks: int,
    cost_ticks: float,
    tick_value: float,
    taus: Sequence[int] = (1, 3, 5, 10, 15, 30, 60),
    bootstrap: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
    min_paths: int = DEFAULT_MIN_PATHS,
) -> EVCurve:
    """EV-кривая по сетке τ на заданных сигналах.

    `cost_ticks` — круговые издержки из `costs.estimate_cost`; вычитаются из каждого ожидания,
    дисперсия от сдвига не зависит.
    """
    if tp_ticks < 1 or sl_ticks < 1:
        raise FirstPassageError("barriers must be at least one tick")
    if cost_ticks < 0 or not np.isfinite(cost_ticks):
        raise FirstPassageError("cost must be finite and non-negative")
    if tick_value <= 0:
        raise FirstPassageError("tick_value must be positive")
    if not taus or any(tau < 1 for tau in taus):
        raise FirstPassageError("taus must be positive integers")
    if bootstrap < 20:
        raise FirstPassageError("bootstrap must be at least 20 resamples")
    if min_paths < 2:
        raise FirstPassageError("min_paths must be at least 2")
    if not signals:
        raise FirstPassageError("no signals: cannot build EV curve")
    for signal in signals:
        if not 0 <= signal.session < len(sessions):
            raise FirstPassageError(f"signal refers to unknown session {signal.session}")
        if not 0 <= signal.index < len(sessions[signal.session]["close"]):
            raise FirstPassageError(f"signal index {signal.index} is outside session {signal.session}")
        if signal.direction not in (1, -1):
            raise FirstPassageError("signal direction must be +1 or -1")

    points: List[EVPoint] = []
    rng = np.random.default_rng(seed)
    used_sessions = {signal.session for signal in signals}
    min_cluster_bars = min(len(sessions[session]["close"]) for session in used_sessions)
    for tau in sorted(set(taus)):
        values: List[float] = []
        kinds: List[int] = []
        session_of: List[int] = []
        for signal in signals:
            resolved = resolve_path(
                sessions[signal.session],
                signal.index,
                tau,
                direction=signal.direction,
                tp_ticks=tp_ticks,
                sl_ticks=sl_ticks,
                tick_value=tick_value,
            )
            if resolved is None:
                continue
            kind, value = resolved
            values.append(value)
            kinds.append(kind)
            session_of.append(signal.session)

        if len(values) < min_paths:
            points.append(
                EVPoint(
                    tau=tau,
                    p_tp=float("nan"),
                    p_sl=float("nan"),
                    p_n=float("nan"),
                    ev=float("nan"),
                    var=float("nan"),
                    ev_lcb=float("nan"),
                    ev_ucb=float("nan"),
                    paths=len(values),
                    clusters=len(set(session_of)),
                    status=STATUS_NO_DATA,
                )
            )
            continue

        sample = np.asarray(values, dtype=float)
        kinds_array = np.asarray(kinds)
        p_tp = float(np.mean(kinds_array == TP_WIN))
        p_sl = float(np.mean(kinds_array == SL_WIN))
        p_n = float(np.mean(kinds_array == NEUTRAL))
        ev = float(np.mean(sample)) - cost_ticks
        var = float(np.var(sample, ddof=1)) if len(sample) > 1 else 0.0
        clusters = len(set(session_of))
        low, high = _cluster_bootstrap(
            sample,
            np.asarray(session_of),
            cost_ticks=cost_ticks,
            resamples=bootstrap,
            rng=rng,
        )
        status = STATUS_OK if clusters > 1 else STATUS_DEGENERATE
        if status == STATUS_OK and min_cluster_bars < BLOCK_MULTIPLIER * tau:
            status = STATUS_DEGENERATE
        points.append(
            EVPoint(
                tau=tau,
                p_tp=p_tp,
                p_sl=p_sl,
                p_n=p_n,
                ev=ev,
                var=var,
                ev_lcb=low,
                ev_ucb=high,
                paths=len(sample),
                clusters=clusters,
                status=status,
            )
        )

    return EVCurve(
        tp_ticks=tp_ticks,
        sl_ticks=sl_ticks,
        cost_ticks=float(cost_ticks),
        protocol=PROTOCOL_SL_FIRST,
        block_rule=BLOCK_RULE,
        bootstrap=bootstrap,
        min_paths=min_paths,
        min_cluster_bars=min_cluster_bars,
        points=tuple(points),
    )


def _cluster_bootstrap(
    values: np.ndarray,
    session_of: np.ndarray,
    *,
    cost_ticks: float,
    resamples: int,
    rng: np.random.Generator,
) -> Tuple[float, float]:
    """CI среднего: ресамплинг торговых окон целиком (кластерный bootstrap).

    Одно окно — один блок: исходы внутри окна зависимы, между окнами независимы. При единственном
    кластере интервал вырождается в точечную оценку; вызывающий помечает это статусом.
    """
    unique = np.unique(session_of)
    clusters = [values[session_of == session] for session in unique]
    if len(clusters) < 2:
        mean = float(np.mean(values)) - cost_ticks
        return (mean, mean)

    draws = np.empty(resamples, dtype=float)
    for replica in range(resamples):
        picks = rng.integers(0, len(clusters), size=len(clusters))
        draws[replica] = float(np.mean(np.concatenate([clusters[pick] for pick in picks]))) - cost_ticks
    low, high = np.percentile(draws, [2.5, 97.5])
    return (float(low), float(high))


__all__ = [
    "BLOCK_MULTIPLIER",
    "BLOCK_RULE",
    "DEFAULT_BOOTSTRAP",
    "DEFAULT_MIN_PATHS",
    "DEFAULT_SEED",
    "EVCurve",
    "EVPoint",
    "FirstPassageError",
    "NEUTRAL",
    "PROTOCOL_SL_FIRST",
    "SL_WIN",
    "STATUS_DEGENERATE",
    "STATUS_NO_DATA",
    "STATUS_OK",
    "TP_WIN",
    "build_curve",
    "resolve_path",
]
