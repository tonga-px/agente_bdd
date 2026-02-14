from datetime import datetime, timezone
from html import escape

from app.schemas.responses import CallAttempt, ExtractedCallData


_STATUS_EMOJI = {
    "connected": "\u2705",
    "no_answer": "\u260e\ufe0f",
    "failed": "\u274c",
    "error": "\u26a0\ufe0f",
}


def build_prospeccion_note(
    company_name: str | None,
    call_attempts: list[CallAttempt],
    extracted: ExtractedCallData | None,
    transcript: str | None,
) -> str:
    title = escape(company_name or "Empresa")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = [
        f"<h2>Llamada de Prospeccion - {title}</h2>",
        f"<p><em>Fecha: {now}</em></p>",
    ]

    # Call result summary
    connected = any(a.status == "connected" for a in call_attempts)
    if connected:
        parts.append("<p>\u2705 <strong>Llamada conectada</strong></p>")
    elif call_attempts:
        parts.append("<p>\u274c <strong>No se pudo conectar</strong></p>")

    # Extracted data table
    fields = [
        ("Hotel", extracted.hotel_name if extracted else None),
        ("Habitaciones", extracted.num_rooms if extracted else None),
        ("Decisor", extracted.decision_maker_name if extracted else None),
        ("Telefono decisor", extracted.decision_maker_phone if extracted else None),
        ("Email decisor", extracted.decision_maker_email if extracted else None),
    ]
    table_rows: list[str] = []
    for label, value in fields:
        display = escape(value) if value else "<em>No proporcionado</em>"
        table_rows.append(
            f"<tr><td><strong>{label}</strong></td><td>{display}</td></tr>"
        )
    parts.append(
        "<h3>Datos clave</h3>"
        '<table border="1" cellpadding="6" cellspacing="0">'
        f"{''.join(table_rows)}</table>"
    )

    # Call attempts detail (collapsed)
    if call_attempts:
        rows: list[str] = []
        for attempt in call_attempts:
            emoji = _STATUS_EMOJI.get(attempt.status, "\u2753")
            phone = escape(attempt.phone_number)
            source = escape(attempt.source)
            row = f"<li>{emoji} {phone} ({source})"
            if attempt.error:
                row += f" - <em>{escape(attempt.error)}</em>"
            row += "</li>"
            rows.append(row)
        parts.append(f"<h3>Intentos de llamada</h3><ul>{''.join(rows)}</ul>")

    return "".join(parts)
