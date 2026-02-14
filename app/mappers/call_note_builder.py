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

    # Call attempts section
    if call_attempts:
        rows: list[str] = []
        for attempt in call_attempts:
            emoji = _STATUS_EMOJI.get(attempt.status, "\u2753")
            source = escape(attempt.source)
            phone = escape(attempt.phone_number)
            row = f"<li>{emoji} {phone} ({source}) - {escape(attempt.status)}"
            if attempt.error:
                row += f" <em>({escape(attempt.error)})</em>"
            row += "</li>"
            rows.append(row)
        parts.append(f"<h3>Intentos de llamada</h3><ul>{''.join(rows)}</ul>")

    # Extracted data section
    if extracted:
        rows = []
        if extracted.hotel_name:
            rows.append(f"<li><strong>Hotel:</strong> {escape(extracted.hotel_name)}</li>")
        if extracted.num_rooms:
            rows.append(f"<li><strong>Habitaciones:</strong> {escape(extracted.num_rooms)}</li>")
        if extracted.decision_maker_name:
            rows.append(f"<li><strong>Decisor:</strong> {escape(extracted.decision_maker_name)}</li>")
        if extracted.decision_maker_phone:
            rows.append(f"<li><strong>Telefono decisor:</strong> {escape(extracted.decision_maker_phone)}</li>")
        if extracted.decision_maker_email:
            rows.append(f"<li><strong>Email decisor:</strong> {escape(extracted.decision_maker_email)}</li>")
        if rows:
            parts.append(f"<h3>Datos extraidos</h3><ul>{''.join(rows)}</ul>")

    # Transcript section
    if transcript:
        parts.append(f"<h3>Transcripcion</h3><pre>{escape(transcript)}</pre>")

    return "".join(parts)
