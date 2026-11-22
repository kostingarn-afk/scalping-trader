"""Тесты расписания сессий и flat-at-close (шаг R0.3 плана)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from analyzer.research.sessions import (
    SessionError,
    SessionSchedule,
    SessionWindow,
)

MSK = timezone(timedelta(hours=3))


def _at(hour: int, minute: int, day: int = 18) -> datetime:
    """Момент в UTC: 2026-11-<day> (будни), заданное время по Москве."""
    return datetime(2026, 11, day, hour, minute, tzinfo=MSK).astimezone(timezone.utc)


def _main_only(buffer_minutes: int = 5) -> SessionSchedule:
    return SessionSchedule.single(10 * 60, 18 * 60 + 40, buffer_minutes=buffer_minutes)


def test_default_windows_cover_morning_main_and_evening():
    schedule = SessionSchedule()

    assert schedule.is_open(_at(7, 0)) is True  # утренняя
    assert schedule.is_open(_at(12, 0)) is True  # основная
    assert schedule.is_open(_at(20, 0)) is True  # вечерняя
    assert schedule.is_open(_at(9, 55)) is False  # разрыв между утренней и основной
    assert schedule.is_open(_at(18, 45)) is False  # разрыв основная/вечерняя
    assert schedule.is_open(_at(6, 49)) is False
    assert schedule.is_open(_at(23, 50)) is False


def test_weekend_window_is_modelled():
    schedule = SessionSchedule()

    saturday_noon = datetime(2026, 11, 21, 12, 0, tzinfo=MSK).astimezone(timezone.utc)
    sunday_early = datetime(2026, 11, 22, 3, 0, tzinfo=MSK).astimezone(timezone.utc)
    sunday_late = datetime(2026, 11, 22, 23, 55, tzinfo=MSK).astimezone(timezone.utc)

    assert schedule.is_trading_day(saturday_noon) is False
    assert schedule.is_open(saturday_noon) is True
    assert schedule.is_open(sunday_early) is True
    assert schedule.is_open(sunday_late) is False


def test_weekend_can_be_disabled():
    schedule = _main_only()

    saturday_noon = datetime(2026, 11, 21, 12, 0, tzinfo=MSK).astimezone(timezone.utc)

    assert schedule.is_open(saturday_noon) is False


def test_open_and_close_bounds_of_single_window():
    schedule = _main_only()

    assert schedule.is_open(_at(10, 0)) is True
    assert schedule.is_open(_at(18, 39)) is True
    assert schedule.is_open(_at(18, 40)) is False
    assert schedule.is_open(_at(9, 59)) is False


def test_weekend_is_trading_day_flag_false_but_session_open():
    schedule = SessionSchedule()

    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=MSK).astimezone(timezone.utc)

    assert schedule.is_trading_day(saturday) is False
    assert schedule.window_bounds(saturday) == (2 * 60, 23 * 60 + 50)


def test_naive_datetime_rejected():
    schedule = SessionSchedule()

    with pytest.raises(SessionError):
        schedule.is_open(datetime(2026, 11, 18, 12, 0))


def test_tau_session_subtracts_execution_and_buffer():
    schedule = _main_only()

    # 18:20 MSK: 20 минут до закрытия окна, tau_liq = 10, buffer = 5, bar = 1
    assert schedule.tau_session(_at(18, 20), tau_liq_minutes=10) == 5
    # при tau_liq = 0 действует max(0, bar_minutes) = 1
    assert schedule.tau_session(_at(18, 20), tau_liq_minutes=0) == 14


def test_tau_session_uses_current_window_not_last():
    schedule = SessionSchedule()

    # 18:20: до конца основной 20 минут, несмотря на вечернюю сессию позже
    assert schedule.tau_session(_at(18, 20), tau_liq_minutes=0) == 14
    # 20:00: до конца вечерней 230 минут, за вычетом бара и buffer
    assert schedule.tau_session(_at(20, 0), tau_liq_minutes=0) == 224


def test_entry_forbidden_in_last_minutes():
    schedule = _main_only()

    assert schedule.entry_allowed(_at(18, 34), tau_liq_minutes=0) is True
    assert schedule.entry_allowed(_at(18, 34), tau_liq_minutes=10) is False
    assert schedule.entry_allowed(_at(18, 40)) is False
    assert schedule.entry_allowed(_at(12, 0), tau_liq_minutes=600) is False


def test_session_minute_indexes_from_window_open():
    schedule = SessionSchedule()

    assert schedule.session_minute(_at(10, 0)) == 0
    assert schedule.session_minute(_at(10, 15)) == 15
    assert schedule.session_minute(_at(19, 5)) == 5
    assert schedule.session_minute(_at(18, 45)) is None


def test_window_bounds_match_current_window():
    schedule = SessionSchedule()

    assert schedule.window_bounds(_at(20, 0)) == (19 * 60, 23 * 60 + 50)
    assert schedule.window_bounds(_at(18, 45)) is None


def test_session_minutes_sum_of_windows():
    schedule = SessionSchedule()

    assert schedule.session_minutes() == 180 + 520 + 290


def test_invalid_schedule_rejected():
    with pytest.raises(SessionError):
        SessionSchedule(weekday_windows=(), weekend_windows=())
    with pytest.raises(SessionError):
        SessionSchedule(buffer_minutes=-1)
    with pytest.raises(SessionError):
        SessionSchedule(buffer_minutes=600)
    with pytest.raises(SessionError):
        SessionSchedule(timezone="Mars/Olympus")
    with pytest.raises(SessionError):
        SessionSchedule(
            weekday_windows=(
                SessionWindow(600, 1080),
                SessionWindow(1000, 1200),
            )
        )


def test_dst_transition_keeps_local_session_hours():
    """Переход на летнее/зимнее время не сдвигает сессию: время берётся в TZ биржи."""
    berlin = SessionSchedule.single(10 * 60, 18 * 60, timezone="Europe/Berlin")

    summer = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)  # 12:00 CEST
    winter = datetime(2026, 12, 15, 10, 0, tzinfo=timezone.utc)  # 11:00 CET

    assert berlin.minute_of_day(summer) == 12 * 60
    assert berlin.minute_of_day(winter) == 11 * 60
    assert berlin.is_open(summer) is True
    assert berlin.is_open(winter) is True


def test_from_mapping_rejects_unknown_fields():
    with pytest.raises(SessionError):
        SessionSchedule.from_mapping({"open_minute": 600, "lunch": 780})


def test_from_mapping_builds_schedule_with_windows():
    schedule = SessionSchedule.from_mapping(
        {
            "name": "custom",
            "buffer_minutes": 10,
            "weekday_windows": [
                {"open_minute": 600, "close_minute": 1080, "name": "main"},
                {"open_minute": 1140, "close_minute": 1380, "name": "late"},
            ],
            "weekend_windows": [],
        }
    )

    assert schedule.name == "custom"
    assert schedule.buffer_minutes == 10
    assert schedule.window_bounds(_at(19, 30)) == (1140, 1380)
    assert schedule.is_open(_at(18, 15)) is False  # между окнами
    assert schedule.weekend_windows == ()


def test_single_window_helper():
    schedule: Optional[SessionSchedule] = SessionSchedule.single(600, 1080)

    assert schedule is not None
    assert schedule.window_bounds(_at(12, 0)) == (600, 1080)
