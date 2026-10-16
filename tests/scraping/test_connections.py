"""Contracts for the Desperado connection export workflows."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, call

import pytest

from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.scraping.contracts import (
    RATE_LIMITED_SECTION_TEXT,
    ExtractedSection,
)
from linkedin_mcp_server.scraping.connections import (
    CONNECTIONS_URL,
    ConnectionExporter,
    parse_contact_record,
)
from linkedin_mcp_server.scraping.capture import SectionCapture
from linkedin_mcp_server.scraping.content import PageContentReader
from linkedin_mcp_server.scraping.navigation import PageNavigator
from linkedin_mcp_server.scraping.session import ScrapingSession


def _exporter(*, evaluated=None, captures=()):
    page = SimpleNamespace(
        wait_for_selector=AsyncMock(),
        evaluate=AsyncMock(return_value=evaluated or []),
    )
    session = SimpleNamespace(
        page=page,
        check_rate_limit=AsyncMock(),
        dismiss_modal=AsyncMock(),
        scroll_body=AsyncMock(),
        delay=AsyncMock(),
    )
    navigator = SimpleNamespace(_navigate_to_page=AsyncMock())
    capture = SimpleNamespace(capture=AsyncMock(side_effect=list(captures)))
    content = SimpleNamespace(get_page_text=AsyncMock(return_value="Connections raw"))
    return (
        ConnectionExporter(
            cast(ScrapingSession, session),
            cast(PageNavigator, navigator),
            cast(SectionCapture, capture),
            cast(PageContentReader, content),
        ),
        session,
        navigator,
        capture,
        content,
    )


def test_parse_contact_record_preserves_the_legacy_structured_fields():
    profile = (
        "Ada Lovelace\n· 1st\nAnalytical Engineer\nLondon\nContact info\nBabbage Labs"
    )
    contact = (
        "Contact info\nEmail\n\nada@example.test\n\nPhone\n\n+44 000\n\n"
        "Website\n\nhttps://example.test (Portfolio)\n\nBirthday\n\n10 December"
    )

    assert parse_contact_record(profile, contact) == {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "headline": "Analytical Engineer",
        "location": "London",
        "company": "Babbage Labs",
        "email": "ada@example.test",
        "phone": "+44 000",
        "website": "https://example.test (Portfolio)",
        "birthday": "10 December",
    }


@pytest.mark.parametrize(
    ("limit", "max_scrolls"),
    [(-1, 10), (0, 0)],
)
async def test_collect_connections_rejects_unbounded_inputs(limit, max_scrolls):
    exporter, *_ = _exporter()

    with pytest.raises(ValueError):
        await exporter.collect_connections(limit=limit, max_scrolls=max_scrolls)


async def test_collect_connections_navigates_once_and_keeps_structured_results():
    connections = [
        {"username": "ada", "name": "Ada Lovelace", "headline": "Engineer"},
        {"username": "grace", "name": "Grace Hopper", "headline": "Admiral"},
    ]
    exporter, session, navigator, _, content = _exporter(evaluated=connections)

    result = await exporter.collect_connections(limit=1, max_scrolls=7)

    navigator._navigate_to_page.assert_awaited_once_with(CONNECTIONS_URL)
    session.check_rate_limit.assert_awaited_once_with()
    session.dismiss_modal.assert_awaited_once_with()
    session.scroll_body.assert_awaited_once_with(pause_time=1.0, max_scrolls=7)
    assert result["connections"] == connections[:1]
    assert result["total"] == 1
    assert result["url"] == CONNECTIONS_URL
    assert result["sections"]["connections"] == "Connections raw"
    content.get_page_text.assert_awaited_once_with()


async def test_enrich_contacts_chunks_progress_and_preserves_raw_sections():
    captures = (
        ExtractedSection(
            text="Ada Lovelace\n· 1st\nEngineer\nLondon\nContact info\nBabbage Labs",
            references=[],
        ),
        ExtractedSection(text="Email\n\nada@example.test", references=[]),
        ExtractedSection(text="Grace Hopper\n· 1st\nAdmiral\nNew York", references=[]),
        ExtractedSection(text="Phone\n\n+1 000", references=[]),
    )
    exporter, session, _, capture, _ = _exporter(captures=captures)
    progress = AsyncMock()

    result = await exporter.enrich_contacts(
        ["ada", "grace"],
        chunk_size=1,
        chunk_delay=12.5,
        progress_cb=progress,
    )

    assert [contact["username"] for contact in result["contacts"]] == [
        "ada",
        "grace",
    ]
    assert result["contacts"][0]["email"] == "ada@example.test"
    assert result["contacts"][1]["phone"] == "+1 000"
    assert result["failed"] == []
    assert result["rate_limited"] is False
    assert result["total"] == 2
    assert len(result["pages_visited"]) == 4
    assert set(result["sections"]) == {
        "ada_main_profile",
        "ada_contact_info",
        "grace_main_profile",
        "grace_contact_info",
    }
    progress.assert_has_awaits([call(1, 2), call(2, 2)])
    assert session.delay.await_args_list == [call(2.0), call(12.5), call(2.0)]
    assert capture.capture.await_count == 4


async def test_enrich_contacts_stops_on_a_hard_rate_limit():
    exporter, session, _, capture, _ = _exporter(
        captures=(RateLimitError("limited", suggested_wait_time=300),)
    )

    result = await exporter.enrich_contacts(["ada", "grace"])

    assert result["contacts"] == []
    assert result["failed"] == ["ada"]
    assert result["rate_limited"] is True
    assert capture.capture.await_count == 1
    session.delay.assert_not_awaited()


async def test_enrich_contacts_skips_a_soft_limited_profile():
    exporter, session, _, capture, _ = _exporter(
        captures=(
            ExtractedSection(text=RATE_LIMITED_SECTION_TEXT, references=[]),
            ExtractedSection(text="Grace Hopper\n· 1st\nAdmiral", references=[]),
            ExtractedSection(text="Email\n\ngrace@example.test", references=[]),
        )
    )

    result = await exporter.enrich_contacts(["ada", "grace"], chunk_size=2)

    assert [contact["username"] for contact in result["contacts"]] == ["grace"]
    assert result["failed"] == ["ada"]
    assert capture.capture.await_count == 3
    assert session.delay.await_args_list == [call(2.0), call(2.0)]
