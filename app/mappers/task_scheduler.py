"""Pure functions for scheduling HubSpot follow-up tasks.

No I/O, no side effects. Uses zoneinfo (stdlib) and holidays (pip).
"""

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import holidays

TASK_AGENT_PREFIX = "Agente:calificar_lead"
AGENT_SUBJECT_PREFIX = "Agente:"

# Country name (lowercase) → IANA timezone
COUNTRY_TIMEZONES: dict[str, str] = {
    "argentina": "America/Argentina/Buenos_Aires",
    "bolivia": "America/La_Paz",
    "brazil": "America/Sao_Paulo",
    "chile": "America/Santiago",
    "colombia": "America/Bogota",
    "costa rica": "America/Costa_Rica",
    "cuba": "America/Havana",
    "dominican republic": "America/Santo_Domingo",
    "ecuador": "America/Guayaquil",
    "el salvador": "America/El_Salvador",
    "guatemala": "America/Guatemala",
    "honduras": "America/Tegucigalpa",
    "mexico": "America/Mexico_City",
    "nicaragua": "America/Managua",
    "panama": "America/Panama",
    "paraguay": "America/Asuncion",
    "peru": "America/Lima",
    "puerto rico": "America/Puerto_Rico",
    "spain": "Europe/Madrid",
    "uruguay": "America/Montevideo",
    "venezuela": "America/Caracas",
}

# Country name (lowercase) → ISO code for holidays library
COUNTRY_HOLIDAYS: dict[str, str] = {
    "argentina": "AR",
    "bolivia": "BO",
    "brazil": "BR",
    "chile": "CL",
    "colombia": "CO",
    "costa rica": "CR",
    "cuba": "CU",
    "dominican republic": "DO",
    "ecuador": "EC",
    "el salvador": "SV",
    "guatemala": "GT",
    "honduras": "HN",
    "mexico": "MX",
    "nicaragua": "NI",
    "panama": "PA",
    "paraguay": "PY",
    "peru": "PE",
    "puerto rico": "US",
    "spain": "ES",
    "uruguay": "UY",
    "venezuela": "VE",
}


def get_timezone(country: str | None) -> ZoneInfo:
    """Return ZoneInfo for a country name. Falls back to UTC."""
    if not country:
        return ZoneInfo("UTC")
    key = country.strip().lower()
    tz_name = COUNTRY_TIMEZONES.get(key, "UTC")
    return ZoneInfo(tz_name)


def next_business_day(
    reference: date, tz: ZoneInfo, country: str | None = None,
    *, include_reference: bool = False,
) -> date:
    """Return the next business day (Mon-Fri, not a national holiday).

    When *include_reference* is False (default), always advances at least
    1 day from *reference*.  When True, *reference* itself is returned if
    it is already a business day.
    """
    iso_code = None
    if country:
        iso_code = COUNTRY_HOLIDAYS.get(country.strip().lower())

    candidate = reference if include_reference else reference + timedelta(days=1)

    for _ in range(30):  # safety cap
        if candidate.weekday() < 5:  # Mon-Fri
            if iso_code:
                year_holidays = holidays.country_holidays(
                    iso_code, years=candidate.year,
                )
                if candidate not in year_holidays:
                    return candidate
            else:
                return candidate
        candidate += timedelta(days=1)

    return candidate  # fallback (shouldn't happen)


def random_business_time(day: date, tz: ZoneInfo) -> datetime:
    """Pick a random time in morning (9:00-11:59) or afternoon (14:00-16:59).

    Returns a UTC datetime.
    """
    slot = random.choice(["morning", "afternoon"])
    if slot == "morning":
        hour = random.randint(9, 11)
    else:
        hour = random.randint(14, 16)
    minute = random.randint(0, 59)

    local_dt = datetime.combine(day, time(hour, minute), tzinfo=tz)
    return local_dt.astimezone(timezone.utc)


MIN_FUTURE_MINUTES = 10


