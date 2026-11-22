"""Сигналы входа по отклонению от якоря окна.

Спека (`buy_frame.md` §6.1) требует сигнальный ряд, а тиков нет: якорь `μ` берётся как
накопленный VWAP текущего окна (только прошлые и текущий бар — look-ahead нет), `Z = (P − μ)/σ_P`,
вход при `|Z| ≥ |Z_entry|` против отклонения (`s = −sign(Z)`).

Это **провизионный** сигнал (`PROVISIONAL_SIGNAL`, наследует `UNVERIFIED:signal-series` из R0):
полноценная сигнальная модель — R5. Если в R5 правило изменится, EV-кривая R3 пересчитывается,
а не «уточняется коэффициентом».
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Sequence, Tuple

import numpy as np

from ..backtest.models import Bar
from .sessions import SessionSchedule

PROVISIONAL = "PROVISIONAL_SIGNAL"
SIGNAL_SERIES_FLAG = "UNVERIFIED:signal-series"
#: Минимум баров до первого сигнала: VWAP должен успеть набрать вес.
MIN_HISTORY = 30

SessionArrays = Dict[str, np.ndarray]


class SignalError(ValueError):
    """Сигналы нельзя построить по заданным данным."""


@dataclass(frozen=True)
class Signal:
    """Вход: номер окна, индекс бара в окне, направление, отклонение в `σ_P`."""

    session: int
    index: int
    direction: int
    z: float
    price: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "session": self.session,
            "index": self.index,
            "direction": self.direction,
            "z": round(self.z, 4),
            "price": round(self.price, 6),
        }


@dataclass(frozen=True)
class SignalSet:
    """Сигналы и геометрия, к которой они привязаны."""

    signals: Tuple[Signal, ...]
    sessions: int
    z_entry: float
    sigma_p_ticks: float
    flags: Dict[str, str]

    @property
    def waiting_bars(self) -> float:
        """Среднее ожидание следующего сигнала в барах (эндогенно порогу входа).

        Считается только внутри окна: разрыв между окнами — не ожидание, а простой
        инструмента, и приписывать его политике нельзя. `inf` — сигналов меньше двух.
        """
        if len(self.signals) < 2:
            return float("inf")
        gaps = [
            current.index - previous.index
            for previous, current in zip(self.signals, self.signals[1:])
            if current.session == previous.session
        ]
        if not gaps:
            return float("inf")
        return float(np.mean(gaps))

    def to_dict(self) -> Dict[str, object]:
        waiting = self.waiting_bars
        return {
            "count": len(self.signals),
            "sessions": self.sessions,
            "z_entry": self.z_entry,
            "sigma_p_ticks": round(self.sigma_p_ticks, 6),
            "waiting_bars": None if not np.isfinite(waiting) else round(waiting, 3),
            "flags": dict(self.flags),
        }


def session_ohlcv(bars: Sequence[Bar], schedule: SessionSchedule) -> List[SessionArrays]:
    """Бары, сгруппированные по (дата, окно) и упорядоченные по времени.

    Дубли меток схлопываются (остаётся последний): иначе VWAP и первый проход считались бы
    по двум барам с одним временем. Окна возвращаются в хронологическом порядке начала окна,
    а не по имени: индекс окна — это ещё и индекс сессии для путей.
    """
    grouped: Dict[Tuple[date, str], Dict[int, Bar]] = {}
    first_seen: Dict[Tuple[date, str], int] = {}
    for bar in bars:
        window = schedule.window_at(bar.ts)
        if window is None:
            continue
        stamp = int(bar.ts.timestamp())
        key = (schedule.local(bar.ts).date(), window.name)
        grouped.setdefault(key, {})[stamp] = bar
        first_seen[key] = min(first_seen.get(key, stamp), stamp)

    result: List[SessionArrays] = []
    for key in sorted(grouped, key=lambda item: (item[0], first_seen[item])):
        ordered = [grouped[key][stamp] for stamp in sorted(grouped[key])]
        result.append(
            {
                "open": np.asarray([bar.open for bar in ordered], dtype=float),
                "high": np.asarray([bar.high for bar in ordered], dtype=float),
                "low": np.asarray([bar.low for bar in ordered], dtype=float),
                "close": np.asarray([bar.close for bar in ordered], dtype=float),
                "volume": np.asarray([bar.volume for bar in ordered], dtype=float),
            }
        )
    return result


def ou_signals(
    sessions: Sequence[SessionArrays],
    *,
    sigma_p_ticks: float,
    z_entry: float,
    tick_value: float,
    min_history: int = MIN_HISTORY,
) -> SignalSet:
    """Сигналы против отклонения от накопленного VWAP с весом по объёму."""
    if sigma_p_ticks <= 0:
        raise SignalError("sigma_p_ticks must be positive")
    if tick_value <= 0:
        raise SignalError("tick_value must be positive")
    if z_entry <= 0:
        raise SignalError("z_entry must be positive")
    if min_history < 1:
        raise SignalError("min_history must be at least 1")

    signals: List[Signal] = []
    sigma_price = sigma_p_ticks * tick_value
    for session_index, arrays in enumerate(sessions):
        close = arrays["close"]
        if len(close) <= min_history:
            continue
        weight = np.cumsum(arrays["volume"])
        if weight[-1] <= 0:
            continue
        typical = (arrays["high"] + arrays["low"] + arrays["close"]) / 3.0
        anchor = np.cumsum(typical * arrays["volume"]) / weight
        for index in range(min_history, len(close)):
            z = (float(close[index]) - float(anchor[index])) / sigma_price
            if abs(z) < z_entry:
                continue
            signals.append(
                Signal(
                    session=session_index,
                    index=index,
                    direction=-1 if z > 0 else 1,
                    z=z,
                    price=float(close[index]),
                )
            )
    return SignalSet(
        signals=tuple(signals),
        sessions=len(sessions),
        z_entry=z_entry,
        sigma_p_ticks=sigma_p_ticks,
        flags={"signal": PROVISIONAL, "series": SIGNAL_SERIES_FLAG},
    )


__all__ = [
    "MIN_HISTORY",
    "PROVISIONAL",
    "SIGNAL_SERIES_FLAG",
    "SessionArrays",
    "Signal",
    "SignalError",
    "SignalSet",
    "ou_signals",
    "session_ohlcv",
]
