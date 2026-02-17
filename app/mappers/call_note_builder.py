from datetime import datetime, timezone
from html import escape

from app.schemas.responses import CallAttempt, ExtractedCallData


_STATUS_EMOJI = {
    "connected": "\u2705",
    "no_answer": "\u260e\ufe0f",
    "failed": "\u274c",
    "error": "\u26a0\ufe0f",
}


def _friendly_source(source: str) -> str:
    """Turn internal source labels into human-readable Spanish."""
    if source == "company":
        return "Empresa"
    if source.startswith("contact:"):
        parts = source.split(":")
        field = parts[2] if len(parts) > 2 else "phone"
        label = "celular" if field == "mobile" else "telefono"
        return f"Contacto ({label})"
    return source


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

    # Extracted data table (only when we have data)
    if extracted:
        fields = [
            ("Hotel", extracted.hotel_name),
            ("Habitaciones", extracted.num_rooms),
            ("Decisor", extracted.decision_maker_name),
            ("Telefono decisor", extracted.decision_maker_phone),
            ("Email decisor", extracted.decision_maker_email),
            ("Disponibilidad demo", extracted.date_and_time),
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

    # Call attempts detail
    if call_attempts:
        rows: list[str] = []
        for attempt in call_attempts:
            emoji = _STATUS_EMOJI.get(attempt.status, "\u2753")
            phone = escape(attempt.phone_number)
            source = escape(_friendly_source(attempt.source))
            row = f"<li>{emoji} {phone} ({source})"
            if attempt.error:
                row += f"<br>&nbsp;&nbsp;&nbsp;Motivo: <strong>{escape(attempt.error)}</strong>"
            row += "</li>"
            rows.append(row)
        parts.append(f"<h3>Intentos de llamada</h3><ul>{''.join(rows)}</ul>")

    return "".join(parts)