def compute_task_due_date(country: str | None, now: datetime | None = None) -> str:
    """Compute the due date for a follow-up task. Returns ISO 8601 UTC string.

    If today is a business day (Mon-Fri, not a holiday) and at least
    MIN_FUTURE_MINUTES remain before 17:00 local, the task is due today
    (now + MIN_FUTURE_MINUTES).  Otherwise it is due at 09:00 on the
    next business day.
    """
    tz = get_timezone(country)
    if now is None:
        now = datetime.now(timezone.utc)

    local_now = now.astimezone(tz)
    today = local_now.date()

    # Check if today is a business day with enough time remaining
    today_viable = False
    if today.weekday() < 5:
        iso_code = (
            COUNTRY_HOLIDAYS.get(country.strip().lower()) if country else None
        )
        if iso_code:
            year_holidays = holidays.country_holidays(
                iso_code, years=today.year,
            )
            today_viable = today not in year_holidays
        else:
            today_viable = True

    if today_viable:
        end_of_day = datetime.combine(today, time(17, 0), tzinfo=tz)
        min_due = local_now + timedelta(minutes=MIN_FUTURE_MINUTES)
        if min_due < end_of_day:
            return min_due.astimezone(timezone.utc).isoformat()

    # Next business day at 09:00 local
    day = next_business_day(today, tz, country)
    utc_dt = datetime.combine(day, time(9, 0), tzinfo=tz).astimezone(timezone.utc)
    return utc_dt.isoformat()


def build_task_subject(company_name: str | None) -> str:
    """Build task subject with agent prefix."""
    name = (company_name or "").strip() or "Sin nombre"
    return f"{TASK_AGENT_PREFIX} | {name}"


def parse_task_agente(subject: str) -> str | None:
    """Extract agent value from task subject.

    "Agente:calificar_lead | Hotel ABC" → "calificar_lead"
    "Agente:datos | Hotel XYZ"          → "datos"
    "Tarea normal"                        → None
    """
    if not subject or not subject.startswith(AGENT_SUBJECT_PREFIX):
        return None
    after_prefix = subject[len(AGENT_SUBJECT_PREFIX):]
    # Take everything before " | " (or the whole thing if no separator)
    agente_part = after_prefix.split(" | ", 1)[0].strip()
    return agente_part or None


def is_business_hour(country: str | None, now: datetime | None = None) -> bool:
    """Check if current time is within 9:00-17:00 in the country's local timezone."""
    tz = get_timezone(country)
    if now is None:
        now = datetime.now(timezone.utc)
    local_now = now.astimezone(tz)
    return 9 <= local_now.hour < 17


def is_business_day(country: str | None, now: datetime | None = None) -> bool:
    """Check if today is a business day (Mon-Fri, not a holiday) in the country."""
    tz = get_timezone(country)
    if now is None:
        now = datetime.now(timezone.utc)
    local_now = now.astimezone(tz)
    local_date = local_now.date()

    # Weekend check
    if local_date.weekday() >= 5:
        return False

    # Holiday check
    if country:
        iso_code = COUNTRY_HOLIDAYS.get(country.strip().lower())
        if iso_code:
            year_holidays = holidays.country_holidays(iso_code, years=local_date.year)
            if local_date in year_holidays:
                return False

    return True


def build_hacer_tareas_note(agente_value: str, task_subject: str) -> str:
    """Build note body for the hacer_tareas agent."""
    return (
        f"Hacer Tareas: se activó agente '{agente_value}'\n"
        f"Tarea: {task_subject}"
    )


def build_task_body(
    company_id: str,
    company_name: str | None,
    city: str | None,
    country: str | None,
) -> str:
    """Build structured task body with company context."""
    lines = [
        f"company_id: {company_id}",
        f"company_name: {company_name or 'N/A'}",
        f"city: {city or 'N/A'}",
        f"country: {country or 'N/A'}",
    ]
    return "\n".join(lines)
