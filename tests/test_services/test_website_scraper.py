"""Tests for WebsiteScraperService."""

import httpx
import pytest
import respx
from httpx import Response

from app.services.website_scraper import WebsiteScraperService


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.fixture
def scraper(client):
    return WebsiteScraperService(client)


def _html(body: str) -> str:
    return f"<html><body>{body}</body></html>"


# --- Phone extraction ---


@respx.mock
async def test_extracts_tel_links(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html('<a href="tel:+5491152630435">Llamar</a>'),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.phones == ["+5491152630435"]


@respx.mock
async def test_extracts_phones_from_text_regex(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html("<p>Tel: +54 11 5263-0435</p>"),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert "+541152630435" in result.phones


@respx.mock
async def test_tel_links_prioritized_over_text(scraper):
    """tel: link phones come before regex-found phones."""
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html(
                '<a href="tel:+111111111">Phone 1</a>'
                "<p>Call us: +22 2222 2222</p>"
            ),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.phones[0] == "+111111111"


@respx.mock
async def test_phone_dedup_by_digits(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html(
                '<a href="tel:+54-11-5263-0435">Call</a>'
                "<p>+54 11 5263 0435</p>"
            ),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert len(result.phones) == 1


@respx.mock
async def test_phone_min_7_digits_filter(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html('<a href="tel:12345">Short</a><a href="tel:+541112345678">Long</a>'),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.phones == ["+541112345678"]


# --- WhatsApp extraction ---


@respx.mock
async def test_extracts_wa_me_link(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html('<a href="https://wa.me/5491123530759">WhatsApp</a>'),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.whatsapp == "+5491123530759"


@respx.mock
async def test_extracts_whatsapp_api_link(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html('<a href="https://api.whatsapp.com/send?phone=5491123530759">WA</a>'),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.whatsapp == "+5491123530759"


# --- Email extraction ---


@respx.mock
async def test_extracts_mailto_links(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html('<a href="mailto:reservas@hotel.com">Email</a>'),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.emails == ["reservas@hotel.com"]


@respx.mock
async def test_extracts_emails_from_text(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html("<p>Email: info@hotel.com</p>"),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert "info@hotel.com" in result.emails


@respx.mock
async def test_email_preference_ranking(scraper):
    """Emails ranked: reserva > info > contacto > booking > other."""
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html(
                '<a href="mailto:booking@hotel.com">Booking</a>'
                '<a href="mailto:reservas@hotel.com">Reservas</a>'
                '<a href="mailto:info@hotel.com">Info</a>'
            ),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.emails[0] == "reservas@hotel.com"
    assert result.emails[1] == "info@hotel.com"
    assert result.emails[2] == "booking@hotel.com"


@respx.mock
async def test_blocked_email_domains_filtered(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html(
                '<a href="mailto:hotel@google.com">G</a>'
                '<a href="mailto:real@hotel.com">Real</a>'
            ),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.emails == ["real@hotel.com"]


@respx.mock
async def test_blocked_email_prefixes_filtered(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html(
                '<a href="mailto:noreply@hotel.com">No</a>'
                '<a href="mailto:admin@hotel.com">Admin</a>'
                '<a href="mailto:reservas@hotel.com">Res</a>'
            ),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.emails == ["reservas@hotel.com"]


# --- Contact page following ---


@respx.mock
async def test_follows_contact_page_for_emails(scraper):
    """If main page has no emails, follow /contacto link."""
    # Register more specific route first to avoid prefix matching
    respx.get("https://hotel.com/contacto").mock(
        return_value=Response(
            200,
            html=_html('<a href="mailto:info@hotel.com">Mail</a>'),
            headers={"content-type": "text/html"},
        )
    )
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html('<a href="/contacto">Contacto</a>'),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.emails == ["info@hotel.com"]


@respx.mock
async def test_does_not_follow_external_links(scraper):
    """Only follows contact links on same domain."""
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html('<a href="https://other.com/contacto">Contacto</a>'),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.emails == []


# --- Edge cases ---


@respx.mock
async def test_non_html_skip(scraper):
    respx.get("https://hotel.com/file.pdf").mock(
        return_value=Response(
            200,
            content=b"PDF content",
            headers={"content-type": "application/pdf"},
        )
    )
    result = await scraper.scrape("https://hotel.com/file.pdf")
    assert result.phones == []
    assert result.emails == []


@respx.mock
async def test_404_returns_empty(scraper):
    respx.get("https://hotel.com").mock(return_value=Response(404))
    result = await scraper.scrape("https://hotel.com")
    assert result.phones == []
    assert result.emails == []
    assert result.source_url == "https://hotel.com"


@respx.mock
async def test_timeout_returns_empty(scraper):
    respx.get("https://hotel.com").mock(side_effect=httpx.ReadTimeout("timeout"))
    result = await scraper.scrape("https://hotel.com")
    assert result.phones == []
    assert result.emails == []


@respx.mock
async def test_source_url_preserved(scraper):
    respx.get("https://hotel.com").mock(
        return_value=Response(
            200,
            html=_html("<p>Nothing here</p>"),
            headers={"content-type": "text/html"},
        )
    )
    result = await scraper.scrape("https://hotel.com")
    assert result.source_url == "https://hotel.com"
