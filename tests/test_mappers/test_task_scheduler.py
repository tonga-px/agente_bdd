"""Tests for task_scheduler mapper (pure functions, no I/O)."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.mappers.task_scheduler import (
    TASK_AGENT_PREFIX,
    build_task_body,
    build_task_subject,
    compute_task_due_date,
    get_timezone,
    next_business_day,
    random_business_time,
)


# --- get_timezone ---


def test_get_timezone_known_country():
    tz = get_timezone("Paraguay")
    assert tz == ZoneInfo("America/Asuncion")


def test_get_timezone_case_insensitive():
    assert get_timezone("MEXICO") == ZoneInfo("America/Mexico_City")
    assert get_timezone("mexico") == ZoneInfo("America/Mexico_City")
    assert get_timezone("Mexico") == ZoneInfo("America/Mexico_City")


def test_get_timezone_unknown_country():
    assert get_timezone("Narnia") == ZoneInfo("UTC")


def test_get_timezone_none():
    assert get_timezone(None) == ZoneInfo("UTC")


def test_get_timezone_empty():
    assert get_timezone("") == ZoneInfo("UTC")


# --- next_business_day ---


def test_next_business_day_monday_to_tuesday():
    """Monday → Tuesday."""
    monday = date(2026, 2, 16)  # Monday
    tz = ZoneInfo("America/Asuncion")
    result = next_business_day(monday, tz, "Paraguay")
    assert result == date(2026, 2, 17)  # Tuesday
    assert result.weekday() == 1


def test_next_business_day_friday_to_monday():
    """Friday → Monday (skips weekend)."""
    friday = date(2026, 2, 20)  # Friday
    tz = ZoneInfo("America/Lima")
    result = next_business_day(friday, tz, "Peru")
    assert result == date(2026, 2, 23)  # Monday
    assert result.weekday() == 0


def test_next_business_day_saturday_to_monday():
    """Saturday → Monday."""
    saturday = date(2026, 2, 21)  # Saturday
    tz = ZoneInfo("UTC")
    result = next_business_day(saturday, tz)
    assert result == date(2026, 2, 23)  # Monday


def test_next_business_day_sunday_to_monday():
    """Sunday → Monday."""
    sunday = date(2026, 2, 22)  # Sunday
    tz = ZoneInfo("UTC")
    result = next_business_day(sunday, tz)
    assert result == date(2026, 2, 23)  # Monday


def test_next_business_day_skips_holiday():
    """If next weekday is a holiday, skip it."""
    # May 1, 2026 is Friday (Labour Day in Paraguay)
    thursday = date(2026, 4, 30)  # Thursday
    tz = ZoneInfo("America/Asuncion")
    result = next_business_day(thursday, tz, "Paraguay")
    # Friday May 1 is a holiday → skip to Monday May 4
    assert result == date(2026, 5, 4)
    assert result.weekday() == 0


def test_next_business_day_unknown_country_only_skips_weekends():
    """Unknown country → only skips weekends, not holidays."""
    friday = date(2026, 2, 20)
    tz = ZoneInfo("UTC")
    result = next_business_day(friday, tz, "Narnia")
    assert result == date(2026, 2, 23)  # Monday


def test_next_business_day_always_advances():
    """Even on a weekday, always returns at least tomorrow."""
    wednesday = date(2026, 2, 18)
    tz = ZoneInfo("UTC")
    result = next_business_day(wednesday, tz)
    assert result > wednesday


# --- random_business_time ---


def test_random_business_time_in_valid_range():
    """Returned hour should be in [9,12) or [14,17) local time."""
    tz = ZoneInfo("America/Asuncion")
    day = date(2026, 2, 17)

    for _ in range(50):
        utc_dt = random_business_time(day, tz)
        assert utc_dt.tzinfo == timezone.utc
        local = utc_dt.astimezone(tz)
        assert local.date() == day
        assert (9 <= local.hour < 12) or (14 <= local.hour < 17)


def test_random_business_time_returns_utc():
    tz = ZoneInfo("Europe/Madrid")
    day = date(2026, 3, 10)
    result = random_business_time(day, tz)
    assert result.tzinfo == timezone.utc


# --- compute_task_due_date ---


def test_compute_task_due_date_returns_iso():
    result = compute_task_due_date("Paraguay")
    dt = datetime.fromisoformat(result)
    assert dt.tzinfo is not None


def test_compute_task_due_date_friday_to_monday():
    """Friday reference → due date is Monday (or later if holiday)."""
    friday = datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc)
    result = compute_task_due_date("Peru", now=friday)
    dt = datetime.fromisoformat(result)
    local = dt.astimezone(ZoneInfo("America/Lima"))
    assert local.weekday() == 0  # Monday


def test_compute_task_due_date_none_country():
    """None country → still returns a valid ISO date (UTC fallback)."""
    result = compute_task_due_date(None)
    dt = datetime.fromisoformat(result)
    assert dt.tzinfo is not None


# --- build_task_subject ---


def test_build_task_subject_with_name():
    result = build_task_subject("Hotel Guaraní")
    assert result == f"{TASK_AGENT_PREFIX} | Hotel Guaraní"


def test_build_task_subject_none():
    result = build_task_subject(None)
    assert TASK_AGENT_PREFIX in result
    assert "Sin nombre" in result


def test_build_task_subject_empty():
    result = build_task_subject("")
    assert "Sin nombre" in result


# --- build_task_body ---


def test_build_task_body_full():
    body = build_task_body("123", "Hotel Sol", "Lima", "Peru")
    assert "company_id: 123" in body
    assert "company_name: Hotel Sol" in body
    assert "city: Lima" in body
    assert "country: Peru" in body


def test_build_task_body_partial():
    body = build_task_body("456", None, None, "Chile")
    assert "company_id: 456" in body
    assert "N/A" in body
    assert "country: Chile" in body
