from app.mappers.call_note_builder import build_prospeccion_note
from app.schemas.responses import CallAttempt, ExtractedCallData


def test_full_note():
    attempts = [
        CallAttempt(phone_number="+56 1 1111", source="company", status="failed", error="No answer"),
        CallAttempt(
            phone_number="+56 2 2222",
            source="contact:100:phone",
            conversation_id="conv-1",
            status="connected",
        ),
    ]
    extracted = ExtractedCallData(
        hotel_name="Hotel Paraiso",
        num_rooms="120",
        decision_maker_name="Juan Perez",
        decision_maker_phone="+56 9 8888",
        decision_maker_email="juan@paraiso.cl",
        date_and_time="Martes 15 a las 10:00",
    )
    transcript = "Agente: Hola\nHotel: Buenos dias"

    html = build_prospeccion_note("Hotel Paraiso", attempts, extracted, transcript)

    assert "Llamada de Prospeccion - Hotel Paraiso" in html
    assert "Fecha:" in html
    assert "Llamada conectada" in html
    # Data table
    assert "Datos clave" in html
    assert "Hotel Paraiso" in html
    assert "120" in html
    assert "Juan Perez" in html
    assert "+56 9 8888" in html
    assert "juan@paraiso.cl" in html
    assert "Disponibilidad demo" in html
    assert "Martes 15 a las 10:00" in html
    # Attempts section
    assert "Intentos de llamada" in html
    assert "+56 1 1111" in html
    assert "Motivo:" in html
    assert "No answer" in html
    assert "+56 2 2222" in html
    # Friendly source labels
    assert "Empresa" in html
    assert "Contacto (telefono)" in html


def test_note_no_extracted_data():
    attempts = [
        CallAttempt(phone_number="+56 1 1111", source="company", status="connected", conversation_id="c-1"),
    ]
    html = build_prospeccion_note("Test Hotel", attempts, None, "Agente: Hola")

    assert "Llamada de Prospeccion - Test Hotel" in html
    assert "Datos clave" not in html


def test_note_no_attempts():
    extracted = ExtractedCallData(hotel_name="Test")
    html = build_prospeccion_note("Test Hotel", [], extracted, None)

    assert "Intentos de llamada" not in html
    assert "Datos clave" in html
    assert "Test" in html


def test_note_html_escaping():
    """Special characters in company name should be escaped."""
    attempts = [
        CallAttempt(phone_number="+1", source="company", status="error", error="<script>alert(1)</script>"),
    ]
    html = build_prospeccion_note("<script>XSS</script>", attempts, None, None)

    assert "&lt;script&gt;XSS&lt;/script&gt;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html


def test_note_empty_extracted_fields():
    """ExtractedCallData with all None fields shows 'No proporcionado'."""
    extracted = ExtractedCallData()
    html = build_prospeccion_note("Hotel", [], extracted, None)

    assert "Datos clave" in html
    assert html.count("No proporcionado") == 6


def test_note_default_company_name():
    """None company name defaults to 'Empresa'."""
    html = build_prospeccion_note(None, [], None, None)
    assert "Empresa" in html


def test_note_failed_call():
    """Failed call shows 'No se pudo conectar'."""
    attempts = [
        CallAttempt(phone_number="+1", source="company", status="failed", error="Timeout"),
    ]
    html = build_prospeccion_note("Hotel", attempts, None, None)

    assert "No se pudo conectar" in html
    assert "Llamada conectada" not in html
