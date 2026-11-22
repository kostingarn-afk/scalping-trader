"""Торговые сессии и flat-at-close.

`τ_session = T_close − t − max(τ_liq, Δt) − buffer` (§6 `holding_exiting.md`): позиция
закрывается до конца сессии, вход в последние минуты запрещён. Расписание — проектное
решение (`PROJECT-DECISION:sessions`), а не биржевой календарь: офлайн-истории календаря нет,
а правило должно совпадать в бэктесте и runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Moscow"
DEFAULT_OPEN_MINUTE = 10 * 60  # 10:00 MSK, основная сессия
DEFAULT_CLOSE_MINUTE = 18 * 60 + 40  # 18:40 MSK
DEFAULT_BUFFER_MINUTES = 5


class SessionError(ValueError):
    """Некорректное расписание или момент времени."""


@dataclass(frozen=True)
class SessionSchedule:
    """Расписание одной сессии в минутах от полуночи в TZ биржи."""

    name: str = "moex-main"
    open_minute: int = DEFAULT_OPEN_MINUTE
    close_minute: int = DEFAULT_CLOSE_MINUTE
    timezone: str = DEFAULT_TZ
    buffer_minutes: int = DEFAULT_BUFFER_MINUTES

    def __post_init__(self) -> None:
        if not 0 <= self.open_minute < self.close_minute <= 24 * 60:
            raise SessionError(
                f"{self.name}: open/close must satisfy 0 <= open < close <= 1440 "
                f"(got {self.open_minute}, {self.close_minute})"
            )
        if self.buffer_minutes < 0:
            raise SessionError(f"{self.name}: buffer must be non-negative")
        if self.buffer_minutes >= self.close_minute - self.open_minute:
            raise SessionError(f"{self.name}: buffer is longer than the session")
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

    def is_open(self, moment: datetime) -> bool:
        if not self.is_trading_day(moment):
            return False
        return self.open_minute <= self.minute_of_day(moment) < self.close_minute

    def minutes_to_close(self, moment: datetime) -> int:
        """Минуты до закрытия; отрицательное значение — сессия уже закрыта."""
        return self.close_minute - self.minute_of_day(moment)

    def session_minute(self, moment: datetime) -> Optional[int]:
        """Минута от открытия сессии (для десезонализации) или None вне сессии."""
        if not self.is_open(moment):
            return None
        return self.minute_of_day(moment) - self.open_minute

    def session_minutes(self) -> int:
        return self.close_minute - self.open_minute

    def tau_session(
        self,
        moment: datetime,
        tau_liq_minutes: int = 0,
        bar_minutes: int = 1,
    ) -> int:
        """Допустимый горизонт удержания до закрытия; `< 0` означает «вход запрещён»."""
        if tau_liq_minutes < 0:
            raise SessionError("tau_liq must be non-negative")
        if bar_minutes < 1:
            raise SessionError("bar_minutes must be at least 1")
        return (
            self.minutes_to_close(moment)
            - max(tau_liq_minutes, bar_minutes)
            - self.buffer_minutes
        )

    def entry_allowed(
        self,
        moment: datetime,
        tau_liq_minutes: int = 0,
        bar_minutes: int = 1,
    ) -> bool:
        return self.is_open(moment) and self.tau_session(moment, tau_liq_minutes, bar_minutes) >= 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "SessionSchedule":
        known = {field for field in cls.__dataclass_fields__}
        unknown = set(payload) - known
        if unknown:
            raise SessionError(f"unknown schedule fields: {', '.join(sorted(unknown))}")
        return cls(**payload)  # type: ignore[arg-type]


__all__ = [
    "DEFAULT_BUFFER_MINUTES",
    "DEFAULT_CLOSE_MINUTE",
    "DEFAULT_OPEN_MINUTE",
    "DEFAULT_TZ",
    "SessionError",
    "SessionSchedule",
]
