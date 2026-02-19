"""Tests for task_scheduler mapper (pure functions, no I/O)."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.mappers.task_scheduler import (
    TASK_AGENT_PREFIX,
    build_hacer_tareas_note,
    build_task_body,
    build_task_subject,
    compute_task_due_date,
    get_timezone,
    is_business_day,
    is_business_hour,
    next_business_day,
    parse_task_agente,
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


# --- next_business_day include_reference ---


def test_next_business_day_include_reference_weekday():
    """include_reference=True on a weekday → returns same day."""
    wednesday = date(2026, 2, 18)
    tz = ZoneInfo("UTC")
    result = next_business_day(wednesday, tz, include_reference=True)
    assert result == wednesday


def test_next_business_day_include_reference_saturday():
    """include_reference=True on Saturday → still advances to Monday."""
    saturday = date(2026, 2, 21)
    tz = ZoneInfo("UTC")
    result = next_business_day(saturday, tz, include_reference=True)
    assert result == date(2026, 2, 23)  # Monday


def test_next_business_day_include_reference_holiday():
    """include_reference=True on a holiday → advances past it."""
    # May 1, 2026 is Friday (Labour Day in Paraguay)
    friday_holiday = date(2026, 5, 1)
    tz = ZoneInfo("America/Asuncion")
    result = next_business_day(friday_holiday, tz, "Paraguay", include_reference=True)
    assert result == date(2026, 5, 4)  # Monday


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


def test_compute_task_due_date_weekday_returns_same_day():
    """Wednesday reference → due date is Wednesday (today, a business day)."""
    wednesday = datetime(2026, 2, 18, 15, 0, tzinfo=timezone.utc)
    result = compute_task_due_date("Peru", now=wednesday)
    dt = datetime.fromisoformat(result)
    local = dt.astimezone(ZoneInfo("America/Lima"))
    assert local.weekday() == 2  # Wednesday

def test_compute_task_due_date_saturday_to_monday():
    """Saturday reference → due date is Monday."""
    saturday = datetime(2026, 2, 21, 15, 0, tzinfo=timezone.utc)
    result = compute_task_due_date("Peru", now=saturday)
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


# --- parse_task_agente ---


def test_parse_task_agente_calificar_lead():
    assert parse_task_agente("Agente:calificar_lead | Hotel ABC") == "calificar_lead"


def test_parse_task_agente_datos():
    assert parse_task_agente("Agente:datos | Hotel XYZ") == "datos"


def test_parse_task_agente_no_hotel_part():
    assert parse_task_agente("Agente:calificar_lead") == "calificar_lead"


def test_parse_task_agente_not_agent_task():
    assert parse_task_agente("Tarea normal") is None


def test_parse_task_agente_empty():
    assert parse_task_agente("") is None


def test_parse_task_agente_none():
    assert parse_task_agente(None) is None


def test_parse_task_agente_prefix_only():
    assert parse_task_agente("Agente:") is None


# --- is_business_hour ---


def test_is_business_hour_within_hours():
    """10:00 local → True."""
    # Paraguay is UTC-3; 13:00 UTC = 10:00 PYT
    now = datetime(2026, 2, 17, 13, 0, tzinfo=timezone.utc)
    assert is_business_hour("Paraguay", now) is True


def test_is_business_hour_before_nine():
    """8:00 local → False."""
    # 11:00 UTC = 8:00 PYT
    now = datetime(2026, 2, 17, 11, 0, tzinfo=timezone.utc)
    assert is_business_hour("Paraguay", now) is False


def test_is_business_hour_at_nine():
    """9:00 local → True (inclusive)."""
    # 12:00 UTC = 9:00 PYT
    now = datetime(2026, 2, 17, 12, 0, tzinfo=timezone.utc)
    assert is_business_hour("Paraguay", now) is True


def test_is_business_hour_at_seventeen():
    """17:00 local → False (exclusive)."""
    # 20:00 UTC = 17:00 PYT
    now = datetime(2026, 2, 17, 20, 0, tzinfo=timezone.utc)
    assert is_business_hour("Paraguay", now) is False


def test_is_business_hour_at_sixteen_fifty_nine():
    """16:59 local → True."""
    # 19:59 UTC = 16:59 PYT
    now = datetime(2026, 2, 17, 19, 59, tzinfo=timezone.utc)
    assert is_business_hour("Paraguay", now) is True


def test_is_business_hour_none_country_uses_utc():
    now = datetime(2026, 2, 17, 12, 0, tzinfo=timezone.utc)
    assert is_business_hour(None, now) is True


# --- is_business_day ---


def test_is_business_day_weekday():
    """Tuesday → True."""
    now = datetime(2026, 2, 17, 12, 0, tzinfo=timezone.utc)  # Tuesday
    assert is_business_day("Paraguay", now) is True


def test_is_business_day_saturday():
    """Saturday → False."""
    now = datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc)  # Saturday
    assert is_business_day("Paraguay", now) is False


def test_is_business_day_sunday():
    """Sunday → False."""
    now = datetime(2026, 2, 22, 12, 0, tzinfo=timezone.utc)  # Sunday
    assert is_business_day("Paraguay", now) is False


def test_is_business_day_holiday():
    """May 1 (Labour Day in Paraguay) → False."""
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    assert is_business_day("Paraguay", now) is False


def test_is_business_day_none_country():
    """None country → only checks weekday (no holiday check)."""
    # May 1 is a Friday in 2026
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    assert is_business_day(None, now) is True


def test_is_business_day_unknown_country():
    """Unknown country → only checks weekday (no holiday check)."""
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    assert is_business_day("Narnia", now) is True


# --- build_hacer_tareas_note ---


def test_build_hacer_tareas_note():
    note = build_hacer_tareas_note("calificar_lead", "Agente:calificar_lead | Hotel ABC")
    assert "calificar_lead" in note
    assert "Agente:calificar_lead | Hotel ABC" in note
    assert note.startswith("Hacer Tareas:")
