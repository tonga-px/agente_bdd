"""Pure functions for scheduling HubSpot follow-up tasks.

No I/O, no side effects. Uses zoneinfo (stdlib) and holidays (pip).
"""

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import holidays

TASK_AGENT_PREFIX = "Agente:calificar_lead"

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
) -> date:
    """Return the next business day (Mon-Fri, not a national holiday).

    Always advances at least 1 day from *reference*.
    """
    iso_code = None
    if country:
        iso_code = COUNTRY_HOLIDAYS.get(country.strip().lower())

    candidate = reference + timedelta(days=1)

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


def compute_task_due_date(country: str | None, now: datetime | None = None) -> str:
    """Compute the due date for a follow-up task. Returns ISO 8601 UTC string."""
    tz = get_timezone(country)
    if now is None:
        now = datetime.now(timezone.utc)

    local_now = now.astimezone(tz)
    day = next_business_day(local_now.date(), tz, country)
    utc_dt = random_business_time(day, tz)
    return utc_dt.isoformat()


def build_task_subject(company_name: str | None) -> str:
    """Build task subject with agent prefix."""
    name = (company_name or "").strip() or "Sin nombre"
    return f"{TASK_AGENT_PREFIX} | {name}"


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
