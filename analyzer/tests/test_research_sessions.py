"""Тесты расписания сессий и flat-at-close (шаг R0.3 плана)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.research.sessions import SessionError, SessionSchedule

MSK = timezone(timedelta(hours=3))


def _at(hour: int, minute: int, day: int = 18) -> datetime:
    """Момент в UTC: 2026-11-<day> (будни), заданное время по Москве."""
    return datetime(2026, 11, day, hour, minute, tzinfo=MSK).astimezone(timezone.utc)


def test_open_and_close_bounds():
    schedule = SessionSchedule()

    assert schedule.is_open(_at(10, 0)) is True
    assert schedule.is_open(_at(18, 39)) is True
    assert schedule.is_open(_at(18, 40)) is False
    assert schedule.is_open(_at(9, 59)) is False


def test_weekend_is_closed():
    schedule = SessionSchedule()

    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=MSK).astimezone(timezone.utc)
    sunday = datetime(2026, 11, 22, 12, 0, tzinfo=MSK).astimezone(timezone.utc)

    assert schedule.is_trading_day(saturday) is False
    assert schedule.is_trading_day(sunday) is False
    assert schedule.is_open(saturday) is False


def test_naive_datetime_rejected():
    schedule = SessionSchedule()

    with pytest.raises(SessionError):
        schedule.is_open(datetime(2026, 11, 18, 12, 0))


def test_tau_session_subtracts_execution_and_buffer():
    schedule = SessionSchedule()

    # 18:20 MSK: 20 минут до закрытия, tau_liq = 10, buffer = 5, bar = 1
    assert schedule.tau_session(_at(18, 20), tau_liq_minutes=10) == 5
    # при tau_liq = 0 действует max(0, bar_minutes) = 1
    assert schedule.tau_session(_at(18, 20), tau_liq_minutes=0) == 14


def test_entry_forbidden_in_last_minutes():
    schedule = SessionSchedule()

    assert schedule.entry_allowed(_at(18, 34), tau_liq_minutes=0) is True
    assert schedule.entry_allowed(_at(18, 34), tau_liq_minutes=10) is False
    assert schedule.entry_allowed(_at(18, 40)) is False


def test_session_minute_indexes_from_open():
    schedule = SessionSchedule()

    assert schedule.session_minute(_at(10, 0)) == 0
    assert schedule.session_minute(_at(10, 15)) == 15
    assert schedule.session_minute(_at(18, 45)) is None


def test_invalid_schedule_rejected():
    with pytest.raises(SessionError):
        SessionSchedule(open_minute=600, close_minute=600)
    with pytest.raises(SessionError):
        SessionSchedule(buffer_minutes=-1)
    with pytest.raises(SessionError):
        SessionSchedule(buffer_minutes=600)
    with pytest.raises(SessionError):
        SessionSchedule(timezone="Mars/Olympus")


def test_dst_transition_keeps_local_session_hours():
    """Переход на летнее/зимнее время не сдвигает сессию: время берётся в TZ биржи."""
    berlin = SessionSchedule(timezone="Europe/Berlin")

    summer = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)  # 12:00 CEST
    winter = datetime(2026, 12, 15, 10, 0, tzinfo=timezone.utc)  # 11:00 CET

    assert berlin.minute_of_day(summer) == 12 * 60
    assert berlin.minute_of_day(winter) == 11 * 60
    assert berlin.is_open(summer) is True
    assert berlin.is_open(winter) is True


def test_from_mapping_rejects_unknown_fields():
    with pytest.raises(SessionError):
        SessionSchedule.from_mapping({"open_minute": 600, "lunch": 780})


def test_from_mapping_builds_schedule():
    schedule = SessionSchedule.from_mapping(
        {"name": "custom", "open_minute": 600, "close_minute": 1080, "buffer_minutes": 10}
    )

    assert schedule.name == "custom"
    assert schedule.close_minute == 1080
    assert schedule.buffer_minutes == 10
