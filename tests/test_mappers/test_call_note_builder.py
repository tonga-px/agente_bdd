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
    )
    transcript = "Agente: Hola\nHotel: Buenos dias"

    html = build_prospeccion_note("Hotel Paraiso", attempts, extracted, transcript)

    assert "Llamada de Prospeccion - Hotel Paraiso" in html
    assert "Fecha:" in html
    # Attempts section
    assert "Intentos de llamada" in html
    assert "+56 1 1111" in html
    assert "company" in html
    assert "No answer" in html
    assert "+56 2 2222" in html
    assert "connected" in html
    # Extracted data section
    assert "Datos extraidos" in html
    assert "Hotel Paraiso" in html
    assert "120" in html
    assert "Juan Perez" in html
    assert "+56 9 8888" in html
    assert "juan@paraiso.cl" in html
    # Transcript section
    assert "Transcripcion" in html
    assert "Agente: Hola" in html
    assert "Hotel: Buenos dias" in html


def test_note_no_extracted_data():
    attempts = [
        CallAttempt(phone_number="+56 1 1111", source="company", status="connected", conversation_id="c-1"),
    ]
    html = build_prospeccion_note("Test Hotel", attempts, None, "Agente: Hola")

    assert "Llamada de Prospeccion - Test Hotel" in html
    assert "Datos extraidos" not in html
    assert "Transcripcion" in html


def test_note_no_transcript():
    attempts = []
    extracted = ExtractedCallData(hotel_name="Test")
    html = build_prospeccion_note("Test Hotel", attempts, extracted, None)

    assert "Intentos de llamada" not in html
    assert "Datos extraidos" in html
    assert "Transcripcion" not in html


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
    """ExtractedCallData with all None fields should not produce a section."""
    extracted = ExtractedCallData()
    html = build_prospeccion_note("Hotel", [], extracted, None)

    assert "Datos extraidos" not in html


def test_note_default_company_name():
    """None company name defaults to 'Empresa'."""
    html = build_prospeccion_note(None, [], None, None)
    assert "Empresa" in html
