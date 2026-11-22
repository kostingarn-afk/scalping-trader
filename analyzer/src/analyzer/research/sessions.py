"""Торговые сессии и flat-at-close.

`τ_session = T_close − t − max(τ_liq, Δt) − buffer` (§6 `holding_exiting.md`): позиция
закрывается до конца текущего торгового окна, вход в последние минуты запрещён.

Расписание — проектное решение (`PROJECT-DECISION:sessions`), а не биржевой календарь: офлайн
истории календаря нет, а правило должно совпадать в бэктесте и runtime. Окон несколько
(утренняя, основная, вечерняя плюс отдельный набор выходного дня), потому что выгрузка
Collector'а содержит весь торговый день вместе с выходными: однооконная модель отбрасывала бы
две трети баров.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Moscow"
DEFAULT_BUFFER_MINUTES = 5


class SessionError(ValueError):
    """Некорректное расписание или момент времени."""


@dataclass(frozen=True)
class SessionWindow:
    """Одно торговое окно в минутах от полуночи в TZ биржи."""

    open_minute: int
    close_minute: int
    name: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.open_minute < self.close_minute <= 24 * 60:
            raise SessionError(
                f"{self.name or 'window'}: open/close must satisfy 0 <= open < close <= 1440 "
                f"(got {self.open_minute}, {self.close_minute})"
            )

    @property
    def length(self) -> int:
        return self.close_minute - self.open_minute


#: Будни: утренняя, основная и вечерняя сессии MOEX для акций.
DEFAULT_WEEKDAY_WINDOWS: Tuple[SessionWindow, ...] = (
    SessionWindow(6 * 60 + 50, 9 * 60 + 50, "morning"),
    SessionWindow(10 * 60, 18 * 60 + 40, "main"),
    SessionWindow(19 * 60, 23 * 60 + 50, "evening"),
)

#: Выходные: единое окно торгов выходного дня. Границы взяты из фактического покрытия
#: выгрузки (02:00–23:50 MSK) и подтверждаются сверкой с расписанием биржи.
DEFAULT_WEEKEND_WINDOWS: Tuple[SessionWindow, ...] = (
    SessionWindow(2 * 60, 23 * 60 + 50, "weekend"),
)


def _validate_windows(windows: Sequence[SessionWindow], label: str) -> Tuple[SessionWindow, ...]:
    ordered = tuple(sorted(windows, key=lambda window: window.open_minute))
    for previous, current in zip(ordered, ordered[1:]):
        if current.open_minute < previous.close_minute:
            raise SessionError(f"{label}: windows overlap ({previous.name} and {current.name})")
    return ordered


@dataclass(frozen=True)
class SessionSchedule:
    """Расписание: наборы непересекающихся торговых окон для будней и выходных."""

    weekday_windows: Tuple[SessionWindow, ...] = DEFAULT_WEEKDAY_WINDOWS
    weekend_windows: Tuple[SessionWindow, ...] = DEFAULT_WEEKEND_WINDOWS
    timezone: str = DEFAULT_TZ
    buffer_minutes: int = DEFAULT_BUFFER_MINUTES
    name: str = "moex"

    def __post_init__(self) -> None:
        object.__setattr__(self, "weekday_windows", _validate_windows(self.weekday_windows, self.name))
        object.__setattr__(self, "weekend_windows", _validate_windows(self.weekend_windows, self.name))
        if not self.weekday_windows and not self.weekend_windows:
            raise SessionError(f"{self.name}: at least one trading window is required")
        if self.buffer_minutes < 0:
            raise SessionError(f"{self.name}: buffer must be non-negative")
        shortest = min(
            window.length for window in self.weekday_windows + self.weekend_windows
        )
        if self.buffer_minutes >= shortest:
            raise SessionError(f"{self.name}: buffer is longer than the shortest window")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:  # noqa: BLE001 - zoneinfo raises several types
            raise SessionError(f"{self.name}: unknown timezone {self.timezone!r}") from exc

    def local(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            raise SessionError("timezone-aware datetime is required")
        return moment.astimezone(ZoneInfo(self.timezone))

    def minute_of_day(self, moment: datetime) -> int:
        local = self.local(moment)
        return local.hour * 60 + local.minute

    def is_trading_day(self, moment: datetime) -> bool:
        """Будни. Праздники не моделируются: их ловит Gate D как пропуски."""
        return self.local(moment).weekday() < 5

    def windows_for(self, moment: datetime) -> Tuple[SessionWindow, ...]:
        return self.weekday_windows if self.is_trading_day(moment) else self.weekend_windows

    def window_at(self, moment: datetime) -> Optional[SessionWindow]:
        minute = self.minute_of_day(moment)
        for window in self.windows_for(moment):
            if window.open_minute <= minute < window.close_minute:
                return window
        return None

    def is_open(self, moment: datetime) -> bool:
        return self.window_at(moment) is not None

    def minutes_to_close(self, moment: datetime) -> int:
        """Минуты до конца текущего окна; вне сессии — `-1`."""
        window = self.window_at(moment)
        if window is None:
            return -1
        return window.close_minute - self.minute_of_day(moment)

    def session_minute(self, moment: datetime) -> Optional[int]:
        """Минута от открытия текущего окна (для десезонализации) или None вне сессии."""
        window = self.window_at(moment)
        if window is None:
            return None
        return self.minute_of_day(moment) - window.open_minute

    def window_bounds(self, moment: datetime) -> Optional[Tuple[int, int]]:
        """Границы текущего окна в минутах от полуночи (для проверки аукционных принтов)."""
        window = self.window_at(moment)
        if window is None:
            return None
        return window.open_minute, window.close_minute

    def session_minutes(self) -> int:
        """Суммарная длительность торговых окон буднего дня."""
        return sum(window.length for window in self.weekday_windows)

    def tau_session(
        self,
        moment: datetime,
        tau_liq_minutes: int = 0,
        bar_minutes: int = 1,
    ) -> int:
        """Допустимый горизонт удержания до закрытия окна; `< 0` — «вход запрещён»."""
        if tau_liq_minutes < 0:
            raise SessionError("tau_liq must be non-negative")
        if bar_minutes < 1:
            raise SessionError("bar_minutes must be at least 1")
        window = self.window_at(moment)
        if window is None:
            return -1
        return (
            window.close_minute
            - self.minute_of_day(moment)
            - max(tau_liq_minutes, bar_minutes)
            - self.buffer_minutes
        )

    def entry_allowed(
        self,
        moment: datetime,
        tau_liq_minutes: int = 0,
        bar_minutes: int = 1,
    ) -> bool:
        return self.tau_session(moment, tau_liq_minutes, bar_minutes) >= 0

    @classmethod
    def single(
        cls,
        open_minute: int,
        close_minute: int,
        *,
        timezone: str = DEFAULT_TZ,
        buffer_minutes: int = DEFAULT_BUFFER_MINUTES,
        name: str = "single",
    ) -> "SessionSchedule":
        """Расписание из одного окна на будни, без торгов выходного дня."""
        return cls(
            weekday_windows=(SessionWindow(open_minute, close_minute, name),),
            weekend_windows=(),
            timezone=timezone,
            buffer_minutes=buffer_minutes,
            name=name,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "SessionSchedule":
        known = {field_name for field_name in cls.__dataclass_fields__}
        unknown = set(payload) - known
        if unknown:
            raise SessionError(f"unknown schedule fields: {', '.join(sorted(unknown))}")

        def parse(raw: object, field_name: str) -> Optional[Tuple[SessionWindow, ...]]:
            if raw is None:
                return None
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                raise SessionError(f"{field_name} must be a sequence of mappings")
            return tuple(
                SessionWindow(
                    open_minute=int(item["open_minute"]),
                    close_minute=int(item["close_minute"]),
                    name=str(item.get("name", "")),
                )
                for item in raw
            )

        weekday = parse(payload.get("weekday_windows"), "weekday_windows")
        weekend = parse(payload.get("weekend_windows"), "weekend_windows")
        rest = {
            key: value
            for key, value in payload.items()
            if key not in ("weekday_windows", "weekend_windows")
        }
        kwargs: dict = dict(rest)
        if weekday is not None:
            kwargs["weekday_windows"] = weekday
        if weekend is not None:
            kwargs["weekend_windows"] = weekend
        return cls(**kwargs)  # type: ignore[arg-type]


__all__ = [
    "DEFAULT_BUFFER_MINUTES",
    "DEFAULT_TZ",
    "DEFAULT_WEEKDAY_WINDOWS",
    "DEFAULT_WEEKEND_WINDOWS",
    "SessionError",
    "SessionSchedule",
    "SessionWindow",
]
